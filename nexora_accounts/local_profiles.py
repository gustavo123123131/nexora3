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
import shutil
import uuid
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
    """Persistent per-account registry. Numeric IDs and folders stay unchanged."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else _default_path()
        self.recovery_notice = ""
        with _LOCK:
            try:
                data = self._read()
            except LocalProfileError:
                if not self._restore_valid_backup():
                    raise
                self.recovery_notice = (
                    "Arquivo de contas danificado: backup recuperado; "
                    "original corrompido preservado."
                )
                data = self._read()
            changed = self._upgrade(data)
            changed = self._import_legacy_registry(data) or changed
            if changed:
                if self.path.exists():
                    self._backup("migration")
                self._write(data)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _base_profile_dir(origin: str):
        if origin == "mae":
            from Settings.Criar_Mae.settings.utils import PROFILES_DIR
        elif origin == "filha":
            from Settings.Criar_Filha.settings.utils import PROFILES_DIR
        else:
            raise LocalProfileError("Origem inválida.")
        return PROFILES_DIR

    @classmethod
    def _profile_path(cls, origin: str, idx: int) -> str:
        from Settings.device_modes import account_profile_directory
        return str(account_profile_directory(cls._base_profile_dir(origin), origin, idx).resolve())

    def _upgrade(self, data: dict) -> bool:
        """Fill missing metadata only. Never move or change existing profiles."""
        changed = False
        if data.get("schemaVersion", 1) < 2:
            data["schemaVersion"] = 2
            changed = True
        seen_accounts = set()
        seen_devices = set()
        for item in data["profiles"]:
            if not isinstance(item, dict) or type(item.get("id")) is not int:
                raise LocalProfileError("Registro de conta inválido; faça backup do arquivo.")
            for key, seen in (("accountId", seen_accounts), ("deviceProfileId", seen_devices)):
                existing = item.get(key)
                if existing:
                    try:
                        uuid.UUID(str(existing))
                    except ValueError as exc:
                        raise LocalProfileError("Identificador persistente inválido.") from exc
                    if existing in seen:
                        raise LocalProfileError("Identificador de conta duplicado.")
                else:
                    item[key] = str(uuid.uuid4())
                    changed = True
                seen.add(item[key])
            if not item.get("profilePath"):
                item["profilePath"] = self._profile_path(item["origin"], item["id"])
                changed = True
            if "history" not in item:
                item["history"] = []
                changed = True
            if "lastOpenedAt" not in item:
                item["lastOpenedAt"] = None
                changed = True
            if "parentId" not in item:
                item["parentId"] = None
                changed = True
            from nexora_accounts import credential_vault
            if credential_vault.is_available() and item.get("password") and not item.get("credentialRef"):
                # Migrate old local plaintext without changing the password.
                item["credentialRef"] = credential_vault.write_secret(
                    self.path, item["accountId"], item["password"]
                )
                item.pop("password", None)
                item.pop("confirmPassword", None)
                changed = True
        return changed

    def _import_legacy_registry(self, data: dict) -> bool:
        """Read-only import from main-branch accounts.json.

        The source JSON and old browser folders are never modified. Profiles
        without a verified device runtime remain visible but cannot be reopened
        until their original configuration is recovered.
        """
        source = self.path.with_name("accounts.json")
        if not source.is_file():
            return False
        try:
            legacy = json.loads(source.read_text(encoding="utf-8"))
            if legacy.get("version") != 1 or not isinstance(legacy.get("accounts"), list):
                raise ValueError("legacy format")
        except (OSError, ValueError, AttributeError) as exc:
            raise LocalProfileError(
                "accounts.json legado não pôde ser lido; arquivo original preservado."
            ) from exc
        existing = {p.get("accountId") for p in data["profiles"]}
        seen_numbers = {p["id"] for p in data["profiles"]}
        next_number = max(data["nextNumber"], max(seen_numbers, default=0) + 1)
        modified = False
        from Settings.device_modes import profile_directory
        for entry in legacy["accounts"]:
            if not isinstance(entry, dict):
                continue
            account_id = entry.get("id")
            try:
                account_id = str(uuid.UUID(str(account_id)))
            except (ValueError, TypeError, AttributeError):
                # Do not replace a bad original ID by a newly invented UUID.
                continue
            if account_id in existing:
                continue
            origin = entry.get("profileScope")
            mode = entry.get("profileMode")
            index = entry.get("profileIndex")
            if origin not in ("mae", "filha") or mode not in ("android","iphone","desktop","mixed"):
                continue
            if type(index) is not int or index < 1:
                continue
            try:
                original_path = str(profile_directory(
                    self._base_profile_dir(origin), mode, index
                ).resolve())
            except (ValueError, OSError):
                continue
            display = next_number
            next_number += 1
            item = {
                "id": display, "label": f"Conta {display}",
                "accountId": account_id,
                "deviceProfileId": str(uuid.uuid4()),
                "deviceProfileIdOrigin": "migration-internal-only",
                "profilePath": original_path,
                "origin": origin, "deviceMode": mode,
                "originalProfileIndex": index,
                "username": entry.get("loginIdentifier") or "",
                "email": "", "fullName": "",
                "createdAt": entry.get("createdAt") or self._now(),
                "status": "pending",
                "statusMessage": "Conta importada; confirmação e dispositivo originais não verificados.",
                "history": [], "lastOpenedAt": None, "parentId": entry.get("parentId"),
                "legacySource": "accounts.json",
                "sessionStatus": "closed",
            }
            self._event(item, "profile.imported", "Origem legada preservada")
            data["profiles"].append(item)
            existing.add(account_id)
            modified = True
        if modified:
            data["nextNumber"] = next_number
        return modified

    def _backup(self, purpose="backup") -> None:
        """Copy intact JSON before any migration/deletion, with 5 rotations."""
        if not self.path.exists():
            return
        backup_dir = self.path.parent / "profile-backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        from nexora_accounts import credential_vault
        suffix = ".json.dpapi" if credential_vault.is_available() else ".json"
        backup = backup_dir / (self.path.stem + "." + uuid.uuid4().hex + suffix)
        if credential_vault.is_available():
            protected = credential_vault._crypt(self.path.read_bytes())
            backup.write_bytes(protected)
        else:
            shutil.copy2(self.path, backup)
            try:
                backup.chmod(0o600)
            except OSError:
                pass
        files = sorted(
            (file for file in backup_dir.iterdir()
             if file.name.startswith(self.path.stem + ".") and
             file.name.endswith((".json", ".json.dpapi"))),
            key=lambda x: x.stat().st_mtime_ns
        )
        for old in files[:-5]:
            old.unlink()

    def _restore_valid_backup(self) -> bool:
        """Keep damaged bytes and restore only a valid, readable backup."""
        folder = self.path.parent / "profile-backups"
        if not folder.is_dir():
            return False
        from nexora_accounts import credential_vault
        candidates = sorted(
            (file for file in folder.iterdir()
             if file.name.startswith(self.path.stem + ".") and
             file.name.endswith((".json", ".json.dpapi"))),
            key=lambda x: x.stat().st_mtime_ns, reverse=True
        )
        for backup in candidates:
            try:
                payload = backup.read_bytes()
                if backup.name.endswith(".dpapi"):
                    payload = credential_vault._crypt(payload, decrypt=True)
                parsed = json.loads(payload.decode("utf-8"))
                if not isinstance(parsed, dict) or type(parsed.get("nextNumber")) is not int or not isinstance(parsed.get("profiles"), list):
                    continue
                damaged = self.path.with_name(self.path.name + ".corrupt-" + uuid.uuid4().hex)
                os.replace(self.path, damaged)
                try:
                    self.path.write_bytes(payload)
                except OSError:
                    os.replace(damaged, self.path)
                    raise
                return True
            except (OSError, ValueError, UnicodeError):
                continue
        return False

    @staticmethod
    def _event(item: dict, action: str, detail: str = "") -> None:
        # Never put credentials, tokens, URLs or request bodies in history.
        item.setdefault("history", []).append({
            "at": datetime.now(timezone.utc).isoformat(),
            "event": action[:48],
            "detail": detail[:160],
        })
        item["history"] = item["history"][-80:]
        item["updatedAt"] = datetime.now(timezone.utc).isoformat()

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

    @staticmethod
    def public_view(item: dict, *, details=False) -> dict:
        """No secrets are serialized through the public panel API."""
        safe = item.copy()
        safe["hasRuntimeConfig"] = isinstance(item.get("runtimeConfig"), dict) and bool(
            item["runtimeConfig"].get("user_agent")
        )
        for name in ("password", "confirmPassword", "credentialRef"):
            safe.pop(name, None)
        if details and isinstance(safe.get("runtimeConfig"), dict):
            # Runtime HTTP headers may contain authentication information.
            config = safe["runtimeConfig"].copy()
            config.pop("extra_headers", None)
            safe["runtimeConfig"] = config
        if not details:
            safe.pop("history", None)
            safe.pop("runtimeConfig", None)
            safe.pop("profilePath", None)
        return safe

    def public_list(self) -> list[dict]:
        return [self.public_view(p) for p in self.list()]

    def public_detail(self, profile_id: int) -> dict:
        with _LOCK:
            found = next((p.copy() for p in self._read()["profiles"] if p["id"] == profile_id), None)
        if found is None:
            raise LocalProfileError("Conta não encontrada.")
        return self.public_view(found, details=True)

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
        if profile.get("credentialRef"):
            from nexora_accounts.credential_vault import read_secret
            try:
                password = read_secret(self.path, profile["credentialRef"])
            except (OSError, ValueError, UnicodeError) as exc:
                raise LocalProfileError("Não foi possível recuperar a credencial da conta.") from exc
            profile["password"] = password
            profile["confirmPassword"] = password
        return profile

    def delete(self, profile_id: int) -> dict:
        """Remove one account record; preserve numbering and other profiles."""
        if type(profile_id) is not int or profile_id < 1:
            raise LocalProfileError("ID da conta inválido.")
        with _LOCK:
            data = self._read()
            remaining = [p for p in data["profiles"] if p["id"] != profile_id]
            if len(remaining) == len(data["profiles"]):
                raise LocalProfileError("Conta não encontrada.")
            self._backup("delete")
            data["profiles"] = remaining
            if data.get("selectedProfileId") == profile_id:
                data["selectedProfileId"] = None
            self._write(data)
            return {"ok": True, "profileId": profile_id}

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
            self._event(item, "device.initialized", "Configuração Chromium fixada")
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
            self._event(item, "registration." + status, "Resultado registrado")
            self._write(state)
            return item.copy()

    def associate_proxy(self, profile_id: int, proxy_id):
        """Persist the association reference, not upstream credentials."""
        with _LOCK:
            data = self._read()
            item = next((p for p in data["profiles"] if p["id"] == profile_id), None)
            if item is None:
                raise LocalProfileError("Conta não encontrada.")
            item["proxyId"] = proxy_id
            self._event(item, "proxy.assigned", "Vínculo persistente registrado")
            self._write(data)

    def record_session(self, profile_id: int, action: str, run_id: str = ""):
        if action not in ("starting", "ready", "closed", "error"):
            raise LocalProfileError("Evento de sessão inválido.")
        with _LOCK:
            data = self._read()
            item = next((p for p in data["profiles"] if p["id"] == profile_id), None)
            if item is None:
                raise LocalProfileError("Conta não encontrada.")
            if action == "starting":
                item["lastOpenedAt"] = self._now()
                item["lastSessionId"] = run_id
            item["sessionStatus"] = action
            self._event(item, "session." + action, "Abertura " + action)
            self._write(data)

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
            data["schemaVersion"] = 2
            # IDs are display indexes and may not collide with imported rows.
            occupied = {p["id"] for p in data["profiles"]}
            number = max(data["nextNumber"], max(occupied, default=0) + 1)
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
                "accountId": str(uuid.uuid4()),
                "deviceProfileId": str(uuid.uuid4()),
                "profilePath": self._profile_path(origin, number),
                "parentId": None,
                "history": [],
                "lastOpenedAt": None,
                "sessionStatus": "closed",
            }
            self._event(record, "profile.created", "Identidade interna reservada")
            if device_mode is not None:
                record["deviceMode"] = device_mode
            if start_url is not None:
                record["startUrl"] = start_url
            if connection_mode is not None:
                record["connectionMode"] = connection_mode
            from nexora_accounts import credential_vault
            persisted = record.copy()
            if credential_vault.is_available():
                persisted["credentialRef"] = credential_vault.write_secret(
                    self.path, record["accountId"], password
                )
                persisted.pop("password", None)
                persisted.pop("confirmPassword", None)
            data["profiles"].append(persisted)
            data["nextNumber"] = number + 1
            self._write(data)
            return record.copy()
