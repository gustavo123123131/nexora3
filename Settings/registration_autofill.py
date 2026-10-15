"""Complete the exact provided registration form in one Playwright page.

The caller selects exactly one local profile. The function does not decide
which sites to visit or how many windows to open.
"""
from __future__ import annotations

import time

# Only selectors derived from the supplied input/button HTML.
FIELDS = (
    ("Username", 'input[data-input-name="account"]', "username"),
    ("Senha", 'input[data-input-name="userpass"]', "password"),
    ("Confirmar senha", 'input[data-input-name="confirmPassword"]', "confirmPassword"),
    ("Nome real", 'input[data-input-name="realName"]', "fullName"),
)
SUBMIT = 'button#insideRegisterSubmitClick'


def fill_registration_form(page, profile: dict, log, stop_event, *,
                           timeout_ms: int = 15000, delay_seconds: float = 0.25) -> bool:
    """Return True when the click is sent, not when the site accepts registration.

    A missing field, changed value or stop request aborts without submitting.
    """
    prefix = f"[#{profile.get('id', '?')}]"
    log(f"{prefix} [Registro] Aguardando formulário.")
    if stop_event.is_set():
        log(f"{prefix} [Registro] Cancelado.")
        return False

    try:
        first = page.locator(FIELDS[0][1])
        first.wait_for(state="visible", timeout=timeout_ms)
        time.sleep(delay_seconds)
        for label, selector, key in FIELDS:
            if stop_event.is_set():
                log(f"{prefix} [Registro] Interrompido antes de {label}.")
                return False
            value = str(profile.get(key) or "")
            if not value:
                log(f"{prefix} [Registro] Campo {label} sem dados na conta selecionada.")
                return False
            field = page.locator(selector)
            field.wait_for(state="visible", timeout=timeout_ms)
            field.fill(value, timeout=timeout_ms)
            time.sleep(delay_seconds)
            if field.input_value(timeout=timeout_ms) != value:
                log(f"{prefix} [Registro] Falha na verificação do campo {label}.")
                return False
            log(f"{prefix} [Registro] {label}: preenchido e conferido.")

        if stop_event.is_set():
            log(f"{prefix} [Registro] Interrompido antes de Registrar.")
            return False
        button = page.locator(SUBMIT)
        button.wait_for(state="visible", timeout=timeout_ms)
        time.sleep(delay_seconds)
        if stop_event.is_set():
            log(f"{prefix} [Registro] Interrompido antes do clique.")
            return False
        button.click(timeout=timeout_ms)
        log(f"{prefix} [Registro] Clique em Registrar enviado; resultado do site não confirmado.")
        return True
    except Exception as exc:
        log(f"{prefix} [Registro] Fluxo interrompido em formulário/clique: {type(exc).__name__}.")
        return False
