import time
import random
import threading
import json
from pathlib import Path
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError


class RegistrationUnverified(Exception):
    """Registration has no trustworthy confirmation; do not retry submission."""
    pass

# --- IMPORTAÇÕES ---
from .identity import get_random_identity
from .withdrawal import configure_withdrawal
from .deposit import make_deposit

# Configuração da pasta de contas (Salva na raiz do projeto)
BASE_DIR = Path(__file__).resolve().parent
CONTAS_DIR = BASE_DIR.parent / "contas"
CONTAS_FILE = CONTAS_DIR / "registros.json"


# Compartilhamento de identidade entre sites diferentes
_SHARED_IDENTITY_LOCK = threading.Lock()
_SHARED_IDENTITY_EVENTS: dict[str, threading.Event] = {}
_SHARED_IDENTITY_VALUES: dict[str, dict] = {}


# =========================
# Selectors universais
# =========================

SEL = {
    # alguns sites precisam clicar no header pra abrir o modal/página de registro
    "open_register_modal": [
        "#registerClickModal",
        "span#registerClickModal",
        "div[role='button']:has(span#registerClickModal)",
        "div[role='button']:has-text('Registro')",
        "span:has-text('Registro')",
    ],
    "open_login_modal": [
        "#loginClickModal",
        "span#loginClickModal",
        "div[role='button']:has(span#loginClickModal)",
        "div[role='button']:has-text('Login')",
        "span:has-text('Login')",
    ],
    "tab_register": [
        "#registerTabButton",
        ".ui-tab:has-text('Registro')",
        ".ui-tab:has-text('Cadastrar')",
    ],
    "tab_login": [
        "#loginTabButton",
        ".ui-tab:has-text('Login')",
        ".ui-tab:has-text('Entrar')",
    ],
    "account": [
        "input[data-input-name='account']",
        "[data-item-name='account'] input",
    ],
    "userpass": [
        "input[data-input-name='userpass']",
        "[data-item-name='userpass'] input",
    ],
    "confirmPassword": [
        "input[data-input-name='confirmPassword']",
        "[data-item-name='confirmPassword'] input",
    ],
    # realName é opcional
    "realName": [
        "input[data-input-name='realName']",
        "[data-item-name='realName'] input",
    ],
    "submit_register": [
        "#insideRegisterSubmitClick",
        "button.ui-button--primary:has-text('Registro')",
        "button.ui-button--primary:has-text('Cadastrar')",
    ],
    "submit_login": [
        "#insideLoginSubmitClick",
        "button.ui-button--primary:has-text('Login')",
        "button.ui-button--primary:has-text('Entrar')",
    ],
    # Erro / sugestões
    "error_box": [
        "section[class*='_suggestion_']",
        "div[class*='_suggestion_']",
        "section:has(span[class*='_item_'])",
        "div:has(span[class*='_item_'])",
    ],
    "suggestion_items": [
        "span[class*='_item_']",
    ],
}


# =========================
# Helpers
# =========================

def _make_pix_email_from_username(base_username: str) -> str:
    base = (base_username or "").strip().replace(" ", "")
    base = base.split("@")[0]
    if not base:
        base = f"user{random.randint(10000, 99999)}"
    return f"{base}@guytmail.com"

def wait_exit_auth_screen(page: Page, timeout_ms: int = 30000) -> None:
    """
    Espera a tela/modal de Login/Registro SUMIR antes de continuar.
    Funciona por 'sinais' universais: fragment principal some OU inputs/botões somem.
    Lança Exception se estourar timeout.
    """
    start = time.time()
    deadline = start + (timeout_ms / 1000.0)

    # Seletores que normalmente existem somente dentro da tela de login/registro
    auth_selectors = [
        "main.lobby-login-register-fragment",
        "main.loginRegisterFragment",
        ".lobby-login-register-fragment",
        ".loginRegisterFragment",
        "input[data-input-name='account']",
        "input[data-input-name='userpass']",
        "#insideRegisterSubmitClick",
        "#insideLoginSubmitClick",
        "#registerTabButton",
        "#loginTabButton",
    ]

    def any_auth_visible() -> bool:
        for sel in auth_selectors:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0 and loc.is_visible():
                    return True
            except Exception:
                # se estiver detached ou der erro, ignora
                pass
        return False

    # Se nem está visível, já acabou
    if not any_auth_visible():
        return

    # Espera sair
    while time.time() < deadline:
        if not any_auth_visible():
            return
        time.sleep(0.3)

    raise Exception("Timeout: a tela de login/registro não fechou a tempo.")

