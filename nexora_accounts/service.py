"""Local registration/account service adapted to Nexora's existing Python/Playwright.

The supplied JavaScript package is preserved in proxybet_registration_bridge/.
This Python adapter avoids requiring Node.js on the Windows Chromium agent.
No third-party endpoint, CAPTCHA workaround or credential storage is included.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, urljoin
import json
import hashlib
import os
import re
import shutil
import sqlite3
import threading
import time
import uuid


ALLOWED_FIELDS = frozenset({"username", "email", "phone", "password",
                            "confirmPassword", "fullName", "cpf"})
SECRET_FIELDS = frozenset({"password", "confirmPassword"})
SITE_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")
STATUS = frozenset({"local", "pending", "completed", "failed"})
STAGES = frozenset({"saved", "review", "submitted", "verification-required",
                    "provider-confirmed", "missing-fields", "submit-error"})


class AccountError(ValueError):
    pass


def _now():
    return datetime.now(timezone.utc).isoformat()


def _text(value, label, limit=120, required=True):
    if not isinstance(value, str):
        raise AccountError(f"{label} inválido.")
    value = value.strip()
    if (required and not value) or len(value) > limit or any(ord(c) < 32 for c in value):
        raise AccountError(f"{label} inválido.")
    return value


def _url(value):
    raw = _text(value, "Endereço", 2048)
    if any(x in raw for x in "\\ \r\n\t"):
        raise AccountError("Endereço inválido.")
    if "://" not in raw:
        raw = "https://" + raw
    try:
        parsed = urlparse(raw)
        _ = parsed.port
    except ValueError as exc:
        raise AccountError("Endereço inválido.") from exc
    local = parsed.hostname in ("localhost", "127.0.0.1", "::1")
    if (parsed.scheme != "https" and not (local and parsed.scheme == "http")) or \
            not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise AccountError("Use HTTPS sem credenciais, ou HTTP apenas no localhost.")
    return raw


def _same_origin(base, path):
    if not isinstance(path, str) or not path.startswith("/") or path.startswith("//"):
        raise AccountError("O caminho deve começar com uma única barra.")
    url = urljoin(base.rstrip("/") + "/", path.lstrip("/"))
    # urljoin with a trailing slash makes paths deterministic.
    url = urljoin(base.rstrip("/") + "/", path.lstrip("/"))
    a, b = urlparse(base), urlparse(url)
    if (a.scheme, a.netloc) != (b.scheme, b.netloc):
        raise AccountError("Caminho fora da origem autorizada.")
    return url


def _site_config(data):
    if not isinstance(data, dict):
        raise AccountError("Dados do site inválidos.")
    key = data.get("id", "")
    if not isinstance(key, str) or not SITE_ID.fullmatch(key):
        raise AccountError("ID do site inválido.")
    base = _url(data.get("baseUrl", ""))
    display = _text(data.get("displayName", ""), "Nome do site", 80)
    authorized = data.get("authorized") is True
    fields = data.get("fields") or {}
    if not isinstance(fields, dict) or any(k not in ALLOWED_FIELDS for k in fields):
        raise AccountError("Configuração de campos inválida.")
    selectors = {}
    for name, definition in fields.items():
        if not isinstance(definition, dict):
            raise AccountError("Definição de campo inválida.")
        selector = _text(definition.get("selector", ""), "Seletor", 200)
        selectors[name] = {"selector": selector, "required": definition.get("required") is True}
    submit_selector = data.get("submitSelector", "")
    if authorized and (not selectors or not isinstance(submit_selector, str) or not submit_selector.strip()):
        raise AccountError("Cadastro autorizado requer seletores dos campos e botão de envio.")
    if not isinstance(submit_selector, str) or len(submit_selector) > 200:
        raise AccountError("Botão de envio inválido.")
    success_selector = data.get("successSelector", "")
    if not isinstance(success_selector, str) or len(success_selector) > 200:
        raise AccountError("Seletor de confirmação inválido.")
    reg = _same_origin(base, data.get("registrationPath", "/register"))
    login = _same_origin(base, data.get("loginPath", "/login"))
    return {
        "id": key, "displayName": display, "baseUrl": base,
        "registrationUrl": reg, "loginUrl": login,
        "fields": selectors, "submitSelector": submit_selector.strip(),
        "successSelector": success_selector.strip(), "authorized": authorized,
    }


def _cpf_valid(raw):
    number = re.sub(r"\D", "", raw)
    if len(number) != 11 or len(set(number)) == 1:
        return False
    for n in (9, 10):
        total = sum(int(number[i]) * (n + 1 - i) for i in range(n))
        if int(number[n]) != ((total * 10) % 11) % 10:
            return False
    return True


def validate_values(values, fields):
    """Validate without including sensitive input in errors/logs."""
    if not isinstance(values, dict):
        raise AccountError("Formulário inválido.")
    cleaned = {}
    errors = {}
    for key, cfg in fields.items():
        v = values.get(key, "")
        if not isinstance(v, str) or len(v) > 256:
            errors[key] = "Valor inválido"
            continue
        if not v.strip():
            if cfg["required"]:
                errors[key] = "Campo obrigatório"
            continue
        value = v if key in SECRET_FIELDS else v.strip()
        if key == "username" and not re.fullmatch(r"[\w.-]{3,40}", value, re.UNICODE):
            errors[key] = "Usuário inválido"
        elif key == "email" and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            errors[key] = "E-mail inválido"
        elif key == "phone" and not re.fullmatch(r"\+?[\d\s()\-]{10,20}", value):
            errors[key] = "Telefone inválido"
        elif key == "password" and not 7 <= len(value) <= 16:
            errors[key] = "Senha deve ter de 7 a 16 caracteres"
        elif key == "fullName" and len(value) < 3:
            errors[key] = "Nome inválido"
        elif key == "cpf" and not _cpf_valid(value):
            errors[key] = "CPF inválido"
        cleaned[key] = value
    if "confirmPassword" in cleaned and "password" in cleaned and \
            cleaned["confirmPassword"] != cleaned["password"]:
        errors["confirmPassword"] = "As senhas não coincidem"
    return cleaned, errors


def _default_folder():
    base = os.environ.get("LOCALAPPDATA") if os.name == "nt" else os.environ.get("XDG_STATE_HOME")
    return Path(base) / "Nexora" if base else Path.home() / ".local" / "share" / "nexora"


_LOCAL_JSON_LOCK = threading.RLock()


class AccountsRepository:
    """Only local JSON persistence. Profiles and sessions remain in __profiles.

    On the first run, import the old accounts.sqlite into accounts.json without
    modifying or deleting the SQLite source. Atomic replaces avoid partial JSON.
    """

    def __init__(self, db_path=None, migrate_test_passwords=True):
        self.path = Path(db_path) if db_path else _default_folder() / "accounts.json"
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._lock = _LOCAL_JSON_LOCK
        with self._lock:
            if not self.path.exists():
                legacy = self.path.with_name("accounts.sqlite")
                state = self._import_legacy(legacy) if self.path.suffix == ".json" and legacy.is_file() else self._empty()
                self._write(state)
            # Preview tools can disable the upgrade to remain read-only.
            # Never rewrite confirmed profiles or legitimate credentials.
            state = self._read()
            upgraded = 0
            from nexora_accounts.profile_data import make_password
            for account in state["accounts"]:
                demo_data = account.get("profileData")
                if (migrate_test_passwords
                        and account.get("status") == "local"
                        and account.get("stage") in ("profile-created", "test-data-blocked", "form-not-found")
                        and isinstance(demo_data, dict)
                        and demo_data.get("isTestData") is True
                        and isinstance(demo_data.get("password"), str)
                        and len(demo_data["password"]) < 7):
                    # Do not silently rotate existing 9- or 16-character
                    # profile passwords; their sessions may already be used.
                    demo_data["password"] = make_password()
                    account["updatedAt"] = _now()
                    upgraded += 1
            if upgraded:
                self._write(state)

    @staticmethod
    def _empty():
        return {"version": 1, "sites": [], "accounts": [],
                "batches": [], "jobs": [], "audit": []}

    def _read(self):
        try:
            if self.path.stat().st_size > 20_000_000:
                raise AccountError("Arquivo JSON de contas muito grande.")
            with self.path.open("r", encoding="utf-8") as stream:
                data = json.load(stream)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise AccountError("Não foi possível ler accounts.json. O arquivo não será sobrescrito.") from exc
        if not isinstance(data, dict) or data.get("version") != 1 or any(
            not isinstance(data.get(name), list)
            for name in ("sites", "accounts", "batches", "jobs", "audit")
        ):
            raise AccountError("Formato inválido de accounts.json. O arquivo não será sobrescrito.")
        return data

    def _write(self, state):
        """Save to a sibling temporary file, fsync, then atomically replace."""
        from tempfile import NamedTemporaryFile
        temporary = None
        try:
            with NamedTemporaryFile("w", encoding="utf-8", dir=self.path.parent,
                                    prefix=".accounts-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                if os.name != "nt":
                    os.chmod(temporary, 0o600)
                json.dump(state, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary and temporary.exists():
                temporary.unlink()

    def _import_legacy(self, legacy):
        """Read-only migration; the original SQLite DB is kept as a backup."""
        state = self._empty()
        try:
            with sqlite3.connect(legacy.as_uri() + "?mode=ro", uri=True) as db:
                db.row_factory = sqlite3.Row
                for row in db.execute("SELECT config FROM sites ORDER BY created_at"):
                    state["sites"].append(json.loads(row["config"]))
                for row in db.execute("SELECT * FROM accounts ORDER BY created_at"):
                    state["accounts"].append(self._legacy_account(row))
                for row in db.execute("SELECT action,entity,occurred_at FROM account_audit ORDER BY rowid"):
                    state["audit"].append({
                        "action": row["action"], "entity": row["entity"], "occurredAt": row["occurred_at"],
                    })
        except (OSError, sqlite3.Error, ValueError, KeyError) as exc:
            raise AccountError("Falha na importação das contas antigas. SQLite preservado.") from exc
        state["audit"].append({"action": "storage.migrated", "entity": "accounts.sqlite",
                               "occurredAt": _now()})
        return state

    @staticmethod
    def _legacy_account(row):
        return {
            "id": row["id"], "siteId": row["site_id"], "displayName": row["display_name"],
            "loginIdentifier": row["login_identifier"], "parentId": row["parent_id"],
            "profileScope": row["profile_scope"], "profileMode": row["profile_mode"],
            "profileIndex": row["profile_index"], "status": row["status"],
            "stage": row["stage"], "createdAt": row["created_at"], "updatedAt": row["updated_at"],
        }

    @staticmethod
    def _audit(state, action, entity):
        state["audit"].append({"action": action, "entity": entity, "occurredAt": _now()})

    def sites(self):
        with self._lock:
            return list(reversed(self._read()["sites"]))

    def site(self, key):
        with self._lock:
            result = next((site for site in self._read()["sites"] if site["id"] == key), None)
            if result is None:
                raise AccountError("Site não configurado.")
            return dict(result)

    def add_site(self, data):
        config = _site_config(data)
        with self._lock:
            state = self._read()
            if any(site["id"] == config["id"] for site in state["sites"]):
                raise AccountError("Já existe um site com esse ID.")
            state["sites"].append(config)
            self._audit(state, "site.create", config["id"])
            self._write(state)
        return dict(config)

    def accounts(self, query="", status="", profiles_only=False):
        if not isinstance(query, str) or len(query) > 128 or status not in ("", *STATUS):
            raise AccountError("Filtro inválido.")
        with self._lock:
            records = sorted(self._read()["accounts"], key=lambda r: r["createdAt"], reverse=True)
        return [dict(item) for item in records
                if (not profiles_only or item.get("createdBy") == "abrir-conta"
                    or (isinstance(item.get("profileData"), dict)
                        and "targetUrl" in item))
                and (not status or item["status"] == status)
                and (not query or query.lower() in (
                    item["displayName"] + " " + item["siteId"] + " " +
                    item.get("loginIdentifier", "")).lower())]

    def account(self, key):
        with self._lock:
            item = next((a for a in self._read()["accounts"] if a["id"] == key), None)
            if item is None:
                raise AccountError("Conta não encontrada.")
            return dict(item)

    @staticmethod
    def _validate_account(state, data):
        if not isinstance(data, dict):
            raise AccountError("Dados inválidos.")
        if not any(site["id"] == data.get("siteId") for site in state["sites"]):
            raise AccountError("Site não configurado.")
        display = _text(data.get("displayName", ""), "Identificação", 120)
        ident = _text(data.get("loginIdentifier", ""), "Identificador de login", 128, False)
        scope = data.get("profileScope", "mae")
        mode = data.get("profileMode", "desktop")
        idx = data.get("profileIndex", 1)
        if scope not in ("mae", "filha") or mode not in ("desktop", "android", "iphone", "mixed") \
                or isinstance(idx, bool) or not isinstance(idx, int) or not 1 <= idx <= 999:
            raise AccountError("Perfil inválido.")
        parent = data.get("parentId") or None
        if parent and (scope != "filha" or not any(
            a["id"] == parent and a["profileScope"] == "mae" for a in state["accounts"]
        )):
            raise AccountError("Conta filha deve apontar para uma conta mãe.")
        return display, ident, scope, mode, idx, parent

    def add_account(self, data):
        with self._lock:
            state = self._read()
            display, ident, scope, mode, idx, parent = self._validate_account(state, data)
            if any(a["profileScope"] == scope and a["profileMode"] == mode
                   and a["profileIndex"] == idx for a in state["accounts"]):
                raise AccountError("Este perfil já está vinculado a uma conta.")
            now = _now()
            account = {
                "id": str(uuid.uuid4()), "siteId": data["siteId"], "displayName": display,
                "loginIdentifier": ident, "parentId": parent, "profileScope": scope,
                "profileMode": mode, "profileIndex": idx, "status": "local",
                "stage": "saved", "createdAt": now, "updatedAt": now,
            }
            state["accounts"].append(account)
            self._audit(state, "account.create", account["id"])
            self._write(state)
            return dict(account)

    def create_batch(self, data):
        """Atomically create 2-100 individual account records and queued jobs."""
        if not isinstance(data, dict):
            raise AccountError("Dados inválidos.")
        quantity = data.get("quantity")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or not 2 <= quantity <= 100:
            raise AccountError("Quantidade deve estar entre 2 e 100.")
        with self._lock:
            state = self._read()
            # Validate site/scope/mode/parent with a temporary legal profile.
            sample = dict(data)
            sample["profileIndex"] = 1
            display, ident, scope, mode, _, parent = self._validate_account(state, sample)
            occupied = {a["profileIndex"] for a in state["accounts"]
                        if a["profileScope"] == scope and a["profileMode"] == mode}
            free = [idx for idx in range(1, 1000) if idx not in occupied]
            if len(free) < quantity:
                raise AccountError("Não há perfis livres suficientes neste modo.")
            now, batch_id = _now(), str(uuid.uuid4())
            state["batches"].append({
                "id": batch_id, "siteId": data["siteId"], "profileScope": scope,
                "profileMode": mode, "requestedCount": quantity,
                "status": "queued", "createdAt": now, "updatedAt": now,
            })
            first = None
            for idx in free[:quantity]:
                key = str(uuid.uuid4())
                record = {
                    "id": key, "siteId": data["siteId"],
                    "displayName": f"{display} #{idx}",
                    "loginIdentifier": ident, "parentId": parent,
                    "profileScope": scope, "profileMode": mode, "profileIndex": idx,
                    "status": "local", "stage": "saved",
                    "batchId": batch_id, "createdAt": now, "updatedAt": now,
                }
                if first is None:
                    first = dict(record)
                state["accounts"].append(record)
                state["jobs"].append({
                    "id": str(uuid.uuid4()), "batchId": batch_id, "accountId": key,
                    "state": "queued", "createdAt": now, "updatedAt": now,
                })
            self._audit(state, "batch.create", batch_id)
            self._write(state)
            return {"batchId": batch_id, "quantity": quantity, "account": first}

    def create_launch_profiles(self, scope, mode, urls, count_per_url, profiles_base):
        """Create individual, isolated local profile folders AND records per click.

        Each returned launch plan uses the allocated persistent profile index.
        No site registration, API request, or real identity generation occurs.
        """
        from Settings.device_modes import profile_directory, resolve_mode
        from nexora_accounts.profile_data import make_profile_data

        if scope not in ("mae", "filha"):
            raise AccountError("Tipo de perfil inválido.")
        try:
            mode = resolve_mode(mode)
        except ValueError as exc:
            raise AccountError(str(exc)) from exc
        if isinstance(count_per_url, bool) or not isinstance(count_per_url, int) or not 1 <= count_per_url <= 50:
            raise AccountError("Quantidade inválida (1 a 50 por link).")
        if not isinstance(urls, list) or not all(isinstance(u, str) and len(u) <= 2048 for u in urls):
            raise AccountError("Lista de links inválida.")
        # A blank URL still creates one local browser profile without navigation.
        targets = urls if urls else [""]
        with self._lock:
            state = self._read()
            # Persisted 15-second idempotency window prevents double clicks
            # and concurrent HTTP event handlers from creating extra records.
            fingerprint = hashlib.sha256(json.dumps(
                [scope, mode, targets, count_per_url],
                ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")).hexdigest()
            now_epoch = time.time()
            recent = [
                item for item in state.get("recentLaunches", [])
                if isinstance(item, dict) and isinstance(item.get("at"), (int, float))
                and now_epoch - item["at"] < 15
                and now_epoch >= item["at"]
            ][-50:]
            if any(item.get("fingerprint") == fingerprint for item in recent):
                raise AccountError(
                    "Abrir Conta já recebeu esse pedido há poucos segundos. "
                    "Aguarde; nenhum perfil duplicado foi criado."
                )
            used = {
                a.get("profileIndex") for a in state["accounts"]
                if a.get("profileScope") == scope and a.get("profileMode") == mode
            }
            total = len(targets) * count_per_url
            indices = []
            folders = []
            for idx in range(1, 1000):
                folder = profile_directory(profiles_base, mode, idx)
                if idx not in used and not folder.exists():
                    indices.append(idx)
                    folders.append(folder)
                if len(indices) == total:
                    break
            if len(indices) != total:
                raise AccountError("Não existem índices livres suficientes para criar os perfis.")
            plans = []
            created_folders = []
            launch_id = str(uuid.uuid4())
            try:
                for position, (idx, folder) in enumerate(zip(indices, folders)):
                    url = targets[position // count_per_url]
                    data = make_profile_data()
                    now = _now()
                    folder.mkdir(parents=True, exist_ok=False)
                    created_folders.append(folder)
                    account = {
                        "id": str(uuid.uuid4()), "siteId": "", "targetUrl": url,
                        "createdBy": "abrir-conta", "launchId": launch_id,
                        "displayName": ("Conta Mãe" if scope == "mae" else "Conta Filha") + f" #{idx}",
                        "loginIdentifier": data["username"], "parentId": None,
                        "profileScope": scope, "profileMode": mode, "profileIndex": idx,
                        "profileData": data, "status": "local", "stage": "profile-created",
                        "registrationLog": [{"at": now, "stage": "profile-created", "durationMs": 0}],
                        "createdAt": now, "updatedAt": now,
                    }
                    state["accounts"].append(account)
                    self._audit(state, "profile.create", account["id"])
                    plans.append({
                        "idx": idx, "url": url, "site_key": "",
                        "site_occurrence": position % count_per_url + 1,
                        "shared_identity_key": "", "shared_identity_owner": False,
                        "should_autochave": False,
                        "profile_record_id": account["id"],
                    })
                state["recentLaunches"] = recent + [
                    {"fingerprint": fingerprint, "at": now_epoch, "launchId": launch_id}
                ]
                self._write(state)
            except Exception:
                # Only remove directories that this transaction just created.
                # Existing browser profiles and the previous JSON stay unchanged.
                for folder in reversed(created_folders):
                    try:
                        folder.rmdir()
                    except OSError:
                        pass
                raise
            return plans

    def set_stage(self, key, status, stage, http_status=None, error_code=None, duration_ms=None):
        if status not in STATUS or not re.fullmatch(r"[a-z0-9_-]{1,60}", stage):
            raise AccountError("Status inválido.")
        if http_status is not None and (
                isinstance(http_status, bool) or not isinstance(http_status, int)
                or not 100 <= http_status <= 599):
            raise AccountError("Código HTTP inválido.")
        if error_code is not None and (
                not isinstance(error_code, str)
                or not re.fullmatch(r"[a-z0-9_-]{1,60}", error_code)):
            raise AccountError("Código de erro inválido.")
        if duration_ms is not None and (
                isinstance(duration_ms, bool) or not isinstance(duration_ms, int)
                or not 0 <= duration_ms <= 86_400_000):
            raise AccountError("Duração inválida.")
        with self._lock:
            state = self._read()
            account = next((a for a in state["accounts"] if a["id"] == key), None)
            if account is None:
                raise AccountError("Conta não encontrada.")
            now = _now()
            changed = account.get("status") != status or account.get("stage") != stage
            account.update({"status": status, "stage": stage, "updatedAt": now})
            if changed or http_status is not None or error_code is not None or duration_ms is not None:
                # Fixed stage identifiers and numeric HTTP codes, never credentials
                # or untrusted response bodies. Logs are per account and bounded.
                entry = {"at": now, "stage": stage}
                if http_status is not None:
                    entry["httpStatus"] = http_status
                if error_code is not None:
                    entry["errorCode"] = error_code
                if duration_ms is not None:
                    entry["durationMs"] = duration_ms
                account.setdefault("registrationLog", []).append(entry)
                account["registrationLog"] = account["registrationLog"][-100:]
            mapping = {"provider-confirmed": "completed", "review": "form_ready",
                       "submit-queued": "submitting", "submitting": "submitting",
                       "awaiting-provider": "submitting",
                       "provider-response-unverified": "verification_required",
                       "timeout-unverified": "verification_required",
                       "submitted": "submitting", "verification-required": "verification_required"}
            for job in state["jobs"]:
                if job["accountId"] == key:
                    job.update({"state": mapping.get(stage, "failed" if status == "failed" else "queued"),
                                "updatedAt": now})
            for batch in state["batches"]:
                jobs = [j["state"] for j in state["jobs"] if j["batchId"] == batch["id"]]
                if jobs:
                    finished = ("completed", "failed", "cancelled")
                    batch["status"] = ("completed" if all(x == "completed" for x in jobs)
                                       else "failed" if all(x in ("failed", "cancelled") for x in jobs)
                                       else "partial" if all(x in finished for x in jobs)
                                       else "running" if any(x != "queued" for x in jobs)
                                       else "queued")
                    batch["updatedAt"] = now
            self._audit(state, "account.status", key)
            self._write(state)
            return dict(account)

    def cleanup_test_candidates(self, apply=False):
        """Preview or remove ONLY provably generated, unsubmitted local tests.

        Real, completed, pending-submitted or ambiguous records are untouched.
        This never deletes Chromium folders, sessions or credentials.
        """
        safe_stages = {
            "profile-created", "test-data-blocked", "form-not-found",
            "browser-starting", "browser-ready", "site-loading", "site-loaded",
            "form-scanning", "form-found", "browser-error", "navigation-error",
            "browser-closed-unverified", "site-load-timeout", "site-unavailable",
            "registration-blocked",
        }
        with self._lock:
            state = self._read()
            eligible = [
                a for a in state["accounts"]
                if isinstance(a.get("profileData"), dict)
                and a["profileData"].get("isTestData") is True
                and "targetUrl" in a
                and a.get("status") == "local"
                and a.get("stage") in safe_stages
                and not any(
                    entry.get("stage") in {
                        "submit-queued", "submitting", "awaiting-provider",
                        "provider-response-unverified", "provider-confirmed",
                    }
                    for entry in a.get("registrationLog", [])
                    if isinstance(entry, dict)
                )
            ]
            report = [{
                "id": a["id"], "scope": a.get("profileScope"),
                "index": a.get("profileIndex"), "stage": a.get("stage"),
            } for a in eligible]
            if apply and eligible:
                # Back up before touching local data; keep every browser folder.
                backup = self.path.with_name(
                    self.path.name + ".backup-" +
                    datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") +
                    "-" + uuid.uuid4().hex[:6]
                )
                shutil.copy2(self.path, backup)
                if os.name != "nt":
                    os.chmod(backup, 0o600)
                ids = {a["id"] for a in eligible}
                state["accounts"] = [a for a in state["accounts"] if a["id"] not in ids]
                state["jobs"] = [j for j in state["jobs"] if j.get("accountId") not in ids]
                self._audit(state, "test-records.cleanup", str(len(ids)))
                self._write(state)
                return {"candidates": report, "deleted": len(ids), "backup": str(backup)}
            return {"candidates": report, "deleted": 0, "backup": None}

    def delete(self, key):
        with self._lock:
            state = self._read()
            if not any(a["id"] == key for a in state["accounts"]):
                raise AccountError("Conta não encontrada.")
            state["accounts"] = [a for a in state["accounts"] if a["id"] != key]
            for account in state["accounts"]:
                if account["parentId"] == key:
                    account["parentId"] = None
            state["jobs"] = [j for j in state["jobs"] if j["accountId"] != key]
            self._audit(state, "account.delete", key)
            self._write(state)


class ExistingProfileBrowser:
    """Runs all sync Playwright operations in a single persistent worker thread.

    Opens original Nexora __profiles directories, never copies/deletes cookies.
    If a profile is already open elsewhere, Chromium's profile lock prevents
    duplicate ownership and the user receives an error instead of data loss.
    """
    def __init__(self, repo):
        self.repo = repo
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="nexora-registration")
        self.playwright = None
        self.handles = {}  # (scope, mode, index) -> (context, page)
        self.review = {}   # account ID -> associated profile key

    def execute(self, action, *args):
        return self.executor.submit(self._run, action, *args)

    def _profile(self, account, url):
        from Settings.device_modes import profile_directory
        if account["profileScope"] == "mae":
            from Settings.Criar_Mae.settings.utils import PROFILES_DIR
            from Settings.Criar_Mae.settings.browser import make_config
        else:
            from Settings.Criar_Filha.settings.utils import PROFILES_DIR
            from Settings.Criar_Filha.settings.browser import make_config
        identity = (account["profileScope"], account["profileMode"], account["profileIndex"])
        if identity in self.handles:
            context, page = self.handles[identity]
            if page.is_closed():
                page = context.new_page()
                self.handles[identity] = (context, page)
        else:
            from playwright.sync_api import sync_playwright
            if self.playwright is None:
                self.playwright = sync_playwright().start()
            cfg = make_config(account["profileMode"], account["profileIndex"])
            folder = profile_directory(PROFILES_DIR, account["profileMode"], account["profileIndex"])
            folder.parent.mkdir(parents=True, exist_ok=True)
            try:
                context = self.playwright.chromium.launch_persistent_context(
                    user_data_dir=str(folder), headless=False,
                    viewport=cfg.viewport, user_agent=cfg.user_agent,
                    device_scale_factor=cfg.device_scale_factor,
                    is_mobile=cfg.is_mobile, has_touch=cfg.has_touch,
                )
            except Exception as exc:
                # Chromium error details may contain private filesystem paths.
                raise AccountError(
                    "Não foi possível abrir o perfil. Feche a janela desse mesmo "
                    "perfil no multi-login e tente novamente."
                ) from exc
            page = context.pages[0] if context.pages else context.new_page()
            self.handles[identity] = (context, page)
        if url:
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=18000)
            except Exception as exc:
                raise AccountError("A navegação falhou. Confira a URL e a conexão.") from exc
        return identity, page

    def _run(self, action, key, values=None):
        if action == "close":
            for context, _ in self.handles.values():
                try: context.close()
                except Exception: pass
            self.handles.clear()
            if self.playwright:
                self.playwright.stop()
                self.playwright = None
            return None
        account = self.repo.account(key)
        site = self.repo.site(account["siteId"])
        if action == "open":
            self._profile(account, site["loginUrl"])
            return {"opened": True, "profileIndex": account["profileIndex"]}
        if not site["authorized"]:
            raise AccountError("O cadastro guiado exige autorização explícita do site.")
        if action == "prepare":
            identity, page = self._profile(account, site["registrationUrl"])
            found = {}
            for name, definition in site["fields"].items():
                try: found[name] = page.locator(definition["selector"]).count() > 0
                except Exception: found[name] = False
            self.review[key] = {"identity": identity, "found": found}
            self.repo.set_stage(key, "pending", "review")
            return {
                "account": self.repo.account(key), "registrationUrl": site["registrationUrl"],
                "fields": [{"name": name, "present": found[name], "required": spec["required"]}
                           for name, spec in site["fields"].items()],
                "missingRequired": [name for name, spec in site["fields"].items()
                                    if spec["required"] and not found[name]],
            }
        review = self.review.get(key)
        if not review:
            raise AccountError("Preparação expirada. Abra o formulário novamente.")
        active = self.handles.get(review["identity"])
        if not active or active[1].is_closed():
            raise AccountError("A janela do perfil foi fechada. Prepare novamente.")
        page = active[1]
        current = urlparse(page.url)
        target = urlparse(site["registrationUrl"])
        if (current.scheme, current.netloc) != (target.scheme, target.netloc):
            raise AccountError("A página saiu da origem configurada. Confira o site manualmente.")
        if action == "submit":
            if not isinstance(values, dict) or values.get("userConfirmed") is not True:
                raise AccountError("Confirme o envio explicitamente.")
            cleaned, errors = validate_values(values.get("values"), site["fields"])
            if errors:
                return {"account": self.repo.account(key), "errors": errors}
            missing = [name for name, cfg in site["fields"].items()
                       if cfg["required"] and not review["found"].get(name)]
            if missing:
                return {"account": self.repo.set_stage(key, "failed", "missing-fields"),
                        "errors": {"fields": "Campos obrigatórios ausentes: " + ", ".join(missing)}}
            try:
                for name, value in cleaned.items():
                    if review["found"].get(name):
                        page.locator(site["fields"][name]["selector"]).first.fill(value, timeout=6000)
                self.repo.set_stage(key, "pending", "submitted")
                page.locator(site["submitSelector"]).first.click(timeout=8000)
            except Exception:
                return {"account": self.repo.set_stage(key, "pending", "verification-required"),
                        "errors": {"operation": "Não foi possível confirmar o envio. Verifique na página."}}
            finally:
                # Do not retain secrets as part of a long-lived session.
                cleaned.clear()
            return {"account": self._verified_or_pending(key, page, site)}
        if action == "refresh":
            return {"account": self._verified_or_pending(key, page, site)}
        raise AccountError("Operação inválida.")

    def _verified_or_pending(self, key, page, site):
        success = site.get("successSelector")
        if success:
            try:
                if page.locator(success).first.is_visible(timeout=800):
                    self.review.pop(key, None)
                    return self.repo.set_stage(key, "completed", "provider-confirmed")
            except Exception:
                pass
        return self.repo.set_stage(key, "pending", "verification-required")

    def stop(self):
        self.execute("close", "").result(timeout=10)
        self.executor.shutdown(wait=False, cancel_futures=True)


class AccountsService:
    def __init__(self, db_path=None, start_browser=True):
        self.repo = AccountsRepository(db_path)
        self.browser = ExistingProfileBrowser(self.repo) if start_browser else None

    def stop(self):
        if self.browser:
            self.browser.stop()
