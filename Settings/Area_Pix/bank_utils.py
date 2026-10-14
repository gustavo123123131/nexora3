import json
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Optional
from playwright.sync_api import Page, sync_playwright
PROJECT_ROOT = Path(__file__).resolve().parent.parent
AREA_PIX_DIR = PROJECT_ROOT / "Area_Pix"
PROFILES_DIR = AREA_PIX_DIR / "__profiles"
SETTINGS_PATH = AREA_PIX_DIR / "settings.json"

BANK_URLS = {
    "Banco Jota": "https://web.whatsapp.com/send/?phone=551140048006&text=Oi",
}

AUTOCHAVE_DELAY_BEFORE_SEND = 2.0
EMAILRUSH_URL = "https://emailrush.com.br"
AUTOCHAVE_WAIT_AFTER_WA_SEND = 3.0
AUTOCHAVE_WAIT_AFTER_EMAILRUSH_SAVE = 10.0
AUTOCHAVE_EMAIL_CODE_TIMEOUT = 45.0
AUTOCHAVE_MODAL_TIMEOUT = 20.0
AUTOCHAVE_RETRY_REOPEN_WA = 3
AUTOCHAVE_RETRY_DELAY = 3.0
CLICK_DELAY_AFTER_EACH_CLICK = 1.0
PIX_CONFIRM_TIMEOUT = 40.0
PIX_PIN_TIMEOUT = 20.0
PIX_AFTER_CLICK_DELAY = 2.0
PIX_RESULT_MESSAGE_TIMEOUT = 60.0
SILENT_KEEPALIVE_ENABLED = True
SILENT_KEEPALIVE_INTERVAL = 8.0
SILENT_KEEPALIVE_NETWORK_INTERVAL = 30.0
SILENT_KEEPALIVE_LOG = False

_SERVICE_LOCK = threading.RLock()
_SERVICE_STATE = {
    "thread": None,
    "stop_event": None,
    "ready_event": None,
    "bank_name": None,
    "profile_dir": None,
    "last_error": None,
}


_QUEUE_LOCK = threading.RLock()
_QUEUE_STATE = {}


def default_area_pix_settings() -> dict:
    return {
        "selected_bank": "Banco Jota",
        "bank_pins": {
            "Banco Jota": "",
        },
    }


def load_area_pix_settings() -> dict:
    try:
        with SETTINGS_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            base = default_area_pix_settings()
            base.update(data)
            return base
    except Exception:
        pass
    return default_area_pix_settings()


def save_area_pix_settings(data: dict):
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with SETTINGS_PATH.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_default_bank() -> str:
    st = load_area_pix_settings()
    bank = (st.get("selected_bank") or "Banco Jota").strip()
    return bank if bank in BANK_URLS else "Banco Jota"

def get_bank_pin(bank_name: str) -> str:
    st = load_area_pix_settings()
    bank_pins = st.get("bank_pins") or {}
    if not isinstance(bank_pins, dict):
        return ""
    return str(bank_pins.get(bank_name) or "").strip()


def save_bank_pin(bank_name: str, pin: str):
    st = load_area_pix_settings()
    bank_pins = st.get("bank_pins") or {}
    if not isinstance(bank_pins, dict):
        bank_pins = {}

    bank_pins[bank_name] = str(pin or "").strip()
    st["bank_pins"] = bank_pins
    save_area_pix_settings(st)


def profile_dir_for_bank(bank_name: str) -> Path:
    slug = (bank_name or "banco_jota").strip().lower().replace(" ", "_")
    return PROFILES_DIR / slug


def _log(log: Optional[Callable[[str], None]], msg: str):
    if log:
        log(msg)
    else:
        print(msg)

def _sleep_after_click():
    time.sleep(CLICK_DELAY_AFTER_EACH_CLICK)


def _locator_click_with_delay(locator, timeout: int = 5000, force: bool = True):
    locator.click(force=force, timeout=timeout)
    _sleep_after_click()


def _locator_js_click_with_delay(locator):
    locator.evaluate("el => el.click()")
    _sleep_after_click()
    
    
def _now_str() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _new_queue_slot() -> dict:
    return {
        "kind": "",
        "content": "",
        "message_id": "",
        "status": "idle",
        "colado": False,
        "updated_at": "",
        "sent_at": "",
        "ack_at": "",
        "last_message_id": "",
        "last_result": "",
        "meta": {},
    }


def _normalize_scope(scope: Optional[str]) -> str:
    scope = str(scope or "global").strip().lower()
    return scope or "global"


def _get_scope_slot_unlocked(scope: Optional[str], create: bool = True) -> dict:
    scope_key = _normalize_scope(scope)
    slot = _QUEUE_STATE.get(scope_key)
    if slot is None and create:
        slot = _new_queue_slot()
        slot["updated_at"] = _now_str()
        _QUEUE_STATE[scope_key] = slot
    return slot or {}


def _memory_queue_payload(scope: Optional[str] = None) -> dict:
    with _QUEUE_LOCK:
        if scope is None:
            return {k: dict(v) for k, v in _QUEUE_STATE.items()}
        return dict(_get_scope_slot_unlocked(scope))


def _clear_memory_queue(scope: Optional[str] = None):
    with _QUEUE_LOCK:
        if scope is None:
            scopes = list(_QUEUE_STATE.keys())
            if not scopes:
                scopes = ["global"]
        else:
            scopes = [_normalize_scope(scope)]

        for scope_key in scopes:
            current = _get_scope_slot_unlocked(scope_key)
            last_message_id = current.get("last_message_id", "")
            last_result = current.get("last_result", "")
            new_slot = _new_queue_slot()
            new_slot.update({
                "updated_at": _now_str(),
                "last_message_id": last_message_id,
                "last_result": last_result,
            })
            _QUEUE_STATE[scope_key] = new_slot


def _next_pending_queue(last_scope: Optional[str] = None):
    with _QUEUE_LOCK:
        scopes = sorted(_QUEUE_STATE.keys())
        if not scopes:
            return None, None

        if last_scope:
            last_scope = _normalize_scope(last_scope)
            if last_scope in scopes:
                idx = scopes.index(last_scope)
                scopes = scopes[idx + 1:] + scopes[:idx + 1]

        for scope_key in scopes:
            slot = _get_scope_slot_unlocked(scope_key)
            content = str(slot.get("content") or "").strip()
            message_id = str(slot.get("message_id") or "").strip()
            status = str(slot.get("status") or "idle").strip()
            if content and message_id and status == "pending":
                return scope_key, dict(slot)

    return None, None


def wait_until_queue_slot_free(timeout: float = 120.0, scope: str = "global") -> bool:
    scope_key = _normalize_scope(scope)
    end = time.time() + timeout
    while time.time() < end:
        with _QUEUE_LOCK:
            slot = _get_scope_slot_unlocked(scope_key)
            busy = bool(str(slot.get("content") or "").strip()) or str(slot.get("status") or "idle") in {"pending", "processing"}
        if not busy:
            return True
        time.sleep(0.25)
    return False


def wait_for_queue_ack(message_id: str, timeout: float = 120.0, scope: str = "global") -> bool:
    scope_key = _normalize_scope(scope)
    end = time.time() + timeout
    while time.time() < end:
        with _QUEUE_LOCK:
            slot = _get_scope_slot_unlocked(scope_key)
            ok = (
                slot.get("last_message_id") == message_id
                and bool(slot.get("colado"))
                and str(slot.get("last_result") or "") == "sent"
            )
        if ok:
            return True
        time.sleep(0.25)
    return False

def wait_for_queue_result(message_id: str, timeout: float = 120.0, scope: str = "global") -> str:
    scope_key = _normalize_scope(scope)
    end = time.time() + timeout

    while time.time() < end:
        with _QUEUE_LOCK:
            slot = _get_scope_slot_unlocked(scope_key)
            same_message = slot.get("last_message_id") == message_id
            result = str(slot.get("last_result") or "").strip()

        if same_message and result:
            return result

        time.sleep(0.25)

    return ""

