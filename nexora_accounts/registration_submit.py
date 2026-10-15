"""Submit an explicitly confirmed registration in an existing Playwright page.

Each browser window has its own watcher, bound to the original profile record.
Submission occurs only in the Playwright-owning thread. No raw API replay,
credential logging, CAPTCHA bypass, or automatic retry after uncertain results.
"""
import os
import re
from datetime import datetime, timezone
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

# Observe a person's own interaction in the already-open Chromium page.
# This only records Boolean activity, never field values or credentials.
# Capture-phase listeners work even when the site's register dialog rerenders.
_MANUAL_TRACKER_INSTALL = r"""() => {
  if (window.__nexoraRegTrackerV1) return;
  const tracker = {name: false, clicks: 0};
  window.__nexoraRegTrackerV1 = tracker;
  document.addEventListener('input', (event) => {
    if (event.target && event.target.matches &&
        event.target.matches('input[data-input-name="realName"]')) {
      tracker.name = true;
    }
  }, true);
  document.addEventListener('click', (event) => {
    if (event.target && event.target.closest &&
        event.target.closest('#insideRegisterSubmitClick')) {
      tracker.clicks += 1;
    }
  }, true);
}"""
_MANUAL_TRACKER_DRAIN = r"""() => {
  const tracker = window.__nexoraRegTrackerV1;
  if (!tracker) return null;
  const state = {name: tracker.name, clicks: tracker.clicks};
  tracker.name = false;
  tracker.clicks = 0;
  return state;
}"""


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
    # Site HTML advertises maxlength=16; Nexora now creates nine-character
    # test passwords. Keep older stored credentials up to 16 unchanged.
    if not 7 <= len(cleaned["password"]) <= 16:
        raise AccountError("Senha inválida: use entre 7 e 16 caracteres.")
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



def request_confirmed_auto_submit(account_id, repository=None):
    """Queue one browser submit only for explicitly confirmed, non-test profiles.

    Called after Abrir Conta has filled its form. Uses the same safe queue
    and one-click watcher as request_submit. A generated test profile must
    never be automatically submitted to a third-party registration service.
    """
    from nexora_accounts.registration_autofill import _demo
    repo = repository if repository is not None else AccountsRepository()
    account = repo.account(account_id)
    profile = account.get("profileData") or {}
    if _demo(account.get("targetUrl", "")):
        return False  # request_demo_submit handles the local test route.
    if profile.get("isTestData") is not False or profile.get("ownerConfirmed") is not True:
        return False
    # Do not click on an unverified or incomplete individual owner identity.
    full_name = profile.get("fullName")
    email = profile.get("email")
    phone = profile.get("phone")
    if not isinstance(full_name, str) or len(full_name.strip().split()) < 2:
        return False
    if isinstance(email, str) and email.lower().endswith(
            ("@example.test", "@example.com", "@example.org", "@example.net")):
        return False
    if not (isinstance(email, str) and email.strip() or
            isinstance(phone, str) and phone.strip()):
        return False
    return bool(request_submit(account_id, True, repo)["queued"])


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
        self.last_stage_at = self.clock()
        self.finished = False
        self._manual_tracking = False
        self.page.on("response", self._on_response)
        self._enable_manual_tracking()

    def _enable_manual_tracking(self):
        """Install readonly event counters in the existing page, when possible."""
        try:
            self.page.evaluate(_MANUAL_TRACKER_INSTALL)
            self._manual_tracking = True
        except Exception:
            # Offline stubs and pages mid-navigation might not support evaluate.
            self._manual_tracking = False

    def _record_manual_activity(self):
        """Observe in-browser typing and clicks without reading their values."""
        if not self._manual_tracking:
            self._enable_manual_tracking()
        if not self._manual_tracking:
            return
        try:
            activity = self.page.evaluate(_MANUAL_TRACKER_DRAIN)
            if not isinstance(activity, dict):
                self._manual_tracking = False
                return
            if activity.get("name"):
                current = self.repo.account(self.key).get("status", "local")
                self._stage(current, "name-entered-in-browser")
            if activity.get("clicks") and self.started_at is None:
                if self._valid_page():
                    self.started_at = self.clock()
                    self._stage("pending", "manual-submit-clicked")
                else:
                    self._stage("local", "origin-changed")
        except Exception:
            # A navigation can destroy the tracker. Reinstall on next tick.
            self._manual_tracking = False

    def _on_response(self, response):
        """Record status and an OPTIONAL numeric provider code, never a response body."""
        try:
            if (urlparse(response.url).path.rstrip("/") == "/hall/api/member/register"
                    and response.request.method.upper() == "POST"):
                business_code = None
                try:
                    data = response.json()
                    raw_code = data.get("code") if isinstance(data, dict) else None
                    if isinstance(raw_code, (int, str)) and re.fullmatch(
                            r"[0-9]{1,8}", str(raw_code)):
                        business_code = int(raw_code)
                except Exception:
                    pass
                self.http_codes.append((int(response.status), business_code))
        except Exception:
            pass

    def _stage(self, status, stage, http_status=None, error_code=None):
        if self.last_stage == stage and http_status is None and error_code is None:
            return
        now = self.clock()
        duration_ms = max(0, min(int((now - self.last_stage_at) * 1000), 86_400_000))
        self.last_stage_at = now
        self.last_stage = stage
        self.repo.set_stage(
            self.key, status, stage, http_status=http_status,
            error_code=error_code, duration_ms=duration_ms,
        )
        at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        line = (
            f"[{at}] [perfil #{self.plan.get('idx', '?')} "
            f"id={self.key[:8]}] [{stage}] {duration_ms} ms"
            + (f" HTTP={http_status}" if http_status else "")
            + (f" erro={error_code}" if error_code else "")
        )
        print(line, flush=True)
        self.log(line)

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
            # Do not turn an untouched/blocked local profile into a submitted one.
            if self.started_at is not None:
                self._stage("pending", "browser-closed-unverified")
            self.finished = True
            return

        # Capture the user's own click before processing any queued automation.
        # Internal button.click() sets started_at before triggering its DOM event,
        # so that later event is not incorrectly described as a manual click.
        self._record_manual_activity()
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
                    self._stage("pending", "submit-button-missing",
                                error_code="selector-not-found")
                    return
                # Never manipulate terms/CAPTCHA; real website validation remains in place.
                self.started_at = self.clock()
                self._stage("pending", "submitting")
                button.click(timeout=8000)
                self._stage("pending", "awaiting-provider")
            except Exception as exc:
                # The click could already have reached the provider; never retry.
                code = type(exc).__name__.lower().replace("_", "-")[:55]
                self._stage("pending", "submit-result-unknown", error_code=code)
                return
            finally:
                if command.get("values") is not None:
                    command["values"].clear()

        if self.http_codes:
            http_code, provider_code = self.http_codes.pop(0)
            if self.started_at is None:
                self.started_at = self.clock()  # User may submit via site itself.
            if provider_code == 1126:
                # Security verification observed in the original trace.
                self._stage("pending", "verification-required",
                            http_status=http_code, error_code="provider-1126")
            elif http_code >= 400:
                self._stage("pending", "provider-http-error", http_status=http_code,
                            error_code=("provider-code-" + str(provider_code))
                            if provider_code is not None else None)
            elif provider_code not in (None, 0):
                # Unknown business codes are never interpreted as success.
                self._stage("pending", "provider-response-unverified",
                            http_status=http_code,
                            error_code="provider-code-" + str(provider_code))
            else:
                self._stage("pending", "provider-response-unverified", http_status=http_code)

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
