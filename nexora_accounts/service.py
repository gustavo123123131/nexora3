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
import os
import re
import sqlite3
import threading
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
        elif key == "password" and not 12 <= len(value) <= 128:
            errors[key] = "Senha deve ter de 12 a 128 caracteres"
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


class AccountsRepository:
    def __init__(self, db_path=None):
        self.path = Path(db_path) if db_path else _default_folder() / "accounts.sqlite"
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._lock = threading.RLock()
        with self._open() as db:
            db.executescript("""
              PRAGMA foreign_keys=ON;
              CREATE TABLE IF NOT EXISTS sites(
                id TEXT PRIMARY KEY, config TEXT NOT NULL, created_at TEXT NOT NULL
              );
              CREATE TABLE IF NOT EXISTS accounts(
                id TEXT PRIMARY KEY, site_id TEXT NOT NULL REFERENCES sites(id),
                display_name TEXT NOT NULL, login_identifier TEXT NOT NULL,
                parent_id TEXT REFERENCES accounts(id) ON DELETE SET NULL,
                profile_scope TEXT NOT NULL CHECK(profile_scope IN ('mae','filha')),
                profile_mode TEXT NOT NULL CHECK(profile_mode IN ('desktop','android','iphone','mixed')),
                profile_index INTEGER NOT NULL CHECK(profile_index BETWEEN 1 AND 999),
                status TEXT NOT NULL CHECK(status IN ('local','pending','completed','failed')),
                stage TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
              );
              CREATE TABLE IF NOT EXISTS account_audit(
                id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT NOT NULL,
                entity TEXT NOT NULL, occurred_at TEXT NOT NULL
              );
            """)

    def _open(self):
        db = sqlite3.connect(str(self.path), timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def _audit(self, db, action, entity):
        db.execute("INSERT INTO account_audit(action,entity,occurred_at) VALUES(?,?,?)",
                   (action, entity, _now()))

    def sites(self):
        with self._lock, self._open() as db:
            return [json.loads(r["config"]) for r in db.execute(
                "SELECT config FROM sites ORDER BY created_at DESC")]

    def site(self, key):
        with self._lock, self._open() as db:
            row = db.execute("SELECT config FROM sites WHERE id=?", (key,)).fetchone()
        if row is None:
            raise AccountError("Site não configurado.")
        return json.loads(row["config"])

    def add_site(self, data):
        config = _site_config(data)
        with self._lock, self._open() as db:
            if db.execute("SELECT 1 FROM sites WHERE id=?", (config["id"],)).fetchone():
                raise AccountError("Já existe um site com esse ID.")
            db.execute("INSERT INTO sites(id,config,created_at) VALUES(?,?,?)",
                       (config["id"], json.dumps(config), _now()))
            self._audit(db, "site.create", config["id"])
        return config

    @staticmethod
    def _account(row):
        return {
            "id": row["id"], "siteId": row["site_id"], "displayName": row["display_name"],
            "loginIdentifier": row["login_identifier"], "parentId": row["parent_id"],
            "profileScope": row["profile_scope"], "profileMode": row["profile_mode"],
            "profileIndex": row["profile_index"], "status": row["status"],
            "stage": row["stage"], "createdAt": row["created_at"], "updatedAt": row["updated_at"],
        }

    def accounts(self, query="", status=""):
        if not isinstance(query, str) or len(query) > 128 or status not in ("", *STATUS):
            raise AccountError("Filtro inválido.")
        with self._lock, self._open() as db:
            rows = db.execute("SELECT * FROM accounts ORDER BY created_at DESC").fetchall()
        return [self._account(r) for r in rows if
                (not status or r["status"] == status) and
                (not query or query.lower() in (r["display_name"] + " " + r["site_id"] +
                                                 " " + r["login_identifier"]).lower())]

    def account(self, key):
        with self._lock, self._open() as db:
            row = db.execute("SELECT * FROM accounts WHERE id=?", (key,)).fetchone()
        if row is None:
            raise AccountError("Conta não encontrada.")
        return self._account(row)

    def add_account(self, data):
        if not isinstance(data, dict):
            raise AccountError("Dados inválidos.")
        site = self.site(data.get("siteId"))
        display = _text(data.get("displayName", ""), "Identificação", 120)
        ident = _text(data.get("loginIdentifier", ""), "Identificador de login", 128, False)
        scope = data.get("profileScope", "mae")
        mode = data.get("profileMode", "desktop")
        idx = data.get("profileIndex", 1)
        if scope not in ("mae", "filha") or mode not in ("desktop", "android", "iphone", "mixed") \
                or isinstance(idx, bool) or not isinstance(idx, int) or not (1 <= idx <= 999):
            raise AccountError("Perfil inválido.")
        parent = data.get("parentId") or None
        if parent and (self.account(parent)["profileScope"] != "mae" or scope != "filha"):
            raise AccountError("Conta filha deve apontar para uma conta mãe.")
        key, now = str(uuid.uuid4()), _now()
        with self._lock, self._open() as db:
            db.execute("""INSERT INTO accounts(id,site_id,display_name,login_identifier,
                parent_id,profile_scope,profile_mode,profile_index,status,stage,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (key, site["id"], display, ident, parent, scope, mode, idx, "local", "saved", now, now))
            self._audit(db, "account.create", key)
        return self.account(key)

    def set_stage(self, key, status, stage):
        if status not in STATUS or not re.fullmatch(r"[a-z0-9_-]{1,60}", stage):
            raise AccountError("Status inválido.")
        self.account(key)
        with self._lock, self._open() as db:
            db.execute("UPDATE accounts SET status=?,stage=?,updated_at=? WHERE id=?",
                       (status, stage, _now(), key))
            self._audit(db, "account.status", key)
        return self.account(key)

    def delete(self, key):
        self.account(key)
        with self._lock, self._open() as db:
            db.execute("DELETE FROM accounts WHERE id=?", (key,))
            self._audit(db, "account.delete", key)


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