def ensure_contas_dir():
    """Garante que a pasta contas existe."""
    if not CONTAS_DIR.exists():
        CONTAS_DIR.mkdir(parents=True, exist_ok=True)


def save_account_to_file(login, password, domain):
    """Salva os dados da conta em um arquivo JSON."""
    ensure_contas_dir()

    novo_registro = {
        "login": login,
        "senha": password,
        "dominio": domain,
        "data": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    try:
        if CONTAS_FILE.exists():
            with open(CONTAS_FILE, "r", encoding="utf-8") as f:
                try:
                    registros = json.load(f)
                    if not isinstance(registros, list):
                        registros = []
                except json.JSONDecodeError:
                    registros = []
        else:
            registros = []

        registros.append(novo_registro)

        with open(CONTAS_FILE, "w", encoding="utf-8") as f:
            json.dump(registros, f, ensure_ascii=False, indent=4)

        print(f"[#{time.strftime('%H:%M:%S')}] [+] Conta SALVA com sucesso: {login}")
    except Exception as e:
        print(f"[-] Erro ao salvar conta: {e}")

def load_saved_accounts() -> list[dict]:
    """Carrega contas já salvas no registros.json e remove duplicados por domínio+login."""
    ensure_contas_dir()

    if not CONTAS_FILE.exists():
        return []

    try:
        with open(CONTAS_FILE, "r", encoding="utf-8") as f:
            registros = json.load(f)
    except Exception:
        return []

    if not isinstance(registros, list):
        return []

    unique: dict[tuple[str, str], dict] = {}
    for item in registros:
        if not isinstance(item, dict):
            continue

        login = str(item.get("login") or "").strip()
        senha = str(item.get("senha") or "").strip()
        dominio = str(item.get("dominio") or "").strip()
        data = str(item.get("data") or "").strip()

        if not login or not senha or not dominio:
            continue

        key = (dominio.lower(), login.lower())
        unique[key] = {
            "login": login,
            "senha": senha,
            "dominio": dominio,
            "data": data,
        }

    contas = list(unique.values())
    contas.sort(key=lambda x: ((x.get("data") or ""), (x.get("dominio") or ""), (x.get("login") or "")), reverse=True)
    return contas


def human_sleep(min_seconds=1.0, max_seconds=2.0):
    """Dorme um tempo aleatório para simular humano."""
    time.sleep(random.uniform(min_seconds, max_seconds))


def _first_visible_locator(page: Page, selectors: list[str], timeout=15000):
    last_err = None
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            loc.wait_for(state="visible", timeout=timeout)
            return loc, sel
        except Exception as e:
            last_err = e
    if last_err:
        raise last_err
    raise Exception("Nenhum seletor ficou visível")


def _try_locator(page: Page, selectors: list[str]):
    """Retorna o primeiro locator existente (count>0). Não espera visível."""
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0:
                return loc, sel
        except Exception:
            pass
    return None, None


def _safe_fill(locator, value: str):
    locator.click(force=True)
    try:
        locator.fill(value)
    except Exception:
        try:
            locator.press("Control+A")
            locator.press("Backspace")
        except Exception:
            pass
        locator.page.keyboard.type(value, delay=60)


def _clear_and_type(locator, value: str):
    locator.click(force=True)
    try:
        locator.fill("")
    except Exception:
        try:
            locator.press("Control+A")
            locator.press("Backspace")
        except Exception:
            pass
    try:
        locator.press_sequentially(value, delay=random.randint(50, 100))
    except Exception:
        locator.page.keyboard.type(value, delay=60)


def _click_any(page: Page, selectors: list[str], timeout=8000):
    try:
        loc, _ = _first_visible_locator(page, selectors, timeout=timeout)
        loc.click(force=True)
        return True
    except Exception:
        return False


def _make_email_from_username(base_username: str) -> str:
    base = (base_username or "").strip().replace(" ", "")
    base = base.split("@")[0]  # garante que não duplica @
    domain = random.choice(["gmail.com", "hotmail.com", "icloud.com"])
    return f"{base}@{domain}"


def _replace_email_local_part(email_value: str, local_part: str) -> str:
    local = (local_part or "").strip().replace(" ", "")
    base_email = (email_value or "").strip()

    if "@" in base_email:
        _, domain = base_email.split("@", 1)
        domain = domain.strip() or "gmail.com"
        return f"{local}@{domain}"

    return _make_email_from_username(local)


def _build_identity_payload(raw_identity: dict | None = None) -> dict:
    identity = dict(raw_identity or get_random_identity() or {})

    username_seed = (
        identity.get("base_username")
        or identity.get("username")
        or identity.get("register_username")
        or ""
    )
    username = str(username_seed or "").strip().replace(" ", "")
    if "@" in username:
        username = username.split("@", 1)[0].strip()
    if not username:
        fallback_email = str(identity.get("email_account") or identity.get("pix_email") or "").strip()
        if "@" in fallback_email:
            username = fallback_email.split("@", 1)[0].strip()
    if not username:
        username = f"user{random.randint(10000, 99999)}"

    password = (identity.get("password") or "").strip()
    if not password:
        password = f"Senha{random.randint(1000, 9999)}!"

    real_name = (identity.get("real_name") or "").strip()

    email_account = str(identity.get("email_account") or "").strip()
    if not email_account:
        email_account = _make_email_from_username(username)

    pix_email = str(identity.get("pix_email") or "").strip()
    if not pix_email:
        pix_email = _make_pix_email_from_username(username)

    return {
        "base_username": username,
        "username": username,
        "password": password,
        "real_name": real_name,
        "email_account": email_account,
        "pix_email": pix_email,
    }


def _get_shared_identity_event(shared_key: str) -> threading.Event:
    with _SHARED_IDENTITY_LOCK:
        ev = _SHARED_IDENTITY_EVENTS.get(shared_key)
        if ev is None:
            ev = threading.Event()
            _SHARED_IDENTITY_EVENTS[shared_key] = ev
        return ev


def _wait_shared_identity(shared_key: str, idx: int, timeout_seconds: float = 120.0) -> dict:
    ev = _get_shared_identity_event(shared_key)
    print(f"[#{idx}] ♻️ Aguardando identidade compartilhada do grupo '{shared_key}'...")

    if not ev.wait(timeout_seconds):
        raise Exception(f"Timeout aguardando identidade compartilhada do grupo '{shared_key}'.")

    with _SHARED_IDENTITY_LOCK:
        cached = dict(_SHARED_IDENTITY_VALUES.get(shared_key) or {})

    if not cached:
        raise Exception(f"Grupo compartilhado '{shared_key}' ficou sem identidade final.")

    print(f"[#{idx}] ♻️ Usando identidade compartilhada do grupo '{shared_key}'.")
    return cached


def _publish_shared_identity(shared_key: str, identity_payload: dict):
    final_payload = _build_identity_payload(identity_payload)
    ev = _get_shared_identity_event(shared_key)

    with _SHARED_IDENTITY_LOCK:
        _SHARED_IDENTITY_VALUES[shared_key] = final_payload
        ev.set()


def _get_or_create_shared_identity(shared_key: str, idx: int) -> dict:
    ev = _get_shared_identity_event(shared_key)

    with _SHARED_IDENTITY_LOCK:
        cached = dict(_SHARED_IDENTITY_VALUES.get(shared_key) or {})
        if cached:
            ev.set()
            print(f"[#{idx}] ♻️ Reutilizando identidade compartilhada pré-gerada do grupo '{shared_key}'.")
            return cached

        created = _build_identity_payload()
        _SHARED_IDENTITY_VALUES[shared_key] = created
        ev.set()

    print(f"[#{idx}] 🧾 Identidade compartilhada gerada para '{shared_key}'.")
    return dict(created)


def _get_or_create_plan_identity(plan: dict, idx: int) -> dict:
    cached_plan_identity = dict((plan or {}).get("identity_payload") or {})
    if cached_plan_identity:
        print(f"[#{idx}] ♻️ Reutilizando identidade fixa desta instância.")
        return cached_plan_identity

    shared_key = ((plan or {}).get("shared_identity_key") or "").strip()
    if shared_key:
        identity = _get_or_create_shared_identity(shared_key, idx)
    else:
        identity = _build_identity_payload()
        print(f"[#{idx}] 🧾 Identidade individual gerada.")

    if isinstance(plan, dict):
        plan["identity_payload"] = dict(identity)

    return dict(identity)


def _normalize_identity_after_register(identity_payload: dict, final_account: str, using_email: bool) -> dict:
    final_identity = _build_identity_payload(identity_payload)
    final_value = (final_account or "").strip()

    if final_value:
        final_identity["register_account_value"] = final_value
        if using_email:
            final_identity["register_email_account"] = final_value
            final_identity["register_username"] = (
                final_value.split("@", 1)[0].strip() if "@" in final_value else final_value
            )
        else:
            final_identity["register_username"] = final_value
            final_identity["register_email_account"] = _replace_email_local_part(
                final_identity.get("email_account", ""),
                final_value,
            )

    return final_identity


def _detect_account_supports_username(page: Page) -> bool:
    """
    Decide se ESTE SITE suporta "Conta/Username" no campo account.

    Regra (forte e estável):
      - se o wrapper [data-item-name='account'] tem data-account-type="account" -> suporta conta
      - OU placeholder contém "conta" -> suporta conta
    Se não der pra detectar, assume que suporta conta (pra NÃO cair em email sem necessidade).
    """
    try:
        meta = page.evaluate(
            """() => {
                const input =
                  document.querySelector("input[data-input-name='account']")
                  || document.querySelector("[data-item-name='account'] input");
                if (!input) return { placeholder: "", accountType: "" };

                const placeholder = (input.getAttribute("placeholder") || "").trim();
                const wrap = input.closest("[data-item-name='account']");
                const accountType = wrap ? (wrap.getAttribute("data-account-type") || "") : "";
                return { placeholder, accountType };
            }"""
        ) or {}
    except Exception:
        meta = {}

    placeholder = (meta.get("placeholder") or "").lower()
    account_type = (meta.get("accountType") or "").lower()

    if account_type == "account":
        return True
    if "conta" in placeholder:
        return True

    # se não deu pra detectar nada, NÃO usa email (default seguro)
    if not placeholder and not account_type:
        return True

    return False


# =========================
# Automations
# =========================

def run_saved_account_automation(page: Page, idx: int, instance_plan: dict | None = None):
    print(f"[#{idx}] --- INICIANDO LOGIN DE CONTA SALVA ---")
    logged_user = check_and_fill_login(page, idx, instance_plan=instance_plan)
    if logged_user:
        print(f"[#{idx}] ✅ LOGIN CONCLUÍDO COM SUCESSO!")
    print(f"[#{idx}] --- FIM DO LOGIN DE CONTA SALVA ---")
    return logged_user


def run_automations(page: Page, idx: int, instance_plan: dict | None = None):
    print(f"[#{idx}] --- INICIANDO CICLO DE AUTOMAÇÃO COMPLETO ---")

    created_user = check_and_fill_register(page, idx, instance_plan=instance_plan)

    if created_user:
        print(f"[#{idx}] Passo 1: Registro OK. Iniciando Configuração de Saque...")
        human_sleep(2, 4)

        configure_withdrawal(page, idx, created_user)

        print(f"[#{idx}] Passo 2: Saque OK. Iniciando Fluxo de Depósito e Verificação...")
        human_sleep(2, 3)

        make_deposit(page, idx)

        print(f"[#{idx}] ✅ CICLO COMPLETO CONCLUÍDO COM SUCESSO!")

    print(f"[#{idx}] --- FIM DA AUTOMAÇÃO ---")


def run_registration_only(page: Page, idx: int, instance_plan: dict | None = None):
    """Only registration, never withdrawal or deposit."""
    print(f"[#{idx}] Cadastro: execução iniciada.")
    result = check_and_fill_register(page, idx, instance_plan=instance_plan)
    print(f"[#{idx}] Cadastro confirmado pelo site.")
    return result


def check_and_fill_register(page: Page, idx: int, instance_plan: dict | None = None):
    """
    Universal:
    - abre modal/página de registro se existir botão no header
    - USA EMAIL SOMENTE quando o site NÃO suporta conta/username
    - realName opcional (só preenche se existir)
    - retry sempre reutiliza a mesma identidade base da instância
    - o Pix usa sempre o email fixo derivado do username base
    """
    print(f"[#{idx}] Aguardando formulário de registro...")

    # Passo 0: alguns sites precisam clicar no "Registro" do header para abrir modal/página
    if _click_any(page, SEL["open_register_modal"], timeout=3500):
        time.sleep(0.8)

    # Aba de Registro (se existir dentro do modal)
    _click_any(page, SEL["tab_register"], timeout=4000)

    try:
        account_loc, account_sel = _first_visible_locator(page, SEL["account"], timeout=5000)
        print(f"[#{idx}] Formulário detectado! ({account_sel})")
    except PlaywrightTimeoutError:
        raise Exception("Timeout: Formulário não apareceu em 5s.")
    except Exception as e:
        raise Exception(f"Timeout/erro: Formulário não apareceu. Detalhe: {e}")

    plan = instance_plan if isinstance(instance_plan, dict) else {}
    shared_key = (plan.get("shared_identity_key") or "").strip()
    shared_owner = bool(plan.get("shared_identity_owner"))

    identity = _get_or_create_plan_identity(plan, idx)
    base_user = (identity.get("base_username") or identity.get("username") or "").strip()
    pwd = identity.get("password", "")
    real_name = identity.get("real_name", "")
    fixed_email_account = identity.get("email_account") or _make_email_from_username(base_user)
    pix_email = identity.get("pix_email") or fixed_email_account

    # >>> REGRA NOVA: só usa email quando NÃO suporta conta <<<
    supports_username = _detect_account_supports_username(page)
    using_email = not supports_username

    default_account_value = fixed_email_account if using_email else base_user
    saved_account_value = str(plan.get("register_account_value") or "").strip()
    saved_using_email = plan.get("register_using_email")

    if saved_account_value and saved_using_email in (True, False) and bool(saved_using_email) == using_email:
        account_value = saved_account_value
        print(f"[#{idx}] ♻️ Reutilizando account anterior da instância: {account_value}")
    else:
        account_value = default_account_value

    plan["register_account_value"] = account_value
    plan["register_using_email"] = using_email

    mode_label = "EMAIL" if using_email else "CONTA"
    share_label = "COMPARTILHADO" if shared_key else "INDIVIDUAL"
    print(f"[#{idx}] Account mode: {mode_label} | identidade: {share_label} -> {account_value}")

    pass_loc, _ = _first_visible_locator(page, SEL["userpass"], timeout=15000)
    conf_loc, _ = _first_visible_locator(page, SEL["confirmPassword"], timeout=15000)

    # Preenche campos fixos
    try:
        _safe_fill(pass_loc, pwd)
        human_sleep(0.5, 1.0)

        _safe_fill(conf_loc, pwd)
        human_sleep(0.5, 1.0)

        # realName opcional
        real_loc, _ = _try_locator(page, SEL["realName"])
        if real_loc is not None:
            try:
                _safe_fill(real_loc, real_name)
                human_sleep(0.5, 1.0)
            except Exception:
                pass

    except Exception as e:
        print(f"[#{idx}] Erro ao preencher campos fixos: {e}")
        raise e

    submit_loc, _ = _first_visible_locator(page, SEL["submit_register"], timeout=15000)

    # Fail before submitting anything when the provider has no verified outcome rule.
    import os
    confirmation_selector = os.environ.get("NEXORA_REGISTRATION_SUCCESS_SELECTOR", "").strip()
    if not confirmation_selector:
        raise RegistrationUnverified(
            "CADASTRO NÃO EXECUTADO: falta o indicador de conta autenticada "
            "NEXORA_REGISTRATION_SUCCESS_SELECTOR do site autorizado. "
            "Nenhum formulário foi enviado."
        )
    max_retries = 6
    success = False
    final_account = None
    final_register_username = str(plan.get("register_username") or "").strip()

    for attempt in range(max_retries):
        print(f"[#{idx}] Tentativa de registro {attempt+1}/{max_retries} com account: {account_value}")

        try:
            plan["register_account_value"] = account_value
            plan["register_using_email"] = using_email

            _clear_and_type(account_loc, account_value)
            human_sleep()

            if not submit_loc.is_visible():
                raise Exception("Botão de registro sumiu da tela.")

            print(f"[#{idx}] Clicando no botão de registro...")
            submit_loc.click(force=True)

            # espera processar
            time.sleep(4)

            # Caso clássico: username/email indisponível (sugestões)
            error_loc, _ = _try_locator(page, SEL["error_box"])
            if error_loc is not None:
                try:
                    if error_loc.is_visible():
                        suggestions = []
                        try:
                            suggestions = page.locator(SEL["suggestion_items"][0]).all_inner_texts()
                        except Exception:
                            suggestions = []

                        if suggestions:
                            sug = suggestions[0].strip()
                            if using_email:
                                if "@" in sug:
                                    account_value = sug
                                    final_register_username = sug.split("@", 1)[0].strip()
                                else:
                                    final_register_username = sug
                                    account_value = _replace_email_local_part(fixed_email_account or account_value, final_register_username)
                            else:
                                account_value = sug
                                final_register_username = sug

                            plan["register_account_value"] = account_value
                            plan["register_username"] = final_register_username
                            print(f"[#{idx}] 💡 Usando sugestão do site: {account_value}")
                            continue
                        else:
                            next_register_username = (final_register_username or base_user) + str(random.randint(10, 99))
                            account_value = (
                                _replace_email_local_part(fixed_email_account or account_value, next_register_username)
                                if using_email else next_register_username
                            )
                            final_register_username = next_register_username
                            plan["register_account_value"] = account_value
                            plan["register_username"] = final_register_username
                            print(f"[#{idx}] Gerando novo sufixo: {account_value}")
                            continue
                except Exception:
                    pass

            # No HTTP 200, modal close, or click can prove a completed account.
            # Use a specific, unambiguous authenticated-account marker.
            try:
                page.locator(confirmation_selector).first.wait_for(
                    state="visible", timeout=30000
                )
            except Exception as exc:
                raise RegistrationUnverified(
                    "CADASTRO PENDENTE/NÃO CONFIRMADO: indicador do site ausente. "
                    "Verifique o resultado e conclua as verificações oficiais. "
                    "Nenhuma conta foi salva como concluída."
                ) from exc
            print(f"[#{idx}] Indicador de conclusão do provedor detectado.")

            if using_email:
                final_register_username = account_value.split("@", 1)[0].strip() if "@" in account_value else account_value
            else:
                final_register_username = account_value

            final_identity = _normalize_identity_after_register(identity, account_value, using_email)
            final_identity["register_username"] = final_register_username
            final_identity["register_account_value"] = account_value

            plan["identity_payload"] = dict(final_identity)
            plan["register_account_value"] = account_value
            plan["register_username"] = final_register_username
            plan["register_using_email"] = using_email

            domain = (
                str(plan.get("site_key") or "").strip()
                or str(plan.get("url") or "").strip()
                or "desconhecido"
            )

            save_account_to_file(account_value, pwd, domain)
            success = True
            final_account = account_value
            break
        
        except Exception as e:
            print(f"[#{idx}] Erro na tentativa {attempt}: {e}")
            raise e

    if not success:
        raise Exception("Falha: Todas as tentativas de account falharam.")

    return {
        "account_value": final_account,
        "username": base_user,
        "base_username": base_user,
        "registered_username": final_register_username,
        "email_account": fixed_email_account,
        "pix_email": pix_email,
        "password": identity.get("password", ""),
        "real_name": identity.get("real_name", ""),
        "using_email": using_email,
        "shared_identity_key": shared_key,
        "shared_identity_owner": shared_owner,
        "should_autochave": bool(plan.get("should_autochave", True)),
    }

def check_and_fill_login(page: Page, idx: int, instance_plan: dict | None = None):
    """
    Faz login com contas já criadas usando login e senha salvos no registros.json.
    Reaproveita o mesmo padrão universal do registro, mas entrando pela aba/botão de login.
    """
    print(f"[#{idx}] Aguardando formulário de login...")

    plan = instance_plan if isinstance(instance_plan, dict) else {}
    saved = dict(plan.get("saved_account") or {})

    login_value = str(saved.get("login") or "").strip()
    password_value = str(saved.get("senha") or "").strip()

    if not login_value or not password_value:
        raise Exception("Conta salva inválida: login/senha não encontrados no instance_plan.")

    opened = _click_any(page, SEL["open_login_modal"], timeout=3500)
    if not opened:
        _click_any(page, SEL["open_register_modal"], timeout=2500)
    time.sleep(0.8)

    _click_any(page, SEL["tab_login"], timeout=4000)

    try:
        account_loc, account_sel = _first_visible_locator(page, SEL["account"], timeout=5000)
        print(f"[#{idx}] Formulário de login detectado! ({account_sel})")
    except PlaywrightTimeoutError:
        raise Exception("Timeout: Formulário de login não apareceu em 5s.")
    except Exception as e:
        raise Exception(f"Timeout/erro: Formulário de login não apareceu. Detalhe: {e}")

    pass_loc, _ = _first_visible_locator(page, SEL["userpass"], timeout=15000)
    submit_loc, _ = _first_visible_locator(page, SEL["submit_login"], timeout=15000)

    try:
        _clear_and_type(account_loc, login_value)
        human_sleep(0.5, 1.0)

        _clear_and_type(pass_loc, password_value)
        human_sleep(0.5, 1.0)

        if not submit_loc.is_visible():
            raise Exception("Botão de login sumiu da tela.")

        print(f"[#{idx}] Clicando no botão de login...")
        submit_loc.click(force=True)
        time.sleep(4)

        print(f"[#{idx}] ✅ Login enviado. Aguardando sair da tela de autenticação...")
        wait_exit_auth_screen(page, timeout_ms=30000)
    except Exception as e:
        print(f"[#{idx}] ❌ Erro ao logar com conta salva '{login_value}': {e}")
        raise e

    return {
        "account_value": login_value,
        "login": login_value,
        "password": password_value,
        "dominio": str(saved.get("dominio") or "").strip(),
        "data": str(saved.get("data") or "").strip(),
    }
