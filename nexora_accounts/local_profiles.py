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
            for key in (
                "platformUserId", "platformIdSource", "platformIdVerifiedAt",
                "platformObservedUserId", "platformObservedSource", "platformObservedAt",
                "registrationConfirmedAt", "registrationConfirmationSource",
                "financialRealName", "financialCpf", "financialIdentitySource",
                "withdrawPinRef",
            ):
                if key not in item:
                    item[key] = None
                    changed = True
            # Preserve old 'created' state, but do not falsely certify a
            # legacy status based on the old generic success heuristics.
            if "registrationReviewRequired" not in item:
                item["registrationReviewRequired"] = (
                    item.get("status") == "created"
                    and not item.get("registrationConfirmationSource")
                )
                changed = True
            if "parentId" not in item:
                item["parentId"] = None
                changed = True
            if "profileIndex" not in item:
                item["profileIndex"] = item["id"]
                changed = True
            if "profileScope" not in item:
                item["profileScope"] = item["origin"]
                changed = True
            if item.get("deviceMode") and "profileMode" not in item:
                item["profileMode"] = item["deviceMode"]
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
        # Preserve deleted imports even if the immutable old accounts.json
        # remains on disk. Without tombstones they reappear at every restart.
        deleted_legacy = set(data.get("deletedLegacyAccountIds", []))
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
            if account_id in existing or account_id in deleted_legacy:
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
                "profileIndex": index, "profileScope": origin,
                "profileMode": mode, "accountId": account_id,
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
                or not isinstance(data.get("deletedLegacyAccountIds", []), list)
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
        safe["emulationType"] = (
            "chromium-" + str(item.get("deviceMode"))
            if item.get("deviceMode") in ("android", "iphone", "desktop")
            else "not-configured"
        )
        # Internal UUID identifies a browser emulation, not physical hardware.
        safe["hasLoginPassword"] = bool(item.get("password") or item.get("credentialRef"))
        safe["hasWithdrawPin"] = bool(item.get("withdrawPinRef"))
        for name in ("password", "confirmPassword", "credentialRef", "withdrawPinRef"):
            safe.pop(name, None)
        if details and isinstance(safe.get("runtimeConfig"), dict):
            # Runtime HTTP headers may contain authentication information.
            config = safe["runtimeConfig"].copy()
            config.pop("extra_headers", None)
            safe["runtimeConfig"] = config
        if not details:
            safe.pop("financialCpf", None)
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

    def set_financial_identity(self, profile_id: int, account_id: str,
                               real_name: str, cpf: str) -> dict:
        """Save operator-confirmed identity for one verified platform account.

        Generated profile identity is never silently treated as real.
        """
        from nexora_accounts.pix_key_store import _cpf
        if not isinstance(real_name, str) or not 5 <= len(real_name.strip()) <= 150 or " " not in real_name.strip():
            raise LocalProfileError("Informe o nome completo real do titular.")
        valid_cpf = _cpf(cpf) if isinstance(cpf, str) else None
        if not valid_cpf:
            raise LocalProfileError("CPF real do titular inválido.")
        with _LOCK:
            data = self._read()
            item = next((p for p in data["profiles"] if p["id"] == profile_id), None)
            if item is None or item.get("accountId") != account_id:
                raise LocalProfileError("Conta e identificador interno não correspondem.")
            if not item.get("platformUserId"):
                raise LocalProfileError("ID real da plataforma ainda não verificado.")
            # Names and CPFs come from authorized operator confirmation, not invented provider claims.
            item["financialRealName"] = " ".join(real_name.strip().split())
            item["financialCpf"] = valid_cpf
            item["financialIdentitySource"] = "operator-confirmed"
            self._event(item, "financial.identity", "Titular financeiro informado pelo operador")
            self._write(data)
            return self.public_view(item, details=True)

    def set_withdraw_pin(self, profile_id: int, account_id: str, pin: str) -> dict:
        """Withdrawal PIN is distinct from login password and encrypted on Windows."""
        from nexora_accounts import credential_vault
        if not credential_vault.is_available():
            raise LocalProfileError("PIN de saque só pode ser salvo no cofre DPAPI do Windows.")
        if not isinstance(pin, str) or not pin.isascii() or not pin.isdigit() or len(pin) != 6:
            raise LocalProfileError("PIN de saque deve ter 6 números.")
        with _LOCK:
            data = self._read()
            item = next((p for p in data["profiles"] if p["id"] == profile_id), None)
            if item is None or item.get("accountId") != account_id:
                raise LocalProfileError("Conta e identificador interno não correspondem.")
            # Storing an existing withdrawal PIN is a local account action.
            # It does not require a second platform-ID verification.
            item["withdrawPinRef"] = credential_vault.write_withdraw_pin(
                self.path, item["accountId"], pin
            )
            self._event(item, "financial.pin", "PIN de saque protegido no Windows")
            self._write(data)
            return self.public_view(item, details=True)

    def read_withdraw_pin(self, profile_id: int) -> str:
        from nexora_accounts import credential_vault
        profile = self.get(profile_id)
        if not profile.get("withdrawPinRef"):
            raise LocalProfileError("Cadastre o PIN de saque desta conta em Contas.")
        try:
            return credential_vault.read_withdraw_pin(self.path, profile["withdrawPinRef"])
        except (OSError, ValueError, UnicodeError) as exc:
            raise LocalProfileError("PIN de saque indisponível no cofre Windows.") from exc

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
            selected = next((p for p in data["profiles"] if p["id"] == profile_id), None)
            if selected is None:
                raise LocalProfileError("Conta não encontrada.")
            remaining = [p for p in data["profiles"] if p["id"] != profile_id]
            self._backup("delete")
            if selected.get("legacySource") == "accounts.json":
                deleted = data.setdefault("deletedLegacyAccountIds", [])
                if selected["accountId"] not in deleted:
                    deleted.append(selected["accountId"])
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
        if status not in ("pending", "created", "error", "unconfirmed",
                          "verification_required", "rejected"):
            raise LocalProfileError("Status inválido.")
        if status == "created":
            raise LocalProfileError(
                "Confirmação exige evidência vinculada à sessão e origem do provedor."
            )
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

    def mark_registration_clicked(
        self, profile_id: int, account_id: str, run_id: str, *,
        site_origin: str,
    ) -> dict:
        """Show 'Criada' as soon as this account's Registrar click is sent.

        This is the operator-requested LOCAL display rule, not proof that the
        remote provider created an account. Keep provider confirmation fields
        empty and registrationReviewRequired=True so later identity checks
        cannot mistake a click for a verified registration.
        """
        from Settings.registration_outcome import _origin

        if type(profile_id) is not int or not isinstance(account_id, str) or not account_id:
            raise LocalProfileError("Conta de cadastro inválida.")
        if not isinstance(run_id, str) or not run_id:
            raise LocalProfileError("Execução de cadastro não identificada.")
        with _LOCK:
            data = self._read()
            item = next((p for p in data["profiles"] if p["id"] == profile_id), None)
            if item is None or item.get("accountId") != account_id:
                raise LocalProfileError("Clique pertence a outra conta.")
            if item.get("lastSessionId") != run_id:
                raise LocalProfileError("Clique de execução antiga ignorado.")
            expected = _origin(item.get("startUrl"))
            observed = _origin(site_origin)
            if not expected or expected != observed:
                raise LocalProfileError("Origem do clique diferente da conta.")
            # Never downgrade an independently provider-verified registration.
            if (item.get("status") == "created"
                and item.get("registrationConfirmedAt")
                and item.get("registrationConfirmationSource")
                and not item.get("registrationReviewRequired")):
                return item.copy()
            item["status"] = "created"
            item["statusMessage"] = (
                "Registrar acionado; status Criada pelo clique, "
                "sem confirmação de sucesso do site."
            )
            item["registrationClickedAt"] = item.get("registrationClickedAt") or self._now()
            item["registrationReviewRequired"] = True
            # A click never writes platform confirmation/identity evidence.
            item["registrationConfirmedAt"] = None
            item["registrationConfirmationSource"] = None
            self._event(item, "registration.click-recorded", "Clique enviado pela sessão da conta")
            self._write(data)
            return item.copy()

    def update_registration_result(
        self, profile_id: int, account_id: str, run_id: str,
        status: str, message: str = "", *, confirmation_source=None,
        site_origin=None, platform_user_id=None, platform_id_source=None,
    ) -> dict:
        """Atomic, session-bound registration update. No guessed remote ID.

        A verified provider adapter must supply the confirmation source.
        Generic HTTP responses cannot call this method to mark 'created'.
        Check the immutable account UUID AND lastSessionId under one lock to
        prevent an old worker from overwriting a newer session's result.
        """
        from urllib.parse import urlsplit

        allowed = (
            "pending", "confirming", "verification_required", "created",
            "rejected", "error", "unconfirmed",
        )
        if status not in allowed or type(profile_id) is not int:
            raise LocalProfileError("Resultado do registro inválido.")
        if not isinstance(account_id, str) or not account_id:
            raise LocalProfileError("UUID interno não informado.")
        if not isinstance(run_id, str) or not run_id:
            raise LocalProfileError("Execução da conta não identificada.")
        if status != "created" and (
            confirmation_source is not None or
            platform_user_id is not None or platform_id_source is not None
        ):
            raise LocalProfileError("Identificação remota sem confirmação de cadastro.")
        if status == "created" and (
            not isinstance(confirmation_source, str) or
            not confirmation_source.strip() or len(confirmation_source) > 128
        ):
            raise LocalProfileError("Contrato de confirmação do provedor não verificado.")
        if platform_user_id is not None and (
            not isinstance(platform_user_id, str)
            or not (1 <= len(platform_user_id) <= 128)
            or not platform_user_id.isprintable()
            or any(ch.isspace() for ch in platform_user_id)
            or not isinstance(platform_id_source, str)
            or not platform_id_source.strip()
            or len(platform_id_source) > 128
        ):
            raise LocalProfileError("ID remoto sem fonte verificada ou formato inválido.")
        if platform_user_id is None and platform_id_source is not None:
            raise LocalProfileError("Fonte de ID remoto sem identificador.")

        def origin(value):
            try:
                p = urlsplit(str(value or ""))
                if p.scheme.lower() not in ("http", "https") or not p.hostname:
                    return ""
                port = p.port
                default = 443 if p.scheme.lower() == "https" else 80
                return p.scheme.lower() + "://" + p.hostname.lower() + (
                    ":" + str(port) if port and port != default else ""
                )
            except (ValueError, TypeError):
                return ""

        with _LOCK:
            state = self._read()
            item = next((p for p in state["profiles"] if p["id"] == profile_id), None)
            if item is None or item.get("accountId") != account_id:
                raise LocalProfileError("A conta original não corresponde ao cadastro.")
            if item.get("lastSessionId") != run_id:
                raise LocalProfileError("Resultado antigo ignorado: execução diferente.")
            # Even the transient confirming state must belong to this
            # exact account/site. It is not evidence of registration success.
            if status in ("confirming", "created"):
                expected = origin(item.get("startUrl"))
                actual = origin(site_origin)
                if not expected or expected != actual:
                    raise LocalProfileError("Origem autenticada diferente da conta.")
            if status == "created":
                existing = item.get("platformUserId")
                if existing is not None and platform_user_id not in (None, existing):
                    raise LocalProfileError("Conflito de ID remoto: atualização bloqueada.")
                if platform_user_id is not None and any(
                    other["id"] != profile_id
                    and other.get("platformUserId") == platform_user_id
                    and other.get("platformOrigin") in (None, actual)
                    for other in state["profiles"]
                ):
                    raise LocalProfileError("ID remoto já associado a outra conta desta plataforma.")
            if item.get("status") == "created" and status != "created":
                return item.copy()
            item["status"] = status
            item["statusMessage"] = str(message or "")[:220]
            if status == "created":
                item["registrationConfirmedAt"] = (
                    item.get("registrationConfirmedAt") or self._now()
                )
                item["registrationConfirmationSource"] = confirmation_source
                item["registrationReviewRequired"] = False
                item["platformOrigin"] = origin(site_origin)
                if platform_user_id is not None:
                    item["platformUserId"] = platform_user_id
                    item["platformIdSource"] = platform_id_source
                    item["platformIdVerifiedAt"] = self._now()
            self._event(item, "registration." + status, "Resultado vinculado à execução")
            self._write(state)
            return item.copy()


    def save_observed_platform_id(
        self, profile_id: int, account_id: str, run_id: str, *,
        site_origin: str, observed_identity,
    ) -> dict:
        """Persist a safely labeled candidate WITHOUT assigning platformUserId.

        Independent remote authentication has NOT been established. This
        operation must not alter status, confirmedAt, or verifiedAt.
        """
        from Settings.platform_identity import (
            ObservedPlatformIdentity, site_origin as normalize_origin,
            FIVEGBET_SITE_ORIGINS,
        )
        if (type(profile_id) is not int or not account_id or not run_id
            or not isinstance(observed_identity, ObservedPlatformIdentity)
            or observed_identity.profile_id != profile_id
            or observed_identity.account_id != account_id
            or observed_identity.run_id != run_id):
            raise LocalProfileError("ID observado não pertence à execução desta conta.")
        actual = normalize_origin(site_origin)
        value, source = observed_identity.user_id, observed_identity.source
        if (actual not in FIVEGBET_SITE_ORIGINS
            or normalize_origin(observed_identity.platform_origin) != actual
            or not isinstance(value, str) or not value.isdecimal()
            or not 0 < int(value) <= 2**53 - 1
            or source not in (
                "5gbet:x-object-id.uid:observed", "5gbet:storage:observed",
                "5gbet:header+storage:observed"
            )):
            raise LocalProfileError("Candidato observado ou plataforma inválidos.")
        with _LOCK:
            data = self._read()
            item = next((p for p in data["profiles"] if p["id"] == profile_id), None)
            if (item is None or item.get("accountId") != account_id
                or item.get("lastSessionId") != run_id
                or normalize_origin(item.get("startUrl")) != actual):
                raise LocalProfileError("Sessão expirada ou perfil divergente.")
            if item.get("platformUserId") not in (None, value):
                raise LocalProfileError("ID observado conflita com ID já verificado.")
            old = item.get("platformObservedUserId")
            if old is not None and old != value:
                raise LocalProfileError("Conflito de IDs observados; não sobrescrever.")
            if any(
                other.get("id") != profile_id
                and (other.get("platformObservedUserId") == value
                     or other.get("platformUserId") == value)
                and normalize_origin(other.get("startUrl")) in FIVEGBET_SITE_ORIGINS
                for other in data["profiles"]
            ):
                raise LocalProfileError("ID observado já associado a outra conta.")
            if old == value and item.get("platformObservedSource") == source:
                return item.copy()
            item["platformObservedUserId"] = value
            item["platformObservedSource"] = source
            item["platformObservedAt"] = self._now()
            self._event(item, "platform-id.observed", "Candidato não verificado nesta sessão")
            self._write(data)
            return item.copy()

    def invalidate_observed_platform_id(self, profile_id: int, account_id: str, run_id: str):
        """Discard only the candidate of a conflicted active session."""
        with _LOCK:
            data = self._read()
            item = next((p for p in data["profiles"] if p["id"] == profile_id), None)
            if (item is None or item.get("accountId") != account_id
                or item.get("lastSessionId") != run_id):
                raise LocalProfileError("Execução divergente.")
            if item.get("platformObservedUserId") is None:
                return
            for key in ("platformObservedUserId", "platformObservedSource",
                        "platformObservedAt"):
                item[key] = None
            self._event(item, "platform-id.conflict", "IDs observados divergentes")
            self._write(data)

    def save_verified_platform_id(
        self, profile_id: int, account_id: str, run_id: str, *,
        site_origin: str, platform_user_id: str, platform_id_source: str,
        identity_evidence=None,
    ) -> dict:
        """Atomically save an authenticated, independently validated remote ID.

        Previously only provider-confirmed registrations were allowed. A
        'Criada by click' record may now hold an independently verified ID,
        but ONLY with identity_evidence returned by the account-bound
        PlatformIdentityObserver and still keeps registrationReviewRequired.
        The click itself never becomes evidence of provider signup.
        """
        from Settings.platform_identity import (
            VerifiedPlatformIdentity, site_origin as normalize_origin,
        )

        if type(profile_id) is not int or not isinstance(account_id, str) or not account_id:
            raise LocalProfileError("Identidade local inválida.")
        if not isinstance(run_id, str) or not run_id:
            raise LocalProfileError("Execução não identificada.")
        if (not isinstance(platform_user_id, str)
            or not (1 <= len(platform_user_id) <= 128)
            or not platform_user_id.isprintable()
            or any(ch.isspace() for ch in platform_user_id)
            or not isinstance(platform_id_source, str)
            or not (1 <= len(platform_id_source) <= 128)
            or not platform_id_source.isprintable()
            or any(ch.isspace() for ch in platform_id_source)):
            raise LocalProfileError("ID remoto ou fonte inválidos.")

        actual = normalize_origin(site_origin)
        with _LOCK:
            data = self._read()
            item = next((p for p in data["profiles"] if p["id"] == profile_id), None)
            if item is None or item.get("accountId") != account_id:
                raise LocalProfileError("O ID local não corresponde à conta.")
            if item.get("lastSessionId") != run_id:
                raise LocalProfileError("Execução expirada: ID remoto não será salvo.")
            # Provider member-info attestation is allowed ONLY when it matches
            # a previously observed candidate belonging to this exact account.
            from nexora_accounts.platform_identity_probe import VERIFICATION_SOURCE
            if platform_id_source == VERIFICATION_SOURCE:
                if (item.get("platformObservedUserId") != platform_user_id
                    or not isinstance(identity_evidence, VerifiedPlatformIdentity)
                    or identity_evidence.user_id != platform_user_id
                    or identity_evidence.source != VERIFICATION_SOURCE
                    or identity_evidence.profile_id != profile_id
                    or identity_evidence.account_id != account_id
                    or identity_evidence.run_id != run_id
                    or normalize_origin(identity_evidence.platform_origin) != actual):
                    raise LocalProfileError(
                        "Resposta autenticada não corresponde à identidade observada desta conta."
                    )
            provider_confirmed = (
                item.get("status") == "created"
                and not item.get("registrationReviewRequired")
                and bool(item.get("registrationConfirmedAt"))
                and bool(item.get("registrationConfirmationSource"))
            )
            click_only = (
                item.get("status") == "created"
                and bool(item.get("registrationClickedAt"))
                and item.get("registrationReviewRequired") is True
                and not item.get("registrationConfirmedAt")
            )
            if not provider_confirmed and not click_only:
                raise LocalProfileError("Cadastro não confirmado por uma fonte verificada.")
            registered_origin = normalize_origin(item.get("platformOrigin"))
            if (not actual or actual != normalize_origin(item.get("startUrl"))
                or (registered_origin and actual != registered_origin)):
                raise LocalProfileError("Origem da plataforma divergente.")
            if click_only:
                # An arbitrary string/ID cannot bypass review. Bind the
                # validated observer result to this exact account and run.
                if (
                    not isinstance(identity_evidence, VerifiedPlatformIdentity)
                    or identity_evidence.user_id != platform_user_id
                    or identity_evidence.source != platform_id_source
                    or identity_evidence.profile_id != profile_id
                    or identity_evidence.account_id != account_id
                    or identity_evidence.run_id != run_id
                    or normalize_origin(identity_evidence.platform_origin) != actual
                ):
                    raise LocalProfileError(
                        "Identificação independente não corresponde à sessão."
                    )
            if platform_user_id in (
                item.get("username"), account_id, run_id,
                str(item.get("deviceProfileId") or ""),
            ):
                raise LocalProfileError("Identificador interno não é ID remoto.")
            previous = item.get("platformUserId")
            if previous is not None:
                if previous != platform_user_id:
                    raise LocalProfileError("Conflito: não substituir ID remoto confirmado.")
                return item.copy()
            if any(
                other["id"] != profile_id
                and other.get("platformUserId") == platform_user_id
                and other.get("platformOrigin") in (None, actual)
                for other in data["profiles"]
            ):
                raise LocalProfileError("ID remoto já associado a outra conta.")
            item["platformUserId"] = platform_user_id
            item["platformIdSource"] = platform_id_source
            item["platformIdVerifiedAt"] = self._now()
            item["platformOrigin"] = actual
            # Keep the original click-based created status, review flag and
            # provider confirmation fields EXACTLY as they were.
            self._event(item, "platform-id.verified", "Identidade remota validada")
            self._write(data)
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

    def integrity_report(self):
        """Read-only inventory; never invent a link for an orphaned folder."""
        with _LOCK:
            profiles = self._read()["profiles"]
        registered = set()
        missing = []
        for profile in profiles:
            raw = profile.get("profilePath")
            if raw:
                path = Path(raw).resolve()
                registered.add(path)
                if not path.is_dir():
                    missing.append({
                        "accountId": profile["accountId"],
                        "label": profile["label"],
                        "path": str(path),
                    })
        orphan = []
        roots = [self._base_profile_dir(area) for area in ("mae", "filha")]
        for root in roots:
            if not root.is_dir():
                continue
            choices = list((root / "accounts").glob("account_*"))
            choices.extend(root.glob("profile_*"))
            for mode in ("android", "iphone", "mixed"):
                choices.extend((root / mode).glob("profile_*"))
            for path in choices:
                if path.is_dir() and not path.is_symlink() and path.resolve() not in registered:
                    orphan.append(str(path.resolve()))
        return {"missing": missing[:100], "orphanFolders": sorted(set(orphan))[:100],
                "recoveryNotice": self.recovery_notice}

    def recovery_candidates(self):
        """List only UUID/labels from valid backups, never secret fields."""
        with _LOCK:
            known = {p.get("accountId") for p in self._read()["profiles"]}
        folder = self.path.parent / "profile-backups"
        if not folder.is_dir():
            return []
        from nexora_accounts import credential_vault
        candidates = {}
        for backup in sorted(folder.iterdir(), key=lambda x: x.stat().st_mtime_ns,
                             reverse=True):
            if not backup.name.startswith(self.path.stem + ".") or not backup.name.endswith((".json",".json.dpapi")):
                continue
            try:
                raw = backup.read_bytes()
                if backup.name.endswith(".dpapi"):
                    raw = credential_vault._crypt(raw, decrypt=True)
                data = json.loads(raw.decode("utf-8"))
                for p in data["profiles"]:
                    ident = p.get("accountId")
                    if ident and ident not in known and ident not in candidates:
                        candidates[ident] = {
                            "accountId": ident, "label": p.get("label", "Conta"),
                            "profilePath": p.get("profilePath", ""),
                        }
            except (ValueError, OSError, TypeError, KeyError, UnicodeError):
                continue
        return list(candidates.values())[:100]

    def restore_full_record(self, original: dict) -> dict:
        """Restore a deleted account without allocating a different identity."""
        if not isinstance(original, dict) or type(original.get("id")) is not int:
            raise LocalProfileError("Registro do backup inválido.")
        try:
            identity = str(uuid.UUID(str(original["accountId"])))
        except (ValueError, TypeError, KeyError) as exc:
            raise LocalProfileError("UUID do backup inválido.") from exc
        with _LOCK:
            data = self._read()
            if any(p["id"] == original["id"] or p.get("accountId") == identity
                   or p.get("profilePath") == original.get("profilePath")
                   for p in data["profiles"]):
                raise LocalProfileError("Índice, UUID ou pasta já ocupados.")
            item = original.copy()
            if item.get("legacySource") == "accounts.json":
                data["deletedLegacyAccountIds"] = [
                    value for value in data.get("deletedLegacyAccountIds", [])
                    if value != item["accountId"]
                ]
            self._event(item, "profile.restored", "Conta e armazenamento restaurados")
            self._backup("restore")
            data["profiles"].append(item)
            data["nextNumber"] = max(data["nextNumber"], item["id"] + 1)
            self._write(data)
            return self.public_view(item, details=True)

    def restore_account_from_backup(self, account_id: str) -> dict:
        """Recover metadata only, with exact UUID/index/path from a backup."""
        try:
            account_id = str(uuid.UUID(str(account_id)))
        except (TypeError, ValueError) as exc:
            raise LocalProfileError("UUID da conta inválido.") from exc
        with _LOCK:
            state = self._read()
            if any(p.get("accountId") == account_id for p in state["profiles"]):
                raise LocalProfileError("A conta já está cadastrada.")
            folder = self.path.parent / "profile-backups"
            if not folder.is_dir():
                raise LocalProfileError("Não existem backups disponíveis.")
            from nexora_accounts import credential_vault
            snapshots = sorted(
                (p for p in folder.iterdir() if p.name.startswith(self.path.stem + ".")
                 and p.name.endswith((".json", ".json.dpapi"))),
                key=lambda p: p.stat().st_mtime_ns, reverse=True
            )
            for backup in snapshots:
                try:
                    contents = backup.read_bytes()
                    if backup.name.endswith(".dpapi"):
                        contents = credential_vault._crypt(contents, decrypt=True)
                    snapshot = json.loads(contents.decode("utf-8"))
                    item = next(p.copy() for p in snapshot["profiles"]
                                if p.get("accountId") == account_id)
                except (ValueError, KeyError, StopIteration, OSError, UnicodeError, TypeError):
                    continue
                if any(p["id"] == item["id"] for p in state["profiles"]):
                    raise LocalProfileError("Índice da conta já ocupado; recuperação cancelada.")
                if any(p.get("profilePath") == item.get("profilePath") for p in state["profiles"]):
                    raise LocalProfileError("Diretório original já vinculado; recuperação cancelada.")
                self._backup("before-restore")
                self._event(item, "profile.restored", "Metadados recuperados de backup")
                state["profiles"].append(item)
                state["nextNumber"] = max(state["nextNumber"], item["id"] + 1)
                self._write(state)
                return self.public_view(item, details=True)
            raise LocalProfileError("Conta não localizada nos backups, nenhuma alteração feita.")

    def record_session(self, profile_id: int, action: str, run_id: str = "", browser_id=None):
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
                if browser_id is not None:
                    item["lastBrowserId"] = str(browser_id)
            item["sessionStatus"] = action
            # A browser closed while awaiting the response cannot leave the
            # account permanently claiming that confirmation is in progress.
            if (action in ("closed", "error")
                and item.get("status") == "confirming"
                and item.get("lastSessionId") == run_id):
                item["status"] = "unconfirmed"
                item["statusMessage"] = (
                    "Sessão encerrada antes da confirmação do cadastro pelo site."
                )
                self._event(item, "registration.unconfirmed", "Monitoramento interrompido")
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
            # Login password must retain the original per-account generator.
            # The six-digit test withdrawal PIN is a separate credential and
            # must never be inserted into password/confirmPassword.
            password = _new_password()
            ddd = secrets.choice(_DDDS)
            phone = "9" + _digits(8)
            record = {
                "id": number,
                "label": f"Conta {number}",
                "profileIndex": number,
                "profileScope": origin,
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
                "platformUserId": None,
                "platformObservedUserId": None,
                "platformObservedSource": None,
                "platformObservedAt": None,
                "platformIdSource": None,
                "platformIdVerifiedAt": None,
                "registrationConfirmedAt": None,
                "registrationConfirmationSource": None,
                "financialRealName": None,
                "financialCpf": None,
                "financialIdentitySource": None,
                "withdrawPinRef": None,
                "registrationReviewRequired": False,
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
                record["profileMode"] = device_mode
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
