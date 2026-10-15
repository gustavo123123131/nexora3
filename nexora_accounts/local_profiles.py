"""Generate and persist demonstration profiles entirely on the local Nexora agent.

No browser, HTTP client, external form or account-registration integration is used.
Names, contact details and document numbers are synthetic and not verified.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import string
import tempfile
import threading
import unicodedata

_LOCK = threading.RLock()
_FIRST_NAMES = (
    "Ana", "Beatriz", "Bianca", "Bruna", "Camila", "Carolina",
    "Daniel", "Eduardo", "Felipe", "Gabriel", "Helena", "Isabela",
    "João", "Julia", "Larissa", "Lucas", "Mariana", "Mateus",
    "Nicolas", "Patricia", "Pedro", "Rafael", "Renata", "Sofia",
    "Thiago", "Vitoria",
)
_MIDDLE_NAMES = (
    "Alice", "Cristina", "Eduarda", "Fernanda", "Gabriela",
    "Henrique", "Luiza", "Maria", "Miguel", "Oliveira",
)
_LAST_NAMES = (
    "Almeida", "Barbosa", "Cardoso", "Carvalho", "Costa",
    "Ferreira", "Gomes", "Lima", "Martins", "Mendes",
    "Moreira", "Oliveira", "Pereira", "Ribeiro", "Rocha",
    "Santos", "Silva", "Souza",
)
_DDDS = ("11", "12", "13", "21", "22", "27", "31", "32", "41", "47", "51", "61", "62", "71", "81", "85")


class LocalProfileError(ValueError):
    """Safe error displayed to the operator, never containing credentials."""


def _default_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_STATE_HOME")
    directory = Path(base) if base else Path.home() / ".local" / "share"
    return directory / "Nexora" / "generated_profiles.json"


def _digits(n: int) -> str:
    return "".join(str(secrets.randbelow(10)) for _ in range(n))


def _new_cpf() -> str:
    """Generate eleven checksum-consistent digits, not a verified identity."""
    while True:
        digits = [secrets.randbelow(10) for _ in range(9)]
        if len(set(digits)) == 1:
            continue
        for size in (9, 10):
            check = (sum(digits[i] * (size + 1 - i) for i in range(size)) * 10) % 11 % 10
            digits.append(check)
        value = "".join(map(str, digits))
        return f"{value[:3]}.{value[3:6]}.{value[6:9]}-{value[9:]}"


def _new_password() -> str:
    symbols = "#@!$%"
    alphabet = string.ascii_letters + string.digits + symbols
    chars = [
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.digits),
        secrets.choice(symbols),
    ]
    chars.extend(secrets.choice(alphabet) for _ in range(5))
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def _username(full_name: str, existing: set[str]) -> str:
    normalized = unicodedata.normalize("NFKD", full_name)
    normalized = "".join(c for c in normalized if not unicodedata.combining(c)).lower()
    pieces = ["".join(c for c in p if c in string.ascii_lowercase) for p in normalized.split()]
    first = (pieces[0] if pieces else "user")[:4]
    last = (pieces[-1] if pieces else "perfil")[:2]
    stem = (first + last).ljust(6, "x")[:6]
    for _ in range(1000):
        # About 1.68 million possible suffixes for each name stem.
        suffix = "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(4))
        name = stem + suffix
        if name not in existing:
            return name
    raise LocalProfileError("Não foi possível criar um username exclusivo.")


class LocalProfileStore:
    """Small append-only JSON repository; the file stays on the Windows agent."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else _default_path()

    def _read(self) -> dict:
        if not self.path.exists():
            return {"nextNumber": 1, "profiles": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if (
                not isinstance(data, dict)
                or not isinstance(data.get("profiles"), list)
                or not isinstance(data.get("nextNumber"), int)
                or data["nextNumber"] < 1
            ):
                raise ValueError("invalid profile file")
            return data
        except (OSError, ValueError, UnicodeError) as exc:
            raise LocalProfileError(
                "O arquivo local de perfis está inválido. Faça backup antes de alterar."
            ) from exc

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.path.parent.chmod(0o700)
        except OSError:
            pass
        temp_name = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent,
                prefix=".profiles-", suffix=".tmp", delete=False,
            ) as stream:
                temp_name = stream.name
                json.dump(data, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temp_name, 0o600)
            os.replace(temp_name, self.path)
        finally:
            if temp_name and os.path.exists(temp_name):
                os.unlink(temp_name)

    def list(self) -> list[dict]:
        with _LOCK:
            return [p.copy() for p in self._read()["profiles"]]

    def selected_id(self) -> int | None:
        with _LOCK:
            value = self._read().get("selectedProfileId")
            return value if isinstance(value, int) and not isinstance(value, bool) else None

    def selected(self) -> dict | None:
        with _LOCK:
            data = self._read()
            for p in data["profiles"]:
                if p["id"] == data.get("selectedProfileId"):
                    return p.copy()
            return None

    def select(self, profile_id: int | None) -> int | None:
        with _LOCK:
            data = self._read()
            if profile_id is not None:
                if isinstance(profile_id, bool) or not isinstance(profile_id, int):
                    raise LocalProfileError("ID da conta inválido.")
                if not any(p["id"] == profile_id for p in data["profiles"]):
                    raise LocalProfileError("Conta não encontrada.")
            data["selectedProfileId"] = profile_id
            self._write(data)
            return profile_id

    def get(self, profile_id: int) -> dict:
        if type(profile_id) is not int or profile_id < 1:
            raise LocalProfileError("ID da conta inválido.")
        with _LOCK:
            profile = next(
                (p.copy() for p in self._read()["profiles"] if p["id"] == profile_id),
                None,
            )
        if profile is None:
            raise LocalProfileError("Conta não encontrada.")
        return profile

    def set_runtime_config(self, profile_id: int, runtime: dict) -> dict:
        """Store the exact device configuration *once*; never regenerate on reopen."""
        if type(profile_id) is not int or not isinstance(runtime, dict):
            raise LocalProfileError("Dados da sessão inválidos.")
        from Settings.device_modes import resolve_mode
        required = (
            "mode", "user_agent", "viewport", "device_scale_factor",
            "is_mobile", "has_touch", "webgl_vendor", "webgl_renderer",
            "extra_headers",
        )
        if any(key not in runtime for key in required):
            raise LocalProfileError("Configuração de dispositivo incompleta.")
        try:
            mode = resolve_mode(runtime["mode"])
            vp = runtime["viewport"]
            if mode == "mixed" or not isinstance(vp, dict):
                raise ValueError("invalid device")
            width, height = int(vp["width"]), int(vp["height"])
            scale = float(runtime["device_scale_factor"])
            if width < 200 or height < 200 or scale <= 0 or scale > 10:
                raise ValueError("invalid viewport")
            if any(not isinstance(runtime[k], str) or not runtime[k].strip()
                   for k in ("user_agent", "webgl_vendor", "webgl_renderer")):
                raise ValueError("invalid identity")
            if type(runtime["is_mobile"]) is not bool or type(runtime["has_touch"]) is not bool:
                raise ValueError("invalid mobile flags")
            if not isinstance(runtime["extra_headers"], dict):
                raise ValueError("invalid headers")
            snapshot = {
                "mode": mode,
                "user_agent": runtime["user_agent"],
                "viewport": {"width": width, "height": height},
                "device_scale_factor": scale,
                "is_mobile": runtime["is_mobile"],
                "has_touch": runtime["has_touch"],
                "webgl_vendor": runtime["webgl_vendor"],
                "webgl_renderer": runtime["webgl_renderer"],
                "extra_headers": runtime["extra_headers"].copy(),
            }
        except (TypeError, ValueError, KeyError) as exc:
            raise LocalProfileError("Configuração original de dispositivo inválida.") from exc
        with _LOCK:
            state = self._read()
            item = next((p for p in state["profiles"] if p["id"] == profile_id), None)
            if item is None:
                raise LocalProfileError("Conta não encontrada.")
            existing = item.get("runtimeConfig")
            if existing is not None:
                if existing != snapshot:
                    raise LocalProfileError("O dispositivo da conta já está fixado e não pode ser substituído.")
                return item.copy()
            if item.get("deviceMode") != mode:
                raise LocalProfileError("Dispositivo da sessão não corresponde à conta.")
            item["runtimeConfig"] = snapshot
            item["deviceLabel"] = self._device_label(snapshot)
            item["updatedAt"] = datetime.now(timezone.utc).isoformat()
            self._write(state)
            return item.copy()

    @staticmethod
    def _device_label(runtime: dict) -> str:
        import re
        mode = runtime.get("mode")
        agent = runtime.get("user_agent", "")
        if mode == "android":
            m = re.search(r"Android[^;)]*;\s*([^;)]+)", agent, flags=re.I)
            model = m.group(1).strip() if m else ""
            model = model.split(" Build/")[0].strip()
            return model[:55] if model and model.lower() not in ("wv", "mobile") else "Android"
        if mode == "iphone":
            return "iPhone"
        return "Computador"

    def update_status(self, profile_id: int, status: str, message: str = "") -> dict:
        """Never infer success from form filling, click or an HTTP 200 alone."""
        if status not in ("pending", "created", "error"):
            raise LocalProfileError("Status inválido.")
        if type(profile_id) is not int:
            raise LocalProfileError("ID da conta inválido.")
        with _LOCK:
            state = self._read()
            item = next((p for p in state["profiles"] if p["id"] == profile_id), None)
            if item is None:
                raise LocalProfileError("Conta não encontrada.")
            if item.get("status") == "created" and status != "created":
                # A later browser/network failure cannot revoke registration.
                return item.copy()
            item["status"] = status
            item["statusMessage"] = str(message or "")[:220]
            item["updatedAt"] = datetime.now(timezone.utc).isoformat()
            self._write(state)
            return item.copy()

    def configure_legacy_launch(self, profile_id: int, device_mode: str, start_url: str = "") -> dict:
        """Capture missing launch settings once for cards created before this fix."""
        from Settings.device_modes import resolve_mode
        from Settings.url_normalizer import normalize_url_list
        try:
            mode = resolve_mode(device_mode)
        except ValueError as exc:
            raise LocalProfileError("Escolha Android, iPhone ou Computador.") from exc
        if mode == "mixed":
            raise LocalProfileError("Selecione somente um dispositivo para a conta.")
        raw_url = str(start_url or "").strip()
        if len(raw_url) > 2048:
            raise LocalProfileError("URL muito longa.")
        if raw_url:
            try:
                urls = normalize_url_list(raw_url)
            except ValueError as exc:
                raise LocalProfileError("URL da conta inválida.") from exc
            if len(urls) != 1:
                raise LocalProfileError("Informe somente uma URL para a conta.")
            raw_url = urls[0]
        with _LOCK:
            data = self._read()
            item = next((p for p in data["profiles"] if p["id"] == profile_id), None)
            if item is None:
                raise LocalProfileError("Conta não encontrada.")
            if item.get("deviceMode"):
                # A normal card's remembered device is never overwritten by
                # a stale UI value or a malicious re-open request.
                return item.copy()
            item["deviceMode"] = mode
            item["startUrl"] = raw_url
            self._write(data)
            return item.copy()

    def create(
        self, origin: str, *,
        device_mode: str | None = None,
        start_url: str | None = None,
        connection_mode: str | None = None,
    ) -> dict:
        if origin not in ("mae", "filha"):
            raise LocalProfileError("Origem do perfil inválida.")
        if device_mode is not None:
            from Settings.device_modes import resolve_mode
            try:
                resolved = resolve_mode(device_mode)
            except ValueError as exc:
                raise LocalProfileError("Dispositivo inválido para a conta.") from exc
            if resolved == "mixed":
                raise LocalProfileError("Escolha Android ou iPhone para esta conta.")
            device_mode = resolved
        if start_url is not None and (
            not isinstance(start_url, str) or len(start_url) > 2048
        ):
            raise LocalProfileError("URL inicial inválida.")
        if connection_mode is not None and connection_mode not in ("managed", "direct", "legacy"):
            raise LocalProfileError("Tipo de conexão inválido.")
        with _LOCK:
            data = self._read()
            number = data["nextNumber"]
            first = secrets.choice(_FIRST_NAMES)
            surname = secrets.choice(_LAST_NAMES)
            parts = [first]
            if secrets.randbelow(2):
                parts.append(secrets.choice(_MIDDLE_NAMES))
            parts.append(surname)
            full_name = " ".join(parts)
            username = _username(
                full_name,
                {str(p.get("username", "")) for p in data["profiles"] if isinstance(p, dict)},
            )
            password = _new_password()
            ddd = secrets.choice(_DDDS)
            phone = "9" + _digits(8)
            record = {
                "id": number,
                "label": f"Conta {number}",
                "origin": origin,
                "fullName": full_name,
                "username": username,
                "email": f"{username}@gmail.com",
                "phone": f"({ddd}) {phone[:5]}-{phone[5:]}",
                "cpf": _new_cpf(),
                "password": password,
                "confirmPassword": password,
                "createdAt": datetime.now(timezone.utc).isoformat(),
                "status": "pending",
                "statusMessage": "Aguardando confirmação do cadastro pelo site.",
            }
            if device_mode is not None:
                record["deviceMode"] = device_mode
            if start_url is not None:
                record["startUrl"] = start_url
            if connection_mode is not None:
                record["connectionMode"] = connection_mode
            data["profiles"].append(record)
            data["nextNumber"] = number + 1
            self._write(data)
            return record.copy()
