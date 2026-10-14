import time
import random
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError
import json
# Importa a função de abrir o jogo do arquivo games.py
from .games import search_and_open_fortune_snake
from .utils import load_settings
from Settings.Area_Pix.bank_utils import get_default_bank, send_pix_code_to_bank

AREA_PIX_SCOPE = "mae"


def _scope_key(name: str) -> str:
    return f"{AREA_PIX_SCOPE}_{name}"


def _get_autopix_runtime_settings() -> tuple[bool, str]:
    settings = load_settings() or {}
    autopix_enabled = bool(settings.get(_scope_key("autopix_enabled"), settings.get("autopix_enabled", False)))
    autopix_bank = (settings.get(_scope_key("autopix_bank")) or settings.get("autopix_bank") or get_default_bank()).strip() or get_default_bank()
    return autopix_enabled, autopix_bank

def _get_deposit_amount_runtime_setting() -> str:
    settings = load_settings() or {}
    amount = str(
        settings.get(_scope_key("deposit_amount"), settings.get("deposit_amount", "10")) or "10"
    ).strip().replace(",", ".")

    if not amount:
        return "10"

    return amount


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CONVITE_PATH = PROJECT_ROOT / "convite.json"


def human_sleep(min_seconds=1.0, max_seconds=2.0):
    time.sleep(random.uniform(min_seconds, max_seconds))

