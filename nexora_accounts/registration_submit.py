"""Submit an explicitly confirmed registration in an existing Playwright page.

Each browser window has its own watcher, bound to the original profile record.
Submission occurs only in the Playwright-owning thread. No raw API replay,
credential logging, CAPTCHA bypass, or automatic retry after uncertain results.
"""
import os
import re
import threading
import time
from urllib.parse import urlparse

from nexora_accounts.service import AccountsRepository, AccountError

_QUEUED = {}  # account ID -> {values: approved data or None}, memory only
_QUEUE_LOCK = threading.Lock()
_SUBMIT_SELECTORS = (
    "#insideRegisterSubmitClick",
    "button.ui-button--primary:has-text('Registro')",
    "button.ui-button--primary:has-text('Cadastrar')",
)


def _real_values(values, generated=None):
    """Require legitimate owner/contact info but reuse the existing generated login.

    The backend owns username/password resolution: the user need not type
    credentials already saved in this profile. Identity/contacts cannot be
    fabricated for a real service. Secrets stay ephemeral in the submission queue.
    """
    if not isinstance(values, dict):
        raise AccountError("Dados de cadastro inválidos.")
    generated = generated if isinstance(generated, dict) else {}
    cleaned = {}
    for field in ("username", "password", "confirmPassword", "fullName", "email", "phone"):
        default = generated.get(field, "") if field in ("username", "password") else ""
        if field == "confirmPassword":
            default = values.get("password", generated.get("password", ""))
        value = values.get(field, "") or default
        if not isinstance(value, str) or len(value) > 160 or any(ord(c) < 32 for c in value):
            raise AccountError("Campo inválido: " + field + ".")
        cleaned[field] = value if field in ("password", "confirmPassword") else value.strip()

    if not 3 <= len(cleaned["username"]) <= 80:
        raise AccountError("Nome de usuário inválido: use entre 3 e 80 caracteres.")
    if not 12 <= len(cleaned["password"]) <= 128:
        raise AccountError("Senha curta: são necessários pelo menos 12 caracteres. Gere um novo perfil.")
    if cleaned["password"] != cleaned["confirmPassword"]:
        raise AccountError("A confirmação da senha não coincide.")
    if len(cleaned["fullName"]) < 3:
        raise AccountError("Informe o nome legítimo do titular.")
    if not cleaned["email"] and not cleaned["phone"]:
        raise AccountError("Informe um e-mail ou telefone legítimo.")
    if cleaned["email"] and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", cleaned["email"]):
        raise AccountError("E-mail inválido.")
    if cleaned["email"].lower().endswith(("@example.test", "@example.com", "@example.org", "@example.net")):
        raise AccountError("E-mail reservado para testes. Use um endereço que você controla.")
    if cleaned["phone"] and not re.fullmatch(r"\+?[\d\s()\-]{10,20}", cleaned["phone"]):
        raise AccountError("Telefone inválido.")
    return cleaned


def request_submit(account_id, user_confirmed, repository=None, values=None):
    """Queue only a confirmed registration; raw personal data lives in memory."""
    if user_confirmed is not True:
        raise AccountError("Confirme o envio do formulário.")
    repo = repository if repository is not None else AccountsRepository()
    account = repo.account(account_id)
    data = account.get("profileData") or {}
    from nexora_accounts.registration_autofill import _demo
    supplied = _real_values(values, data) if values is not None else None
    if supplied is None and not _demo(account.get("targetUrl", "")) and (
            data.get("isTestData") is not False or data.get("ownerConfirmed") is not True):
        raise AccountError("Dados de teste não podem ser enviados ao provedor.")
    allowed_stages = ("review",) if supplied is None else (
        "review", "test-data-blocked", "form-not-found")
    with _QUEUE_LOCK:
        if account_id in _QUEUED:
            return {"queued": True, "duplicate": True}
        if account.get("stage") not in allowed_stages:
            raise AccountError("Navegador ainda não está pronto para cadastrar.")
        _QUEUED[account_id] = {"values": supplied}
    try:
        repo.set_stage(account_id, "pending", "submit-queued")
    except Exception:
        with _QUEUE_LOCK:
            _QUEUED.pop(account_id, None)
        raise
    return {"queued": True, "duplicate": False}


def request_demo_submit(account_id, repository=None):
    """Auto-send ONLY to local/.test demo pages after Abrir Conta; never remotely.

    The user's Abrir Conta action starts the local-only test workflow.
    No personal identity is claimed and no real registration is attempted.
    """
    from nexora_accounts.registration_autofill import _demo
    repo = repository if repository is not None else AccountsRepository()
    account = repo.account(account_id)
    if not _demo(account.get("targetUrl", "")):
        return False
    if (account.get("profileData") or {}).get("isTestData") is not True:
        return False
    with _QUEUE_LOCK:
        if account.get("stage") != "review" or account_id in _QUEUED:
            return False
        _QUEUED[account_id] = {"values": None}
    try:
        repo.set_stage(account_id, "pending", "submit-queued")
    except Exception:
        with _QUEUE_LOCK:
            _QUEUED.pop(account_id, None)
        raise
    return True