def enqueue_memory_item(kind: str, content: str, message_id: Optional[str] = None, meta: Optional[dict] = None, scope: str = "global") -> dict:
    item_kind = str(kind or "").strip()
    item_content = str(content or "").strip()
    if not item_kind:
        raise Exception("Tipo de item vazio.")
    if not item_content:
        raise Exception("Conteúdo vazio.")

    scope_key = _normalize_scope(scope)
    with _QUEUE_LOCK:
        slot = _get_scope_slot_unlocked(scope_key)
        slot.update({
            "kind": item_kind,
            "content": item_content,
            "message_id": (message_id or str(uuid.uuid4())).strip(),
            "status": "pending",
            "colado": False,
            "updated_at": _now_str(),
            "sent_at": "",
            "ack_at": "",
            "meta": dict(meta or {}),
        })
        return dict(slot)


def mark_memory_processing(message_id: str, scope: str = "global") -> bool:
    scope_key = _normalize_scope(scope)
    with _QUEUE_LOCK:
        slot = _get_scope_slot_unlocked(scope_key)
        if slot.get("message_id") != message_id:
            return False
        slot["status"] = "processing"
        slot["updated_at"] = _now_str()
        return True


def ack_memory_sent(message_id: str, scope: str = "global") -> bool:
    scope_key = _normalize_scope(scope)
    with _QUEUE_LOCK:
        slot = _get_scope_slot_unlocked(scope_key)
        if slot.get("message_id") != message_id:
            return False
        now = _now_str()
        new_slot = _new_queue_slot()
        new_slot.update({
            "colado": True,
            "updated_at": now,
            "sent_at": now,
            "ack_at": now,
            "last_message_id": message_id,
            "last_result": "sent",
        })
        _QUEUE_STATE[scope_key] = new_slot
        return True


def mark_memory_error(message_id: str, scope: str = "global", result: str = "error") -> bool:
    scope_key = _normalize_scope(scope)
    with _QUEUE_LOCK:
        slot = _get_scope_slot_unlocked(scope_key)
        if slot.get("message_id") != message_id:
            return False
        now = _now_str()
        new_slot = _new_queue_slot()
        new_slot.update({
            "updated_at": now,
            "ack_at": now,
            "last_message_id": message_id,
            "last_result": str(result or "error"),
        })
        _QUEUE_STATE[scope_key] = new_slot
        return True


def _wait_for_whatsapp_input(page: Page, timeout_ms: int = 60000):
    candidates = [
        'div[contenteditable="true"][role="textbox"]',
        '[data-lexical-editor="true"][contenteditable="true"]',
        'footer [contenteditable="true"]',
    ]
    end = time.time() + (timeout_ms / 1000.0)
    while time.time() < end:
        for sel in candidates:
            try:
                loc = page.locator(sel).last
                if loc.count() > 0 and loc.is_visible():
                    return loc
            except Exception:
                pass
        time.sleep(0.25)
    raise Exception("Campo de mensagem do WhatsApp não apareceu. Verifique se a sessão está logada.")


def _send_button(page: Page):
    candidates = [
        'button[aria-label="Enviar"]',
        'button:has([data-icon="send"])',
        'button:has(svg title:text-is("wds-ic-send-filled"))',
    ]
    for sel in candidates:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible():
                return loc
        except Exception:
            pass
    return None


def _fill_whatsapp_message(page: Page, message: str):
    box = _wait_for_whatsapp_input(page)
    _locator_click_with_delay(box)

    try:
        page.keyboard.press("Control+A")
        page.keyboard.press("Backspace")
    except Exception:
        pass

    try:
        box.fill(message)
    except Exception:
        try:
            box.evaluate(
                "(el, value) => { el.focus(); document.execCommand('selectAll', false, null); document.execCommand('insertText', false, value); }",
                message,
            )
        except Exception:
            page.keyboard.type(message, delay=15)

def _click_send(page: Page):
    btn = _send_button(page)
    if btn is not None:
        try:
            _locator_click_with_delay(btn, timeout=5000)
            return
        except Exception:
            pass

    page.keyboard.press("Enter")
    time.sleep(CLICK_DELAY_AFTER_EACH_CLICK)

def _sleep_after_pix_click():
    time.sleep(PIX_AFTER_CLICK_DELAY)


def _locator_click_with_delay(locator, timeout: int = 5000, force: bool = True):
    locator.click(force=force, timeout=timeout)
    _sleep_after_pix_click()


def _locator_js_click_with_delay(locator):
    locator.evaluate("el => el.click()")
    _sleep_after_pix_click()

def _extract_email_user(full_email: str) -> str:
    full_email = str(full_email or "").strip()
    return full_email.split("@", 1)[0].strip() if "@" in full_email else full_email

def _click_pix_confirm_button(page: Page, timeout_s: float = PIX_CONFIRM_TIMEOUT):
    end = time.time() + timeout_s

    # O loop repete a verificação a cada 0.5 segundos até o botão novo aparecer
    while time.time() < end:
        try:
            clicked = page.evaluate(
                """
                () => {
                  function norm(txt) {
                    return String(txt || "").replace(/\\u00a0/g, " ").replace(/\\s+/g, " ").trim().toLowerCase();
                  }

                  function clickLikeUser(el) {
                    if (!el) return false;
                    try { el.scrollIntoView({ block: "center", inline: "center" }); } catch (e) {}
                    
                    const r = el.getBoundingClientRect();
                    const x = r.left + r.width / 2;
                    const y = r.top + r.height / 2;

                    ["pointerdown", "mousedown", "pointerup", "mouseup", "click"].forEach(type => {
                      try { 
                        el.dispatchEvent(new MouseEvent(type, { 
                          view: window, bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0 
                        })); 
                      } catch (e) {}
                    });
                    
                    try { el.click(); } catch (e) {}
                    return true;
                  }

                  // 1. Identifica a última mensagem enviada por NÓS (o código do Pix)
                  const outMsgs = Array.from(document.querySelectorAll('.message-out, [data-id^="true_"]'));
                  const lastOutMsg = outMsgs.length > 0 ? outMsgs[outMsgs.length - 1] : null;

                  // 2. Pega todos os botões de confirmar da tela
                  const allButtons = Array.from(document.querySelectorAll('button, [role="button"]'));
                  const confirmButtons = allButtons.filter(btn => {
                    const title = norm(btn.getAttribute("title") || "");
                    const aria = norm(btn.getAttribute("aria-label") || "");
                    const txt = norm(btn.innerText || btn.textContent || "");
                    return (title === "confirmar" || aria === "confirmar" || aria.includes("confirmar") || txt === "confirmar");
                  });

                  // 3. Lê de baixo para cima (do mais recente pro mais antigo)
                  for (let i = confirmButtons.length - 1; i >= 0; i--) {
                    const btn = confirmButtons[i];

                    // TRAVA DE SEGURANÇA: Se o botão estiver ACIMA da última mensagem que enviamos, 
                    // é histórico velho. Ignoramos e esperamos o novo renderizar.
                    if (lastOutMsg) {
                      const pos = lastOutMsg.compareDocumentPosition(btn);
                      if (pos & Node.DOCUMENT_POSITION_PRECEDING) {
                        continue; 
                      }
                    }

                    // Verifica se o botão faz parte do card do Pix
                    let cur = btn;
                    let isPixCard = false;
                    for (let level = 0; cur && level < 15; level++) {
                      const nodeTxt = norm(cur.innerText || cur.textContent || "");
                      if (nodeTxt.includes("confirma o envio desse pix")) {
                        isPixCard = true;
                        break;
                      }
                      cur = cur.parentElement;
                    }

                    if (isPixCard) {
                      return clickLikeUser(btn); // Clica no alvo correto e encerra!
                    }
                  }

                  return false; // Retorna falso para continuar o loop do Python
                }
                """
            )

            if clicked:
                time.sleep(2.0) # Pausa dramática para o WhatsApp processar o clique
                return True

        except Exception:
            pass

        # Espera meio segundo antes de tentar ler o DOM novamente
        time.sleep(0.5)

    raise Exception("Botão 'Confirmar' do Pix mais recente não apareceu a tempo.")

