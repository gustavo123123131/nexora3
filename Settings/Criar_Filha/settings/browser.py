import json
import random
import re
import string
from dataclasses import dataclass
from typing import Optional, Dict
from urllib.parse import urlparse

# utils agora fornece perfil Android coerente
from .utils import (
    pick_desktop_ua,
    pick_desktop_gpu,
    pick_mobile_ua,
    pick_android_profile,
    load_settings,  # ✅ precisa estar no seu utils.py
)

AREA_PIX_SCOPE = "mae"
LOCAL_PROXY_GATEWAY_HOST = "127.0.0.1"
LOCAL_PROXY_GATEWAY_PORT = 8877
LOCAL_PROXY_GATEWAY_PASSWORD = "cpa-gateway"

def _scope_key(name: str) -> str:
    return f"{AREA_PIX_SCOPE}_{name}"

@dataclass
class ProxyConfig:
    server: str
    username: Optional[str] = None
    password: Optional[str] = None


@dataclass
class ModeConfig:
    mode: str
    user_agent: str
    viewport: dict
    device_scale_factor: float
    is_mobile: bool
    has_touch: bool
    webgl_vendor: str
    webgl_renderer: str
    extra_headers: dict


def _to_webview_ua(android_ua: str) -> str:
    """
    Converte Chrome Mobile Android em UA de WebView (wv + Version/4.0),
    sem inventar versão: reaproveita o Chrome/xxx já presente no UA.
    """
    if "wv" in android_ua:
        return android_ua

    chrome = re.search(r"(Chrome/[\d\.]+)", android_ua)
    chrome_token = chrome.group(1) if chrome else "Chrome/120.0.0.0"

    ua = android_ua.replace(
        "Mobile Safari/537.36",
        f"Version/4.0 {chrome_token} Mobile Safari/537.36"
    )

    # insere "; wv)" antes do fechamento do parêntese
    ua = ua.replace(")", "; wv)")
    return ua

_SMARTPROXY_SESSION_BY_IDX: Dict[str, str] = {}


def _smartproxy_fixed_ip_enabled() -> bool:
    try:
        st = load_settings() or {}
    except Exception:
        st = {}

    return bool(
        st.get(_scope_key("smartproxy_fixed_ip"), st.get("smartproxy_fixed_ip", False))
    )


def _generate_smartproxy_session_id() -> str:
    alphabet = string.ascii_letters + string.digits
    length = random.randint(4, 12)
    return "".join(random.choice(alphabet) for _ in range(length))


def _get_smartproxy_session_for_idx(idx: Optional[int]) -> str:
    key = str(idx) if idx is not None else "__default__"

    if key not in _SMARTPROXY_SESSION_BY_IDX:
        _SMARTPROXY_SESSION_BY_IDX[key] = _generate_smartproxy_session_id()

    return _SMARTPROXY_SESSION_BY_IDX[key]


def _apply_smartproxy_fixed_ip(username: Optional[str], idx: Optional[int] = None) -> Optional[str]:
    username = (username or "").strip()
    if not username:
        return username

    if not _smartproxy_fixed_ip_enabled():
        return username

    # evita duplicar caso já exista life/session antigo
    base = re.sub(r"_life-\d+", "", username)
    base = re.sub(r"_session-[A-Za-z0-9]+", "", base)
    base = re.sub(r"_+", "_", base).strip("_")

    session_id = _get_smartproxy_session_for_idx(idx)
    return f"{base}_life-60_session-{session_id}"

def _viewport_from_settings(idx: Optional[int]) -> Optional[dict]:
    """
    Lê settings e tenta achar viewport para esse idx.
    Aceita:
      - formato antigo: window_bounds_by_idx[idx] = {left, top, width, height}
      - formato novo:   window_bounds_by_idx[idx] = {"bounds": {...}, "viewport": {...}}
    Retorna {"width": w, "height": h} ou None.
    """
    if idx is None:
        return None

    try:
        st = load_settings()
    except Exception:
        return None

    try:
        by_idx = (st or {}).get("window_bounds_by_idx") or {}
        rec = by_idx.get(str(idx))
    except Exception:
        return None

    if not isinstance(rec, dict):
        return None

    # formato novo
    if "viewport" in rec or "bounds" in rec:
        vp = rec.get("viewport")
        if isinstance(vp, dict):
            try:
                w = int(vp.get("width", 0))
                h = int(vp.get("height", 0))
                if w > 50 and h > 50:
                    return {"width": w, "height": h}
            except Exception:
                pass

        b = rec.get("bounds")
        if isinstance(b, dict):
            try:
                w = int(b.get("width", 0))
                h = int(b.get("height", 0))
                if w > 50 and h > 50:
                    return {"width": w, "height": h}
            except Exception:
                pass

        return None

    # formato antigo (dict direto)
    try:
        w = int(rec.get("width", 0))
        h = int(rec.get("height", 0))
        if w > 50 and h > 50:
            return {"width": w, "height": h}
    except Exception:
        return None

    return None


