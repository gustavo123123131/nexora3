"""Read-only withdrawal prerequisites and history for a selected account.

This module records consultation results; it does not submit cashV3 requests.
Saved records survive application restarts.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import os
import tempfile
import threading
from uuid import UUID, uuid4

from nexora_accounts.local_profiles import LocalProfileStore
from nexora_accounts.pix_key_store import _disk_lock

_LOCK = threading.RLock()
_STATES = {"queued", "blocked", "indeterminate"}
_STAGES = {"local", "site", "client", "withdrawInfoV2",
           "gameCenter/gold", "getWithdrawFee", "contract"}
_EVENTS = {"request_start", "api_accepted", "api_rejected",
           "request_exception", "result", "shape_checked"}


class WithdrawalPreparationError(ValueError):
    pass


def _uuid(value):
    try:
        return type(value) is str and str(UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def _now():
    return datetime.now(timezone.utc).isoformat()


def _path():
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_STATE_HOME")
    return (Path(base) if base else Path.home() / ".local" / "share") / "Nexora" / "withdrawal_operations.json"


def _live(profile_id, op_id, message):
    try:
        from webui.live_logs import publish
        publish("Sistema", f"[Saque] Conta #{int(profile_id)} op={str(op_id)[:8]} {message}")
    except Exception:
        pass


def describe_preflight_for_log(operation: dict) -> str:
    """Show the stored preflight cause, never the network payload or credentials."""
    stage = operation.get("stage", "client")
    if stage not in _STAGES:
        stage = "client"
    status = operation.get("status", "indeterminate")
    if status not in _STATES:
        status = "indeterminate"
    # The cause comes from the existing WithdrawalStore.finish() allowlist.
    # Unknown or legacy entries are deliberately not echoed as raw text.
    cause = operation.get("message", "")
    allowed_causes = {
        "PIN de saque não confirmado pela plataforma.",
        "Nenhum destino de saque vinculado nesta conta.",
        "Mais de um destino remoto: seleção precisa de contrato validado.",
        "Conta de destino sem identificador remoto comprovado.",
        "Consulta de saldo falhou ou não é suportada.",
        "Consulta de taxa falhou ou não é suportada.",
        "Limites de saque não foram identificados.",
        "Saldo realmente sacável não foi identificado em userInfos.game_gold.",
        "Saldo disponível inferior ao mínimo permitido.",
        "Tipo de saque remoto não identificado.",
        "Taxa do saque não foi confirmada.",
        "Consultas de saque válidas; envio financeiro requer contrato autorizado.",
        "Não foi possível consultar a conta de saque.",
        "Não foi possível carregar o cliente autenticado da página.",
        "Navegador fora do domínio autorizado.",
        "O valor realmente sacável e os campos de envio ainda não foram validados no domínio real.",
        "Dados de saque não retornaram no formato esperado.",
        "Sessão da conta não corresponde à operação.",
        "Pré-requisitos não verificados; nenhum saque enviado.",
    }
    if cause not in allowed_causes:
        cause = "Motivo não identificado; consulte o histórico da operação."
    parts = [f"diagnóstico={stage}", f"status={status}", f"motivo={cause}"]
    for event in reversed(operation.get("events", [])):
        if not isinstance(event, dict) or event.get("stage") != stage:
            continue
        if event.get("event") not in {"api_accepted", "api_rejected", "request_exception"}:
            continue
        for name, title in (("httpStatus", "HTTP"), ("providerCode", "códigoAPI")):
            value = event.get(name)
            if type(value) is int and 0 <= value <= 999999:
                parts.append(f"{title}={value}")
        break
    parts.append("saque enviado=não")
    return "; ".join(parts)


class WithdrawalStore:
    def __init__(self, path=None, *, profiles=None):
        self.path = Path(path) if path else _path()
        self.profiles = profiles or LocalProfileStore()

    def _read(self):
        if self.path.is_symlink():
            raise WithdrawalPreparationError("Histórico de saques inválido.")
        if not self.path.exists():
            return {"version": 1, "operations": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if (not isinstance(data, dict) or data.get("version") != 1 or
                    not isinstance(data.get("operations"), list) or
                    len(data["operations"]) > 10000):
                raise ValueError("invalid")
            for item in data["operations"]:
                if (not isinstance(item, dict) or not _uuid(item.get("operationId"))
                        or not _uuid(item.get("accountId"))
                        or item.get("status") not in _STATES):
                    raise ValueError("invalid")
            return data
        except (OSError, ValueError, TypeError, UnicodeError) as exc:
            raise WithdrawalPreparationError("Histórico de saques inválido.") from exc

    def _write(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        pending = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent,
                prefix=".withdrawal-", suffix=".tmp", delete=False
            ) as file:
                pending = file.name
                json.dump(data, file, ensure_ascii=False, indent=2)
                file.flush()
                os.fsync(file.fileno())
            os.chmod(pending, 0o600)
            os.replace(pending, self.path)
        finally:
            if pending and os.path.exists(pending):
                os.unlink(pending)

    def list(self, profile_id: int):
        if type(profile_id) is not int or profile_id < 1:
            raise WithdrawalPreparationError("Conta inválida.")
        with _LOCK, _disk_lock(self.path):
            return {"operations": [
                dict(x) for x in reversed(self._read()["operations"])
                if x.get("profileId") == profile_id
            ][:60], "submissionEnabled": False}

    def reserve(self, *, profile_id, account_id, run_id):
        if (type(profile_id) is not int or profile_id < 1 or
                not _uuid(account_id) or not _uuid(run_id)):
            raise WithdrawalPreparationError("Conta ou sessão inválida.")
        detail = self.profiles.public_detail(profile_id)
        if detail.get("accountId") != account_id or detail.get("lastSessionId") != run_id:
            raise WithdrawalPreparationError("A conta selecionada ou a sessão mudou.")
        with _LOCK, _disk_lock(self.path):
            data = self._read()
            for old in reversed(data["operations"]):
                if old.get("accountId") == account_id and old.get("status") in (
                        "queued", "indeterminate"):
                    return {"operation": dict(old), "reused": True}
            now = _now()
            op = {"operationId": str(uuid4()), "profileId": profile_id,
                  "accountId": account_id, "runId": run_id,
                  "status": "queued", "stage": "local",
                  "message": "Consulta dos pré-requisitos agendada.",
                  "submissionSent": False, "createdAt": now,
                  "updatedAt": now, "events": []}
            data["operations"].append(op)
            self._write(data)
            return {"operation": dict(op), "reused": False}

    def get(self, op_id, *, profile_id, account_id, run_id):
        with _LOCK, _disk_lock(self.path):
            found = next((x for x in self._read()["operations"]
                          if x.get("operationId") == op_id and
                          x.get("profileId") == profile_id and
                          x.get("accountId") == account_id and
                          x.get("runId") == run_id), None)
            if found is None:
                raise WithdrawalPreparationError("Operação pertence a outra conta ou sessão.")
            return dict(found)

    def finish(self, op_id, *, profile_id, account_id, run_id, result):
        stage = result.get("stage", "client")
        if stage not in _STAGES:
            stage = "client"
        message = result.get("message", "")
        if not isinstance(message, str) or message not in {
            "PIN de saque não confirmado pela plataforma.",
            "Nenhum destino de saque vinculado nesta conta.",
            "Mais de um destino remoto: seleção precisa de contrato validado.",
            "Conta de destino sem identificador remoto comprovado.",
            "Consulta de saldo falhou ou não é suportada.",
            "Consulta de taxa falhou ou não é suportada.",
            "Limites de saque não foram identificados.",
            "Saldo realmente sacável não foi identificado em userInfos.game_gold.",
            "Saldo disponível inferior ao mínimo permitido.",
            "Tipo de saque remoto não identificado.",
            "Taxa do saque não foi confirmada.",
            "Consultas de saque válidas; envio financeiro requer contrato autorizado.",
            "Não foi possível consultar a conta de saque.",
            "Não foi possível carregar o cliente autenticado da página.",
            "Navegador fora do domínio autorizado.",
            "O valor realmente sacável e os campos de envio ainda não foram validados no domínio real.",
            "Dados de saque não retornaram no formato esperado.",
            "Sessão da conta não corresponde à operação."
        }:
            message = "Pré-requisitos não verificados; nenhum saque enviado."
        events = []
        trace = result.get("trace", [])
        if isinstance(trace, list):
            for row in trace[:25]:
                if (isinstance(row, dict) and row.get("stage") in _STAGES
                        and row.get("event") in _EVENTS):
                    clean = {"stage": row["stage"], "event": row["event"]}
                    for key in ("httpStatus", "providerCode", "elapsedMs"):
                        if type(row.get(key)) is int and 0 <= row[key] <= 999999:
                            clean[key] = row[key]
                    events.append(clean)
        with _LOCK, _disk_lock(self.path):
            data = self._read()
            target = next((x for x in data["operations"]
                           if x.get("operationId") == op_id and
                           x.get("profileId") == profile_id and
                           x.get("accountId") == account_id and
                           x.get("runId") == run_id), None)
            if target is None:
                raise WithdrawalPreparationError("Operação da conta não encontrada.")
            if target["status"] != "queued":
                return dict(target)
            target.update(status="blocked", stage=stage, message=message,
                          updatedAt=_now(), submissionSent=False,
                          events=events)
            self._write(data)
            return dict(target)