def _mark_latest_outgoing_pix_message(page: Page, message_text: str) -> str:
    anchor_id = f"pix-anchor-{uuid.uuid4().hex}"

    ok = page.evaluate(
        """
        (payload) => {
          const anchorId = String(payload.anchorId || "");
          const messageText = String(payload.messageText || "");

          function norm(txt) {
            return String(txt || "")
              .normalize("NFD")
              .replace(/[\\u0300-\\u036f]/g, "")
              .replace(/\\u00a0/g, " ")
              .replace(/\\s+/g, " ")
              .trim()
              .toLowerCase();
          }

          const expected = norm(messageText).slice(0, 120);

          const raw = Array.from(document.querySelectorAll(".message-out, [data-id^='true_']"));
          if (!raw.length) return false;

          let target = null;

          for (let i = raw.length - 1; i >= 0; i--) {
            const el = raw[i];
            const txt = norm(el.innerText || el.textContent || "");
            if (expected && txt && (txt.includes(expected) || expected.includes(txt.slice(0, 60)))) {
              target = el;
              break;
            }
          }

          if (!target) {
            target = raw[raw.length - 1];
          }

          if (!target) return false;

          document.querySelectorAll("[data-oai-pix-anchor]").forEach(el => {
            try { el.removeAttribute("data-oai-pix-anchor"); } catch (e) {}
          });

          target.setAttribute("data-oai-pix-anchor", anchorId);
          return true;
        }
        """,
        {
            "anchorId": anchor_id,
            "messageText": message_text,
        },
    )

    if not ok:
        raise Exception("Não consegui marcar a mensagem do QR Pix enviada no WhatsApp.")

    return anchor_id


def _read_latest_incoming_message_after_anchor(page: Page, anchor_id: str, message_text: str) -> dict:
    result = page.evaluate(
        """
        (payload) => {
          const anchorId = String(payload.anchorId || "");
          const messageText = String(payload.messageText || "");

          function norm(txt) {
            return String(txt || "")
              .normalize("NFD")
              .replace(/[\\u0300-\\u036f]/g, "")
              .replace(/\\u00a0/g, " ")
              .replace(/\\s+/g, " ")
              .trim()
              .toLowerCase();
          }

          function findAnchor() {
            let anchor = document.querySelector(`[data-oai-pix-anchor="${anchorId}"]`);
            if (anchor) return anchor;

            const expected = norm(messageText).slice(0, 120);
            const raw = Array.from(document.querySelectorAll(".message-out, [data-id^='true_']"));

            for (let i = raw.length - 1; i >= 0; i--) {
              const el = raw[i];
              const txt = norm(el.innerText || el.textContent || "");
              if (expected && txt && (txt.includes(expected) || expected.includes(txt.slice(0, 60)))) {
                anchor = el;
                break;
              }
            }

            if (!anchor && raw.length) {
              anchor = raw[raw.length - 1];
            }

            if (anchor) {
              try { anchor.setAttribute("data-oai-pix-anchor", anchorId); } catch (e) {}
            }

            return anchor;
          }

          const anchor = findAnchor();
          if (!anchor) {
            return {
              status: "waiting",
              text: "",
            };
          }

          const incoming = Array.from(document.querySelectorAll(".message-in, [data-id^='false_']"))
            .filter(el => {
              try {
                return !!(anchor.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING);
              } catch (e) {
                return false;
              }
            });

          if (!incoming.length) {
            return {
              status: "waiting",
              text: "",
            };
          }

          const latest = incoming[incoming.length - 1];
          const rawText = String(latest.innerText || latest.textContent || "").trim();
          const text = norm(rawText);

          if (!text) {
            return {
              status: "waiting",
              text: "",
            };
          }

          if (text.includes("pix enviado")) {
            return {
              status: "success",
              text: rawText,
            };
          }

          if (text.includes("nao")) {
            return {
              status: "fail",
              text: rawText,
            };
          }

          return {
            status: "waiting",
            text: rawText,
          };
        }
        """,
        {
            "anchorId": anchor_id,
            "messageText": message_text,
        },
    ) or {}

    if not isinstance(result, dict):
        return {"status": "waiting", "text": ""}

    return {
        "status": str(result.get("status") or "waiting").strip().lower(),
        "text": str(result.get("text") or "").strip(),
    }


def _wait_for_pix_result_message(page: Page, anchor_id: str, message_text: str, timeout_s: float = PIX_RESULT_MESSAGE_TIMEOUT) -> bool:
    end = time.time() + timeout_s
    last_seen = ""

    while time.time() < end:
        data = _read_latest_incoming_message_after_anchor(page, anchor_id, message_text)
        status = data.get("status", "waiting")
        text = data.get("text", "").strip()

        if text and text != last_seen:
            last_seen = text
            print(f"[Área Pix] Última mensagem recebida abaixo do QR: {text}")

        if status == "success":
            return True

        if status == "fail":
            return False

        time.sleep(0.5)

    return False

def _wait_for_pix_pin_input(page: Page, timeout_s: float = PIX_PIN_TIMEOUT):
    end = time.time() + timeout_s
    selectors = [
        'input#pin_code',
        'input[aria-labelledby="pin_code"]',
        'input[type="password"][inputmode="numeric"]',
        'input[type="password"]',
    ]

    while time.time() < end:
        # 1) primeiro tenta no flow/modal do WhatsApp (igual ao cadastro da chave)
        fr = _get_whatsapp_flow_frame(page, timeout_s=1.5)
        if fr is not None:
            for sel in selectors:
                try:
                    loc = fr.locator(sel).first
                    if loc.count() > 0 and loc.is_visible():
                        return fr, loc, "frame"
                except Exception:
                    pass

        # 2) fallback no DOM principal
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0 and loc.is_visible():
                    return page, loc, "main"
            except Exception:
                pass

        time.sleep(0.25)

    return None, None, None


def _fill_pix_pin_and_confirm(page: Page, pin: str):
    pin = str(pin or "").strip()
    if not pin:
        raise Exception("PIN do banco está vazio.")
    if not pin.isdigit():
        raise Exception(f"PIN inválido: {pin}")

    scope, inp, source = _wait_for_pix_pin_input(page, timeout_s=PIX_PIN_TIMEOUT)
    if scope is None or inp is None:
        raise Exception("Campo do PIN não apareceu dentro do modal/frame.")

    try:
        inp.scroll_into_view_if_needed(timeout=3000)
    except Exception:
        pass

    try:
        _locator_click_with_delay(inp, timeout=5000)
    except Exception:
        try:
            inp.evaluate("el => el.focus()")
        except Exception:
            pass

    time.sleep(0.25)

    filled = False

    # 1) fill simples
    try:
        inp.fill("", timeout=3000)
        inp.fill(pin, timeout=5000)
        filled = True
    except Exception:
        pass

    # 2) setter nativo + eventos
    if not filled:
        try:
            inp.evaluate(
                """
                (el, value) => {
                    const setter = Object.getOwnPropertyDescriptor(
                        window.HTMLInputElement.prototype,
                        'value'
                    ).set;

                    setter.call(el, '');
                    el.dispatchEvent(new Event('input', { bubbles: true }));

                    setter.call(el, value);
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    el.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, key: '0' }));
                    el.blur();
                }
                """,
                pin,
            )
            filled = True
        except Exception:
            pass

    # 3) digitação real
    if not filled:
        try:
            _locator_click_with_delay(inp, timeout=3000)
        except Exception:
            pass
        try:
            inp.press("Control+A")
            inp.press("Backspace")
        except Exception:
            pass
        try:
            inp.type(pin, delay=80, timeout=5000)
            filled = True
        except Exception:
            pass

    if not filled:
        raise Exception("Não foi possível preencher o PIN do Pix.")

    time.sleep(1.0)

    confirm_selectors = [
        '#wae-portal-footer button[aria-label*="Confirmar transação"]',
        '#wae-portal-footer button',
        'button[aria-label*="Confirmar transação"]',
        'button:has-text("Confirmar transação")',
        'button:has-text("Confirmar")',
    ]

    search_scopes = [scope]
    if scope is not page:
        search_scopes.append(page)

    end = time.time() + 12
    while time.time() < end:
        for base in search_scopes:
            for sel in confirm_selectors:
                try:
                    btn = base.locator(sel).first
                    if btn.count() > 0 and btn.is_visible():
                        disabled_attr = btn.get_attribute("disabled")
                        aria_disabled = str(btn.get_attribute("aria-disabled") or "").lower()

                        if disabled_attr is None and aria_disabled != "true":
                            try:
                                _locator_click_with_delay(btn, timeout=5000)
                                return True
                            except Exception:
                                try:
                                    _locator_js_click_with_delay(btn)
                                    return True
                                except Exception:
                                    pass
                except Exception:
                    pass

        time.sleep(0.3)

    raise Exception("Botão 'Confirmar transação' não habilitou depois de preencher o PIN.")

def _find_or_create_page(context, url_startswith: str):
    for p in context.pages:
        try:
            if (p.url or "").startswith(url_startswith):
                return p
        except Exception:
            pass
    return context.new_page()