def _save_invite_link_json(invite_link: str):
    CONVITE_PATH.parent.mkdir(parents=True, exist_ok=True)

    links = []

    if CONVITE_PATH.exists():
        try:
            raw = CONVITE_PATH.read_text(encoding="utf-8").strip()
            if raw:
                data = json.loads(raw)
                if isinstance(data, dict):
                    data = data.get("links", [])
                if isinstance(data, list):
                    links = [str(item).strip() for item in data if str(item).strip()]
        except Exception:
            links = []

    if invite_link not in links:
        links.append(invite_link)

    CONVITE_PATH.write_text(
        json.dumps(links, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

# -------------------------
# Helpers universais
# -------------------------

def _wait_any_visible(page: Page, selectors: list[str], timeout=20000) -> str:
    end = time.time() + (timeout / 1000.0)
    while time.time() < end:
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0 and loc.is_visible():
                    return sel
            except Exception:
                pass
        time.sleep(0.25)
    raise Exception(f"Timeout: nenhum desses elementos apareceu: {selectors}")


def _click_candidates_until(page: Page, selectors: list[str], ok_check_fn, idx: int, timeout=15000) -> bool:
    """
    Tenta clicar em vários candidatos (inclusive duplicados) e só considera sucesso
    quando ok_check_fn() retornar True.
    """
    end = time.time() + (timeout / 1000.0)

    while time.time() < end:
        for sel in selectors:
            try:
                locs = page.locator(sel)
                cnt = locs.count()
                if cnt <= 0:
                    continue

                for i in range(min(cnt, 10)):
                    el = locs.nth(i)

                    try:
                        if not el.is_visible():
                            continue
                    except Exception:
                        continue

                    try:
                        el.scroll_into_view_if_needed(timeout=2000)
                    except Exception:
                        pass

                    # 1) PRIORIDADE: JS click (evita “clicar no lugar errado” por coordenada)
                    clicked = False
                    try:
                        el.evaluate("node => node.click()")
                        clicked = True
                    except Exception:
                        pass

                    # 2) fallback: click normal (sem force) + force
                    if not clicked:
                        try:
                            el.click(timeout=2500)
                            clicked = True
                        except Exception:
                            try:
                                el.click(force=True, timeout=2500)
                                clicked = True
                            except Exception:
                                clicked = False

                    if not clicked:
                        continue

                    # dá tempo do site reagir
                    time.sleep(0.8)

                    # fecha popups que podem aparecer após clique
                    try:
                        close_popups_deposit(page, idx)
                    except Exception:
                        pass

                    # valida: abriu a tela certa?
                    try:
                        if ok_check_fn():
                            return True
                    except Exception:
                        pass

            except Exception:
                continue

        time.sleep(0.25)

    return False


def _deposit_screen_is_open(page: Page) -> bool:
    """
    Confirma que a tela/modal de depósito abriu:
    - apareceu input de amount
    - ou botão de submit de depósito
    """
    markers = [
        'section[data-item-name="amount"] input.ui-input__input',
        'section[data-item-name="amount"] input',
        'input[data-input-name="amount"]',
        '#depositSubmitClick',
        'button#depositSubmitClick',
    ]
    for sel in markers:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible():
                return True
        except Exception:
            pass
    return False


def click_deposit_universal(page: Page, idx: int, timeout=15000) -> None:
    """
    Universal para Depósito:
    - tenta primeiro os botões do tipo NAV/ATALHO (class *_navItem_* / data-sensors-click / sem role=tab)
    - evita clicar em TABBAR/role=tab quando isso não abre a tela
    - só retorna quando detectar que a tela de depósito abriu
    """
    # Seletores priorizados (do mais “nav” para o mais “genérico”)
    selectors = [
        # 1) NAVITEM com id (mais comum e mais certo)
        "div#depositClick[class*='_navItem_']:not([role='tab'])",
        "div#depositClick[data-sensors-click='true']:not([role='tab'])",
        "[id='depositClick'][class*='_navItem_']:not([role='tab'])",
        "[id='depositClick'][data-sensors-click='true']:not([role='tab'])",

        # 2) Qualquer depositClick que NÃO seja tab/barra
        "#depositClick:not([role='tab']):not(.ui-tabbar-item)",
        "[id='depositClick']:not([role='tab']):not(.ui-tabbar-item)",

        # 3) Fallback por texto (Depósito / Deposito) dentro de NAVITEMs
        "div[class*='_navItem_']:has(p:text-is('Depósito'))",
        "div[class*='_navItem_']:has(span:text-is('Depósito'))",
        "div[class*='_navItem_']:has(p:text-is('Deposito'))",
        "div[class*='_navItem_']:has(span:text-is('Deposito'))",

        # 4) Alguns sites chamam de Recarga/Recarregar/Recharge/Deposit
        "div[class*='_navItem_']:has(:is(p,span):has-text('Recarga'))",
        "div[class*='_navItem_']:has(:is(p,span):has-text('Recarregar'))",
        "div[class*='_navItem_']:has(:is(p,span):has-text('Recharge'))",
        "div[class*='_navItem_']:has(:is(p,span):has-text('Deposit'))",

        # 5) Último recurso: TAB (se realmente for o único disponível)
        "div#depositClick[role='tab']",
        "[role='tab']#depositClick",
        "div[role='tab']:has-text('Depósito')",
        "div[role='tab']:has-text('Deposit')",
    ]

    # se já estiver aberto, não clica de novo
    if _deposit_screen_is_open(page):
        return

    ok = _click_candidates_until(
        page,
        selectors=selectors,
        ok_check_fn=lambda: _deposit_screen_is_open(page),
        idx=idx,
        timeout=timeout
    )

    if not ok:
        raise Exception("Não consegui abrir a tela de Depósito (clicou mas não abriu / nenhum botão válido).")


def safe_goto(page: Page, url: str, idx: int):
    """Tenta navegar com segurança. Se falhar, apenas recarrega."""
    try:
        if page.url == url:
            page.reload(wait_until="domcontentloaded")
        else:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        print(f"[#{idx}] ⚠️ Aviso de Navegação: {e}. Tentando apenas atualizar a página...")
        time.sleep(2)
        try:
            page.reload(wait_until="domcontentloaded")
        except Exception:
            pass


# -------------------------
# Popups (versão leve, suficiente pro depósito)
# -------------------------

def _remove_notice_user_tip(page: Page) -> bool:
    """
    Fecha banner: div.notic-user-tip-warp (sempre cancela; se falhar remove via JS)
    """
    wrap_sel = "div.notic-user-tip-warp"
    cancel_sel = "div.notic-user-tip-warp span.web-push-tip-cancel"

    try:
        wrap = page.locator(wrap_sel).first
        if wrap.count() <= 0 or not wrap.is_visible():
            return False
    except Exception:
        return False

    # tenta cancelar
    try:
        btn = page.locator(cancel_sel).first
        if btn.count() > 0 and btn.is_visible():
            try:
                btn.click(force=True, timeout=2000)
            except Exception:
                btn.evaluate("node => node.click()")
            time.sleep(0.3)
            return True
    except Exception:
        pass

    # fallback remove
    try:
        page.evaluate("""() => { const el = document.querySelector('div.notic-user-tip-warp'); if (el) el.remove(); }""")
        time.sleep(0.2)
        return True
    except Exception:
        return False


def close_popups_deposit(page: Page, idx: int):
    """
    Fecha popups que atrapalham cliques do depósito (universal).
    """
    close_selectors = [
        "i.ui-dialog-close-box__icon:visible",
        ".ui-dialog-close-box:visible",
        "button.ui-dialog__confirm:visible",
        "button:has(span.ui-button__text:text-is('Confirmar')):visible",
    ]

    for _ in range(10):
        found = False

        # banner web-push
        try:
            if _remove_notice_user_tip(page):
                found = True
        except Exception:
            pass

        # X / confirm genéricos
        for sel in close_selectors:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0 and loc.is_visible():
                    try:
                        loc.click(force=True, timeout=2000)
                    except Exception:
                        try:
                            loc.evaluate("node => node.click()")
                        except Exception:
                            pass
                    time.sleep(0.5)
                    found = True
                    break
            except Exception:
                continue

        # overlay
        try:
            if page.is_visible(".ui-overlay"):
                try:
                    page.mouse.click(10, 10)
                    time.sleep(0.4)
                    found = True
                except Exception:
                    pass
        except Exception:
            pass

        if not found:
            break


# -------------------------
# Saldo / depósito
# -------------------------

def get_balance(page: Page, idx: int):
    try:
        candidates = [
            "._currency-count_1whhp_112",
            "[class*='_currency-count_']",
            "[class*='currency-count']",
            ".currency-count",
            ".user-balance",
        ]
        sel = _wait_any_visible(page, candidates, timeout=10000)
        balance_raw = page.locator(sel).first.inner_text()
        balance_cleaned = (
            balance_raw.replace("R$", "")
            .replace(" ", "")
            .replace(".", "")
            .replace(",", ".")
            .strip()
        )
        return float(balance_cleaned)
    except Exception:
        return 0.0


def _fill_amount(page: Page, idx: int, amount: str = "10"):
    sel_input_candidates = [
        'section[data-item-name="amount"] input.ui-input__input',
        'section[data-item-name="amount"] input',
        'input[data-input-name="amount"]',
        'input[placeholder*="valor"]',
    ]
    sel = _wait_any_visible(page, sel_input_candidates, timeout=20000)
    input_field = page.locator(sel).first
    amount = str(amount).strip()

    for attempt in range(3):
        input_field.click(force=True)
        time.sleep(0.2)

        try:
            input_field.fill("")
        except Exception:
            try:
                page.keyboard.press("Control+A")
                page.keyboard.press("Backspace")
            except Exception:
                pass

        time.sleep(0.2)

        typed = False

        try:
            input_field.press_sequentially(amount, delay=120)
            typed = True
        except Exception:
            pass

        if not typed:
            try:
                input_field.fill(amount)
                typed = True
            except Exception:
                pass

        try:
            input_field.evaluate(
                """(el, value) => {
                    const setter = Object.getOwnPropertyDescriptor(
                        HTMLInputElement.prototype,
                        "value"
                    )?.set;

                    if (setter) {
                        setter.call(el, value);
                    } else {
                        el.value = value;
                    }

                    el.dispatchEvent(new Event("input", { bubbles: true }));
                    el.dispatchEvent(new Event("change", { bubbles: true }));
                    el.dispatchEvent(new Event("blur", { bubbles: true }));
                }""",
                amount
            )
        except Exception:
            pass

        time.sleep(0.3)

        current_value = ""
        try:
            current_value = (input_field.input_value() or "").strip()
        except Exception:
            try:
                current_value = (input_field.evaluate("el => (el.value || '').trim()") or "").strip()
            except Exception:
                current_value = ""

        if current_value == amount:
            return

    raise Exception(f"Não consegui manter o valor '{amount}' no campo de depósito.")

def _submit_deposit(page: Page, idx: int):
    submit_candidates = [
        "#depositSubmitClick",
        "button#depositSubmitClick",
        "button:has-text('Depositar')",
        "button:has-text('Depósito')",
        "button:has(span.ui-button__text:text-is('Confirmar'))",
        "button:has-text('Confirmar')",
    ]
    ok = _click_candidates_until(
        page,
        selectors=submit_candidates,
        ok_check_fn=lambda: True,  # aqui só precisa clicar mesmo
        idx=idx,
        timeout=15000
    )
    if not ok:
        raise Exception("Não consegui clicar no botão de enviar depósito (submit).")


def _install_copy_capture(page: Page):
    script = r"""() => {
        try {
            if (window.__pixCopyCaptureInstalled) return;
            window.__pixCopyCaptureInstalled = true;
            window.__pixCapturedText = '';

            const remember = (value) => {
                try {
                    const txt = String(value || '').trim();
                    if (txt) window.__pixCapturedText = txt;
                } catch (e) {}
            };

            document.addEventListener('copy', (e) => {
                try {
                    const txt = e && e.clipboardData ? (e.clipboardData.getData('text/plain') || e.clipboardData.getData('text')) : '';
                    remember(txt);
                } catch (err) {}
            }, true);

            const originalWriteText = navigator.clipboard && navigator.clipboard.writeText ? navigator.clipboard.writeText.bind(navigator.clipboard) : null;
            if (navigator.clipboard && originalWriteText) {
                navigator.clipboard.writeText = async (value) => {
                    remember(value);
                    return Promise.resolve();
                };
            }

            const originalExec = document.execCommand ? document.execCommand.bind(document) : null;
            if (originalExec) {
                document.execCommand = function(commandId, showUI, value) {
                    const cmd = String(commandId || '').toLowerCase();
                    if (cmd === 'copy') {
                        try {
                            const selected = String(window.getSelection ? window.getSelection().toString() : '').trim();
                            remember(selected);
                        } catch (e) {}
                    }
                    if (cmd === 'inserttext') {
                        remember(value);
                    }
                    return true;
                };
            }
        } catch (e) {}
    }"""
    try:
        page.evaluate(script)
    except Exception:
        pass


def _read_captured_pix_code(page: Page) -> str:
    try:
        txt = page.evaluate("() => String(window.__pixCapturedText || '').trim()")
        return txt.strip() if isinstance(txt, str) else ''
    except Exception:
        return ''


def _copy_pix_code(page: Page, idx: int, timeout: int = 20000) -> str:
    copy_candidates = [
        "button:has-text('Copiar código QR')",
        "button:has(span:has-text('Copiar código QR'))",
        "span.copy button",
        "span[class*='_copy_'].copy button",
        "button.ui-button--primary:has-text('Copiar código QR')",
        "p:has-text('Copiar código QR')",
        "button:has(p:has-text('Copiar código QR'))",
        "._actions_93lqh_93 button",
    ]

    _install_copy_capture(page)
    end = time.time() + (timeout / 1000.0)
    last_error = None
    while time.time() < end:
        for sel in copy_candidates:
            try:
                locs = page.locator(sel)
                count = locs.count()
                if count <= 0:
                    continue
                for i in range(min(count, 10)):
                    btn = locs.nth(i)
                    try:
                        if not btn.is_visible():
                            continue
                    except Exception:
                        continue
                    try:
                        btn.scroll_into_view_if_needed(timeout=1500)
                    except Exception:
                        pass

                    try:
                        page.evaluate("() => { window.__pixCapturedText = ''; }")
                    except Exception:
                        pass

                    clicked = False
                    try:
                        btn.evaluate("node => node.click()")
                        clicked = True
                    except Exception:
                        pass
                    if not clicked:
                        try:
                            btn.click(force=True, timeout=3000)
                            clicked = True
                        except Exception as e:
                            last_error = e
                            continue

                    limit = time.time() + 2.5
                    while time.time() < limit:
                        code = _read_captured_pix_code(page)
                        if code:
                            print(f"[#{idx}] ✅ Código Pix capturado sem usar clipboard do navegador.")
                            return code
                        time.sleep(0.15)
            except Exception as e:
                last_error = e
            time.sleep(0.2)
    raise Exception(f"Não consegui capturar o código QR. Último erro: {last_error}")


def _handle_autopix_after_submit(page: Page, idx: int) -> bool:
    autopix_enabled, autopix_bank = _get_autopix_runtime_settings()
    pix_code = _copy_pix_code(page, idx, timeout=30000)

    if autopix_enabled:
        print(f"[#{idx}] [AutoPIX/{AREA_PIX_SCOPE}] Enfileirando QRCode para {autopix_bank} e aguardando confirmação...")

        ok = send_pix_code_to_bank(
            autopix_bank,
            pix_code,
            log=lambda m: print(f"[#{idx}] {m}"),
            scope=AREA_PIX_SCOPE,
        )

        if ok:
            print(f"[#{idx}] [AutoPIX/{AREA_PIX_SCOPE}] QRCode confirmado com mensagem 'Pix enviado'.")
            return True

        print(f"[#{idx}] [AutoPIX/{AREA_PIX_SCOPE}] Última mensagem abaixo do QR não confirmou 'Pix enviado'.")
        return False

    print(f"[#{idx}] ℹ️ AutoPIX ({AREA_PIX_SCOPE}) desativado; QRCode capturado, mas não enviado.")
    return False

def make_deposit(page: Page, idx: int):
    """Loop de depósito e verificação sem encerramento brutal."""
    print(f"[#{idx}] --- INICIANDO ETAPA DE DEPÓSITO ---")

    current_url = page.url
    base_url = "/".join(current_url.split("/")[:3])
    target_url = f"{base_url}/home/mine"

    autopix_enabled, _ = _get_autopix_runtime_settings()
    deposit_amount = _get_deposit_amount_runtime_setting()
    autopix_locked_to_balance_only = False

    while True:
        try:
            if not autopix_locked_to_balance_only:
                # 1) DEPÓSITO INICIAL
                print(f"[#{idx}] Indo para área de depósito...")
                safe_goto(page, target_url, idx)
                close_popups_deposit(page, idx)

                # ✅ CORRIGIDO: clique universal + validação (não clica “no lugar errado”)
                click_deposit_universal(page, idx, timeout=15000)
                time.sleep(2)
                close_popups_deposit(page, idx)

                # garante que a tela está aberta
                _wait_any_visible(
                    page,
                    [
                        'section[data-item-name="amount"] input',
                        '#depositSubmitClick',
                        'button#depositSubmitClick',
                    ],
                    timeout=20000
                )

                print(f"[#{idx}] Preenchendo valor do depósito: {deposit_amount}")
                _fill_amount(page, idx, amount=deposit_amount)
                time.sleep(4.0)

                _submit_deposit(page, idx)
                print(f"[#{idx}] ✅ DEPÓSITO SOLICITADO!")

                try:
                    pix_sent_ok = _handle_autopix_after_submit(page, idx)

                    if autopix_enabled and pix_sent_ok:
                        autopix_locked_to_balance_only = True
                        print(f"[#{idx}] [AutoPIX] Mensagem 'Pix enviado' confirmada. Esta instância não vai mais gerar novos depósitos; agora só verifica saldo.")
                    elif autopix_enabled:
                        print(f"[#{idx}] [AutoPIX] Pix ainda não confirmado no WhatsApp. Esta instância poderá tentar novamente no próximo ciclo.")
                except Exception as e_pix_auto:
                    print(f"[#{idx}] ⚠️ Aviso: falha ao copiar/enviar o código Pix: {e_pix_auto}")

                # 2) ESPERA DE 40 segundos
                print(f"[#{idx}] Aguardando 40 Segundos (compensação)...")
                time.sleep(40)
            else:
                print(f"[#{idx}] [AutoPIX] Modo somente verificação de saldo ativo. Nenhum novo depósito será gerado nesta instância.")
                time.sleep(15)

            # 3) VERIFICAÇÃO DE SALDO
            print(f"[#{idx}] Verificando saldo...")
            safe_goto(page, target_url, idx)
            close_popups_deposit(page, idx)

            saldo = get_balance(page, idx)
            print(f"[#{idx}] Saldo lido: R$ {saldo:.2f}")

            if saldo < 4.0:
                if autopix_locked_to_balance_only:
                    print(f"[#{idx}] [AutoPIX] Saldo ainda insuficiente. Mantendo esta instância apenas em monitoramento de saldo...")
                    time.sleep(15)
                    continue
                print(f"[#{idx}] Saldo insuficiente. Reiniciando ciclo de depósito no mesmo navegador...")
                continue
            else:
                # 4) EXTRAIR E SALVAR O ID DE CONVITE E CHAVE PIX
                print(f"[#{idx}] Saldo OK! Copiando ID do Perfil e Username...")

                try:
                    sel_id_candidates = [
                        'p:has(span:text-is("ID:")) span:nth-child(2)',
                        'p:has-text("ID:") span:nth-child(2)',
                        'span:has-text("ID:") >> xpath=following-sibling::span[1]',
                    ]
                    sel_id = _wait_any_visible(page, sel_id_candidates, timeout=10000)
                    user_id = page.locator(sel_id).first.inner_text().strip()

                    domain = urlparse(page.url).netloc
                    if not domain.startswith("www."):
                        domain = "www." + domain

                    invite_link = f"https://{domain}/?id={user_id}"

                    _save_invite_link_json(invite_link)

                    print(f"[#{idx}] 🔗 Link salvo com sucesso no convite.json: {invite_link}")
                    
                    # o código Pix real já é copiado logo após o submit do depósito

                except Exception as e:
                    print(f"[#{idx}] ⚠️ Aviso: Não foi possível copiar/salvar o ID: {e}")

                # 5) ABRIR O JOGO
                print(f"[#{idx}] Abrindo jogo...")
                search_and_open_fortune_snake(page, idx)
                break

        except Exception as e:
            print(f"[#{idx}] ⚠️ Ocorreu um erro, mas mantendo navegador aberto: {e}")
            print(f"[#{idx}] Tentando recuperar em 10 segundos...")
            time.sleep(10)
            # loop continua tentando novamente

