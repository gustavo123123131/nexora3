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


def autofill_existing_registration(page, instance_plan, repository=None):
    """Use only this browser's saved profile; NEVER submit the form."""
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
        repo.set_stage(key, "local", "test-data-blocked")
        return {"status": "blocked",
                "message": "Dados fictícios não usados no cadastro real. Informe dados legítimos."}

    fields = _wait_for_registration(page)
    if not fields:
        repo.set_stage(key, "local", "form-not-found")
        return {"status": "not-found", "message": "Formulário de registro não encontrado."}
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
    for name, element in fields.items():
        value = values.get(name)
        if element is not None and isinstance(value, str) and value.strip():
            if not _same_site(target, page.url):
                return {"status": "blocked", "message": "Domínio alterado durante o preenchimento."}
            element.fill(value, timeout=6000)
            filled += 1
    repo.set_stage(key, "pending", "review")
    return {
        "status": "filled", "fieldCount": filled,
        "message": "Campos preenchidos. Revise os dados, aceite os termos e envie manualmente.",
    }