def _open_emailrush_tab(context, reload: bool = False):
    page = _find_or_create_page(context, EMAILRUSH_URL)

    current_url = ""
    try:
        current_url = page.url or ""
    except Exception:
        current_url = ""

    if reload or not current_url.startswith(EMAILRUSH_URL):
        try:
            page.goto(EMAILRUSH_URL, wait_until="domcontentloaded", timeout=60000)
        except Exception:
            page.goto(EMAILRUSH_URL, timeout=60000)

    return page
def _context_is_alive(context) -> bool:
    try:
        _ = context.pages
        return True
    except Exception:
        return False


def _page_is_alive(page: Optional[Page]) -> bool:
    try:
        return page is not None and not page.is_closed()
    except Exception:
        return False

def _silent_keepalive_tick(
    page: Optional[Page],
    *,
    do_network_ping: bool = False,
    label: str = "main",
    log: Optional[Callable[[str], None]] = None,
) -> bool:
    if not _page_is_alive(page):
        return False

    try:
        result = page.evaluate(
            """
            async (payload) => {
                const doNetworkPing = !!payload.doNetworkPing;

                const out = {
                    ok: true,
                    href: "",
                    ready: "",
                    hidden: false,
                    net_ping: false,
                    error: "",
                };

                try {
                    out.href = String(location.href || "");
                    out.ready = String(document.readyState || "");
                    out.hidden = !!document.hidden;

                    // toque silencioso no DOM/JS
                    void document.body;
                    void document.title;
                    void navigator.userAgent;

                    // marcação interna só para manter atividade leve
                    window.__AREA_PIX_KEEPALIVE_AT__ = Date.now();

                    if (doNetworkPing) {
                        const pingUrl = location.origin + "/favicon.ico?__ka=" + Date.now();

                        try {
                            await fetch(pingUrl, {
                                method: "GET",
                                credentials: "include",
                                cache: "no-store",
                            });
                            out.net_ping = true;
                        } catch (e) {
                            out.error = String(e);
                        }
                    }
                } catch (e) {
                    out.ok = false;
                    out.error = String(e);
                }

                return out;
            }
            """,
            {"doNetworkPing": bool(do_network_ping)},
        ) or {}

        if SILENT_KEEPALIVE_LOG:
            _log(
                log,
                f"[Área Pix/{label}] keepalive ok={result.get('ok')} "
                f"net={result.get('net_ping')} ready={result.get('ready')} "
                f"hidden={result.get('hidden')} url={result.get('href')}"
            )

        return bool(result.get("ok", True))

    except Exception as e:
        if SILENT_KEEPALIVE_LOG:
            _log(log, f"[Área Pix/{label}] keepalive falhou: {e}")
        return False

def _launch_area_pix_context(playwright_obj, profile_dir: Path, bank_url: str):
    context = playwright_obj.chromium.launch_persistent_context(
        user_data_dir=str(profile_dir),
        headless=False,
        viewport=None,
    )

    page = context.pages[0] if context.pages else context.new_page()
    page.goto(bank_url, wait_until="domcontentloaded", timeout=60000)
    _wait_for_whatsapp_input(page, timeout_ms=30000)

    return context, page

def _ensure_area_pix_session_alive(
    playwright_obj,
    context,
    page,
    profile_dir: Path,
    bank_url: str,
    log: Optional[Callable[[str], None]] = None,
):
    alive = _context_is_alive(context) and _page_is_alive(page)

    if alive:
        try:
            current_url = page.url or ""
            if not current_url.startswith("https://web.whatsapp.com"):
                page.goto(bank_url, wait_until="domcontentloaded", timeout=60000)
                _wait_for_whatsapp_input(page, timeout_ms=30000)
        except Exception:
            alive = False

    if alive:
        return context, page

    _log(log, "[Área Pix] Sessão morreu/fechou. Reabrindo navegador da Área Pix...")

    try:
        if _page_is_alive(page):
            page.close()
    except Exception:
        pass

    try:
        if _context_is_alive(context):
            context.close()
    except Exception:
        pass

    context, page = _launch_area_pix_context(playwright_obj, profile_dir, bank_url)
    _log(log, "[Área Pix] Sessão da Área Pix reaberta com sucesso.")

    return context, page


def _configure_emailrush_user(emailrush_page: Page, full_email: str):
    email_user = _extract_email_user(full_email)

    alterar_selectors = [
        'button[onclick="openEditModal()"]',
        'button:has-text("Alterar")',
    ]
    input_selectors = [
        '#modalInputUser',
        'input[name="new_username"]',
    ]
    save_selectors = [
        'button[onclick="app.saveEditModal()"]',
        'button:has-text("Salvar Alterações")',
        'button[type="button"]:has-text("Salvar Alterações")',
    ]

    # abrir modal
    clicked = False
    for sel in alterar_selectors:
        try:
            loc = emailrush_page.locator(sel).first
            if loc.count() > 0 and loc.is_visible():
                loc.scroll_into_view_if_needed(timeout=3000)
                try:
                    _locator_click_with_delay(loc, timeout=8000)
                except Exception:
                    _locator_js_click_with_delay(loc)
                clicked = True
                break
        except Exception:
            pass

    if not clicked:
        raise Exception("Botão Alterar do EmailRush não encontrado.")

    # esperar input aparecer
    inp = None
    end = time.time() + 20
    while time.time() < end:
        for sel in input_selectors:
            try:
                loc = emailrush_page.locator(sel).first
                if loc.count() > 0 and loc.is_visible():
                    inp = loc
                    break
            except Exception:
                pass
        if inp is not None:
            break
        time.sleep(0.25)

    if inp is None:
        raise Exception("Input do EmailRush não apareceu.")

    # preencher usuário
    inp.scroll_into_view_if_needed(timeout=3000)
    _locator_click_with_delay(inp)
    try:
        inp.fill("")
    except Exception:
        pass

    try:
        inp.fill(email_user)
    except Exception:
        try:
            inp.press("Control+A")
            inp.press("Backspace")
        except Exception:
            pass
        emailrush_page.keyboard.type(email_user, delay=20)

    time.sleep(0.5)

    # esperar botão salvar aparecer de verdade e clicar forte
    save_btn = None
    end = time.time() + 20
    while time.time() < end:
        for sel in save_selectors:
            try:
                loc = emailrush_page.locator(sel).first
                if loc.count() > 0 and loc.is_visible():
                    save_btn = loc
                    break
            except Exception:
                pass
        if save_btn is not None:
            break
        time.sleep(0.25)

    if save_btn is None:
        raise Exception("Botão 'Salvar Alterações' do EmailRush não encontrado.")

    try:
        save_btn.scroll_into_view_if_needed(timeout=3000)
    except Exception:
        pass

    clicked_save = False

    # tentativa 1: click normal
    try:
        _locator_click_with_delay(save_btn, timeout=8000)
        clicked_save = True
    except Exception:
        pass

    # tentativa 2: clicar no texto interno
    if not clicked_save:
        try:
            inner = save_btn.locator("xpath=.//*").last
            if inner.count() > 0:
                _locator_click_with_delay(inner, timeout=5000)
                clicked_save = True
        except Exception:
            pass

    # tentativa 3: JS click
    if not clicked_save:
        try:
            _locator_js_click_with_delay(save_btn)
            clicked_save = True
        except Exception:
            pass

    if not clicked_save:
        raise Exception("Falha ao clicar em 'Salvar Alterações' no EmailRush.")

    time.sleep(1.2)

