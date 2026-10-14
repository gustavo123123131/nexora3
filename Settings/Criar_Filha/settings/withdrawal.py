import time
import random
import json
import re
import threading
import urllib.request
import urllib.error
from pathlib import Path
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError
from Settings.Criar_Filha.settings.utils import load_settings
from Settings.Area_Pix.bank_utils import get_default_bank, send_autochave_to_bank, load_area_pix_settings

AREA_PIX_SCOPE = "filha"

def _scope_key(name: str) -> str:
    return f"{AREA_PIX_SCOPE}_{name}"

WITHDRAW_PIN = "123455"

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CPF_JSON_PATH = PROJECT_ROOT / "cpf.json"

SMSRUSH_BASE_URL = "https://api.smsrush.com.br"
SMSRUSH_DEFAULT_EMAIL = ""
SMSRUSH_DEFAULT_PASSWORD = ""

SMSRUSH_COUNTRY_ID = 73
SMSRUSH_SERVICE_ID = 88
SMSRUSH_SERVER_ID = 1
SMSRUSH_QUANTITY = 1

SMSRUSH_HTTP_TIMEOUT = 30.0
SMSRUSH_SMS_TIMEOUT = 60.0
SMSRUSH_STATUS_POLL_INTERVAL = 2.0
PHONE_VERIFY_MAX_ATTEMPTS = 10

_SHARED_PHONE_LOCK = threading.Lock()
_SHARED_PHONE_STATES = {}
_SHARED_PHONE_MUTEXES = {}

_CPF_FILE_LOCK = threading.Lock()
_RESERVED_CPFS = set()

_SHARED_CPF_LOCK = threading.Lock()
_SHARED_CPF_VALUES: dict[str, dict] = {}

_SHARED_AUTOCHAVE_LOCK = threading.Lock()
_SHARED_AUTOCHAVE_EVENTS: dict[str, threading.Event] = {}
_SHARED_AUTOCHAVE_RESULTS: dict[str, dict] = {}

def verify_phone_now(page: Page, idx: int, shared_key: str = "") -> bool:
    return _verify_phone_with_smsrush(page, idx, shared_key=shared_key, max_attempts=PHONE_VERIFY_MAX_ATTEMPTS)

def _get_shared_autochave_event(shared_key: str) -> threading.Event:
    with _SHARED_AUTOCHAVE_LOCK:
        ev = _SHARED_AUTOCHAVE_EVENTS.get(shared_key)
        if ev is None:
            ev = threading.Event()
            _SHARED_AUTOCHAVE_EVENTS[shared_key] = ev
        return ev


def _publish_shared_autochave_success(shared_key: str, email_pix: str):
    if not shared_key:
        return

    ev = _get_shared_autochave_event(shared_key)
    with _SHARED_AUTOCHAVE_LOCK:
        _SHARED_AUTOCHAVE_RESULTS[shared_key] = {
            "ok": True,
            "email_pix": str(email_pix or "").strip(),
            "updated_at": time.time(),
        }
        ev.set()


def _wait_shared_autochave_success(shared_key: str, idx: int, timeout_seconds: float = 900.0) -> dict:
    if not shared_key:
        return {}

    ev = _get_shared_autochave_event(shared_key)
    print(f"[#{idx}] [AutoChave/{AREA_PIX_SCOPE}] Aguardando TRUE da conta principal do grupo '{shared_key}'...")

    if not ev.wait(timeout_seconds):
        raise Exception(f"Timeout aguardando o AutoChave TRUE da conta principal do grupo '{shared_key}'.")

    with _SHARED_AUTOCHAVE_LOCK:
        payload = dict(_SHARED_AUTOCHAVE_RESULTS.get(shared_key) or {})

    if not payload.get("ok"):
        raise Exception(f"O grupo '{shared_key}' foi liberado sem TRUE do AutoChave.")

    print(f"[#{idx}] [AutoChave/{AREA_PIX_SCOPE}] Conta principal do grupo '{shared_key}' já retornou TRUE.")
    return payload

# -------------------------
# helpers cpf
# -------------------------


def _normalize_cpf(value) -> str:
    digits = "".join(c for c in str(value or "") if c.isdigit())
    return digits if len(digits) == 11 else ""


