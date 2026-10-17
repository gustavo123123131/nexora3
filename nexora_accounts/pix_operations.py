"""Read-only Pix binding preparation, never a remote Pix operation.

The ProxyBet trace is synthetic, not an authenticated provider contract.
No code in this module can submit a withdrawal or bind a payment key.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import tempfile
import threading
from uuid import UUID, uuid4

from nexora_accounts.local_profiles import LocalProfileError, LocalProfileStore
from nexora_accounts.pix_key_store import PixKeyError, PixKeyStore, _disk_lock

_LOCK = threading.RLock()
LOG = logging.getLogger("nexora.pix.operations")
TYPES = frozenset({"CPF", "PHONE", "EMAIL", "EVP"})
BLOCKERS = (
    "Contrato externo de cadastro Pix não confirmado.",
    "Autenticação da sessão e identidade financeira não comprovadas.",
    "Titularidade real do recebedor não validada.",
    "Requisitos de PIN/verificação adicional não confirmados.",
)


class PixPreparationError(ValueError):
    """Public error text must never include Pix values or credentials."""


def _now():
    return datetime.now(timezone.utc).isoformat()


def _default_path():
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_STATE_HOME")
    return (Path(base) if base else Path.home() / ".local" / "share") / "Nexora" / "pix_operations.json"


def masked(value: str, key_type: str) -> str:
    """Show no complete personal identifier in logs, receipts or API replies."""
    if key_type == "EMAIL":
        return "***@***"
    if key_type == "CPF":
        return "***.***.***-**"
    if key_type == "PHONE":
        return "(**) *****-****"
    return "********-****-****-****-************"


def remote_ref(uid: str) -> str:
    """Reference only; never disclose the full platform UID in the audit log."""
    digits = str(uid)
    return "••••" + digits[-3:] if digits.isdecimal() else "não identificado"


def _event(stage: str, *, reason: str = "", elapsed_ms: int = 0) -> dict:
    # No request was made. Empty endpoint/status are intentional, not synthetic.
    return {"at": _now(), "stage": stage, "detail": reason,
            "elapsedMs": elapsed_ms, "httpMethod": None,
            "endpoint": None, "httpStatus": None, "responseShape": None}


def _is_uuid(value) -> bool:
    try:
        return isinstance(value, str) and str(UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


class PixOperationStore:
    def __init__(self, path=None, *, profile_store=None, key_store=None):
        self.path = Path(path) if path is not None else _default_path()
        self.profiles = profile_store or LocalProfileStore()
        self.keys = key_store or PixKeyStore()

    @contextmanager
    def _locked(self):
        with _LOCK:
            with _disk_lock(self.path):
                yield

    def _read(self):
        if self.path.is_symlink():
            raise PixPreparationError("Histórico de operações inválido; preserve para diagnóstico.")
        if not self.path.exists():
            return {"version": 1, "operations": []}
        try:
            obj = json.loads(self.path.read_text(encoding="utf-8"))
            if (not isinstance(obj, dict) or obj.get("version") != 1 or
                    not isinstance(obj.get("operations"), list) or
                    len(obj["operations"]) > 10000):
                raise ValueError("shape")
            for op in obj["operations"]:
                if (not isinstance(op, dict) or
                    not _is_uuid(op.get("operationId")) or
                    not _is_uuid(op.get("accountId")) or
                    not _is_uuid(op.get("keyId")) or
                    type(op.get("profileId")) is not int or
                    op.get("status") != "prepared" or
                    op.get("keyType") not in TYPES or
                    not isinstance(op.get("events"), list)):
                    raise ValueError("record")
            return obj
        except (OSError, ValueError, TypeError, UnicodeError) as exc:
            raise PixPreparationError(
                "Histórico de operações inválido; preserve para diagnóstico."
            ) from exc

    def _write(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        name = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent,
                prefix=".pix-operation-", suffix=".tmp", delete=False,
            ) as file:
                name = file.name
                json.dump(data, file, ensure_ascii=False, indent=2)
                file.flush()
                os.fsync(file.fileno())
            os.chmod(name, 0o600)
            os.replace(name, self.path)
        finally:
            if name and os.path.exists(name):
                os.unlink(name)

    def list(self, profile_id=None):
        if profile_id is not None and (
            type(profile_id) is not int or profile_id < 1
        ):
            raise PixPreparationError("Conta selecionada inválida.")
        with self._locked():
            operations = self._read()["operations"]
            return {"operations": [
                dict(op) for op in reversed(operations)
                if profile_id is None or op["profileId"] == profile_id
            ][:60], "providerIntegration": "unavailable"}

    def prepare(self, *, profile_id, account_id, key_id, key_type,
                ownership_acknowledged=False):
        # This operation only validates LOCAL records; never check a remote
        # network, submit a PIN, create a Pix key, or alter the stored pool.
        if type(profile_id) is not int or profile_id < 1 or not _is_uuid(account_id):
            raise PixPreparationError("Conta selecionada inválida ou divergente.")
        if not _is_uuid(key_id) or key_type not in TYPES:
            raise PixPreparationError("Seleção da chave Pix inválida.")
        if ownership_acknowledged is not True:
            raise PixPreparationError("Confirme que está autorizado a usar esta chave Pix.")
        try:
            profile = self.profiles.public_detail(profile_id)
        except LocalProfileError as exc:
            raise PixPreparationError("Conta não encontrada.") from exc
        if profile.get("accountId") != account_id:
            raise PixPreparationError("A conta mudou desde a seleção. Atualize Operações.")
        uid = profile.get("platformUserId") or profile.get("platformObservedUserId")
        if not isinstance(uid, str) or not uid.isdecimal() or not (0 < int(uid) <= 2**53 - 1):
            raise PixPreparationError("ID da plataforma ausente ou inválido.")
        identity_status = "verified" if profile.get("platformUserId") else "observed"
        identity_seen_at = (profile.get("platformIdVerifiedAt") if
                            identity_status == "verified" else
                            profile.get("platformObservedAt"))
        keys = self.keys.list().get("keys", [])
        key = next((k for k in keys if k.get("id") == key_id), None)
        if key is None:
            raise PixPreparationError("Chave Pix não encontrada. Confira a aba Chave Pix.")
        if key.get("type") != key_type:
            raise PixPreparationError("A chave escolhida não pertence ao tipo solicitado.")
        # Trust the current, validated store value. Never store the raw key
        # or the account login credentials in operation history.
        from nexora_accounts.pix_key_store import normalize_key
        try:
            normalize_key(key["value"], key_type)
        except (KeyError, PixKeyError) as exc:
            raise PixPreparationError("Chave Pix inválida no armazenamento local.") from exc

        with self._locked():
            data = self._read()
            for op in reversed(data["operations"]):
                if (op["accountId"] == account_id and op["profileId"] == profile_id
                    and op["keyId"] == key_id and op["keyType"] == key_type
                    and op.get("keyUpdatedAt") == key.get("updatedAt")
                    and op.get("platformIdentityRef") == remote_ref(uid)
                    and op.get("identityStatus") == identity_status
                    and op.get("identitySeenAt") == identity_seen_at):
                    LOG.info(
                        "[Pix] preparo existente op=%s perfil=%s uid=%s tipo=%s",
                        op["operationId"], profile_id, remote_ref(uid), key_type,
                    )
                    return {"operation": dict(op), "reused": True}
            timestamp = _now()
            op = {
                "operationId": str(uuid4()), "profileId": profile_id,
                "accountId": account_id, "platformIdentityRef": remote_ref(uid),
                "identityStatus": identity_status,
                "identitySeenAt": identity_seen_at, "keyId": key_id,
                "keyType": key_type, "keyMasked": masked(key["value"], key_type),
                "keyUpdatedAt": key.get("updatedAt"), "status": "prepared",
                "providerStatus": "not_submitted", "externalRequestSent": False,
                "createdAt": timestamp, "updatedAt": timestamp,
                "missingRequirements": list(BLOCKERS),
                "events": [
                    _event("account_checked", reason="ID local/UUID consistentes"),
                    _event("pix_key_checked", reason="Tipo e registro local consistentes"),
                    _event("ownership_acknowledged", reason="Declaração do operador; não verifica titularidade"),
                    _event("provider_blocked", reason="Contrato remoto não comprovado; nenhum POST externo enviado"),
                ],
            }
            data["operations"].append(op)
            self._write(data)
            LOG.info(
                "[Pix] op=%s perfil=%s uid=%s tipo=%s estado=prepared "
                "envioExterno=nao motivo=contrato_nao_verificado",
                op["operationId"], profile_id, remote_ref(uid), key_type,
            )
            return {"operation": dict(op), "reused": False}


# A provider adapter can be implemented later only after its actual schema,
# authorization and outcome confirmation are independently established.
# Deliberately expose no submit/bind endpoint in the application.