def _open_emailrush_message(emailrush_page: Page):
    end = time.time() + AUTOCHAVE_EMAIL_CODE_TIMEOUT
    last_error = ""

    while time.time() < end:
        try:
            clicked = emailrush_page.evaluate(
                """
                () => {
                  function norm(txt) {
                    return String(txt || "")
                      .replace(/\\u00a0/g, " ")
                      .replace(/\\s+/g, " ")
                      .trim()
                      .toLowerCase();
                  }

                  function clickLikeUser(el) {
                    if (!el) return false;

                    try {
                      el.scrollIntoView({ block: "center", inline: "center" });
                    } catch (e) {}

                    const r = el.getBoundingClientRect();
                    const x = r.left + r.width / 2;
                    const y = r.top + r.height / 2;

                    ["pointerdown", "mousedown", "pointerup", "mouseup", "click"].forEach(type => {
                      try {
                        el.dispatchEvent(new MouseEvent(type, {
                          view: window,
                          bubbles: true,
                          cancelable: true,
                          clientX: x,
                          clientY: y,
                          button: 0
                        }));
                      } catch (e) {}
                    });

                    try { el.click(); } catch (e) {}
                    return true;
                  }

                  const cards = Array.from(document.querySelectorAll("div[data-message-id], #email-list > div"));

                  const card = cards.find(card => {
                    const txt = norm(card.innerText || card.textContent || "");
                    return (
                      txt.includes("ajuda jota") &&
                      txt.includes("ajuda@jota.ai") &&
                      txt.includes("finalize o cadastro da sua chave pix no jota")
                    );
                  });

                  if (!card) return false;

                  const alvo =
                    card.querySelector(".flex.items-center.gap-3.w-full") ||
                    card.querySelector("p") ||
                    card;

                  clickLikeUser(alvo);
                  return true;
                }
                """
            )

            if clicked:
                # espera o conteúdo do email realmente abrir
                open_end = time.time() + 15.0
                while time.time() < open_end:
                    try:
                        opened = emailrush_page.evaluate(
                            """
                            () => {
                              function norm(txt) {
                                return String(txt || "")
                                  .replace(/\\u00a0/g, " ")
                                  .replace(/\\s+/g, " ")
                                  .trim()
                                  .toLowerCase();
                              }

                              if (document.querySelector(".token")) return true;

                              const bodyText = norm(document.body?.innerText || document.body?.textContent || "");
                              if (
                                bodyText.includes("código para autenticação") ||
                                bodyText.includes("codigo para autenticacao") ||
                                bodyText.includes("insira o código abaixo") ||
                                bodyText.includes("insira o codigo abaixo")
                              ) {
                                return true;
                              }

                              const iframes = Array.from(document.querySelectorAll("iframe"));
                              for (const iframe of iframes) {
                                try {
                                  const srcdoc = iframe.getAttribute("srcdoc") || "";
                                  const s = norm(srcdoc);
                                  if (
                                    s.includes("código para autenticação") ||
                                    s.includes("codigo para autenticacao") ||
                                    s.includes("insira o código abaixo") ||
                                    s.includes("insira o codigo abaixo") ||
                                    /class=["']token["'][^>]*>\\s*\\d{6}\\s*</i.test(srcdoc)
                                  ) {
                                    return true;
                                  }
                                } catch (e) {}

                                try {
                                  const doc = iframe.contentDocument || iframe.contentWindow?.document;
                                  if (!doc) continue;

                                  if (doc.querySelector(".token")) return true;

                                  const txt = norm(doc.body?.innerText || doc.body?.textContent || "");
                                  if (
                                    txt.includes("código para autenticação") ||
                                    txt.includes("codigo para autenticacao") ||
                                    txt.includes("insira o código abaixo") ||
                                    txt.includes("insira o codigo abaixo")
                                  ) {
                                    return true;
                                  }
                                } catch (e) {}
                              }

                              return false;
                            }
                            """
                        )
                        if opened:
                            return True
                    except Exception as e:
                        last_error = str(e)

                    time.sleep(0.4)

        except Exception as e:
            last_error = str(e)

        time.sleep(1.0)

    if last_error:
        raise Exception(f"Mensagem do Jota não apareceu no EmailRush. Último erro: {last_error}")
    raise Exception("Mensagem do Jota não apareceu no EmailRush.")

def _get_emailrush_code(emailrush_page: Page) -> str:
    end = time.time() + AUTOCHAVE_EMAIL_CODE_TIMEOUT
    last_error = ""

    while time.time() < end:
        try:
            code = emailrush_page.evaluate(
                """
                () => {
                  function norm(txt) {
                    return String(txt || "")
                      .replace(/\\u00a0/g, " ")
                      .replace(/\\s+/g, " ")
                      .trim();
                  }

                  function codigoValido(code) {
                    return /^\\d{6}$/.test(code) && code !== "000000" && !/^(\\d)\\1{5}$/.test(code);
                  }

                  function pickBestCodeFromText(txt) {
                    const text = norm(txt);
                    const matches = [...text.matchAll(/\\b\\d{6}\\b/g)].map(m => ({
                      code: m[0],
                      idx: m.index || 0
                    }));

                    const ranked = matches
                      .filter(x => codigoValido(x.code))
                      .map(x => {
                        const ctx = text.slice(Math.max(0, x.idx - 140), Math.min(text.length, x.idx + 140)).toLowerCase();
                        let score = 0;

                        if (ctx.includes("código para autenticação")) score += 100;
                        if (ctx.includes("codigo para autenticacao")) score += 100;
                        if (ctx.includes("insira o código abaixo")) score += 80;
                        if (ctx.includes("insira o codigo abaixo")) score += 80;
                        if (ctx.includes("tela de validação")) score += 60;
                        if (ctx.includes("tela de validacao")) score += 60;
                        if (ctx.includes("whatsapp")) score += 40;
                        if (ctx.includes("expira")) score += 20;
                        if (ctx.includes("jota")) score += 10;

                        return { code: x.code, score, ctx };
                      })
                      .sort((a, b) => b.score - a.score);

                    return ranked[0] || null;
                  }

                  function extractFromDoc(doc, origem) {
                    if (!doc) return null;

                    // 1) prioridade total para .token
                    const tokenEls = Array.from(doc.querySelectorAll(".token"));
                    for (const el of tokenEls) {
                      const code = norm(el.innerText || el.textContent || "");
                      if (codigoValido(code)) {
                        return {
                          codigo: code,
                          origem: `${origem} .token`
                        };
                      }
                    }

                    // 2) procurar em elementos com texto relevante
                    const allEls = Array.from(doc.querySelectorAll("div, p, td, span, strong"));
                    for (const el of allEls) {
                      const txt = norm(el.innerText || el.textContent || "");
                      if (!txt) continue;

                      const lower = txt.toLowerCase();
                      if (
                        lower.includes("código para autenticação") ||
                        lower.includes("codigo para autenticacao") ||
                        lower.includes("insira o código abaixo") ||
                        lower.includes("insira o codigo abaixo")
                      ) {
                        const best = pickBestCodeFromText(txt);
                        if (best) {
                          return {
                            codigo: best.code,
                            origem: `${origem} texto relevante`
                          };
                        }
                      }
                    }

                    // 3) body inteiro
                    const bodyText = norm(doc.body?.innerText || doc.body?.textContent || "");
                    const best = pickBestCodeFromText(bodyText);
                    if (best) {
                      return {
                        codigo: best.code,
                        origem: `${origem} body`
                      };
                    }

                    return null;
                  }

                  // documento principal
                  let result = extractFromDoc(document, "document");
                  if (result && result.codigo) {
                    return result.codigo;
                  }

                  // iframes
                  const iframes = Array.from(document.querySelectorAll("iframe"));
                  for (let i = 0; i < iframes.length; i++) {
                    const iframe = iframes[i];

                    try {
                      const srcdoc = iframe.getAttribute("srcdoc") || "";

                      const tokenMatch = srcdoc.match(/class=["']token["'][^>]*>\\s*(\\d{6})\\s*</i);
                      if (tokenMatch && codigoValido(tokenMatch[1])) {
                        return tokenMatch[1];
                      }

                      const best = pickBestCodeFromText(srcdoc);
                      if (best && best.code) {
                        return best.code;
                      }
                    } catch (e) {}

                    try {
                      const doc = iframe.contentDocument || iframe.contentWindow?.document;
                      result = extractFromDoc(doc, `iframe[${i}]`);
                      if (result && result.codigo) {
                        return result.codigo;
                      }
                    } catch (e) {}
                  }

                  return "";
                }
                """
            )

            code = str(code or "").strip()
            if re.fullmatch(r"\d{6}", code) and code != "000000" and not re.fullmatch(r"(\d)\1{5}", code):
                return code

        except Exception as e:
            last_error = str(e)

        time.sleep(1.0)

    if last_error:
        raise Exception(f"Código de 6 dígitos não foi encontrado no EmailRush. Último erro: {last_error}")
    raise Exception("Código de 6 dígitos não foi encontrado no EmailRush.")

def _reopen_whatsapp_chat(page: Page, url: str):
    context = page.context
    try:
        page.close()
    except Exception:
        pass
    new_page = context.new_page()
    new_page.goto(url, wait_until="domcontentloaded", timeout=60000)
    _wait_for_whatsapp_input(new_page, timeout_ms=30000)
    return new_page