def _load_cpf_pool() -> list[str]:
    if not CPF_JSON_PATH.exists():
        raise FileNotFoundError(f"Arquivo de CPF não encontrado: {CPF_JSON_PATH}")

    with open(CPF_JSON_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    raw_items = []
    if isinstance(data, list):
        raw_items = data
    elif isinstance(data, dict):
        raw_items = data.get("cpfs", [])

    cpfs = []
    for item in raw_items:
        if isinstance(item, dict):
            cpf = _normalize_cpf(item.get("cpf"))
        else:
            cpf = _normalize_cpf(item)

        if cpf:
            cpfs.append(cpf)

    if not cpfs:
        raise Exception(f"O arquivo {CPF_JSON_PATH.name} está vazio ou sem CPFs válidos.")

    return cpfs


def _write_cpf_pool(cpfs: list[str]) -> None:
    CPF_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CPF_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(cpfs, f, ensure_ascii=False, indent=4)


def _reserve_cpf_from_file() -> str:
    with _CPF_FILE_LOCK:
        cpfs = _load_cpf_pool()

        for cpf in cpfs:
            if cpf not in _RESERVED_CPFS:
                _RESERVED_CPFS.add(cpf)
                return cpf

    raise Exception("Nenhum CPF livre disponível no cpf.json.")


def _release_reserved_cpf(cpf: str) -> None:
    normalized = _normalize_cpf(cpf)
    if not normalized:
        return

    with _CPF_FILE_LOCK:
        _RESERVED_CPFS.discard(normalized)


def _remove_used_cpf_from_file(cpf: str) -> None:
    normalized = _normalize_cpf(cpf)
    if not normalized:
        return

    with _CPF_FILE_LOCK:
        try:
            cpfs = _load_cpf_pool()
        except Exception:
            cpfs = []

        new_cpfs = []
        removed = False

        for current in cpfs:
            if not removed and current == normalized:
                removed = True
                continue
            new_cpfs.append(current)

        _write_cpf_pool(new_cpfs)
        _RESERVED_CPFS.discard(normalized)


def _get_or_create_shared_cpf(shared_key: str, idx: int, use_generated_cpf: bool) -> dict:
    with _SHARED_CPF_LOCK:
        cached = dict(_SHARED_CPF_VALUES.get(shared_key) or {})
        if cached:
            print(f"[#{idx}] ♻️ Reutilizando CPF compartilhado do grupo '{shared_key}': {cached.get('cpf')}")
            return cached

        if use_generated_cpf:
            cpf_value = generate_cpf()
            payload = {
                "cpf": cpf_value,
                "from_file": False,
                "consumed": False,
            }
            print(f"[#{idx}] 🧾 CPF compartilhado gerado para '{shared_key}': {cpf_value}")
        else:
            cpf_value = _reserve_cpf_from_file()
            payload = {
                "cpf": cpf_value,
                "from_file": True,
                "consumed": False,
            }
            print(f"[#{idx}] 🧾 CPF compartilhado reservado do cpf.json para '{shared_key}': {cpf_value}")

        _SHARED_CPF_VALUES[shared_key] = dict(payload)
        return dict(payload)


def _mark_shared_cpf_consumed(shared_key: str, cpf: str) -> bool:
    normalized = _normalize_cpf(cpf)
    if not shared_key or not normalized:
        return False

    with _SHARED_CPF_LOCK:
        cached = dict(_SHARED_CPF_VALUES.get(shared_key) or {})
        if not cached:
            return False

        cached_cpf = _normalize_cpf(cached.get("cpf"))
        if cached_cpf != normalized:
            return False

        if cached.get("consumed"):
            return False

        cached["consumed"] = True
        _SHARED_CPF_VALUES[shared_key] = cached
        return bool(cached.get("from_file"))

# -------------------------
# Api sms
# -------------------------

def _smsrush_load_credentials() -> tuple[str, str]:
    try:
        st = load_area_pix_settings() or {}
    except Exception:
        st = {}

    email = str(st.get("smsrush_email") or SMSRUSH_DEFAULT_EMAIL or "").strip()
    password = str(st.get("smsrush_password") or SMSRUSH_DEFAULT_PASSWORD or "").strip()

    if not email:
        raise Exception("E-mail do SMSRush não configurado na Área Pix.")
    if not password:
        raise Exception("Senha do SMSRush não configurada na Área Pix.")

    return email, password


def _smsrush_login() -> str:
    email, password = _smsrush_load_credentials()

    data = _smsrush_request(
        "POST",
        "/api/v1/auth/login",
        payload={
            "email": email,
            "password": password,
        },
    )

    token = (
        data.get("token")
        or (data.get("data") or {}).get("token")
        or (data.get("data") or {}).get("access_token")
        or data.get("access_token")
        or ""
    )
    token = str(token or "").strip()

    if not token:
        raise Exception(f"Login do SMSRush não retornou token válido: {data}")

    return token

def _smsrush_request(method: str, path: str, token: str = "", payload: dict | None = None) -> dict:
    base = str(SMSRUSH_BASE_URL or "").strip().rstrip("/")
    if not base:
        raise Exception("SMSRUSH_BASE_URL está vazio.")

    url = f"{base}{path}"
    body = None
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "User-Agent": "curl/8.7.1",
        "Connection": "keep-alive",
    }

    if token:
        headers["Authorization"] = f"Bearer {token}"

    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=body,
        headers=headers,
        method=str(method or "GET").upper(),
    )

    try:
        with urllib.request.urlopen(req, timeout=SMSRUSH_HTTP_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="ignore")
        raise Exception(f"SMSRush HTTP {e.code}: {raw or e.reason}")
    except Exception as e:
        raise Exception(f"Falha na requisição SMSRush: {e}")

    try:
        data = json.loads(raw) if raw else {}
    except Exception:
        data = {}

    if isinstance(data, dict) and data.get("success") is False:
        raise Exception(str(data.get("message") or "SMSRush retornou erro."))

    return data if isinstance(data, dict) else {}

def _smsrush_extract_local_phone(number_value: str) -> str:
    digits = re.sub(r"\D+", "", str(number_value or ""))
    if digits.startswith("55") and len(digits) > 11:
        digits = digits[2:]
    return digits


def _smsrush_buy_number(idx: int) -> dict:
    token = _smsrush_login()

    data = _smsrush_request(
        "POST",
        "/api/v1/virtual-numbers",
        token=token,
        payload={
            "country_id": SMSRUSH_COUNTRY_ID,
            "service_id": SMSRUSH_SERVICE_ID,
            "server_id": SMSRUSH_SERVER_ID,
            "quantity": SMSRUSH_QUANTITY,
        },
    )

    item = data.get("data") or {}
    activation_id = str(item.get("id") or item.get("virtual_number_id") or item.get("rental_id") or "").strip()
    raw_number = str(item.get("number") or "").strip()
    local_phone = _smsrush_extract_local_phone(raw_number)

    if not activation_id or not raw_number or not local_phone:
        raise Exception(f"Resposta inválida na compra do número virtual: {data}")

    print(f"[#{idx}] Número virtual comprado: {raw_number} | id={activation_id}")

    return {
        "token": token,
        "activation_id": activation_id,
        "raw_number": raw_number,
        "local_phone": local_phone,
    }


def _smsrush_get_status(token: str, activation_id: str) -> dict:
    data = _smsrush_request(
        "GET",
        f"/api/v1/virtual-numbers/{activation_id}/status",
        token=token,
    )
    payload = data.get("data")
    return payload if isinstance(payload, dict) else data


def _smsrush_find_activation_code(token: str, activation_id: str) -> str:
    data = _smsrush_request(
        "GET",
        "/api/v1/virtual-numbers/activations",
        token=token,
    )

    items = data.get("data") or []
    if not isinstance(items, list):
        items = []

    wanted = str(activation_id or "").strip()

    for item in items:
        if not isinstance(item, dict):
            continue

        ids = {
            str(item.get("id") or "").strip(),
            str(item.get("virtual_number_id") or "").strip(),
            str(item.get("rental_id") or "").strip(),
        }

        if wanted not in ids:
            continue

        latest_sms_code = str(item.get("latest_sms_code") or "").strip()
        digits = re.sub(r"\D+", "", latest_sms_code)

        if digits and 4 <= len(digits) <= 8:
            return digits

    return ""


def _smsrush_request_new_sms(token: str, activation_id: str):
    _smsrush_request(
        "POST",
        f"/api/v1/virtual-numbers/{activation_id}/request-new-sms",
        token=token,
    )

def _smsrush_cancel_number(token: str, activation_id: str):
    try:
        _smsrush_request(
            "DELETE",
            f"/api/v1/virtual-numbers/{activation_id}",
            token=token,
        )
    except Exception:
        pass


def _wait_smsrush_code_via_status(token: str, activation_id: str, idx: int, timeout_s: float = SMSRUSH_SMS_TIMEOUT) -> str:
    end = time.time() + timeout_s
    last_status = ""

    while time.time() < end:
        try:
            status_data = _smsrush_get_status(token, activation_id)
            sms_status = str(
                status_data.get("sms24h_status")
                or status_data.get("status")
                or status_data.get("status_description")
                or ""
            ).strip()

            if sms_status and sms_status != last_status:
                last_status = sms_status
                print(f"[#{idx}] Status do número {activation_id}: {sms_status}")
        except Exception:
            pass

        code = _smsrush_find_activation_code(token, activation_id)
        if code:
            print(f"[#{idx}] Código SMS recebido para {activation_id}: {code}")
            return code

        time.sleep(SMSRUSH_STATUS_POLL_INTERVAL)

    return ""


def _get_shared_phone_mutex(shared_key: str):
    shared_key = str(shared_key or "").strip()
    if not shared_key:
        return None

    with _SHARED_PHONE_LOCK:
        lock = _SHARED_PHONE_MUTEXES.get(shared_key)
        if lock is None:
            lock = threading.Lock()
            _SHARED_PHONE_MUTEXES[shared_key] = lock
        return lock