def _take(account_id):
    with _QUEUE_LOCK:
        return _QUEUED.pop(account_id, None)


class RegistrationWatcher:
    """A watcher belongs to exactly one page and must be ticked on its thread."""

    def __init__(self, page, instance_plan, log=lambda _message: None,
                 repository=None, success_selector=None, clock=None):
        self.page = page
        self.plan = instance_plan
        self.key = instance_plan["profile_record_id"]
        self.repo = repository if repository is not None else AccountsRepository()
        self.log = log
        self.clock = clock if clock is not None else time.monotonic
        self.success_selector = (
            success_selector if success_selector is not None else
            os.environ.get("NEXORA_REGISTRATION_SUCCESS_SELECTOR", "").strip()
        )
        self.http_codes = []
        self.started_at = None
        self.last_check = 0
        self.last_stage = "review"
        self.finished = False
        self.page.on("response", self._on_response)

    def _on_response(self, response):
        """Only numeric status and the known registration route; never read body."""
        try:
            if (urlparse(response.url).path.rstrip("/") == "/hall/api/member/register"
                    and response.request.method.upper() == "POST"):
                self.http_codes.append(int(response.status))
        except Exception:
            pass

    def _stage(self, status, stage, http_status=None):
        if self.last_stage == stage:
            return
        self.last_stage = stage
        self.repo.set_stage(self.key, status, stage, http_status=http_status)
        self.log(f"[Cadastro] Perfil {self.plan.get('idx', '?')}: {stage}.")

    def _valid_page(self):
        from nexora_accounts.registration_autofill import _same_site
        account = self.repo.account(self.key)
        return _same_site(account.get("targetUrl", ""), self.page.url)

    def _check_success(self):
        # Site-specific, documented authenticated success evidence is mandatory.
        if not self.success_selector:
            return False
        try:
            return self.page.locator(self.success_selector).first.is_visible()
        except Exception:
            return False

    def tick(self):
        if self.finished:
            return
        if self.page.is_closed():
            self._stage("pending", "browser-closed-unverified")
            self.finished = True
            return

        command = _take(self.key)
        if command is not None:
            if not self._valid_page():
                self._stage("pending", "origin-changed")
                return
            try:
                values = command.get("values")
                if values is not None:
                    from nexora_accounts.registration_autofill import (
                        _wait_for_registration, _same_site
                    )
                    fields = _wait_for_registration(self.page)
                    if not fields:
                        self._stage("pending", "form-not-found")
                        return
                    mapping = {
                        "account": (
                            values["email"] if (
                                fields["account"].get_attribute("type") or ""
                            ).lower() == "email" and values["email"]
                            else values["username"]
                        ),
                        "userpass": values["password"],
                        "confirmPassword": values["password"],
                        "realName": values["fullName"],
                        "email": values["email"],
                        "phone": values["phone"],
                    }
                    target = self.repo.account(self.key)["targetUrl"]
                    for name, element in fields.items():
                        value = mapping.get(name)
                        if element is not None and value:
                            if not _same_site(target, self.page.url):
                                self._stage("pending", "origin-changed")
                                return
                            element.fill(value, timeout=6000)
                    self._stage("pending", "review-real-data")
                button = next((btn for selector in _SUBMIT_SELECTORS
                               if (btn := self.page.locator(selector).first).is_visible()), None)
                if button is None:
                    self._stage("pending", "submit-button-missing")
                    return
                # Never manipulate terms/CAPTCHA; real website validation remains in place.
                self.started_at = self.clock()
                self._stage("pending", "submitting")
                button.click(timeout=8000)
                self._stage("pending", "awaiting-provider")
            except Exception:
                # The click may have reached the server before an error/timeout.
                self._stage("pending", "submit-result-unknown")
                return
            finally:
                if command.get("values") is not None:
                    command["values"].clear()

        if self.http_codes:
            code = self.http_codes.pop(0)
            if self.started_at is None:
                self.started_at = self.clock()  # User submitted in site itself.
            if code >= 400:
                self._stage("pending", "provider-http-error", http_status=code)
            else:
                self._stage("pending", "provider-response-unverified", http_status=code)

        # Throttle DOM polling but keep processing network events immediately.
        now = self.clock()
        if now - self.last_check < 0.8:
            return
        self.last_check = now
        if self.started_at is not None and self._check_success():
            self._stage("completed", "provider-confirmed")
            self.finished = True
            return
        if self.started_at is not None and now - self.started_at > 45:
            self._stage("pending", "timeout-unverified")
            # Keep watcher alive: completing email/SMS verification can take longer.