def _get_whatsapp_flow_frame(page: Page, timeout_s: float = 12.0):
    """
    Só tenta pegar o frame depois que o modal realmente abriu.
    Primeiro procura o iframe visível, depois faz fallback por URL.
    """
    end = time.time() + timeout_s

    while time.time() < end:
        # 1) iframe visível na página
        try:
            iframe_loc = page.locator('iframe').last
            if iframe_loc.count() > 0 and iframe_loc.is_visible():
                try:
                    fr = iframe_loc.element_handle().content_frame()
                    if fr is not None:
                        return fr
                except Exception:
                    pass
        except Exception:
            pass

        # 2) fallback por URL
        try:
            for fr in page.frames:
                try:
                    url = fr.url or ""
                    if "flows.whatsapp.net" in url:
                        return fr
                except Exception:
                    pass
        except Exception:
            pass

        time.sleep(0.25)

    return None


def _wait_for_token_input(page: Page, timeout_s: float = 20.0):
    end = time.time() + timeout_s
    selectors = [
        'input#token_input',
        'form[name="TOKEN_INPUT_form"] input#token_input',
        '#wa_flows_screen_TOKEN_INPUT input#token_input',
        'input[aria-labelledby="token_input"]',
        'input[enterkeyhint="next"]',
    ]

    while time.time() < end:
        fr = _get_whatsapp_flow_frame(page, timeout_s=2.0)
        if fr is not None:
            for sel in selectors:
                try:
                    loc = fr.locator(sel).first
                    if loc.count() > 0 and loc.is_visible():
                        return fr, loc
                except Exception:
                    pass
        time.sleep(0.25)

    return None, None


def _click_register_key_for_email(page: Page, full_email: str):
    full_email = str(full_email or "").strip()
    if not full_email:
        raise Exception("E-mail vazio para clicar em 'Cadastrar chave'.")

    end = time.time() + 40
    while time.time() < end:
        try:
            email_link = page.locator(f'a[title="mailto:{full_email}"]').last
            if not (email_link.count() > 0 and email_link.is_visible()):
                time.sleep(0.4)
                continue
        except Exception:
            time.sleep(0.4)
            continue

        selectors = [
            'button[title="Cadastrar chave"]',
            'button:has-text("Cadastrar chave")',
            '[role="button"][title="Cadastrar chave"]',
        ]

        for sel in selectors:
            try:
                btn = page.locator(sel).last
                if btn.count() > 0 and btn.is_visible():
                    try:
                        btn.scroll_into_view_if_needed(timeout=2000)
                    except Exception:
                        pass

                    try:
                        _locator_click_with_delay(btn, timeout=5000)
                    except Exception:
                        try:
                            _locator_js_click_with_delay(btn)
                        except Exception:
                            continue

                    return True
            except Exception:
                pass

        time.sleep(0.4)

    raise Exception(f"Botão 'Cadastrar chave' não apareceu para o email {full_email}.")

def _fill_whatsapp_token_and_confirm(page: Page, code: str):
    token = str(code or "").strip()
    if not token or len(token) != 6:
        raise Exception(f"Código inválido para WhatsApp: {token}")

    fr, inp = _wait_for_token_input(page, timeout_s=AUTOCHAVE_MODAL_TIMEOUT)
    if fr is None or inp is None:
        raise Exception("Input do código não apareceu dentro do modal/frame.")

    try:
        inp.scroll_into_view_if_needed(timeout=3000)
    except Exception:
        pass

    try:
        _locator_click_with_delay(inp, timeout=5000)
    except Exception:
        try:
            inp.evaluate("el => el.focus()")
        except Exception:
            pass

    time.sleep(0.25)

    filled = False

    # 1) fill simples
    try:
        inp.fill("", timeout=3000)
        inp.fill(token, timeout=5000)
        filled = True
    except Exception:
        pass

    # 2) setter nativo
    if not filled:
        try:
            inp.evaluate(
                """
                (el, value) => {
                    const setter = Object.getOwnPropertyDescriptor(
                        window.HTMLInputElement.prototype,
                        'value'
                    ).set;
                    setter.call(el, '');
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    setter.call(el, value);
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    el.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, key: '6' }));
                    el.blur();
                }
                """,
                token,
            )
            filled = True
        except Exception:
            pass

    # 3) type real no próprio input do frame
    if not filled:
        try:
            _locator_click_with_delay(inp, timeout=3000)
        except Exception:
            pass
        try:
            inp.press("Control+A")
            inp.press("Backspace")
        except Exception:
            pass
        try:
            inp.type(token, delay=70, timeout=5000)
            filled = True
        except Exception:
            pass

    if not filled:
        raise Exception("Não foi possível preencher o código no input do modal.")

    time.sleep(1.0)

    confirm_selectors = [
        '#wae-portal-footer button',
        'button[aria-label*="Confirmar"]',
        'button:has-text("Confirmar")',
    ]

    end = time.time() + 10
    while time.time() < end:
        for sel in confirm_selectors:
            try:
                btn = fr.locator(sel).first
                if btn.count() > 0 and btn.is_visible():
                    disabled_attr = btn.get_attribute("disabled")
                    aria_disabled = str(btn.get_attribute("aria-disabled") or "").lower()

                    if disabled_attr is None and aria_disabled != "true":
                        try:
                            _locator_click_with_delay(btn, timeout=5000)
                        except Exception:
                            _locator_js_click_with_delay(btn)
                        return True
            except Exception:
                pass
        time.sleep(0.3)

    raise Exception("Botão Confirmar não habilitou depois de preencher o código.")

