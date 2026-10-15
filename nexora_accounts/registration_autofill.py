"""Pre-fill the already-open Playwright registration page using its linked Nexora account.

This module never sends a registration, invokes a private HTTP API, confirms a
real account or solves CAPTCHA. The site owns its final submission.
"""
import time
from urllib.parse import urlparse
from nexora_accounts.service import AccountsRepository

FIELDS = {
    "account": ("input[data-input-name='account']", "[data-item-name='account'] input"),
    "userpass": ("input[data-input-name='userpass']", "[data-item-name='userpass'] input"),
    "confirmPassword": ("input[data-input-name='confirmPassword']", "[data-item-name='confirmPassword'] input"),
    "realName": ("input[data-input-name='realName']", "[data-item-name='realName'] input"),
    "email": ("input[data-input-name='email']",),
    "phone": ("input[data-input-name='phone']",),
    "cpf": ("input[data-input-name='cpf']",),
}


def _visible(page, selectors):
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if locator.is_visible():
                return locator
        except Exception:
            pass
    return None


def _same_site(expected, current):
    a, b = urlparse(expected), urlparse(current)
    if not a.hostname or not b.hostname or a.hostname.lower() != b.hostname.lower():
        return False
    return b.scheme == "https" or (b.scheme == "http" and b.hostname.lower()
                                    in ("localhost", "127.0.0.1", "::1"))


def _demo(url):
    host = (urlparse(url).hostname or "").lower()
    return host in ("localhost", "127.0.0.1", "::1") or host.endswith(".test")


def _wait_for_registration(page, seconds=12):
    try:
        page.wait_for_load_state("domcontentloaded", timeout=10000)
    except Exception:
        pass
    deadline = time.monotonic() + seconds
    opened = False
    while time.monotonic() < deadline:
        found = {key: _visible(page, selectors) for key, selectors in FIELDS.items()}
        if all(found[key] is not None for key in ("account", "userpass", "confirmPassword")):
            return found
        if not opened:
            for selector in ("#registerClickModal", "#registerTabButton"):
                opener = _visible(page, (selector,))
                if opener:
                    try:
                        opener.click(timeout=3000)
                        opened = True
                        break
                    except Exception:
                        pass
        time.sleep(0.25)
    return None


def _wait_for_username(page, seconds=8):
    """Find the user-supplied account input, without requiring other fields.

    This remains useful when the remote site's identity checks prevent the
    rest of the registration form from appearing.
    """
    try:
        page.wait_for_load_state("domcontentloaded", timeout=5000)
    except Exception:
        pass
    deadline = time.monotonic() + seconds
    opened = False
    while time.monotonic() < deadline:
        field = _visible(page, FIELDS["account"])
        if field is not None:
            return field
        if not opened:
            for selector in ("#registerClickModal", "#registerTabButton"):
                opener = _visible(page, (selector,))
                if opener:
                    try:
                        opener.click(timeout=3000)
                        opened = True
                        break
                    except Exception:
                        pass
        time.sleep(0.2)
    return None