def make_config(mode: str, idx: Optional[int] = None) -> ModeConfig:
    """Build a Chromium browser profile for the selected environment.

    Mobile modes are *browser emulations*. A native Android app and iOS
    Safari are not available in this Playwright Chromium implementation.
    """
    from Settings.device_modes import profile_mode

    mode = profile_mode(mode, idx or 1)

    if mode == "desktop":
        ua = pick_desktop_ua()
        v, r = pick_desktop_gpu()
        cfg = ModeConfig(
            "desktop", ua, {"width": 1366, "height": 768}, 1,
            False, False, v, r, {},
        )

    elif mode == "android":
        ua, v, r = pick_android_profile()
        if "Android" not in ua:
            raise ValueError("Não há User-Agent Android válido nas configurações.")
        cfg = ModeConfig(
            "android", ua, {"width": 412, "height": 915}, 2.75,
            True, True, v, r, {},
        )

    elif mode == "iphone":
        # Never accept Android/iPad in the iPhone-only browser configuration.
        from .utils import load_json, MOBILE_UA_PATH
        items = load_json(MOBILE_UA_PATH).get("userAgents", [])
        iphones = [ua for ua in items if isinstance(ua, str)
                   and "iPhone" in ua and "Android" not in ua]
        ua = random.choice(iphones) if iphones else (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
            "Mobile/15E148 Safari/604.1"
        )
        cfg = ModeConfig(
            "iphone", ua, {"width": 390, "height": 844}, 3,
            True, True, "Apple", "Apple GPU", {},
        )

    else:
        raise ValueError("Modo de dispositivo inválido")

    # Keep the original desktop window-size settings compatible.
    # New mobile profiles use their own viewport to avoid mixing modes.
    if mode == "desktop":
        vp = _viewport_from_settings(idx)
        if isinstance(vp, dict):
            cfg.viewport = vp

    return cfg


def webgl_spoof_script(vendor, renderer):
    return f"""
    (() => {{
        const vendor = {json.dumps(vendor)};
        const renderer = {json.dumps(renderer)};

        const override = (proto) => {{
            if (!proto) return;
            const original = proto.getParameter;

            Object.defineProperty(proto, 'getParameter', {{
                value: function(parameter) {{
                    if (parameter === 37445) return vendor;
                    if (parameter === 37446) return renderer;
                    return original.apply(this, arguments);
                }},
                configurable: true
            }});
        }};

        override(WebGLRenderingContext?.prototype);
        override(WebGL2RenderingContext?.prototype);
    }})();
    """


def get_touch_injector_script():
    return """
    (() => {
      if (window.__TOUCHIFY_INSTALLED__) return;
      window.__TOUCHIFY_INSTALLED__ = true;

      const state = {
        enabled: true,
        activePointers: new Map()
      };

      try {
        const navProto = Navigator.prototype;
        Object.defineProperty(navProto, "maxTouchPoints", {
          configurable: true,
          get() { return 5; }
        });
      } catch (_) {}

      try {
        if (!("ontouchstart" in window)) {
          Object.defineProperty(window, "ontouchstart", {
            configurable: true,
            get() { return null; }
          });
        }
      } catch (_) {}

      function makeTouchFromPointer(pe, target) {
        const touchInit = {
          identifier: pe.pointerId,
          target,
          clientX: pe.clientX, clientY: pe.clientY,
          screenX: pe.screenX, screenY: pe.screenY,
          pageX: pe.pageX, pageY: pe.pageY,
          radiusX: 12, radiusY: 12,
          rotationAngle: 0,
          force: pe.pressure || 0.5
        };
        if (typeof Touch === "function") {
          try { return new Touch(touchInit); } catch (_) {}
        }
        return touchInit;
      }

      function dispatchTouch(type, pe) {
        const target = pe.target || document;
        const touch = makeTouchFromPointer(pe, target);
        const touches = [];
        const changedTouches = [touch];

        for (const [, last] of state.activePointers) {
          touches.push(makeTouchFromPointer(last, last.target || document));
        }

        const finalTouches = (type === "touchend" || type === "touchcancel")
            ? touches.filter(t => t.identifier !== pe.pointerId)
            : touches;

        let ev;
        if (typeof TouchEvent === "function") {
          try {
            ev = new TouchEvent(type, {
              bubbles: true, cancelable: true, composed: true,
              touches: finalTouches, targetTouches: finalTouches, changedTouches
            });
          } catch (_) {}
        }

        if (!ev) {
          ev = new Event(type, { bubbles: true, cancelable: true });
          ev.touches = finalTouches;
          ev.targetTouches = finalTouches;
          ev.changedTouches = changedTouches;
        }
        target.dispatchEvent(ev);
      }

      function onPointerDown(e) {
        if (!state.enabled) return;
        state.activePointers.set(e.pointerId, e);
        dispatchTouch("touchstart", e);
      }
      function onPointerMove(e) {
        if (!state.enabled) return;
        if (!state.activePointers.has(e.pointerId)) return;
        state.activePointers.set(e.pointerId, e);
        dispatchTouch("touchmove", e);
      }
      function onPointerUp(e) {
        if (!state.enabled) return;
        if (!state.activePointers.has(e.pointerId)) return;
        state.activePointers.set(e.pointerId, e);
        dispatchTouch("touchend", e);
        state.activePointers.delete(e.pointerId);
      }
      function onPointerCancel(e) {
        if (!state.enabled) return;
        if (!state.activePointers.has(e.pointerId)) return;
        dispatchTouch("touchcancel", e);
        state.activePointers.delete(e.pointerId);
      }

      window.addEventListener("pointerdown", onPointerDown, true);
      window.addEventListener("pointermove", onPointerMove, true);
      window.addEventListener("pointerup", onPointerUp, true);
      window.addEventListener("pointercancel", onPointerCancel, true);
    })();
    """