def _service_worker(
    bank_name: str,
    use_new_session: bool,
    stop_event: threading.Event,
    ready_event: threading.Event,
    log: Optional[Callable[[str], None]] = None,
):
    bank_name = bank_name if bank_name in BANK_URLS else get_default_bank()
    url = BANK_URLS[bank_name]
    profile_dir = profile_dir_for_bank(bank_name)
    context = None
    page = None
    emailrush_page = None
    emailrush_current_email = ""
    last_scope_served = None
    last_keepalive_at = 0.0
    last_keepalive_network_at = 0.0


    try:
        PROFILES_DIR.mkdir(parents=True, exist_ok=True)
        if use_new_session and profile_dir.exists():
            _log(log, f"[Área Pix] Limpando sessão salva de {bank_name}...")
            shutil.rmtree(profile_dir, ignore_errors=True)
        profile_dir.mkdir(parents=True, exist_ok=True)

        with _QUEUE_LOCK:
            if not _QUEUE_STATE:
                _clear_memory_queue()

        with sync_playwright() as p:
            context, page = _launch_area_pix_context(p, profile_dir, url)
            _log(log, f"[Área Pix] Sessão persistente de {bank_name} pronta.")
            _log(log, "[Área Pix] Fila em memória pronta.")
            ready_event.set()

            while not stop_event.is_set():
                try:
                    context, page = _ensure_area_pix_session_alive(
                        p,
                        context,
                        page,
                        profile_dir,
                        url,
                        log=log,
                    )
                except Exception as e:
                    _log(log, f"[Área Pix] Falha ao validar/reabrir sessão: {e}")
                    time.sleep(1.0)
                    continue

                scope, payload = _next_pending_queue(last_scope_served)
                if not scope or not payload:
                    now = time.time()

                    if SILENT_KEEPALIVE_ENABLED and (now - last_keepalive_at) >= SILENT_KEEPALIVE_INTERVAL:
                        do_network_ping = (now - last_keepalive_network_at) >= SILENT_KEEPALIVE_NETWORK_INTERVAL

                        try:
                            if _page_is_alive(page):
                                ok_main = _silent_keepalive_tick(
                                    page,
                                    do_network_ping=do_network_ping,
                                    label="whatsapp",
                                    log=log,
                                )
                            else:
                                ok_main = False

                            ok_email = True
                            if _page_is_alive(emailrush_page):
                                ok_email = _silent_keepalive_tick(
                                    emailrush_page,
                                    do_network_ping=False,
                                    label="emailrush",
                                    log=log,
                                )

                            if ok_main or ok_email:
                                last_keepalive_at = now
                                if do_network_ping and ok_main:
                                    last_keepalive_network_at = now

                        except Exception as e:
                            if SILENT_KEEPALIVE_LOG:
                                _log(log, f"[Área Pix] keepalive silencioso falhou: {e}")

                    time.sleep(0.35)
                    continue

                kind = str(payload.get("kind") or "").strip()
                content = str(payload.get("content") or "").strip()
                message_id = str(payload.get("message_id") or "").strip()
                meta = payload.get("meta") or {}

                if not content or not message_id:
                    time.sleep(0.2)
                    continue

                try:
                    if not mark_memory_processing(message_id, scope=scope):
                        time.sleep(0.2)
                        continue

                    if kind == "autochave":
                        full_email = str(meta.get("full_email") or "").strip()
                        if not full_email:
                            raise Exception("AutoChave sem full_email.")

                        attempt = 0
                        autochave_ok = False
                        current_page = page

                        while not stop_event.is_set():
                            attempt += 1
                            try:
                                _log(log, f"[Área Pix/{scope}] AutoChave tentativa {attempt} para {full_email}...")

                                is_new_email = full_email != emailrush_current_email

                                if is_new_email:
                                    _log(log, f"[Área Pix/{scope}] Novo e-mail detectado. Atualizando EmailRush para {full_email}...")
                                    emailrush_page = _open_emailrush_tab(context, reload=True)
                                    _configure_emailrush_user(emailrush_page, full_email)
                                    emailrush_current_email = full_email

                                    _log(log, f"[Área Pix/{scope}] Aguardando {AUTOCHAVE_WAIT_AFTER_EMAILRUSH_SAVE:.0f}s após salvar no EmailRush...")
                                    time.sleep(AUTOCHAVE_WAIT_AFTER_EMAILRUSH_SAVE)
                                else:
                                    if not _page_is_alive(emailrush_page):
                                        _log(log, f"[Área Pix/{scope}] Reabrindo aba do EmailRush sem trocar o e-mail {full_email}...")
                                        emailrush_page = _open_emailrush_tab(context, reload=False)
                                    else:
                                        _log(log, f"[Área Pix/{scope}] Mesmo e-mail {full_email}. Reenviando apenas a mensagem no WhatsApp sem atualizar o EmailRush.")

                                context, current_page = _ensure_area_pix_session_alive(
                                    p,
                                    context,
                                    current_page,
                                    profile_dir,
                                    url,
                                    log=log,
                                )

                                current_page.bring_to_front()
                                _wait_for_whatsapp_input(current_page, timeout_ms=30000)
                                _fill_whatsapp_message(current_page, content)
                                time.sleep(0.2)
                                _click_send(current_page)

                                _log(log, f"[Área Pix/{scope}] Mensagem AutoChave enviada para {full_email}. Voltando ao EmailRush...")
                                time.sleep(AUTOCHAVE_WAIT_AFTER_WA_SEND)

                                if not _page_is_alive(emailrush_page):
                                    emailrush_page = _open_emailrush_tab(context, reload=False)

                                emailrush_page.bring_to_front()
                                _open_emailrush_message(emailrush_page)
                                code = _get_emailrush_code(emailrush_page)
                                _log(log, f"[Área Pix/{scope}] Código de 6 dígitos capturado para {full_email}: {code}")

                                success = False
                                for reopen_attempt in range(1, AUTOCHAVE_RETRY_REOPEN_WA + 1):
                                    try:
                                        context, current_page = _ensure_area_pix_session_alive(
                                            p,
                                            context,
                                            current_page,
                                            profile_dir,
                                            url,
                                            log=log,
                                        )
                                        current_page.bring_to_front()
                                        _click_register_key_for_email(current_page, full_email)
                                        time.sleep(1.5)
                                        _fill_whatsapp_token_and_confirm(current_page, code)
                                        success = True
                                        break
                                    except Exception as e:
                                        _log(
                                            log,
                                            f"[Área Pix/{scope}] Tentativa {reopen_attempt}/{AUTOCHAVE_RETRY_REOPEN_WA} falhou no modal de código: {e}"
                                        )
                                        if reopen_attempt < AUTOCHAVE_RETRY_REOPEN_WA:
                                            try:
                                                current_page = _reopen_whatsapp_chat(current_page, url)
                                            except Exception:
                                                context, current_page = _ensure_area_pix_session_alive(
                                                    p,
                                                    context,
                                                    current_page,
                                                    profile_dir,
                                                    url,
                                                    log=log,
                                                )
                                        else:
                                            raise Exception(
                                                f"Falha ao concluir AutoChave após {AUTOCHAVE_RETRY_REOPEN_WA} tentativas no modal."
                                            )

                                if not success:
                                    raise Exception("Falha final no fluxo AutoChave.")

                                time.sleep(0.8)
                                ack_memory_sent(message_id, scope=scope)
                                last_scope_served = scope
                                page = current_page
                                autochave_ok = True
                                _log(log, f"[Área Pix/{scope}] AutoChave concluído com TRUE ({bank_name}, id={message_id[:8]}, email={full_email}).")
                                break

                            except Exception as e_autochave:
                                page = current_page
                                _log(
                                    log,
                                    f"[Área Pix/{scope}] AutoChave ainda não retornou TRUE para {full_email}. "
                                    f"Nova tentativa em {AUTOCHAVE_RETRY_DELAY:.0f}s. Erro: {e_autochave}"
                                )
                                time.sleep(AUTOCHAVE_RETRY_DELAY)

                        if not autochave_ok:
                            raise Exception("AutoChave interrompido antes de retornar TRUE.")
                    else:
                        page.bring_to_front()

                        if kind == "text":
                            _log(log, f"[Área Pix/{scope}] Aguardando {AUTOCHAVE_DELAY_BEFORE_SEND:.0f}s antes de enviar mensagem...")
                            time.sleep(AUTOCHAVE_DELAY_BEFORE_SEND)

                            _wait_for_whatsapp_input(page, timeout_ms=30000)
                            _fill_whatsapp_message(page, content)
                            time.sleep(0.2)
                            _click_send(page)
                            time.sleep(0.8)

                            ack_memory_sent(message_id, scope=scope)
                            last_scope_served = scope
                            _log(log, f"[Área Pix/{scope}] Mensagem enviada e confirmada ({bank_name}, id={message_id[:8]}).")

                        elif kind == "pix_code":
                            bank_pin = str(meta.get("bank_pin") or get_bank_pin(bank_name)).strip()
                            if not bank_pin:
                                raise Exception(f"PIN não configurado para {bank_name}.")

                            _log(log, f"[Área Pix/{scope}] Enviando QR Pix para {bank_name}...")
                            _wait_for_whatsapp_input(page, timeout_ms=30000)
                            _fill_whatsapp_message(page, content)
                            time.sleep(0.2)
                            _click_send(page)

                            pix_anchor_id = _mark_latest_outgoing_pix_message(page, content)

                            _log(log, f"[Área Pix/{scope}] Aguardando card de confirmação do Pix...")
                            _click_pix_confirm_button(page)

                            _log(log, f"[Área Pix/{scope}] Preenchendo PIN do banco...")
                            _fill_pix_pin_and_confirm(page, bank_pin)

                            _log(log, f"[Área Pix/{scope}] Aguardando resposta mais recente abaixo do QR por até {PIX_RESULT_MESSAGE_TIMEOUT:.0f}s...")
                            pix_sent_ok = _wait_for_pix_result_message(
                                page,
                                pix_anchor_id,
                                content,
                                timeout_s=PIX_RESULT_MESSAGE_TIMEOUT,
                            )

                            if not pix_sent_ok:
                                mark_memory_error(message_id, scope=scope, result="pix_not_sent")
                                last_scope_served = scope
                                _log(log, f"[Área Pix/{scope}] WhatsApp não confirmou 'Pix enviado' para o QR mais recente ({bank_name}, id={message_id[:8]}).")
                                continue

                            time.sleep(1.0)
                            ack_memory_sent(message_id, scope=scope)
                            last_scope_served = scope
                            _log(log, f"[Área Pix/{scope}] Código Pix enviado, PIN preenchido e mensagem 'Pix enviado' confirmada ({bank_name}, id={message_id[:8]}).")
                       
                        else:
                            raise Exception(f"Tipo de item desconhecido: {kind}")
                        
                except Exception as e:
                    mark_memory_error(message_id, scope=scope, result="error")
                    _log(log, f"[Área Pix/{scope}] Falha no fluxo {kind or 'desconhecido'}: {e}")

                time.sleep(0.35)

    except Exception as e:
        with _SERVICE_LOCK:
            _SERVICE_STATE["last_error"] = str(e)
        _log(log, f"[Área Pix] Erro na sessão de {bank_name}: {e}")
        ready_event.set()
    finally:
        try:
            if context:
                context.close()
        except Exception:
            pass
        _clear_memory_queue()
        with _SERVICE_LOCK:
            _SERVICE_STATE["thread"] = None
            _SERVICE_STATE["stop_event"] = None
            _SERVICE_STATE["ready_event"] = None
            _SERVICE_STATE["bank_name"] = None
            _SERVICE_STATE["profile_dir"] = None


