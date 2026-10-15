"""Submit an explicitly confirmed registration in an existing Playwright page.

Each browser window has its own watcher, bound to the original profile record.
Submission occurs only in the Playwright-owning thread. No raw API replay,
credential logging, CAPTCHA bypass, or automatic retry after uncertain results.
"""
import os
import threading
import time
from urllib.parse import urlparse

from nexora_accounts.service import AccountsRepository, AccountError

_QUEUED = set()
_QUEUE_LOCK = threading.Lock()
_SUBMIT_SELECTORS = (
    "#insideRegisterSubmitClick",
    "button.ui-button--primary:has-text('Registro')",
    "button.ui-button--primary:has-text('Cadastrar')",
)


def request_submit(account_id, user_confirmed, repository=None):
    """Queue one explicit action; never touch a Playwright page from API threads."""
    if user_confirmed is not True:
        raise AccountError("Confirme o envio do formulário.")
    repo = repository if repository is not None else AccountsRepository()
    account = repo.account(account_id)
    data = account.get("profileData") or {}
    from nexora_accounts.registration_autofill import _demo
    if not _demo(account.get("targetUrl", "")) and (
            data.get("isTestData") is not False or data.get("ownerConfirmed") is not True):
        raise AccountError("Dados de teste não podem ser enviados ao provedor.")
    with _QUEUE_LOCK:
        if account_id in _QUEUED:
            return {"queued": True, "duplicate": True}
        if account.get("status") != "pending" or account.get("stage") != "review":
            raise AccountError("O formulário não está pronto. Aguarde a preparação ou revise o perfil.")
        _QUEUED.add(account_id)
    try:
        repo.set_stage(account_id, "pending", "submit-queued")
    except Exception:
        with _QUEUE_LOCK:
            _QUEUED.discard(account_id)
        raise
    return {"queued": True, "duplicate": False}


def _take(account_id):
    with _QUEUE_LOCK:
        if account_id not in _QUEUED:
            return False
        _QUEUED.remove(account_id)
        return True


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

        if _take(self.key):
            if not self._valid_page():
                self._stage("pending", "origin-changed")
                return
            try:
                button = next((btn for selector in _SUBMIT_SELECTORS
                               if (btn := self.page.locator(selector).first).is_visible()), None)
                if button is None:
                    self._stage("pending", "submit-button-missing")
                    return
                # Does not check terms or circumvent disabled controls: site validates.
                self.started_at = self.clock()
                self._stage("pending", "submitting")
                button.click(timeout=8000)
                self._stage("pending", "awaiting-provider")
            except Exception:
                # The click could have reached the remote server before timing out.
                self._stage("pending", "submit-result-unknown")
                return

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