def get_app_spoof_script():
    return """
    (() => {
        window.Android = { postMessage: () => {}, jsCall: () => {}, getVersion: () => "1.0.0" };
        window.AppInterface = window.Android;
        window.app = window.Android;
        window.jsBridge = window.Android;

        window.webkit = window.webkit || {};
        window.webkit.messageHandlers = window.webkit.messageHandlers || {};
        window.webkit.messageHandlers.app = { postMessage: () => {} };
        window.webkit.messageHandlers.cordova_native = { postMessage: () => {} };

        Object.defineProperty(window.navigator, 'standalone', { get: () => true });

        const originalMatchMedia = window.matchMedia;
        window.matchMedia = function(query) {
            if (query.includes('display-mode: standalone')) {
                return { matches: true, media: query, onchange: null, addListener: () => {}, removeListener: () => {} };
            }
            return originalMatchMedia(query);
        };
    })();
    """


def parse_proxy_string(raw: str) -> Optional[ProxyConfig]:
    raw = (raw or "").strip()
    if not raw:
        return None

    scheme = "http"
    body = raw

    if "://" in raw:
        scheme, body = raw.split("://", 1)
        scheme = (scheme or "http").strip() or "http"

    # Formato novo: ip:porta:user:senha
    # Ex.: 123.123.123.123:8080:meuuser:minhasenha
    if "@" not in body and body.count(":") >= 3:
        try:
            host, port, username, password = body.split(":", 3)

            host = host.strip()
            port = int(str(port).strip())
            username = username.strip()
            password = password.strip()

            if not host:
                raise ValueError

            return ProxyConfig(
                f"{scheme}://{host}:{port}",
                username or None,
                password or None
            )
        except Exception:
            raise ValueError(f"Proxy inválido: {raw}")

    # Compatibilidade com formato antigo:
    # user:pass@host:port
    # host:port
    raw_for_parse = f"{scheme}://{body}"
    u = urlparse(raw_for_parse)

    if not u.hostname or not u.port:
        raise ValueError(f"Proxy inválido: {raw}")

    return ProxyConfig(
        f"{u.scheme or 'http'}://{u.hostname}:{u.port}",
        u.username,
        u.password
    )

def build_upstream_proxy_dict(p: Optional[ProxyConfig], idx: Optional[int] = None) -> Optional[Dict]:
    if not p:
        return None

    d = {"server": p.server}

    username = _apply_smartproxy_fixed_ip(p.username, idx=idx)

    if username:
        d["username"] = username

    if p.password:
        d["password"] = p.password

    return d

def build_proxy_dict(p: Optional[ProxyConfig], idx: Optional[int] = None) -> Optional[Dict]:
    if not p:
        return None

    local_idx = int(idx or 0)

    return {
        "server": f"http://{LOCAL_PROXY_GATEWAY_HOST}:{LOCAL_PROXY_GATEWAY_PORT}",
        "username": f"idx-{local_idx}",
        "password": LOCAL_PROXY_GATEWAY_PASSWORD,
    }