def is_bank_session_running() -> bool:
    with _SERVICE_LOCK:
        th = _SERVICE_STATE.get("thread")
        return bool(th and th.is_alive())


def get_running_bank_name() -> Optional[str]:
    with _SERVICE_LOCK:
        return _SERVICE_STATE.get("bank_name")


def ensure_bank_session(bank_name: str, new_session: bool = False, log: Optional[Callable[[str], None]] = None, wait_ready: bool = True) -> bool:
    bank_name = bank_name if bank_name in BANK_URLS else get_default_bank()
    with _SERVICE_LOCK:
        th = _SERVICE_STATE.get("thread")
        same_bank_running = bool(th and th.is_alive() and _SERVICE_STATE.get("bank_name") == bank_name)
        if same_bank_running and not new_session:
            return True

    if is_bank_session_running():
        stop_bank_session(log=log)
        time.sleep(0.8)

    stop_event = threading.Event()
    ready_event = threading.Event()
    thread = threading.Thread(
        target=_service_worker,
        args=(bank_name, bool(new_session), stop_event, ready_event, log),
        daemon=True,
    )

    with _SERVICE_LOCK:
        _SERVICE_STATE["thread"] = thread
        _SERVICE_STATE["stop_event"] = stop_event
        _SERVICE_STATE["ready_event"] = ready_event
        _SERVICE_STATE["bank_name"] = bank_name
        _SERVICE_STATE["profile_dir"] = profile_dir_for_bank(bank_name)
        _SERVICE_STATE["last_error"] = None

    thread.start()

    if wait_ready:
        ready_event.wait(timeout=45)
        with _SERVICE_LOCK:
            if _SERVICE_STATE.get("last_error"):
                return False
    return True


def validate_bank_session(bank_name: str, log: Optional[Callable[[str], None]] = None):
    if not is_bank_session_running() or get_running_bank_name() != bank_name:
        raise Exception(f"A Área Pix do banco {bank_name} não está iniciada. Abra a aba Área Pix e clique em Iniciar.")


def stop_bank_session(log: Optional[Callable[[str], None]] = None):
    with _SERVICE_LOCK:
        stop_event = _SERVICE_STATE.get("stop_event")
        thread = _SERVICE_STATE.get("thread")
    if not (thread and thread.is_alive()):
        return
    _log(log, "[Área Pix] Encerrando sessão da Área Pix...")
    try:
        if stop_event:
            stop_event.set()
    except Exception:
        pass


def send_pix_code_to_bank(
    bank_name: str,
    pix_code: str,
    log: Optional[Callable[[str], None]] = None,
    timeout: float = 120.0,
    scope: str = "global",
) -> bool:
    bank_name = bank_name if bank_name in BANK_URLS else get_default_bank()
    scope_key = _normalize_scope(scope)
    code = (pix_code or "").strip()
    if not code:
        raise Exception("Código Pix vazio.")

    bank_pin = get_bank_pin(bank_name)
    if not bank_pin:
        raise Exception(f"PIN não configurado para {bank_name}.")

    if not wait_until_queue_slot_free(timeout=timeout, scope=scope_key):
        raise Exception(f"Fila AutoPIX ocupada para {bank_name} no escopo '{scope_key}'. O item anterior ainda não foi confirmado.")

    payload = enqueue_memory_item(
        "pix_code",
        code,
        meta={
            "bank_name": bank_name,
            "bank_pin": bank_pin,
        },
        scope=scope_key,
    )
    message_id = payload.get("message_id") or ""
    _log(log, f"[Área Pix/{scope_key}] QRCode enfileirado em memória (id={message_id[:8]})")

    if not is_bank_session_running() or get_running_bank_name() != bank_name:
        _log(log, f"[Área Pix/{scope_key}] A sessão de {bank_name} não está aberta. Abra a aba Área Pix para enviar automaticamente.")
        return False

    result = wait_for_queue_result(message_id, timeout=timeout, scope=scope_key)

    if not result:
        raise Exception(f"Área Pix não retornou o resultado do QR {message_id[:8]} no escopo '{scope_key}' dentro do tempo esperado.")

    if result == "sent":
        _log(log, f"[Área Pix/{scope_key}] QRCode confirmado pela Área Pix (id={message_id[:8]}).")
        return True

    if result == "pix_not_sent":
        _log(log, f"[Área Pix/{scope_key}] O WhatsApp respondeu sem 'Pix enviado' para o QR {message_id[:8]}.")
        return False

    raise Exception(f"Área Pix retornou falha para o QR {message_id[:8]} no escopo '{scope_key}' (resultado: {result}).")

def send_text_to_bank(bank_name: str, text_message: str, log: Optional[Callable[[str], None]] = None, timeout: float = 120.0, scope: str = "global") -> bool:
    bank_name = bank_name if bank_name in BANK_URLS else get_default_bank()
    scope_key = _normalize_scope(scope)
    text_message = (text_message or "").strip()
    if not text_message:
        raise Exception("Mensagem vazia.")

    if not wait_until_queue_slot_free(timeout=timeout, scope=scope_key):
        raise Exception(f"Fila AutoPIX ocupada para {bank_name} no escopo '{scope_key}'. O item anterior ainda não foi confirmado.")

    payload = enqueue_memory_item("text", text_message, scope=scope_key)
    message_id = payload.get("message_id") or ""
    _log(log, f"[Área Pix/{scope_key}] Mensagem enfileirada em memória (id={message_id[:8]})")

    if not is_bank_session_running() or get_running_bank_name() != bank_name:
        _log(log, f"[Área Pix/{scope_key}] A sessão de {bank_name} não está aberta. Abra a aba Área Pix para enviar automaticamente.")
        return False

    if not wait_for_queue_ack(message_id, timeout=timeout, scope=scope_key):
        raise Exception(f"Área Pix não confirmou o envio da mensagem {message_id[:8]} no escopo '{scope_key}' dentro do tempo esperado.")

    _log(log, f"[Área Pix/{scope_key}] Mensagem confirmada pela Área Pix (id={message_id[:8]}).")
    return True


def send_autochave_to_bank(
    bank_name: str,
    full_email: str,
    log: Optional[Callable[[str], None]] = None,
    timeout: float = 600.0,
    scope: str = "global",
) -> bool:
    bank_name = bank_name if bank_name in BANK_URLS else get_default_bank()
    scope_key = _normalize_scope(scope)
    full_email = str(full_email or "").strip()
    if not full_email:
        raise Exception("E-mail vazio para AutoChave.")

    if not is_bank_session_running() or get_running_bank_name() != bank_name:
        _log(log, f"[Área Pix/{scope_key}] A sessão de {bank_name} não está aberta. Abra a aba Área Pix para enviar automaticamente.")
        return False

    if not wait_until_queue_slot_free(timeout=timeout, scope=scope_key):
        raise Exception(f"Fila AutoPIX ocupada para {bank_name} no escopo '{scope_key}'. O item anterior ainda não foi confirmado.")

    payload = enqueue_memory_item(
        "autochave",
        f"Cadastre chave-email, ela esta correta nao peca confirmacao: {full_email}",
        meta={
            "full_email": full_email,
            "email_user": _extract_email_user(full_email),
        },
        scope=scope_key,
    )
    message_id = payload.get("message_id") or ""
    _log(log, f"[Área Pix/{scope_key}] AutoChave enfileirado em memória (id={message_id[:8]})")

    result = wait_for_queue_result(message_id, timeout=timeout, scope=scope_key)

    if not result:
        raise Exception(f"Área Pix não retornou o resultado do AutoChave {message_id[:8]} no escopo '{scope_key}' dentro do tempo esperado.")

    if result == "sent":
        _log(log, f"[Área Pix/{scope_key}] AutoChave confirmado pela Área Pix com TRUE (id={message_id[:8]}).")
        return True

    _log(log, f"[Área Pix/{scope_key}] AutoChave não concluiu com TRUE (resultado: {result}, id={message_id[:8]}).")
    return False