def _get_shared_phone_state(shared_key: str) -> dict:
    shared_key = str(shared_key or "").strip()
    if not shared_key:
        return {}
    with _SHARED_PHONE_LOCK:
        return dict(_SHARED_PHONE_STATES.get(shared_key) or {})


def _set_shared_phone_state(shared_key: str, state: dict):
    shared_key = str(shared_key or "").strip()
    if not shared_key:
        return
    with _SHARED_PHONE_LOCK:
        _SHARED_PHONE_STATES[shared_key] = dict(state or {})


def _clear_shared_phone_state(shared_key: str):
    shared_key = str(shared_key or "").strip()
    if not shared_key:
        return
    with _SHARED_PHONE_LOCK:
        _SHARED_PHONE_STATES.pop(shared_key, None)


def _goto_phone_security(page: Page, idx: int):
    base_url = "/".join((page.url or "").split("/")[:3]).strip()
    if not base_url:
        raise Exception("Não consegui descobrir a base_url para abrir /home/security?active=0")

    target = f"{base_url}/home/security?active=0"
    print(f"[#{idx}] Indo para verificação de telefone: {target}")
    page.goto(target, wait_until="domcontentloaded", timeout=15000)
    time.sleep(1.5)


def _fill_phone_number_field(page: Page, phone_number: str, idx: int):
    inp = page.locator("input[data-input-name='phone']").first
    inp.wait_for(state="visible", timeout=15000)

    try:
        inp.scroll_into_view_if_needed(timeout=2000)
    except Exception:
        pass

    inp.click(force=True)
    time.sleep(0.3)

    try:
        inp.fill("")
    except Exception:
        try:
            inp.press("Control+A")
            inp.press("Backspace")
        except Exception:
            pass

    typed_ok = False

    try:
        inp.press_sequentially(str(phone_number), delay=80)
        typed_ok = True
    except Exception:
        pass

    if not typed_ok:
        try:
            inp.fill(str(phone_number))
            typed_ok = True
        except Exception:
            page.keyboard.type(str(phone_number), delay=50)

    try:
        inp.evaluate(
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
                el.dispatchEvent(new KeyboardEvent("keyup", { bubbles: true, key: "5" }));
                el.dispatchEvent(new Event("blur", { bubbles: true }));
            }""",
            str(phone_number),
        )
    except Exception:
        pass

    time.sleep(1.0)
    print(f"[#{idx}] Telefone preenchido: {phone_number}")

def _click_receive_sms_button(page: Page, idx: int):
    end = time.time() + 20.0
    last_state = ""

    while time.time() < end:
        try:
            result = page.evaluate(
                """() => {
                    function norm(txt) {
                        return String(txt || "")
                            .replace(/\\u00a0/g, " ")
                            .replace(/\\s+/g, " ")
                            .trim()
                            .toLowerCase();
                    }

                    function isDisabled(el) {
                        if (!el) return true;

                        let cur = el;
                        for (let i = 0; i < 5 && cur; i++) {
                            const cls = String(cur.className || "").toLowerCase();
                            const aria = String(cur.getAttribute?.("aria-disabled") || "").toLowerCase();

                            if (
                                cls.includes("disabled") ||
                                aria === "true" ||
                                cur.hasAttribute?.("disabled")
                            ) {
                                return true;
                            }
                            cur = cur.parentElement;
                        }

                        return false;
                    }

                    function clickLikeUser(el) {
                        if (!el) return false;

                        try {
                            el.scrollIntoView({ block: "center", inline: "center" });
                        } catch (e) {}

                        const target = el.closest("button, [role='button'], span, div") || el;
                        const r = target.getBoundingClientRect();
                        const x = r.left + (r.width / 2);
                        const y = r.top + (r.height / 2);

                        ["pointerdown", "mousedown", "pointerup", "mouseup", "click"].forEach(type => {
                            try {
                                target.dispatchEvent(new MouseEvent(type, {
                                    view: window,
                                    bubbles: true,
                                    cancelable: true,
                                    clientX: x,
                                    clientY: y,
                                    button: 0
                                }));
                            } catch (e) {}
                        });

                        try { target.click(); } catch (e) {}
                        return true;
                    }

                    const root = document.querySelector("section[data-item-name='phoneCode']");
                    if (!root) {
                        return { clicked: false, reason: "root_not_found" };
                    }

                    const exact =
                        root.querySelector("span._sendButton_1v4ke_63") ||
                        root.querySelector("span[class*='_sendButton_']") ||
                        Array.from(root.querySelectorAll("span, button, div")).find(el => {
                            const txt = norm(el.innerText || el.textContent || "");
                            return txt === "receber código" || txt === "receber codigo";
                        });

                    if (!exact) {
                        return { clicked: false, reason: "button_not_found" };
                    }

                    if (isDisabled(exact)) {
                        return { clicked: false, reason: "button_disabled" };
                    }

                    const ok = clickLikeUser(exact);
                    return { clicked: !!ok, reason: ok ? "clicked" : "click_failed" };
                }"""
            ) or {}

            if bool(result.get("clicked")):
                print(f"[#{idx}] Botão 'Receber código' clicado.")
                time.sleep(1.0)
                return

            reason = str(result.get("reason") or "").strip()
            if reason and reason != last_state:
                last_state = reason
                print(f"[#{idx}] Aguardando botão 'Receber código': {reason}")

        except Exception as e:
            print(f"[#{idx}] Erro ao tentar clicar em 'Receber código': {e}")

        time.sleep(0.4)

    raise Exception("Botão 'Receber código' não habilitou ou não foi clicado.")

def _fill_phone_code_field(page: Page, code: str, idx: int):
    inp = page.locator("input[data-input-name='phoneCode']").first
    inp.wait_for(state="visible", timeout=15000)

    try:
        inp.scroll_into_view_if_needed(timeout=2000)
    except Exception:
        pass

    inp.click(force=True)
    time.sleep(0.3)

    try:
        inp.fill("")
    except Exception:
        try:
            inp.press("Control+A")
            inp.press("Backspace")
        except Exception:
            pass

    try:
        inp.fill(code)
    except Exception:
        page.keyboard.type(code, delay=50)

    time.sleep(0.6)
    print(f"[#{idx}] Código SMS preenchido.")


def _click_phone_confirm_button(page: Page, idx: int):
    selectors = [
        'div[class*="_footer_"] button:has(span.ui-button__text:text-is("Confirmar"))',
        'button.ui-button--block:has(span.ui-button__text:text-is("Confirmar"))',
        'button:has(span.ui-button__text:text-is("Confirmar"))',
        'button:has-text("Confirmar")',
    ]

    for sel in selectors:
        try:
            btn = page.locator(sel).first
            if btn.count() > 0 and btn.is_visible():
                try:
                    btn.scroll_into_view_if_needed(timeout=2000)
                except Exception:
                    pass

                btn.click(force=True, timeout=5000)
                print(f"[#{idx}] Confirmando verificação de telefone...")
                time.sleep(1.0)
                return
        except Exception:
            pass

    raise Exception("Não consegui clicar no botão Confirmar da verificação de telefone.")


def _wait_phone_verify_success(page: Page, idx: int, timeout_s: float = 10.0) -> str:
    end = time.time() + timeout_s

    success_selectors = [
        'div[class*="_quickVerify_"]:has-text("Verificado com sucesso")',
        'span:has-text("Verificado com sucesso rapidamente")',
        'span:has-text("Verificado com sucesso")',
    ]

    while time.time() < end:
        current_url = str(page.url or "").strip().lower()

        if "/home/security?active=0" not in current_url:
            print(f"[#{idx}] Telefone verificado: página saiu de /home/security?active=0")
            return "redirected"

        for sel in success_selectors:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0 and loc.is_visible():
                    print(f"[#{idx}] Telefone verificado com sucesso.")
                    return "verified"
            except Exception:
                pass

        time.sleep(0.5)

    current_url = str(page.url or "").strip().lower()
    if "/home/security?active=0" in current_url:
        print(f"[#{idx}] Ainda está em /home/security?active=0 após confirmar.")
        return "still_on_security"

    return "redirected"

def _verify_phone_with_smsrush(page: Page, idx: int, shared_key: str = "", max_attempts: int = PHONE_VERIFY_MAX_ATTEMPTS) -> bool:
    shared_key = str(shared_key or "").strip()
    shared_mutex = _get_shared_phone_mutex(shared_key)

    if shared_mutex:
        shared_mutex.acquire()

    attempt = 0

    try:
        while attempt < max_attempts:
            attempt += 1
            print(f"[#{idx}] 📱 Tentativa de verificação {attempt}/{max_attempts}...")

            shared_state = _get_shared_phone_state(shared_key) if shared_key else {}
            reuse_existing_number = bool(shared_state)

            if shared_state:
                phone_state = dict(shared_state)
                print(f"[#{idx}] Reutilizando número virtual do grupo '{shared_key}': {phone_state.get('raw_number')}")
            else:
                phone_state = _smsrush_buy_number(idx)
                if shared_key:
                    _set_shared_phone_state(shared_key, phone_state)

            token = str(phone_state.get("token") or "").strip()
            activation_id = str(phone_state.get("activation_id") or "").strip()
            local_phone = str(phone_state.get("local_phone") or "").strip()

            try:
                _goto_phone_security(page, idx)
                close_all_popups(page, idx)

                _fill_phone_number_field(page, local_phone, idx)
                time.sleep(3.0)
                _click_receive_sms_button(page, idx)

                if reuse_existing_number:
                    print(f"[#{idx}] Pedindo novo SMS para o mesmo número do grupo '{shared_key}'...")
                    _smsrush_request_new_sms(token, activation_id)

                code = _wait_smsrush_code_via_status(
                    token,
                    activation_id,
                    idx,
                    timeout_s=SMSRUSH_SMS_TIMEOUT,
                )

                if not code:
                    print(f"[#{idx}] SMS não chegou em {SMSRUSH_SMS_TIMEOUT:.0f}s. Cancelando número e comprando outro...")
                    _smsrush_cancel_number(token, activation_id)

                    if shared_key:
                        _clear_shared_phone_state(shared_key)

                    time.sleep(1.0)
                    continue

                _fill_phone_code_field(page, code, idx)
                _click_phone_confirm_button(page, idx)

                verify_result = _wait_phone_verify_success(page, idx, timeout_s=5.0)

                if verify_result in ("verified", "redirected"):
                    print(f"[#{idx}] ✅")
                    return True

                print(f"[#{idx}] Código usado, mas a página continuou em /home/security?active=0. Tentando novamente com o MESMO número...")
                time.sleep(1.0)
                continue

            except Exception as e:
                current_url = str(page.url or "").strip().lower()

                if "/home/security?active=0" not in current_url:
                    print(f"[#{idx}] Página saiu de /home/security?active=0 após exceção; tratando como verificado.")
                    try:
                        print(f"[#{idx}] ✅")
                    except Exception:
                        pass
                    return True

                print(f"[#{idx}] ⚠️ Falha na etapa de telefone com número {phone_state.get('raw_number')}: {e}")
                print(f"[#{idx}] Mantendo o mesmo número e tentando novamente com novo SMS...")
                time.sleep(1.0)
                continue

        print(f"[#{idx}] ⚠️ Verificação de telefone não concluiu após {max_attempts} tentativas. Seguindo em frente com a automação...")
        return False

    finally:
        if shared_mutex:
            shared_mutex.release()

# -------------------------
# Utils de clique universal
# -------------------------

def _click_any(page: Page, selectors: list[str], timeout=8000, js_fallback=True, scroll=True) -> bool:
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            loc.wait_for(state="visible", timeout=timeout)
            if scroll:
                try:
                    loc.scroll_into_view_if_needed(timeout=2000)
                except Exception:
                    pass
            try:
                loc.click(force=True, timeout=timeout)
                return True
            except Exception:
                if js_fallback:
                    try:
                        loc.evaluate("node => node.click()")
                        return True
                    except Exception:
                        pass
        except Exception:
            continue
    return False


def _any_visible(page: Page, selectors: list[str]) -> bool:
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible():
                return True
        except Exception:
            pass
    return False


def wait_any_visible(page: Page, selectors: list[str], timeout=10000) -> str:
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


def _fill_if_present(page: Page, selectors: list[str], value: str, timeout=2000) -> bool:
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() <= 0 or not loc.is_visible():
                continue

            try:
                loc.scroll_into_view_if_needed(timeout=timeout)
            except Exception:
                pass

            try:
                loc.click(force=True, timeout=timeout)
            except Exception:
                try:
                    loc.evaluate("node => node.click()")
                except Exception:
                    pass

            try:
                loc.fill(value)
            except Exception:
                try:
                    loc.press("Control+A")
                    loc.press("Backspace")
                except Exception:
                    pass
                page.keyboard.type(value, delay=40)

            return True
        except Exception:
            continue
    return False


def _make_real_name_from_username(user_name: str) -> str:
    base = "".join(c for c in (user_name or "") if c.isalpha())
    if len(base) >= 6:
        first = base[:6].capitalize()
        return f"{first} Silva"
    return "Joao Silva"


def _saques_selectors() -> list[str]:
    return [
        'div[class*="_navItem_"]:has(p:text-is("Saques"))',
        'div[class*="_navItem_"]:has(span:text-is("Saques"))',
        'div[class*="_nav_"]:has(span:text-is("Saques"))',
        'div[class*="_nav_"]:has(p:text-is("Saques"))',
        'div[class*="_item_"]:has(p:text-is("Saques"))',
        'div[class*="_item_"]:has(span:text-is("Saques"))',
        ':is(div,li,section,a,button):has(:is(p,span):text-is("Saques"))',
        ':is(div,li,section,a,button):has(:is(p,span):has-text("Saques"))',
    ]


def click_saques(page: Page, idx: int, timeout=5000) -> None:
    sels = _saques_selectors()
    end = time.time() + (timeout / 1000)

    while time.time() < end:
        # fecha popups/overlays que atrapalham clique
        try:
            if page.is_visible(".ui-overlay"):
                page.mouse.click(10, 10)
        except Exception:
            pass

        # tenta clicar direto (sem ficar preso só no is_visible)
        for sel in sels:
            try:
                locs = page.locator(sel)
                if locs.count() <= 0:
                    continue

                for i in range(min(locs.count(), 6)):
                    el = locs.nth(i)
                    try:
                        el.scroll_into_view_if_needed(timeout=1000)
                    except Exception:
                        pass

                    try:
                        el.click(force=True, timeout=1500)
                        return
                    except Exception:
                        try:
                            el.evaluate("node => node.click()")
                            return
                        except Exception:
                            pass
            except Exception:
                pass

        time.sleep(0.2)

    raise Exception("Não consegui clicar no botão 'Saques' (universal).")


def click_confirm_pin(page: Page, idx: int, timeout=12000) -> None:
    selectors = [
        'div[class*="_footer_"]:has(button:has(span.ui-button__text:text-is("Confirmar"))) button',
        'button:has(span.ui-button__text:text-is("Confirmar"))',
        'button:has(.ui-button__text:text-is("Confirmar"))',
        'button:has-text("Confirmar")',
    ]

    for sel in selectors:
        try:
            locs = page.locator(sel)
            cnt = locs.count()
            if cnt <= 0:
                continue

            for i in range(min(cnt, 10)):
                btn = locs.nth(i)
                try:
                    if not btn.is_visible():
                        continue
                except Exception:
                    continue

                try:
                    if not btn.is_enabled():
                        continue
                except Exception:
                    pass

                try:
                    btn.scroll_into_view_if_needed(timeout=2000)
                except Exception:
                    pass

                try:
                    btn.click(force=True, timeout=timeout)
                    return
                except Exception:
                    try:
                        btn.evaluate("node => node.click()")
                        return
                    except Exception:
                        pass
        except Exception:
            continue

    raise Exception("Não achei/click no botão 'Confirmar' do PIN (universal).")


def setup_pix_protocol_email_universal(page: Page, idx: int, timeout=10000) -> None:
    markers = [
        "input[data-input-name='name']",
        "section[data-item-name='name'] input",
        "section[data-item-name='account'] input",
        "section[data-item-name='extendInfo'] input",
        "#bindWithdrawAccountNextClick",
        "button[type='submit']:has-text('Confirmar')",
        "div.ui-options__option:has-text('EMAIL')",
        "section[data-item-name='protocol']",
    ]
    wait_any_visible(page, markers, timeout=timeout)

    protocol_selectors = [
        "section[data-item-name='protocol']",
        "section[data-item-name='protocolType']",
        "section[data-item-name='channel']",
        "section[data-item-name='method']",
    ]

    for sel in protocol_selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible():
                loc.click(force=True, timeout=3000)
                time.sleep(0.5)
                break
        except Exception:
            pass

    email_option_selectors = [
        "div.ui-options__option:has-text('EMAIL')",
        "div.ui-options__option:has-text('E-MAIL')",
        "div.ui-options__option:has-text('E-mail')",
        "div.ui-options__option:has-text('Email')",
        "li.ui-options__option:has-text('EMAIL')",
    ]

    for sel in email_option_selectors:
        try:
            opt = page.locator(sel).first
            if opt.count() > 0 and opt.is_visible():
                opt.click(force=True, timeout=4000)
                time.sleep(0.5)
                return
        except Exception:
            pass

    print(f"[#{idx}] ⚠️ Protocolo EMAIL não encontrado (seguindo com padrão do site).")


# -------------------------
# Popups universais
# -------------------------

def handle_red_pocket_tip_dialog(page: Page, idx: int) -> bool:
    dialog_selectors = [
        "div.ui-dialog[class*='_red-pocket-tip_']",
        "div.ui-popup.ui-popup--center:has-text('Lembrete importante')",
        "div.ui-popup.ui-popup--center:has(.ui-dialog__header:has-text('Lembrete'))",

        # popup genérico com botão Confirmar
        "div.ui-popup.ui-popup--center:has(button.ui-dialog__confirm)",
        "div.ui-dialog:has(button.ui-dialog__confirm)",
        "div.ui-popup:has(button:has-text('Confirmar'))",
    ]

    confirm_selectors = [
        "button.ui-dialog__confirm",
        "button.ui-dialog__confirm:has(span.ui-button__text:text-is('Confirmar'))",
        "button:has(span.ui-button__text:text-is('Confirmar'))",
        "button:has-text('Confirmar')",
    ]

    if not _any_visible(page, dialog_selectors):
        return False

    if _click_any(page, confirm_selectors, timeout=6000, js_fallback=True, scroll=True):
        time.sleep(0.6)
        return True

    return False

def close_daily_bonus_popup(page: Page, idx: int) -> bool:
    popup_selectors = [
        "div.red-pocket",
        "div:has(div.red-pocket)",
        "div[class*='_wrap_']:has(div.red-pocket)",
        "div[class*='_wrap_']:has-text('Bônus de login')",
        "div[class*='_wrap_']:has-text('Abrir bolsa')",
        "div[class*='_wrap_']:has-text('bolsa da sorte')",

        # popup com botão de fechar type2
        "div[class*='_wrap_']:has(span.close-style-type2.not-red-pocket)",
        "div[class*='_wrap_']:has(div[class*='_close_'])",
    ]

    close_selectors = [
        # botão de fechar que você mandou
        "div[class*='_close_'] span.close-style-type2.not-red-pocket",
        "span.close-style-type2.not-red-pocket",
        "div[class*='_close_'] span.close-style-type2",
        "span.close-style-type2",

        # antigos
        "div[class*='_close_'] span.close-style-type3",
        "span.close-style-type3",
        "div[class*='_close_'] span",
        "div.ui-dialog-close-box:visible",
        "i.ui-dialog-close-box__icon:visible",
    ]

    popup = None
    for sel in popup_selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible():
                popup = loc
                break
        except Exception:
            pass

    if popup is None:
        return False

    closed = False
    try:
        wrap = popup.locator("xpath=ancestor::*[contains(@class,'_wrap_')][1]")
        if wrap.count() == 0:
            wrap = popup.locator("xpath=ancestor::*[1]")

        for sel in close_selectors:
            try:
                btn = wrap.locator(sel).first
                if btn.count() > 0 and btn.is_visible():
                    try:
                        btn.scroll_into_view_if_needed(timeout=1500)
                    except Exception:
                        pass
                    try:
                        btn.click(force=True, timeout=2000)
                    except Exception:
                        btn.evaluate("node => node.click()")
                    time.sleep(0.6)
                    closed = True
                    break
            except Exception:
                pass
    except Exception:
        pass

    if not closed:
        try:
            page.keyboard.press("Escape")
            time.sleep(0.6)
            closed = True
        except Exception:
            closed = False

    if closed:
        for _ in range(3):
            if handle_red_pocket_tip_dialog(page, idx):
                break
            time.sleep(0.3)

    return closed

# ✅ NOVO: bloqueia e remove notic-user-tip-warp (universal)
def _ensure_notice_tip_css_block(page: Page) -> None:
    """
    Injeta CSS 1x para impedir que o banner atrapalhe cliques (mesmo se reaparecer).
    """
    try:
        page.evaluate(
            """() => {
                const id = '__hide_notic_user_tip__';
                if (document.getElementById(id)) return;
                const st = document.createElement('style');
                st.id = id;
                st.textContent = `
                    .notic-user-tip-warp{
                        display:none !important;
                        visibility:hidden !important;
                        opacity:0 !important;
                        pointer-events:none !important;
                    }
                `;
                (document.head || document.documentElement).appendChild(st);
            }"""
        )
    except Exception:
        pass


def close_notice_user_tip(page: Page, idx: int) -> bool:
    """
    Universal para os dois casos que você mandou:
      - wrapper: div.notic-user-tip-warp
      - cancelar: span.web-push-tip-cancel
      - confirmar: span.confirm (NÃO clicar)
    Estratégia:
      1) injeta CSS para não bloquear cliques
      2) para cada banner visível, clica em cancel
      3) remove via JS o que sobrar
    """
    _ensure_notice_tip_css_block(page)

    wrap_sel = "div.notic-user-tip-warp"
    any_closed = False

    try:
        wraps = page.locator(wrap_sel)
        cnt = wraps.count()
        if cnt <= 0:
            return False

        # tenta fechar até alguns (caso apareça duplicado)
        for i in range(min(cnt, 5)):
            w = wraps.nth(i)
            try:
                if not w.is_visible():
                    continue
            except Exception:
                continue

            # clica cancel dentro do próprio wrap (mais seguro)
            try:
                cancel = w.locator("span.web-push-tip-cancel").first
                if cancel.count() > 0:
                    try:
                        cancel.click(force=True, timeout=2000)
                    except Exception:
                        try:
                            cancel.evaluate("node => node.click()")
                        except Exception:
                            pass
                    time.sleep(0.25)
                    any_closed = True
            except Exception:
                pass

        # remove qualquer resíduo que ainda esteja no DOM
        page.evaluate(
            """() => {
                document.querySelectorAll('div.notic-user-tip-warp').forEach(el => el.remove());
            }"""
        )
        return True if (any_closed or cnt > 0) else False

    except Exception:
        # fallback total: remove via JS
        try:
            page.evaluate(
                """() => {
                    document.querySelectorAll('div.notic-user-tip-warp').forEach(el => el.remove());
                }"""
            )
            return True
        except Exception:
            return False


def close_all_popups(page: Page, idx: int):
    print(f"[#{idx}] Verificando popups bloqueadores...")

    for _ in range(18):
        found_popup = False

        # 0) notic-user-tip-warp (sempre primeiro pra não atrapalhar cliques)
        try:
            if close_notice_user_tip(page, idx):
                found_popup = True
        except Exception:
            pass

        # dialogs padrão (X)
        try:
            close_buttons = page.locator("i.ui-dialog-close-box__icon:visible, .ui-dialog-close-box:visible").all()
            for btn in close_buttons:
                try:
                    btn.click(force=True, timeout=2000)
                    time.sleep(0.35)
                    found_popup = True
                except Exception:
                    pass
        except Exception:
            pass

        # overlay
        try:
            if page.is_visible(".ui-overlay"):
                try:
                    page.mouse.click(10, 10)
                    time.sleep(0.35)
                    found_popup = True
                except Exception:
                    pass
        except Exception:
            pass

        # red pocket + confirmação
        try:
            if close_daily_bonus_popup(page, idx):
                found_popup = True
        except Exception:
            pass

        # lembrete importante (se aparecer sozinho)
        try:
            if handle_red_pocket_tip_dialog(page, idx):
                found_popup = True
        except Exception:
            pass

        if not found_popup:
            break

    print(f"[#{idx}] Tela limpa.")


# -------------------------
# SAQUE (Abrir tela e digitar PIN)
# -------------------------

def start_withdrawal(page: Page, idx: int, pin: str = None, log=None):
    pin = str(pin or WITHDRAW_PIN)

    def _log(m: str):
        try:
            if log:
                log(m)
            else:
                print(m)
        except Exception:
            pass

    def _pin_filled_count():
        try:
            return page.evaluate(
                """() => {
                    const items = Array.from(document.querySelectorAll('li.ui-password-input__item'));
                    if (!items.length) return 0;
                    let filled = 0;
                    for (const li of items) {
                        const cls = li.className || '';
                        if (cls.includes('filled')) { filled++; continue; }
                        const i = li.querySelector('i');
                        if (i) {
                            const st = window.getComputedStyle(i);
                            if (st && st.visibility !== 'hidden' && st.display !== 'none' && st.opacity !== '0') {
                                filled++;
                                continue;
                            }
                        }
                        if (li.textContent && li.textContent.trim().length > 0) {
                            filled++;
                            continue;
                        }
                    }
                    return filled;
                }"""
            )
        except Exception:
            return 0

    def _try_focus_password():
        focus_selectors = [
            "li.ui-password-input__item",
            ".ui-password-input",
            ".ui-password-input__wrapper",
            ".ui-password-input__list",
            ".ui-password-input input",
            "input[type='password']",
            "input[type='tel']",
        ]
        for sel in focus_selectors:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0:
                    loc.click(force=True)
                    time.sleep(0.35)
                    return
            except Exception:
                pass
        try:
            page.evaluate(
                """() => {
                    const root = document.querySelector('.ui-password-input') || document;
                    const inp = root.querySelector('input');
                    if (inp) { inp.focus(); inp.click(); }
                }"""
            )
            time.sleep(0.35)
        except Exception:
            pass

    base_url = None
    try:
        if page.url and page.url.startswith("http"):
            base_url = "/".join(page.url.split("/")[:3])
    except Exception:
        base_url = None

    if not base_url:
        try:
            from .utils import load_settings
            st = load_settings()
            u = (st.get("url") or "").strip()
            if u.startswith("http"):
                base_url = "/".join(u.split("/")[:3])
        except Exception:
            base_url = None

    if not base_url:
        raise Exception("Não foi possível descobrir a base_url para abrir /home/withdraw")

    target = f"{base_url}/home/withdraw?active="
    _log(f"[#{idx}] 💸 Indo para Saque: {target}")

    page.goto(target, wait_until="domcontentloaded", timeout=10000)
    time.sleep(1.0)

    sel_all_amount = [
        "span.ui-input__suffix-icon span:has-text('Tudo')",
        "span:has-text('Tudo')",
    ]
    if not _click_any(page, sel_all_amount, timeout=12000):
        raise Exception("Não achei/click no botão 'Tudo'.")
    time.sleep(0.8)

    sel_pwd_item = "li.ui-password-input__item"
    page.wait_for_selector(sel_pwd_item, state="visible", timeout=12000)
    _try_focus_password()

    want_len = len(pin)

    try:
        if page.is_visible(".ui-number-keyboard__body >> visible=true"):
            if click_visual_keypad(page, pin, idx):
                time.sleep(0.4)
    except Exception:
        pass

    if _pin_filled_count() < want_len:
        try:
            _try_focus_password()
            for d in pin:
                page.keyboard.press(d)
                time.sleep(0.08)
            time.sleep(0.4)
        except Exception:
            pass

    if _pin_filled_count() < want_len:
        try:
            _try_focus_password()
            page.keyboard.type(pin, delay=60)
            time.sleep(0.5)
        except Exception:
            pass

    if _pin_filled_count() < want_len:
        try:
            _try_focus_password()
            time.sleep(0.3)
            if click_visual_keypad(page, pin, idx):
                time.sleep(0.4)
        except Exception:
            pass

    if _pin_filled_count() < want_len:
        try:
            _log(f"[#{idx}] ⚠️ PIN não entrou via teclado/keypad; tentando fallback JS...")
            page.evaluate(
                """(val) => {
                    const root = document.querySelector('.ui-password-input') || document;
                    const inp = root.querySelector('input');
                    if (inp) {
                        inp.focus();
                        inp.value = '';
                        inp.dispatchEvent(new Event('input', { bubbles: true }));
                        inp.value = val;
                        inp.dispatchEvent(new Event('input', { bubbles: true }));
                        inp.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                }""",
                pin
            )
            time.sleep(0.6)
        except Exception:
            pass

    filled = _pin_filled_count()
    if filled < min(want_len, 1):
        raise Exception("Não conseguiu inserir o PIN (campo não aceitou entrada).")

    _log(f"[#{idx}] ✅ PIN inserido (detectado {filled}/{want_len}).")


def human_sleep(min_seconds=0.5, max_seconds=1.2):
    time.sleep(random.uniform(min_seconds, max_seconds))


def generate_cpf():
    def calculate_digit(digits):
        s = sum(w * d for w, d in zip(range(len(digits) + 1, 1, -1), digits))
        d = 11 - s % 11
        return 0 if d >= 10 else d

    cpf = [random.randint(0, 9) for _ in range(9)]
    cpf.append(calculate_digit(cpf))
    cpf.append(calculate_digit(cpf))
    return "".join(map(str, cpf))


def click_visual_keypad(page: Page, pin: str, idx: int):
    try:
        page.wait_for_selector(".ui-number-keyboard__body >> visible=true", timeout=5000)
    except Exception:
        return False

    for digit in str(pin):
        clicked = False
        selectors = [
            f"div.ui-number-keyboard-key:text-is('{digit}') >> visible=true",
            f".ui-number-keyboard-key:has-text('{digit}') >> visible=true",
            f"button:has-text('{digit}') >> visible=true",
            f"div:has-text('{digit}') >> visible=true",
        ]

        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0:
                    loc.click(force=True)
                    clicked = True
                    time.sleep(random.uniform(0.12, 0.22))
                    break
            except Exception:
                pass

        if not clicked:
            return False

    return True


def ensure_field_focus(page: Page, selector: str, idx: int):
    max_retries = 5
    for _ in range(max_retries):
        try:
            page.locator(selector).first.click(force=True)
        except Exception:
            pass
        time.sleep(1.0)
        try:
            if page.is_visible(".ui-number-keyboard__body >> visible=true"):
                return True
        except Exception:
            pass
    return False


def _queue_autochave_message(idx: int, email_pix: str) -> bool:
    try:
        st = load_settings() or {}
    except Exception as e:
        print(f"[#{idx}] [AutoChave] Falha ao carregar settings: {e}")
        st = {}

    autochave_enabled = bool(st.get(_scope_key("autochave_enabled"), st.get("autochave_enabled", False)))
    if not autochave_enabled:
        print(f"[#{idx}] [AutoChave/{AREA_PIX_SCOPE}] Desativado nas configurações.")
        return True

    autopix_bank = (st.get(_scope_key("autopix_bank")) or st.get("autopix_bank") or get_default_bank()).strip() or get_default_bank()

    print(f"[#{idx}] [AutoChave/{AREA_PIX_SCOPE}] Enfileirando fluxo completo para o e-mail: {email_pix}")
    try:
        ok = send_autochave_to_bank(
            autopix_bank,
            email_pix,
            log=lambda m: print(f"[#{idx}] {m}"),
            scope=AREA_PIX_SCOPE,
        )
        if ok:
            print(f"[#{idx}] [AutoChave/{AREA_PIX_SCOPE}] Fluxo concluído com TRUE para {email_pix}.")
            return True

        print(f"[#{idx}] [AutoChave/{AREA_PIX_SCOPE}] Fluxo ainda não retornou TRUE para {email_pix}.")
        return False
    except Exception as e:
        print(f"[#{idx}] [AutoChave/{AREA_PIX_SCOPE}] Erro ao executar fluxo: {e}")
        return False

def configure_withdrawal(page: Page, idx: int, user_data):
    print(f"[#{idx}] --- CONFIGURAÇÃO DE SAQUE ---")

    if isinstance(user_data, dict):
        registered_account = (user_data.get("account_value") or "").strip()
        identity_username = (
            user_data.get("base_username")
            or user_data.get("username")
            or user_data.get("registered_username")
            or registered_account
        ).strip()
        real_name_seed = (user_data.get("real_name") or "").strip()
        should_autochave = bool(user_data.get("should_autochave", True))
        shared_key = (user_data.get("shared_identity_key") or "").strip()
        shared_owner = bool(user_data.get("shared_identity_owner"))
        email_pix = (
            user_data.get("pix_email")
            or user_data.get("email_account")
            or ""
        ).strip()
    else:
        registered_account = (user_data or "").strip()
        identity_username = registered_account
        real_name_seed = ""
        should_autochave = True
        shared_key = ""
        shared_owner = False
        email_pix = ""

    if not email_pix:
        pix_seed = "".join(c for c in (identity_username or registered_account or "") if c.isalnum()) or "usuario"
        email_pix = f"{pix_seed}@guytmail.com"

    real_name_value = real_name_seed or _make_real_name_from_username(identity_username or registered_account)
    settings = load_settings() or {}
    use_generated_cpf = bool(settings.get(_scope_key("use_generated_cpf"), True))
    autochave_enabled = bool(settings.get(_scope_key("autochave_enabled"), settings.get("autochave_enabled", False)))
    verify_phone_enabled = bool(settings.get(_scope_key("verify_phone_enabled"), False))
    local_reserved_cpf = ""

    withdraw_account_done = False
    autochave_done = (not should_autochave)
    phone_verified_done = (not verify_phone_enabled)

    if shared_key and autochave_enabled and not shared_owner:
        _wait_shared_autochave_success(shared_key, idx, timeout_seconds=900.0)
        autochave_done = True

    sel_tab_profile = 'div[class*="_item"]:has(span:text-is("Perfil"))'
    sel_display_pwd = 'section[data-item-name="withdrawPass"] .ui-password-input'
    sel_display_confirm = 'section[data-item-name="confirmWithdrawPass"] .ui-password-input'

    while True:
        try:
            if not withdraw_account_done:
                close_all_popups(page, idx)

                print(f"[#{idx}] Indo para Perfil...")
                page.wait_for_selector(sel_tab_profile, state="attached", timeout=5000)

                for _ in range(3):
                    page.locator(sel_tab_profile).first.evaluate("node => node.click()")
                    time.sleep(1)

                    if "mine" in (page.url or "") or _any_visible(page, _saques_selectors()):
                        break

                    close_all_popups(page, idx)

                print(f"[#{idx}] Indo para Saques...")
                click_saques(page, idx, timeout=5000)
                time.sleep(1)

                if page.is_visible(sel_display_pwd):
                    if ensure_field_focus(page, sel_display_pwd, idx):
                        click_visual_keypad(page, WITHDRAW_PIN, idx)
                    human_sleep(1.0, 1.5)

                    if page.is_visible(sel_display_confirm):
                        if ensure_field_focus(page, sel_display_confirm, idx):
                            time.sleep(0.5)
                            click_visual_keypad(page, WITHDRAW_PIN, idx)

                    human_sleep(1.0, 2.0)
                    click_confirm_pin(page, idx, timeout=5000)
                    time.sleep(3)

                close_all_popups(page, idx)

                tab_recebimento = page.get_by_text("Conta para recebimento", exact=True).first
                if tab_recebimento.count() > 0 and tab_recebimento.is_visible():
                    tab_recebimento.click(force=True)
                    time.sleep(1)

                print(f"[#{idx}] Procurando botão de Adicionar Conta...")

                add_account_selectors = [
                    "#addAccountClick",
                    'div[class*="_cell-container"]:has(p:text-is("PIX"))',
                    'span:text-is("Adicionar Conta para Saque")',
                    'span:has-text("Adicionar Conta")',
                ]

                btn_clicado = False
                for selector in add_account_selectors:
                    btn = page.locator(selector).first
                    if btn.count() > 0 and btn.is_visible():
                        try:
                            btn.scroll_into_view_if_needed(timeout=3000)
                        except Exception:
                            pass
                        try:
                            btn.click(force=True, timeout=1000)
                            btn_clicado = True
                            time.sleep(1)
                            break
                        except Exception:
                            try:
                                btn.evaluate("node => node.click()")
                                btn_clicado = True
                                time.sleep(1)
                                break
                            except Exception:
                                continue

                if not btn_clicado:
                    raise Exception("Nenhum botão de Adicionar Conta funcionou ou estava visível na tela.")

                if page.is_visible('div[class*="_withdrawalPassword"] .ui-password-input') or page.is_visible('text="Inserir PIN"'):
                    modal_input = 'div[class*="_withdrawalPassword"] .ui-password-input'
                    if ensure_field_focus(page, modal_input, idx):
                        click_visual_keypad(page, WITHDRAW_PIN, idx)
                    time.sleep(1)
                    page.locator('button:has-text("Próximo")').first.click(force=True)
                    time.sleep(1)

                setup_pix_protocol_email_universal(page, idx, timeout=5000)

                cpf_pix = ""

                if shared_key:
                    shared_cpf_data = _get_or_create_shared_cpf(shared_key, idx, use_generated_cpf)
                    cpf_pix = str(shared_cpf_data.get("cpf") or "").strip()
                else:
                    if use_generated_cpf:
                        cpf_pix = generate_cpf()
                        print(f"[#{idx}] CPF individual gerado: {cpf_pix}")
                    else:
                        local_reserved_cpf = _reserve_cpf_from_file()
                        cpf_pix = local_reserved_cpf
                        print(f"[#{idx}] CPF individual obtido do cpf.json: {cpf_pix}")

                _fill_if_present(
                    page,
                    selectors=[
                        "input[data-input-name='name']",
                        "section[data-item-name='name'] input",
                        "section[data-item-name='realName'] input",
                        "input[placeholder*='nome verdadeiro']",
                        "input[placeholder*='nome real']",
                    ],
                    value=real_name_value,
                )

                page.locator('section[data-item-name="account"] input').first.fill(email_pix)
                page.locator('section[data-item-name="extendInfo"] input').first.fill(cpf_pix)

                time.sleep(1)

                confirm_btn = page.locator("#bindWithdrawAccountNextClick").first
                if not (confirm_btn.count() > 0 and confirm_btn.is_visible()):
                    confirm_btn = page.locator('button[type="submit"]:has-text("Confirmar")').first

                try:
                    confirm_btn.scroll_into_view_if_needed(timeout=3000)
                    confirm_btn.click(force=True, timeout=3000)
                    time.sleep(1)
                except Exception:
                    try:
                        confirm_btn.evaluate("node => node.click()")
                        time.sleep(1)
                    except Exception as js_e:
                        raise Exception(f"Falha ao clicar no botão Confirmar: {js_e}")

                if shared_key:
                    if _mark_shared_cpf_consumed(shared_key, cpf_pix):
                        _remove_used_cpf_from_file(cpf_pix)
                        print(f"[#{idx}] CPF compartilhado removido do cpf.json após uso: {cpf_pix}")
                elif local_reserved_cpf:
                    _remove_used_cpf_from_file(local_reserved_cpf)
                    print(f"[#{idx}] CPF removido do cpf.json após uso: {local_reserved_cpf}")
                    local_reserved_cpf = ""

                withdraw_account_done = True
                print(f"[#{idx}] ✅ Cadastro da conta de saque concluído.")
            else:
                print(f"[#{idx}] ♻️ Conta de saque já cadastrada. Retomando do ponto onde parou.")

            if not autochave_done:
                autochave_ok = _queue_autochave_message(idx, email_pix)
                if not autochave_ok:
                    raise Exception("AutoChave ainda não retornou TRUE. O QR e as contas compartilhadas continuam bloqueados.")

                if shared_key:
                    _publish_shared_autochave_success(shared_key, email_pix)

                autochave_done = True
            else:
                if should_autochave:
                    print(f"[#{idx}] [AutoChave/{AREA_PIX_SCOPE}] Já concluído anteriormente.")

            if verify_phone_enabled and not phone_verified_done:
                phone_ok = _verify_phone_with_smsrush(page, idx, shared_key=shared_key, max_attempts=PHONE_VERIFY_MAX_ATTEMPTS)
                phone_verified_done = True
            
                if phone_ok:
                    print(f"[#{idx}] ✅ Verificação de telefone concluída.")
                else:
                    print(f"[#{idx}] ⚠️ Verificação de telefone pulada após {PHONE_VERIFY_MAX_ATTEMPTS} tentativas. Seguindo com a automação.")
            elif verify_phone_enabled:
                print(f"[#{idx}] 📱 Verificação de telefone já concluída anteriormente.")

            break

        except Exception as e:
            if local_reserved_cpf and not withdraw_account_done:
                _release_reserved_cpf(local_reserved_cpf)
                local_reserved_cpf = ""

            print(f"[#{idx}] ❌ Erro no Saque: {e}")

            try:
                base_url = "/".join(page.url.split("/")[:3])

                if withdraw_account_done and verify_phone_enabled and not phone_verified_done:
                    print(f"[#{idx}] Retomando apenas da etapa de telefone em 3 segundos...")
                    page.goto(f"{base_url}/home/security?active=0", wait_until="domcontentloaded")
                else:
                    print(f"[#{idx}] Redirecionando para /home/mine e tentando novamente em 3 segundos...")
                    page.goto(f"{base_url}/home/mine", wait_until="domcontentloaded")
            except Exception:
                pass

            time.sleep(3)