def autofill_existing_registration(page, instance_plan, repository=None, progress=None):
    """Fill available fields in the original browser; never submit fictional identities.

    Optional progress callback records completed DOM actions (no field values).
    """
    def report(stage):
        if callable(progress):
            progress(stage)

    plan = instance_plan if isinstance(instance_plan, dict) else {}
    key = plan.get("profile_record_id")
    if not isinstance(key, str) or not key:
        return {"status": "skipped", "message": "Perfil sem registro local associado."}
    repo = repository if repository is not None else AccountsRepository()
    record = repo.account(key)
    target = record.get("targetUrl", "")
    if not target:
        return {"status": "skipped", "message": "Perfil sem URL."}
    if not _same_site(target, page.url):
        return {"status": "blocked", "message": "Domínio diferente do perfil; preenchimento bloqueado."}
    data = record.get("profileData")
    if not isinstance(data, dict):
        return {"status": "blocked", "message": "Dados do perfil indisponíveis."}
    demo = _demo(target)
    if demo and not _demo(page.url):
        return {"status": "blocked", "message": "Página de teste redirecionou para site externo."}
    if not demo and (data.get("isTestData") is not False or
                     data.get("ownerConfirmed") is not True):
        # A generated LOGIN NAME is not a civil identity. It is safe to
        # preview it in the visible username field, without forwarding
        # fictional name/phone/CPF/email or submitting a real registration.
        username = data.get("username")
        if not isinstance(username, str) or not username.strip():
            repo.set_stage(key, "local", "username-missing")
            return {
                "status": "not-found", "fieldCount": 0,
                "message": "Este perfil ainda não tem username salvo.",
            }
        field = _wait_for_username(page)
        if field is None:
            repo.set_stage(key, "local", "username-field-not-found")
            return {
                "status": "not-found", "fieldCount": 0,
                "missingFields": ["account"],
                "message": "Campo não encontrado: input[data-input-name='account'].",
            }
        if not _same_site(target, page.url):
            return {
                "status": "blocked", "fieldCount": 0,
                "message": "O site mudou de domínio; não preenchi dados.",
            }
        try:
            field.fill(username.strip(), timeout=6000)
            report("username-filled")
        except Exception:
            repo.set_stage(key, "local", "username-fill-error")
            return {
                "status": "error", "fieldCount": 0,
                "message": "Falha ao preencher o input account (verifique se está habilitado).",
            }
        # The generated username/password are account credentials, not proof
        # of a person's identity. Prefill both password inputs when available;
        # never fill a synthetic legal name, phone, email or CPF on real sites.
        filled = 1
        password = data.get("password")
        if isinstance(password, str) and 7 <= len(password) <= 16:
            fields = _wait_for_registration(page, seconds=5)
            if fields is not None:
                for name in ("userpass", "confirmPassword"):
                    if not _same_site(target, page.url):
                        return {
                            "status": "blocked", "fieldCount": filled,
                            "message": "Domínio alterado durante o preenchimento.",
                        }
                    try:
                        fields[name].fill(password, timeout=6000)
                        report("password-filled" if name == "userpass"
                               else "password-confirmed")
                    except Exception:
                        repo.set_stage(key, "local", "credentials-fill-error")
                        return {
                            "status": "error", "fieldCount": filled,
                            "message": f"Falha ao preencher o campo {name}.",
                        }
                    filled += 1
        stage = "credentials-prefilled" if filled == 3 else "username-prefilled"
        repo.set_stage(key, "local", stage)
        if filled == 3:
            # Report the exact reason the externally hosted registration stops.
            # A synthetic "fullName" is not the verified owner of a real account.
            real_name = _visible(page, FIELDS["realName"])
            report("real-name-field-found" if real_name is not None
                   else "real-name-field-missing")
            submit_button = _visible(page, ("#insideRegisterSubmitClick",))
            report("submit-button-found" if submit_button is not None
                   else "submit-button-missing")
            report("real-name-required")
            report("submit-not-sent")
            return {
                "status": "credentials-filled", "fieldCount": filled,
                "message": "Username, senha e confirmação preenchidos. "
                           "Nome civil e verificações devem ser concluídos "
                           "pelo titular antes de qualquer envio.",
            }
        return {
            "status": "username-filled", "fieldCount": 1,
            "message": "Username preenchido. Campos de senha indisponíveis "
                       "ou senha do perfil fora do intervalo 7-16; "
                       "cadastro externo não enviado.",
        }

    fields = _wait_for_registration(page)
    if not fields:
        repo.set_stage(key, "local", "form-not-found")
        required = ("account", "userpass", "confirmPassword")
        missing = [name for name in required if _visible(page, FIELDS[name]) is None]
        return {
            "status": "not-found",
            "missingFields": missing,
            "message": ("Formulário não encontrado: faltam os campos "
                        + ", ".join(missing) if missing
                        else "Formulário de cadastro não ficou visível a tempo."),
        }
    if not _same_site(target, page.url):
        return {"status": "blocked", "message": "A página mudou de domínio."}

    account_field = fields["account"]
    identifier = (data.get("email") if (account_field.get_attribute("type") or "").lower() == "email"
                  else data.get("username"))
    values = {
        "account": identifier,
        "userpass": data.get("password"),
        "confirmPassword": data.get("password"),
        "realName": data.get("fullName"),
        "email": data.get("email"),
        "phone": data.get("phone"),
        "cpf": data.get("cpf") if data.get("cpfVerified") is True else None,
    }
    if any(not isinstance(values[k], str) or not values[k].strip()
           for k in ("account", "userpass", "confirmPassword")):
        return {"status": "blocked", "message": "Dados obrigatórios incompletos."}
    filled = 0
    progress_stages = {
        "account": "username-filled",
        "userpass": "password-filled",
        "confirmPassword": "password-confirmed",
        "realName": "real-name-filled",
        "email": "email-filled",
        "phone": "phone-filled",
        "cpf": "cpf-filled",
    }
    for name, element in fields.items():
        value = values.get(name)
        if element is not None and isinstance(value, str) and value.strip():
            if not _same_site(target, page.url):
                return {"status": "blocked", "message": "Domínio alterado durante o preenchimento."}
            element.fill(value, timeout=6000)
            filled += 1
            report(progress_stages[name])
    submit_button = _visible(page, ("#insideRegisterSubmitClick",))
    report("submit-button-found" if submit_button is not None
           else "submit-button-missing")
    repo.set_stage(key, "pending", "review")
    return {
        "status": "filled", "fieldCount": filled,
        "message": "Campos preenchidos. Revise os dados, aceite os termos e envie manualmente.",
    }
