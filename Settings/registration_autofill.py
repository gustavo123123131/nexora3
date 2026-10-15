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

    stage = "Aguardar formulário"
    try:
        first = page.locator(FIELDS[0][1])
        first.wait_for(state="visible", timeout=timeout_ms)
        time.sleep(delay_seconds)
        for label, selector, key in FIELDS:
            stage = label
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
        stage = "Encontrar botão Registrar"
        log(f"{prefix} [Registro] Todos os campos conferidos. Procurando botão Registrar.")
        button = page.locator(SUBMIT)
        initial_count = button.count()
        log(f"{prefix} [Registro] Seletor {SUBMIT}: {initial_count} elemento(s) no DOM.")
        stage = "Aguardar botão Registrar visível"
        button.wait_for(state="visible", timeout=timeout_ms)
        matches = button.count()
        if matches != 1:
            log(f"{prefix} [Registro] Clique não realizado: encontrados {matches} botões; esperado exatamente 1.")
            return False
        enabled = button.is_enabled()
        log(f"{prefix} [Registro] Botão visível; habilitado={'sim' if enabled else 'não'}.")
        if not enabled:
            log(f"{prefix} [Registro] Aguardando o site habilitar o botão antes do clique.")
        time.sleep(delay_seconds)
        if stop_event.is_set():
            log(f"{prefix} [Registro] Interrompido antes do clique.")
            return False

        stage = "Clicar em Registrar"
        log(f"{prefix} [Registro] Iniciando clique no botão Registrar (timeout {timeout_ms} ms).")
        button.click(timeout=timeout_ms)
        log(f"{prefix} [Registro] Clique em Registrar enviado; resultado do site não confirmado.")
        return True
    except Exception as exc:
        detail = str(exc).lower()
        if "intercepts pointer events" in detail:
            reason = "Outro elemento está interceptando o clique no botão."
        elif "not enabled" in detail or "disabled" in detail:
            reason = "O site manteve o botão desabilitado."
        elif "strict mode violation" in detail:
            reason = "O seletor encontrou mais de um botão."
        elif "not visible" in detail or "hidden" in detail:
            reason = "O botão está oculto."
        elif "detached" in detail:
            reason = "O site substituiu o botão enquanto o clique era preparado."
        elif "target page, context or browser has been closed" in detail:
            reason = "A página ou o navegador foi fechado."
        elif "timeout" in detail:
            reason = "O tempo de espera acabou; o botão pode não estar clicável."
        else:
            reason = "O navegador não concluiu esta etapa."
        log(f"{prefix} [Registro] Falha na etapa {stage}: {type(exc).__name__}. {reason}")
        return False
