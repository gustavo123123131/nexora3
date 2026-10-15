import threading
import shutil
import time
from pathlib import Path
from typing import List, Optional, Callable
from urllib.parse import urlparse
import json
import urllib.request
import urllib.error
import tkinter.messagebox as tkmb
import tkinter.simpledialog as tksd
import customtkinter as ctk
from Settings.device_modes import MODE_LABELS, mode_label, resolve_mode, profile_directory
from Settings.url_normalizer import normalize_url_list, is_https_url, HttpsNavigationError
from Settings.mobile_window import mobile_window_args, fit_mobile_content
from Settings.speed_controller import SpeedController
from Settings.window_pos_controller import WindowPosController
from Settings.mirror_easy import MirrorEasy
from playwright.sync_api import sync_playwright
from Settings.Criar_Filha.settings.popup import sweep_popups_if_enabled
from Settings.Criar_Filha.settings.utils import BASE_DIR, PROFILES_DIR, load_settings, write_settings
from Settings.Criar_Filha.settings.browser import (
    make_config,
    webgl_spoof_script,
    get_touch_injector_script,
    get_app_spoof_script,
    parse_proxy_string,
    build_proxy_dict,
    build_upstream_proxy_dict,
    ProxyConfig,
)
from Settings.Criar_Filha.settings.withdrawal import verify_phone_now
from Settings.Criar_Filha.settings.withdrawal import start_withdrawal
from Settings.withdraw_controller import WithdrawController
from Settings.Criar_Filha.settings.games import search_and_open_game
from Settings.Area_Pix.bank_utils import BANK_URLS, ensure_bank_session, get_default_bank

AREA_PIX_SCOPE = "filha"
QUICK_GAME_BUTTONS_COUNT = 6
DEFAULT_WITHDRAW_PIN = "123455"


def _scope_key(name: str) -> str:
    return f"{AREA_PIX_SCOPE}_{name}"


def _clean_withdraw_pin(value: str, default: str = DEFAULT_WITHDRAW_PIN) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits or str(default or DEFAULT_WITHDRAW_PIN)


def _get_saved_withdraw_pin(default: str = DEFAULT_WITHDRAW_PIN) -> str:
    try:
        st = load_settings()
    except Exception:
        st = {}

    return _clean_withdraw_pin(
        st.get(_scope_key("withdraw_pin"), st.get("withdraw_pin", default)),
        default=default,
    )

def _scope_key(name: str) -> str:
    return f"{AREA_PIX_SCOPE}_{name}"


class GameLaunchRequest:
    def __init__(self, version: int = 0, game_name: str = "", game_id: str = ""):
        self.version = int(version)
        self.game_name = game_name or ""
        self.game_id = str(game_id or "")


class VerifyPhoneController:
    def __init__(self):
        self._lock = threading.Lock()
        self._version = 0

    def request(self) -> int:
        with self._lock:
            self._version += 1
            return self._version

    def get_version(self) -> int:
        with self._lock:
            return self._version

    def reset(self):
        with self._lock:
            self._version = 0


class GameLaunchController:
    def __init__(self):
        self._lock = threading.Lock()
        self._version = 0
        self._game_name = ""
        self._game_id = ""

    def request(self, game_name: str, game_id: str) -> int:
        with self._lock:
            self._version += 1
            self._game_name = (game_name or "").strip()
            self._game_id = str(game_id or "").strip()
            return self._version

    def get(self) -> GameLaunchRequest:
        with self._lock:
            return GameLaunchRequest(
                version=self._version,
                game_name=self._game_name,
                game_id=self._game_id,
            )

    def reset(self):
        with self._lock:
            self._version = 0
            self._game_name = ""
            self._game_id = ""

class InstanceTargetController:
    def __init__(self):
        self._lock = threading.Lock()
        self._apply_all = True
        self._targets = set()

    def set(self, apply_all: bool, targets=None):
        with self._lock:
            self._apply_all = bool(apply_all)
            self._targets = {int(x) for x in (targets or []) if int(x) > 0}

    def get(self):
        with self._lock:
            return {
                "apply_all": self._apply_all,
                "targets": set(self._targets),
            }

    def matches(self, idx: int) -> bool:
        with self._lock:
            return self._apply_all or int(idx) in self._targets

    def summary(self) -> str:
        st = self.get()
        if st["apply_all"]:
            return "Geral"
        ordered = sorted(st["targets"])
        return ", ".join(f"#{i}" for i in ordered) if ordered else "Geral"

# =====================================================
# Paths (ancorados na raiz do projeto)
# =====================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent
INVITE_PATH = PROJECT_ROOT / "convite.json"
PIX_PATH = PROJECT_ROOT / "extensao_pix" / "chavepix.txt"


# =====================================================
# Helpers / Arquivos
# =====================================================

_SETTINGS_LOCK = threading.Lock()


def _update_settings_atomic(patch_fn) -> bool:
    """
    Read-modify-write com lock (evita sobrescrever configs quando várias telas salvam ao mesmo tempo).
    patch_fn(st: dict) deve modificar st IN-PLACE.
    """
    with _SETTINGS_LOCK:
        st = load_settings()
        if not isinstance(st, dict):
            st = {}

        try:
            patch_fn(st)
        except Exception:
            return False

        write_settings(st)
        return True


def ensure_clean_profile_dir(profile_dir: Path, keep: bool):
    profile_dir.parent.mkdir(parents=True, exist_ok=True)
    if profile_dir.exists() and not keep:
        shutil.rmtree(profile_dir, ignore_errors=True)
    profile_dir.mkdir(parents=True, exist_ok=True)

def clear_pix_file(log: Optional[Callable[[str], None]] = None):
    PIX_PATH.parent.mkdir(parents=True, exist_ok=True)
    PIX_PATH.write_text("", encoding="utf-8")
    if log:
        log(f"[+] chavepix.txt limpo: {PIX_PATH}")


def _default_quick_game_buttons() -> List[dict]:
    return [{"name": "", "id": ""} for _ in range(QUICK_GAME_BUTTONS_COUNT)]


def _normalize_quick_game_buttons(raw) -> List[dict]:
    items = raw if isinstance(raw, list) else []
    normalized: List[dict] = []

    for item in items[:QUICK_GAME_BUTTONS_COUNT]:
        if isinstance(item, dict):
            name = (item.get("name") or "").strip()
            game_id = str(item.get("id") or "").strip()
        else:
            name = ""
            game_id = ""
        normalized.append({"name": name, "id": game_id})

    while len(normalized) < QUICK_GAME_BUTTONS_COUNT:
        normalized.append({"name": "", "id": ""})

    return normalized


def discover_extensions(base_dir: Path) -> List[Path]:
    # base_dir é .../Criar_Filha/settings; extensões ficam em .../Criar_Filha/extensao_*
    root_dir = base_dir.parent
    ex: List[Path] = []
    for p in sorted(root_dir.glob("extensao_*")):
        if p.is_dir():
            ex.append(p.resolve())
    return ex


def _cdp_get_window_bounds(page) -> Optional[dict]:
    """
    Retorna bounds atuais da janela do Chromium (left, top, width, height).
    """
    try:
        ctx = page.context
        cdp = ctx.new_cdp_session(page)

        # 1) tenta sem targetId (funciona em muitos casos)
        try:
            win = cdp.send("Browser.getWindowForTarget", {})
        except Exception:
            # 2) fallback com targetId
            tinfo = cdp.send("Target.getTargetInfo", {})
            target_id = (tinfo or {}).get("targetInfo", {}).get("targetId")
            if not target_id:
                return None
            win = cdp.send("Browser.getWindowForTarget", {"targetId": target_id})

        bounds = (win or {}).get("bounds") or {}
        return {
            "left": int(bounds.get("left", 0)),
            "top": int(bounds.get("top", 0)),
            "width": int(bounds.get("width", 0)),
            "height": int(bounds.get("height", 0)),
        }
    except Exception:
        return None


def _stable_viewport(page, samples: int = 7, delay: float = 0.10) -> Optional[dict]:
    """
    Lê window.innerWidth/innerHeight várias vezes e retorna a mediana.
    Evita salvar viewport errado quando barras/overlays mudam o layout.
    """
    vals = []
    for _ in range(max(3, int(samples))):
        try:
            vp = page.evaluate("() => ({ width: window.innerWidth, height: window.innerHeight })")
        except Exception:
            vp = None

        if isinstance(vp, dict):
            try:
                w = int(vp.get("width", 0))
                h = int(vp.get("height", 0))
                if w > 50 and h > 50:
                    vals.append({"width": w, "height": h})
            except Exception:
                pass

        time.sleep(max(0.05, float(delay)))

    if not vals:
        return None

    def med(key: str) -> int:
        arr = sorted(v[key] for v in vals)
        return arr[len(arr) // 2]

    return {"width": med("width"), "height": med("height")}


def _stable_window_bounds(page, samples: int = 5, delay: float = 0.12) -> Optional[dict]:
    """
    Lê bounds várias vezes e retorna a mediana (evita salvar tamanhos irregulares).
    """
    vals = []
    for _ in range(max(3, int(samples))):
        b = _cdp_get_window_bounds(page)
        if isinstance(b, dict) and int(b.get("width", 0)) > 50 and int(b.get("height", 0)) > 50:
            vals.append({
                "left": int(b.get("left", 0)),
                "top": int(b.get("top", 0)),
                "width": int(b.get("width", 0)),
                "height": int(b.get("height", 0)),
            })
        time.sleep(max(0.05, float(delay)))

    if not vals:
        return None

    def med(key: str) -> int:
        arr = sorted(v[key] for v in vals)
        return arr[len(arr) // 2]

    return {
        "left": med("left"),
        "top": med("top"),
        "width": med("width"),
        "height": med("height"),
    }


# =====================================================
# Speed control (sem Date.now e sem media.playbackRate)
# =====================================================

def speed_init_script(enabled: bool, speed: float) -> str:
    try:
        speed_f = float(speed)
    except Exception:
        speed_f = 1.0

    if not enabled:
        speed_f = 1.0

    perf_now = "true" if enabled else "false"
    set_timeout = "true" if enabled else "false"
    set_interval = "true" if enabled else "false"
    raf = "true" if enabled else "false"

    return f"""
(() => {{
  const CHANNEL = "__UHSH__";

  const DEFAULTS = {{
    speed: {speed_f},
    perfNow: {perf_now},
    dateNow: false,
    setTimeout: {set_timeout},
    setInterval: {set_interval},
    raf: {raf},
    mediaRate: false,
    injectIframes: true
  }};

  function installHooks(cfg) {{
    if (window.__UHSH_INSTALLED__) {{
      window.__UHSH_applyPatch__?.(cfg || {{}});
      return;
    }}
    window.__UHSH_INSTALLED__ = true;

    let C = {{ ...(cfg || {{}}) }};

    const realPerfNow = performance.now.bind(performance);
    const realDateNow = Date.now.bind(Date);
    const realSetTimeout = window.setTimeout.bind(window);
    const realSetInterval = window.setInterval.bind(window);
    const realRAF = window.requestAnimationFrame.bind(window);

    const clamp = (n, a, b) => Math.min(b, Math.max(a, n));
    const asNum = (x, fb) => (Number.isFinite(Number(x)) ? Number(x) : fb);

    let baseRealPerf = realPerfNow();
    let baseVirtualPerf = baseRealPerf;

    let baseRealDate = realDateNow();
    let baseVirtualDate = baseRealDate;

    function speed() {{
      return clamp(asNum(C.speed, 1.0), 0.01, 100);
    }}

    function freeze() {{
      const s = speed();
      const rp = realPerfNow();
      const rd = realDateNow();

      baseVirtualPerf = baseVirtualPerf + (rp - baseRealPerf) * s;
      baseRealPerf = rp;

      baseVirtualDate = baseVirtualDate + (rd - baseRealDate) * s;
      baseRealDate = rd;
    }}

    function applyPatch(patch) {{
      freeze();
      C = {{ ...C, ...(patch || {{}}) }};
    }}
    window.__UHSH_applyPatch__ = applyPatch;
    window.__UHSH_installHooks__ = installHooks;

    try {{
      const hookedPerfNow = function () {{
        if (!C.perfNow) return realPerfNow();
        const rp = realPerfNow();
        return baseVirtualPerf + (rp - baseRealPerf) * speed();
      }};

      const d1 = Object.getOwnPropertyDescriptor(performance, "now");
      if (d1?.configurable) {{
        Object.defineProperty(performance, "now", {{ configurable: true, value: hookedPerfNow }});
      }} else {{
        const proto = performance?.constructor?.prototype;
        const d2 = proto && Object.getOwnPropertyDescriptor(proto, "now");
        if (d2?.configurable) {{
          Object.defineProperty(proto, "now", {{ configurable: true, value: hookedPerfNow }});
        }} else {{
          performance.now = hookedPerfNow;
        }}
      }}
    }} catch {{}}

    try {{
      Date.now = function () {{
        if (!C.dateNow) return realDateNow();
        const rd = realDateNow();
        return baseVirtualDate + (rd - baseRealDate) * speed();
      }};
    }} catch {{}}

    const scaleDelay = (d) => asNum(d, 0) / speed();

    try {{
      window.setTimeout = function (fn, delay, ...rest) {{
        if (!C.setTimeout) return realSetTimeout(fn, delay, ...rest);
        return realSetTimeout(fn, scaleDelay(delay), ...rest);
      }};
    }} catch {{}}

    try {{
      window.setInterval = function (fn, delay, ...rest) {{
        if (!C.setInterval) return realSetInterval(fn, delay, ...rest);
        return realSetInterval(fn, scaleDelay(delay), ...rest);
      }};
    }} catch {{}}

    try {{
      window.requestAnimationFrame = function (cb) {{
        if (!C.raf) return realRAF(cb);
        return realRAF(() => cb(performance.now()));
      }};
    }} catch {{}}

    window.addEventListener("message", (ev) => {{
      if (ev.source !== window) return;
      const msg = ev.data;
      if (!msg || msg.channel !== CHANNEL) return;
      if (msg.type === "SET_CONFIG" || msg.type === "PATCH_CONFIG") {{
        applyPatch(msg.cfg || {{}});
      }}
    }});
  }}

  try {{
    installHooks(DEFAULTS);
  }} catch {{}}
}})();
"""


def _uhsh_cfg_from_state(enabled: bool, speed: float) -> dict:
    return {
        "speed": float(speed),
        "perfNow": bool(enabled),
        "dateNow": False,
        "setTimeout": bool(enabled),
        "setInterval": bool(enabled),
        "raf": bool(enabled),
        "mediaRate": False,
        "injectIframes": bool(enabled),
    }


def _apply_speed_to_page_and_frames(page, enabled: bool, speed: float):
    if not enabled:
        return

    cfg = _uhsh_cfg_from_state(True, speed)

    try:
        if not page.evaluate("() => !!window.__UHSH_INSTALLED__"):
            page.evaluate(speed_init_script(True, speed))
    except Exception:
        try:
            page.evaluate(speed_init_script(True, speed))
        except Exception:
            pass

    try:
        page.evaluate(
            """(cfg) => {
                try {
                    if (window.__UHSH_applyPatch__) window.__UHSH_applyPatch__(cfg);
                    else if (window.__UHSH_installHooks__) window.__UHSH_installHooks__(cfg);
                    else window.postMessage({ channel: "__UHSH__", type: "PATCH_CONFIG", cfg }, "*");
                } catch (e) {}
            }""",
            cfg,
        )
    except Exception:
        pass

    try:
        for fr in page.frames:
            try:
                u = fr.url or ""
                if not (u.startswith("http://") or u.startswith("https://")):
                    continue

                try:
                    if not fr.evaluate("() => !!window.__UHSH_INSTALLED__"):
                        fr.evaluate(speed_init_script(True, speed))
                except Exception:
                    try:
                        fr.evaluate(speed_init_script(True, speed))
                    except Exception:
                        pass

                fr.evaluate(
                    """(cfg) => {
                        try {
                            if (window.__UHSH_applyPatch__) window.__UHSH_applyPatch__(cfg);
                            else if (window.__UHSH_installHooks__) window.__UHSH_installHooks__(cfg);
                            else window.postMessage({ channel: "__UHSH__", type: "PATCH_CONFIG", cfg }, "*");
                        } catch (e) {}
                    }""",
                    cfg,
                )
            except Exception:
                pass
    except Exception:
        pass

def _idle_wait_with_speed(
    page,
    stop_event: threading.Event,
    winpos_controller: WindowPosController,
    speed_controller: SpeedController,
    mirror: MirrorEasy,
    withdraw_controller: WithdrawController,
    verify_phone_controller: VerifyPhoneController,
    game_launch_controller: GameLaunchController,
    instance_target_controller: InstanceTargetController,
    log: Callable[[str], None],
    idx: int,
    shared_phone_key: str = "",
):
    last_version = -1

    last_heartbeat = 0.0
    HEARTBEAT_EVERY = 8.0

    last_popup_sweep = 0.0
    POPUP_SWEEP_EVERY = 0.8

    current_speed_enabled = False

    # cada clique no botão cria uma versão nova; cada idx salva 1x por versão
    last_saved_pos_version = -1

    # Inicia sabendo qual é a versão atual, para ignorar cliques de sessões anteriores
    last_withdraw_version = withdraw_controller.get_version()
    last_game_launch_version = game_launch_controller.get().version
    last_verify_phone_version = verify_phone_controller.get_version()
    while not stop_event.is_set():
        # ---- salvar posição individual por idx (1x por clique) ----
        try:
            v = winpos_controller.get_version()
            if v > 0 and v != last_saved_pos_version:
                if instance_target_controller.matches(idx):
                    b = _stable_window_bounds(page, samples=5, delay=0.12)
                    vp = _stable_viewport(page, samples=7, delay=0.10)

                    if b:
                        def _patch(st):
                            by_idx = st.get("window_bounds_by_idx")
                            if not isinstance(by_idx, dict):
                                by_idx = {}

                            by_idx[str(idx)] = {
                                "bounds": b,
                                "viewport": vp,
                            }
                            st["window_bounds_by_idx"] = by_idx

                        ok = _update_settings_atomic(_patch)
                        if ok:
                            log(f"[#{idx}] 🧷 Posição salva (idx {idx}): bounds={b} viewport={vp}")
                        else:
                            log(f"[#{idx}] ⚠️ Falha ao salvar settings (atomic).")
                    else:
                        log(f"[#{idx}] ⚠️ Não consegui capturar posição estável da janela.")

                last_saved_pos_version = v
        except Exception:
            pass
        # ---- iniciar saque por idx (1x por clique) ----
        try:
            v = withdraw_controller.get_version()
            if v != last_withdraw_version:
                if v > 0 and instance_target_controller.matches(idx):
                    log(f"[#{idx}] 💸 Iniciando saque...")
                    try:
                        withdraw_pin = _get_saved_withdraw_pin()
                        start_withdrawal(page, idx, pin=withdraw_pin, log=log)

                        log(f"[#{idx}] ✅ Saque iniciado.")
                    except Exception as e:
                        log(f"[#{idx}] ❌ Falha ao iniciar saque: {e}")
                last_withdraw_version = v
        except Exception:
            pass

        # ---- verificar número por idx (1x por clique) ----
        try:
            v = verify_phone_controller.get_version()
            if v != last_verify_phone_version:
                if v > 0 and instance_target_controller.matches(idx):
                    if callable(verify_phone_now):
                        log(f"[#{idx}] 📱 Iniciando verificação de número...")
                        try:
                            ok = verify_phone_now(page, idx, shared_key=shared_phone_key)
                            if ok:
                                log(f"[#{idx}] ✅ Verificação de número concluída.")
                            else:
                                log(f"[#{idx}] ⚠️ Verificação de número não concluiu após 10 tentativas. Fluxo encerrado sem bloquear a automação.")
                        except Exception as e:
                            log(f"[#{idx}] ❌ Falha ao verificar número: {e}")
                    else:
                        log(f"[#{idx}] ❌ Função de verificar número indisponível.")
                last_verify_phone_version = v
        except Exception:
            pass

        # ---- abrir jogo por idx (1x por clique) ----
        try:
            launch_req = game_launch_controller.get()
            if launch_req.version != last_game_launch_version:
                if launch_req.version > 0 and instance_target_controller.matches(idx):
                    game_name = (launch_req.game_name or "").strip()
                    game_id = str(launch_req.game_id or "").strip()
                    if game_name and game_id:
                        log(f"[#{idx}] 🎮 Abrindo jogo: {game_name} ({game_id})...")
                        try:
                            search_and_open_game(page, idx, game_name, game_id, log=log)
                            log(f"[#{idx}] ✅ Jogo aberto: {game_name} ({game_id}).")
                        except Exception as e:
                            log(f"[#{idx}] ❌ Falha ao abrir jogo {game_name} ({game_id}): {e}")
                last_game_launch_version = launch_req.version
        except Exception:
            pass

        # Mirror
        try:
            mirror_state = mirror.get()
        except Exception:
            mirror_state = None

        # Mirror (sempre roda: atualiza flags + drena backlog)
        try:
            mirror.tick(idx, page)
        except Exception:
            pass

        # Speed
        try:
            st = speed_controller.get()
            if st.version != last_version:
                last_version = st.version
                if instance_target_controller.matches(idx):
                    _apply_speed_to_page_and_frames(page, st.enabled, float(st.speed))
                    log(f"[#{idx}] ⚡ Speed {'ON' if st.enabled else 'OFF'} | x{st.speed}")
        except Exception:
            pass
        # Popup sweep
        try:
            now = time.time()
            if now - last_popup_sweep >= POPUP_SWEEP_EVERY:
                last_popup_sweep = now
                sweep_popups_if_enabled(page, idx)
        except Exception:
            pass

        # Heartbeat
        try:
            now = time.time()
            if now - last_heartbeat >= HEARTBEAT_EVERY:
                last_heartbeat = now
                page.evaluate("()=>0")
        except Exception:
            pass

        try:
            if mirror_state and mirror_state.enabled:
                time.sleep(0.02)
            else:
                time.sleep(0.12)
        except Exception:
            time.sleep(0.12)


def overlay_init_script(idx: int, ip_value: str = "") -> str:
    ip_js = repr(ip_value or "")
    return f"""
(() => {{
  const Z = "2147483647";
  const NEXT_IP = {ip_js};

  window.__CPA_OVERLAY_STATE__ = window.__CPA_OVERLAY_STATE__ || {{ idx: "", ip: "" }};
  const state = window.__CPA_OVERLAY_STATE__;
  state.idx = "#{idx}";

  if (typeof state.ip !== "string") {{
    state.ip = "";
  }}
  if (typeof NEXT_IP === "string" && NEXT_IP.trim() !== "") {{
    state.ip = NEXT_IP.trim();
  }}

  const ensureBox = (id) => {{
    let el = document.getElementById(id);
    if (!el) {{
      el = document.createElement("div");
      el.id = id;
      document.documentElement.appendChild(el);
    }} else {{
      if (el.parentNode !== document.documentElement) {{
        try {{ document.documentElement.appendChild(el); }} catch {{}}
      }} else {{
        try {{ document.documentElement.appendChild(el); }} catch {{}}
      }}
    }}

    el.style.position = "fixed";
    el.style.zIndex = Z;
    el.style.fontFamily = "Arial, sans-serif";
    el.style.fontSize = "16px";
    el.style.padding = "10px 12px";
    el.style.borderRadius = "12px";
    el.style.background = "rgba(0,0,0,0.18)";
    el.style.color = "rgba(255,255,255,0.85)";
    el.style.backdropFilter = "blur(2px)";
    el.style.pointerEvents = "none";
    el.style.userSelect = "none";
    return el;
  }};

  const render = () => {{
    const tag = ensureBox("__CPA_IDX__");
    tag.style.right = "10px";
    tag.style.bottom = "10px";
    tag.textContent = state.idx || "#{idx}";

    const ipBox = ensureBox("__CPA_IP__");
    ipBox.style.right = "10px";
    ipBox.style.top = "25px";

    if (state.ip && state.ip.trim() !== "") {{
      ipBox.textContent = "IP: " + state.ip;
    }} else if (!ipBox.textContent || ipBox.textContent.trim() === "") {{
      ipBox.textContent = "IP: ...";
    }}
  }};

  window.__CPA_RENDER_OVERLAY__ = render;
  window.__CPA_SET_OVERLAY_IP__ = (ip) => {{
    try {{
      state.ip = (typeof ip === "string" ? ip : String(ip || "")).trim();
    }} catch {{
      state.ip = "";
    }}
    render();
  }};

  render();

  if (!window.__CPA_OVERLAY_WATCHDOG__) {{
    window.__CPA_OVERLAY_WATCHDOG__ = true;
    setInterval(() => {{
      try {{
        render();
      }} catch {{}}
    }}, 1000);
  }}
}})();
"""

# =====================================================
# Playwright core (com stop_event) + log_fn
# =====================================================

def devtools_device_toggle_fix(context, page, width: int, height: int, dpr: float = 1.0):
    try:
        cdp = context.new_cdp_session(page)

        try:
            cdp.send("Emulation.setDeviceMetricsOverride", {
                "width": int(width),
                "height": int(height),
                "deviceScaleFactor": float(dpr) if dpr else 1.0,
                "mobile": True,
            })
        except Exception:
            pass

        try:
            time.sleep(0.12)
        except Exception:
            pass

        try:
            cdp.send("Emulation.clearDeviceMetricsOverride")
        except Exception:
            pass

        try:
            page.evaluate("() => { try { window.dispatchEvent(new Event('resize')); } catch {} }")
        except Exception:
            pass
        try:
            page.evaluate("() => { try { window.visualViewport && window.visualViewport.dispatchEvent(new Event('resize')); } catch {} }")
        except Exception:
            pass

    except Exception:
        pass


def _proxy_url_from_playwright(proxy_dict: dict) -> str:
    server = (proxy_dict or {}).get("server") or ""
    user = (proxy_dict or {}).get("username") or ""
    pwd = (proxy_dict or {}).get("password") or ""
    if not server:
        return ""
    if user:
        try:
            scheme, rest = server.split("://", 1)
        except ValueError:
            scheme, rest = "http", server
        return f"{scheme}://{user}:{pwd}@{rest}"
    return server


def _build_instance_plans(urls: List[str], count_per_url: int, use_same_identity: bool = False) -> List[dict]:
    """Allocate browser windows only; never generate or store account details."""
    return [
        {"idx": idx, "url": url}
        for idx, url in enumerate(
            (url for url in urls for _ in range(count_per_url)), start=1
        )
    ]


def fetch_public_ip(proxy_dict: dict | None, timeout: float = 6.0) -> str:
    endpoints = [
        "https://api.ipify.org?format=json",
        "https://ifconfig.co/json",
    ]
    proxy_url = _proxy_url_from_playwright(proxy_dict or {})
    handlers = []
    if proxy_url:
        handlers.append(urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url}))
    opener = urllib.request.build_opener(*handlers)

    for url in endpoints:
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Accept": "application/json,text/plain,*/*",
                },
            )
            with opener.open(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="ignore").strip()

            try:
                j = json.loads(raw)
                ip = (j or {}).get("ip") or ""
                if ip:
                    return str(ip)
            except Exception:
                pass

            if raw and "." in raw and len(raw) < 80:
                return raw

        except (urllib.error.URLError, TimeoutError, Exception):
            continue

    return ""


def fetch_public_ip_via_context(context, timeout_ms: float = 6000) -> str:
    request_ctx = getattr(context, "request", None)
    if request_ctx is None:
        return ""

    endpoints = [
        "https://api.ipify.org?format=json",
        "https://ifconfig.co/json",
        "https://api.ipify.org",
    ]

    for url in endpoints:
        try:
            resp = request_ctx.get(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Accept": "application/json,text/plain,*/*",
                },
                timeout=float(timeout_ms),
                fail_on_status_code=False,
            )

            raw = ""
            try:
                data = resp.json()
                ip = (data or {}).get("ip") or ""
                if ip:
                    return str(ip)
            except Exception:
                try:
                    raw = resp.text().strip()
                except Exception:
                    raw = ""

            if raw:
                try:
                    data = json.loads(raw)
                    ip = (data or {}).get("ip") or ""
                    if ip:
                        return str(ip)
                except Exception:
                    pass

                if "." in raw and len(raw) < 80:
                    return raw

        except Exception:
            continue

    return ""


def run_instance(
    idx: int,
    url: str,
    mode: str,
    headless: bool,
    proxy: Optional[ProxyConfig],
    ext_paths: List[Path],
    auto_goto: bool,
    keep_profiles: bool,
    run_auto: bool,
    start_game_enabled: bool,
    start_game_name: str,
    start_game_id: str,
    stop_event: threading.Event,
    winpos_controller: WindowPosController,
    speed_controller: SpeedController,
    mirror: MirrorEasy,
    withdraw_controller: WithdrawController,
    verify_phone_controller: VerifyPhoneController,
    game_launch_controller: GameLaunchController,
    instance_target_controller: InstanceTargetController,
    instance_plan: Optional[dict],
    log: Callable[[str], None],
    automation_callback: Optional[Callable] = None,
):
    MAX_BROWSER_RESTARTS = 3

    shared_phone_key = ""
    try:
        shared_phone_key = str((instance_plan or {}).get("shared_identity_key") or "").strip()
    except Exception:
        shared_phone_key = ""

    for attempt in range(1, MAX_BROWSER_RESTARTS + 1):
        if stop_event.is_set():
            log(f"[#{idx}] ⏹️ Parado antes de iniciar.")
            return

        cfg = make_config(mode, idx)
        log(f"[#{idx}] Dispositivo selecionado: {cfg.mode} (navegador Chromium).")
        # Keep the existing local HTTP gateway (including per-instance idx).
        # HTTPS/SOCKS5 upstreams use Playwright's native proxy configuration.
        proxy_dict = (
            build_upstream_proxy_dict(proxy, idx=idx)
            if proxy and proxy.server.split(":", 1)[0] in ("https", "socks5")
            else build_proxy_dict(proxy, idx=idx) if proxy else None
        )

        log(f"[#{idx}] Iniciando navegador (ciclo {attempt}/{MAX_BROWSER_RESTARTS}).")

        user_data_dir = profile_directory(PROFILES_DIR, mode, idx)
        ensure_clean_profile_dir(user_data_dir, keep=keep_profiles)

        # --- abre o Chrome já na posição/tamanho salvo (por idx) ---
        saved_bounds = None
        try:
            st_ui = load_settings()
            by_idx = (st_ui.get("window_bounds_by_idx") or {}) if cfg.mode == "desktop" else {}
            rec = by_idx.get(str(idx))

            # compat: se estiver no formato antigo (dict direto)
            if isinstance(rec, dict) and "bounds" not in rec and "viewport" not in rec:
                saved_bounds = rec
            elif isinstance(rec, dict):
                b = rec.get("bounds")
                if isinstance(b, dict):
                    saved_bounds = b
        except Exception:
            saved_bounds = None

        saved_vp = None
        try:
            st_ui = load_settings()
            by_idx = (st_ui.get("window_bounds_by_idx") or {}) if cfg.mode == "desktop" else {}
            rec = by_idx.get(str(idx))

            if isinstance(rec, dict) and isinstance(rec.get("viewport"), dict):
                vp = rec["viewport"]
                saved_vp = {"width": int(vp.get("width", 0)), "height": int(vp.get("height", 0))}
        except Exception:
            saved_vp = None

        args = [
            "--disable-blink-features=AutomationControlled",
            "--high-dpi-support=1",
            "--force-device-scale-factor=0.5",
        ]

        # Presentation only: legacy 0.5 Chrome scale makes mobile content
        # appear half-width inside a normal Windows window. Keep the
        # Android/iPhone CSS viewport, DPR, profile and touch unchanged.
        if cfg.mode in ("android", "iphone"):
            args = [a for a in args if not a.startswith("--force-device-scale-factor=")]
            if not headless or ext_paths:
                args.extend(mobile_window_args(cfg.viewport, idx=idx))

        if isinstance(saved_bounds, dict) and saved_bounds:
            try:
                x = int(saved_bounds.get("left", 0))
                y = int(saved_bounds.get("top", 0))
                w = int(saved_bounds.get("width", 0))
                h = int(saved_bounds.get("height", 0))
                if w > 50 and h > 50:
                    args += [
                        f"--window-position={x},{y}",
                        f"--window-size={w},{h}",
                    ]
            except Exception:
                pass

        if ext_paths:
            joined = ",".join(str(p) for p in ext_paths)
            args += [f"--disable-extensions-except={joined}", f"--load-extension={joined}"]

        effective_headless = False if ext_paths else headless
        success_finished = False

        try:
            with sync_playwright() as p:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=str(user_data_dir),
                    headless=effective_headless,
                    proxy=proxy_dict,
                    args=args,
                    user_agent=cfg.user_agent,
                    viewport=(
                        saved_vp
                        if (
                            isinstance(saved_vp, dict)
                            and int(saved_vp.get("width", 0)) > 50
                            and int(saved_vp.get("height", 0)) > 50
                        )
                        else cfg.viewport
                    ),
                    device_scale_factor=cfg.device_scale_factor,
                    is_mobile=cfg.is_mobile,
                    has_touch=cfg.has_touch,
                )

                context.add_init_script(webgl_spoof_script(cfg.webgl_vendor, cfg.webgl_renderer))

                if cfg.has_touch:
                    context.add_init_script(get_touch_injector_script())

                if mode == "app":
                    context.add_init_script(get_app_spoof_script())

                st0 = speed_controller.get()
                if st0.enabled:
                    context.add_init_script(speed_init_script(True, st0.speed))

                context.add_init_script(overlay_init_script(idx, ""))

                if cfg.extra_headers:
                    context.set_extra_http_headers(cfg.extra_headers)

                page = context.pages[0] if context.pages else context.new_page()


                # Match the native window to the emulated phone independently
                # from the site's CSS viewport. No DOM/CSS edits or zoom.
                mobile_presentation_session = None
                if cfg.mode in ("android", "iphone") and not effective_headless:
                    try:
                        presentation, mobile_presentation_session = fit_mobile_content(
                            context, page, cfg.viewport, cfg.device_scale_factor, idx=idx
                        )
                        log(f"[#{idx}] Janela mobile: {presentation.width}x{presentation.height} "
                            f"(viewport {presentation.css_width}x{presentation.css_height}, "
                            f"escala visual {presentation.scale:.2f}x).")
                    except Exception as exc:
                        log(f"[#{idx}] Aviso: ajuste da janela mobile indisponível: {exc}")

                mirror.register(idx)
                mirror.install_receiver(page)

                last_overlay_ip_refresh = {"at": 0.0, "url": "", "ip": ""}

                def _ensure_overlay():
                    try:
                        page.evaluate(overlay_init_script(idx, ""))
                    except Exception:
                        pass

                def _set_overlay_ip(ip_value: str):
                    try:
                        page.evaluate(
                            """(ip) => {
                                try {
                                    if (window.__CPA_SET_OVERLAY_IP__) {
                                        window.__CPA_SET_OVERLAY_IP__(ip || "");
                                    } else if (window.__CPA_RENDER_OVERLAY__) {
                                        window.__CPA_RENDER_OVERLAY__();
                                    }
                                } catch (e) {}
                            }""",
                            (ip_value or ""),
                        )
                    except Exception:
                        pass

                def _refresh_overlay_ip(force: bool = False):
                    try:
                        current_url = (page.url or "").strip()
                    except Exception:
                        current_url = ""

                    now = time.time()
                    if (
                        not force
                        and current_url == last_overlay_ip_refresh["url"]
                        and (now - float(last_overlay_ip_refresh["at"])) < 1.5
                    ):
                        return last_overlay_ip_refresh["ip"]

                    _ensure_overlay()
                    ip_live = fetch_public_ip_via_context(context)
                    last_overlay_ip_refresh["at"] = now
                    last_overlay_ip_refresh["url"] = current_url
                    last_overlay_ip_refresh["ip"] = ip_live or ""
                    _set_overlay_ip(last_overlay_ip_refresh["ip"])
                    return last_overlay_ip_refresh["ip"]

                _ensure_overlay()
                _refresh_overlay_ip(force=True)

                def _reapply_overlay(*_args):
                    _refresh_overlay_ip(force=True)

                def _reapply_overlay_main_frame(frame):
                    try:
                        if frame != page.main_frame:
                            return
                    except Exception:
                        pass
                    _refresh_overlay_ip(force=True)

                try:
                    page.on("domcontentloaded", _reapply_overlay)
                except Exception:
                    pass

                try:
                    page.on("load", _reapply_overlay)
                except Exception:
                    pass

                try:
                    page.on("framenavigated", _reapply_overlay_main_frame)
                except Exception:
                    pass

                try:
                    if auto_goto:
                        try:
                            page.goto(url, wait_until="domcontentloaded", timeout=15000)
                        except Exception as exc:
                            if is_https_url(url):
                                raise HttpsNavigationError(
                                    f"Não foi possível abrir '{url}' por HTTPS. "
                                    "O site pode não oferecer HTTPS ou a conexão pode estar "
                                    "indisponível (certificado, DNS, proxy ou rede). "
                                    "Verifique o endereço e tente novamente. "
                                    f"Detalhes: {exc}"
                                ) from exc
                            raise

                        try:
                            vw = int((cfg.viewport or {}).get("width", 420))
                            vh = int((cfg.viewport or {}).get("height", 860))
                        except Exception:
                            vw, vh = 420, 860

                        try:
                            dpr = float(getattr(cfg, "device_scale_factor", 1.0) or 1.0)
                        except Exception:
                            dpr = 1.0

                        if mode in ("celular", "app"):
                            devtools_device_toggle_fix(context, page, vw, vh, dpr)

                        try:
                            mirror.install_receiver(page)
                        except Exception:
                            pass

                        try:
                            _ensure_overlay()
                            _refresh_overlay_ip(force=True)
                        except Exception:
                            pass

                        # Browser-only mode: do not fill, register, or submit forms.
                        log(f"[#{idx}] Navegador pronto. Nenhum cadastro será executado.")

                        success_finished = True
                        _idle_wait_with_speed(
                            page,
                            stop_event,
                            winpos_controller,
                            speed_controller,
                            mirror,
                            withdraw_controller,
                            verify_phone_controller,
                            game_launch_controller,
                            instance_target_controller,
                            log,
                            idx,
                            shared_phone_key=shared_phone_key,
                        )

                    else:
                        success_finished = True
                        log(f"[#{idx}] Navegador aberto (Sem URL).")
                        _idle_wait_with_speed(
                            page,
                            stop_event,
                            winpos_controller,
                            speed_controller,
                            mirror,
                            withdraw_controller,
                            verify_phone_controller,
                            game_launch_controller,
                            instance_target_controller,
                            log,
                            idx,
                            shared_phone_key=shared_phone_key,
                        )

                except HttpsNavigationError:
                    # Fall through the context cleanup and stop the retry
                    # loop. Do not silently downgrade HTTPS to HTTP.
                    raise
                except Exception as e:
                    err_msg = str(e)
                    if "Timeout" in err_msg or "Formulário não apareceu" in err_msg:
                        log(f"[#{idx}] ⏳ FALHA: Timeout/Form ausente.")
                    elif "ERR_PROXY" in err_msg or "ERR_TUNNEL" in err_msg:
                        log(f"[#{idx}] ❌ FALHA DE PROXY.")
                    else:
                        log(f"[#{idx}] ⚠️ Erro: {err_msg}")
                    log(f"[#{idx}] 💀 Reiniciando ciclo...")

                finally:
                    try:
                        mirror.unregister(idx)
                    except Exception:
                        pass

                    try:
                        context.close()
                    except Exception:
                        pass

        except HttpsNavigationError as e:
            log(f"[#{idx}] {e}")
            log(f"[#{idx}] ⚠️ Navegação HTTPS indisponível; verifique conexão, proxy e certificado.")
            log(f"[#{idx}] Navegação HTTPS interrompida sem novas tentativas.")
            return
        except Exception as e:
            log(f"[#{idx}] Erro crítico ao abrir navegador: {type(e).__name__}.")

        if success_finished:
            break

        if attempt < MAX_BROWSER_RESTARTS and not stop_event.is_set():
            time.sleep(2)

    if not success_finished and not stop_event.is_set():
        log(f"[#{idx}] ⛔ Falha total após {MAX_BROWSER_RESTARTS} tentativas.")


def _sleep_with_stop(stop_event: threading.Event, seconds: float):
    try:
        total = float(seconds)
    except Exception:
        total = 0.0

    if total <= 0:
        return

    end = time.time() + total
    while not stop_event.is_set():
        now = time.time()
        if now >= end:
            break
        time.sleep(min(0.2, end - now))


def run_all(
    urls_string: str,
    mode: str,
    count_per_url: int,
    headless: bool,
    proxy: Optional[ProxyConfig],
    auto_goto: bool,
    keep_profiles: bool,
    run_auto: bool,
    start_game_enabled: bool,
    start_game_name: str,
    start_game_id: str,
    stop_event: threading.Event,
    winpos_controller: WindowPosController,
    speed_controller: SpeedController,
    verify_phone_controller: VerifyPhoneController,
    mirror: MirrorEasy,
    withdraw_controller: WithdrawController,
    game_launch_controller: GameLaunchController,
    instance_target_controller: InstanceTargetController,
    log: Callable[[str], None],
    delay_seconds: float,
    use_same_identity: bool = False,
    instance_plans: Optional[List[dict]] = None,
    automation_callback: Optional[Callable] = None,
):
    exts = discover_extensions(BASE_DIR)
    if exts:
        log(f"[+] Extensões carregadas: {[e.name for e in exts]}")

    if instance_plans is None:
        if not str(urls_string or "").strip():
            urls = ["https://google.com"]  # fallback legado de chamadas internas
        else:
            urls = normalize_url_list(urls_string)
        instance_plans = _build_instance_plans(urls, count_per_url, use_same_identity)
    else:
        instance_plans = [dict(plan or {}) for plan in instance_plans if isinstance(plan, dict)]
    total_instances = len(instance_plans)
    log(f"[+] Iniciando {total_instances} instâncias...")

    threads: List[threading.Thread] = []

    for plan in instance_plans:
        if stop_event.is_set():
            break

        t = threading.Thread(
            target=run_instance,
            args=(
                plan["idx"], plan["url"], mode, headless, proxy, exts,
                auto_goto, keep_profiles, run_auto, start_game_enabled, start_game_name, start_game_id, stop_event,
                winpos_controller, speed_controller, mirror, withdraw_controller, verify_phone_controller,
                game_launch_controller, instance_target_controller, plan, log, automation_callback
            ),
            daemon=True
        )
        t.start()
        threads.append(t)

        _sleep_with_stop(stop_event, delay_seconds)

    for t in threads:
        t.join()


# =====================================================
# GUI: Aba (Frame) para o Tabview
# =====================================================

class FilhaTab(ctk.CTkFrame):
    def __init__(self, master, speed_controller: Optional[SpeedController] = None):
        super().__init__(master)

        self.speed_controller = speed_controller or SpeedController(enabled=False, speed=2.0)
        self.mirror = MirrorEasy()

        self.withdraw_controller = WithdrawController()
        self.game_launch_controller = GameLaunchController()
        self.quick_game_buttons_data = _default_quick_game_buttons()
        self.quick_game_buttons: List[ctk.CTkButton] = []
        self.verify_phone_controller = VerifyPhoneController()
        self.instance_target_controller = InstanceTargetController()
        self.available_instance_ids: List[int] = []
        self.instance_targets_var = ctk.StringVar(value="Controlando: Geral")
        self.stop_event = threading.Event()
        self.winpos_controller = WindowPosController()
        self.worker_thread: Optional[threading.Thread] = None
        self._invite_json_last_snapshot = ""

        self._build_ui()
        self._load_settings()
        self._set_status("Pronto.")
        self.after(300, self._watch_invite_json)

    def _read_invite_links_json(self) -> list[str]:
        try:
            if not INVITE_PATH.exists():
                return []

            raw = INVITE_PATH.read_text(encoding="utf-8").strip()
            if not raw:
                return []

            data = json.loads(raw)

            if isinstance(data, dict):
                data = data.get("links", [])

            if isinstance(data, list):
                return [str(item).strip() for item in data if str(item).strip()]
        except Exception:
            pass

        return []

    def _watch_invite_json(self):
        try:
            links = self._read_invite_links_json()
            snapshot = json.dumps(links, ensure_ascii=False)

            if snapshot != self._invite_json_last_snapshot:
                self._invite_json_last_snapshot = snapshot
                self.urls_var.set(", ".join(links))
        except Exception:
            pass

        self.after(1000, self._watch_invite_json)

    def _refresh_proxy_toggle_button(self):
        enabled = bool(self.proxy_enabled_var.get())

        if enabled:
            self.proxy_toggle_button.configure(
                text="Proxy Ligado",
                fg_color="#16A34A",
                hover_color="#15803D",
            )
        else:
            self.proxy_toggle_button.configure(
                text="Proxy Desativado",
                fg_color="#DC2626",
                hover_color="#B91C1C",
            )

    def _toggle_live_proxy(self):
        enabled = not bool(self.proxy_enabled_var.get())
        self.proxy_enabled_var.set(enabled)

        def _patch(st):
            st[_scope_key("proxy_enabled")] = enabled

        ok = _update_settings_atomic(_patch)

        self._refresh_proxy_toggle_button()

        if ok:
            self._log(f"[UI] {'🟢' if enabled else '🔴'} Proxy {'Ligado' if enabled else 'Desativado'}")
            self._set_status("Proxy Ligado" if enabled else "Proxy Desativado")
        else:
            self._log("[UI] ⚠️ Falha ao alternar estado do proxy.")

    def save_window_position(self):
        """
        Dispara um pedido para SALVAR a posição/tamanho de CADA tela (idx 1,2,3...)
        (cada thread do Playwright salva a sua, 1x por clique).
        """
        if not (self.worker_thread and self.worker_thread.is_alive()):
            self._set_status("Abra as telas antes de salvar posição.")
            self._log("[UI] ⚠️ Nenhuma tela rodando para capturar posição.")
            return

        v = self.winpos_controller.request()
        self._set_status("Salvando posição...")
        self._log(f"[UI] 🧷 Pedido: salvar posição das telas (v{v}) — cada tela salva a sua.")
        self._set_status("Posição Salva")

    def request_phone_verification(self):
        if not (self.worker_thread and self.worker_thread.is_alive()):
            self._set_status("Abra as telas antes de verificar número.")
            self._log("[UI] ⚠️ Nenhuma tela rodando para verificar número.")
            return

        v = self.verify_phone_controller.request()
        self._set_status("Verificando número...")
        self._log(f"[UI] 📱 Pedido: verificar número (v{v}) — cada tela executa 1x.")

    def _persist_withdraw_pin(self) -> str:
        pin_value = _clean_withdraw_pin(self.withdraw_pin_var.get())
        self.withdraw_pin_var.set(pin_value)

        def _patch(st):
            st[_scope_key("withdraw_pin")] = pin_value

        ok = _update_settings_atomic(_patch)
        if not ok:
            self._log("[UI] ⚠️ Falha ao salvar o PIN do saque.")

        return pin_value

    def request_withdrawal(self):
        """
        Dispara um pedido para INICIAR SAQUE em CADA tela (idx 1,2,3...)
        (cada thread do Playwright executa 1x por clique).
        """
        if not (self.worker_thread and self.worker_thread.is_alive()):
            self._set_status("Abra as telas antes de iniciar saque.")
            self._log("[UI] ⚠️ Nenhuma tela rodando para iniciar saque.")
            return

        pin_value = self._persist_withdraw_pin()
        v = self.withdraw_controller.request()
        self._set_status("Iniciando saque...")
        self._log(f"[UI] 💸 Pedido: iniciar saque (v{v}) — cada tela executa 1x com PIN configurado ({len(pin_value)} dígitos).")

    def _get_available_instance_ids(self) -> List[int]:
        if getattr(self, "available_instance_ids", None):
            return list(self.available_instance_ids)

        urls = [u.strip() for u in (self.urls_var.get() or "").split(",") if u.strip()]
        total_urls = len(urls) if urls else 1
        count = self._get_int(self.count_var, default=1, min_v=1, max_v=50)
        total = total_urls * count
        return list(range(1, total + 1))

    def _update_instance_target_label(self):
        self.instance_targets_var.set(f"Controlando: {self.instance_target_controller.summary()}")

    def _open_instance_selector(self):
        available_ids = self._get_available_instance_ids()
        state = self.instance_target_controller.get()

        top = ctk.CTkToplevel(self)
        top.title("Selecionar instâncias")
        top.geometry("360x420")
        top.transient(self.winfo_toplevel())
        top.grab_set()

        wrapper = ctk.CTkFrame(top, corner_radius=12)
        wrapper.pack(fill="both", expand=True, padx=12, pady=12)

        ctk.CTkLabel(
            wrapper,
            text="Escolha quais instâncias serão controladas",
            font=ctk.CTkFont(size=15, weight="bold"),
        ).pack(anchor="w", padx=12, pady=(12, 8))

        all_var = ctk.BooleanVar(value=state["apply_all"])
        item_vars = {}

        def _toggle_all():
            if all_var.get():
                for var in item_vars.values():
                    var.set(False)

        ctk.CTkCheckBox(
            wrapper,
            text="Geral",
            variable=all_var,
            command=_toggle_all,
        ).pack(anchor="w", padx=12, pady=(0, 8))

        scroll = ctk.CTkScrollableFrame(wrapper, corner_radius=10, height=240)
        scroll.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        for idx in available_ids:
            checked = (not state["apply_all"] and idx in state["targets"])
            var = ctk.BooleanVar(value=checked)
            item_vars[idx] = var

            def _make_uncheck_all(v=var):
                def _inner():
                    if v.get():
                        all_var.set(False)
                return _inner

            ctk.CTkCheckBox(
                scroll,
                text=f"#{idx}",
                variable=var,
                command=_make_uncheck_all(),
            ).pack(anchor="w", padx=8, pady=4)

        def _save_targets():
            if all_var.get():
                self.instance_target_controller.set(True, [])
            else:
                selected = [idx for idx, var in item_vars.items() if var.get()]
                if selected:
                    self.instance_target_controller.set(False, selected)
                else:
                    self.instance_target_controller.set(True, [])

            self._update_instance_target_label()
            self._log(f"[UI] 🎯 Instâncias selecionadas: {self.instance_target_controller.summary()}")
            top.destroy()

        actions = ctk.CTkFrame(wrapper, fg_color="transparent")
        actions.pack(fill="x", padx=12, pady=(0, 12))

        ctk.CTkButton(actions, text="Salvar", command=_save_targets).pack(side="left", expand=True, fill="x", padx=(0, 6))
        ctk.CTkButton(actions, text="Cancelar", command=top.destroy, fg_color="#374151", hover_color="#1F2937").pack(side="left", expand=True, fill="x", padx=(6, 0))

    def _save_quick_game_buttons(self):
        buttons_data = _normalize_quick_game_buttons(self.quick_game_buttons_data)

        def _patch(st):
            st[_scope_key("quick_game_buttons")] = buttons_data

        ok = _update_settings_atomic(_patch)
        if not ok:
            self._log("[UI] ⚠️ Falha ao salvar botões de jogo.")
        self.quick_game_buttons_data = buttons_data
        self._refresh_quick_game_buttons()

    def _refresh_quick_game_buttons(self):
        for button_index, btn in enumerate(getattr(self, "quick_game_buttons", [])):
            data = self.quick_game_buttons_data[button_index] if button_index < len(self.quick_game_buttons_data) else {"name": "", "id": ""}
            label = (data.get("name") or "").strip()
            btn.configure(text=label if label else "Vazio")

    def _prompt_quick_game_button(self, button_index: int):
        current = self.quick_game_buttons_data[button_index]
        initial_name = (current.get("name") or "").strip()
        initial_id = str(current.get("id") or "").strip()

        game_name = tksd.askstring("Salvar jogo", "Nome do jogo:", initialvalue=initial_name or None, parent=self.winfo_toplevel())
        if game_name is None:
            return
        game_name = game_name.strip()
        if not game_name:
            tkmb.showwarning("Salvar jogo", "Informe o nome do jogo.")
            return

        game_id = tksd.askstring("Salvar jogo", "ID do jogo:", initialvalue=initial_id or None, parent=self.winfo_toplevel())
        if game_id is None:
            return
        game_id = str(game_id).strip()
        if not game_id:
            tkmb.showwarning("Salvar jogo", "Informe o ID do jogo.")
            return

        self.quick_game_buttons_data[button_index] = {"name": game_name, "id": game_id}
        self._save_quick_game_buttons()
        self._log(f"[UI] 🎮 Botão {button_index + 1} salvo: {game_name} ({game_id})")

    def _clear_quick_game_button(self, button_index: int):
        data = self.quick_game_buttons_data[button_index]
        label = (data.get("name") or "").strip() or f"Botão {button_index + 1}"
        if not tkmb.askyesno("Limpar botão", f"Limpar botão?\n\n{label}", parent=self.winfo_toplevel()):
            return
        self.quick_game_buttons_data[button_index] = {"name": "", "id": ""}
        self._save_quick_game_buttons()
        self._log(f"[UI] 🧹 Botão {button_index + 1} limpo.")

    def _launch_game(self, game_name: str, game_id: str):
        game_name = (game_name or "").strip()
        game_id = str(game_id or "").strip()
        if not game_name or not game_id:
            self._set_status("Preencha nome e ID do jogo.")
            return

        if not (self.worker_thread and self.worker_thread.is_alive()):
            self._set_status("Abra as telas antes de iniciar o jogo.")
            self._log("[UI] ⚠️ Nenhuma tela rodando para abrir jogo.")
            return

        version = self.game_launch_controller.request(game_name, game_id)
        self._set_status(f"Abrindo jogo: {game_name}")
        self._log(f"[UI] 🎮 Pedido: abrir jogo {game_name} ({game_id}) (v{version}) — cada tela executa 1x.")

    def _on_quick_game_button_click(self, button_index: int):
        data = self.quick_game_buttons_data[button_index]
        game_name = (data.get("name") or "").strip()
        game_id = str(data.get("id") or "").strip()

        if not game_name or not game_id:
            self._prompt_quick_game_button(button_index)
            return

        self._launch_game(game_name, game_id)

    def _on_quick_game_button_right_click(self, event, button_index: int):
        self._clear_quick_game_button(button_index)
        return "break"

    def _on_start_game_ui_change(self):
        enabled = bool(self.start_game_enabled_var.get())
        if enabled:
            self.start_game_form.grid()
        else:
            self.start_game_form.grid_remove()

    # ---------- logging UI ----------
    def _log(self, msg: str):
        if not msg.endswith("\n"):
            msg += "\n"
        self.log_text.configure(state="normal")
        self.log_text.insert("end", msg)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _get_float(self, var: ctk.StringVar, default: float = 1.0, min_v: float = 0.0, max_v: float = 9999.0) -> float:
        s = (var.get() or "").strip().replace(",", ".")
        try:
            n = float(s)
        except Exception:
            return default
        if n < min_v:
            return min_v
        if n > max_v:
            return max_v
        return n

    def _get_int(self, var: ctk.StringVar, default: int = 1, min_v: int = 1, max_v: int = 50) -> int:
        s = (var.get() or "").strip()
        if not s.isdigit():
            return default
        n = int(s)
        if n < min_v:
            return min_v
        if n > max_v:
            return max_v
        return n

    def _set_status(self, s: str):
        self.status_var.set(s)

    # ---------- Speed ----------
    def _on_speed_ui_change(self):
        try:
            enabled = bool(self.speed_enabled_var.get())
        except Exception:
            enabled = False
        try:
            speed = float(str(self.speed_value_var.get()).replace(',', '.'))
        except Exception:
            speed = 1.0
        st = self.speed_controller.set(enabled=enabled, speed=speed)
        self._speed_ui_last_version = st.version
        try:
            self._log(f"[ui] ⚡ Speed {'ON' if st.enabled else 'OFF'} | x{st.speed}")
        except Exception:
            pass

    def _sync_speed_ui(self):
        try:
            st = self.speed_controller.get()
            if getattr(self, '_speed_ui_last_version', None) != st.version:
                self.speed_enabled_var.set(st.enabled)
                self.speed_value_var.set(str(st.speed))
                self._speed_ui_last_version = st.version
        except Exception:
            pass
        self.after(600, self._sync_speed_ui)

    def _on_popup_ui_change(self):
        enabled = bool(self.auto_close_popups_var.get())

        def _patch(st):
            st[_scope_key("auto_close_popups")] = enabled

        ok = _update_settings_atomic(_patch)

        if ok:
            self._log(f"[ui] 🧹 Fechar Popups {'ON' if enabled else 'OFF'}")
        else:
            self._log("[ui] ⚠️ Falha ao salvar estado do Fechar Popups.")

    # ---------- Mirror ----------
    def _on_mirror_ui_change(self):
        enabled = bool(self.mirror_enabled_var.get())
        master = 1
        self.mirror.set(enabled, master)
        try:
            self._log(f"[ui] 🪞 Espelho {'ON' if enabled else 'OFF'} | master #1")
        except Exception:
            pass

    # ---------- UI ----------
    def _build_ui(self):
        header = ctk.CTkFrame(self, corner_radius=18)
        header.pack(fill="x", padx=16, pady=(16, 10))

        title = ctk.CTkLabel(header, text="Criar Filha", font=ctk.CTkFont(size=20, weight="bold"))
        title.pack(anchor="w", padx=16, pady=(14, 0))

        subtitle = ctk.CTkLabel(header, text="Abertura de perfis e acompanhamento da execução", text_color="#9CA3AF")
        subtitle.pack(anchor="w", padx=16, pady=(2, 8))

        # Speed
        st = self.speed_controller.get()
        speed_row = ctk.CTkFrame(header, corner_radius=14)
        speed_row.pack(fill="x", padx=16, pady=(0, 14))
        speed_row.pack_forget()  # Simplified account interface; internal speed state preserved.
        speed_row.grid_columnconfigure(3, weight=1)

        self.speed_enabled_var = ctk.BooleanVar(value=st.enabled)
        self.speed_value_var = ctk.StringVar(value=str(st.speed))
        self.auto_close_popups_var = ctk.BooleanVar(value=False)

        self.speed_toggle = ctk.CTkSwitch(
            speed_row,
            text="Speed",
            variable=self.speed_enabled_var,
            command=self._on_speed_ui_change
        )
        self.speed_toggle.grid(row=0, column=0, padx=(12, 10), pady=10, sticky="w")

        ctk.CTkLabel(speed_row, text="Velocidade:", text_color="#9CA3AF").grid(
            row=0, column=1, padx=(0, 8), pady=10, sticky="w"
        )

        self.speed_menu = ctk.CTkOptionMenu(
            speed_row,
            values=["1.0", "1.25", "1.5", "2", "3", "5", "10", "20", "50", "100"],
            variable=self.speed_value_var,
            command=lambda _=None: self._on_speed_ui_change()
        )
        self.speed_menu.grid(row=0, column=2, padx=(0, 12), pady=10, sticky="w")

        self.popup_toggle = ctk.CTkSwitch(
            speed_row,
            text="Fechar Popups",
            variable=self.auto_close_popups_var,
            command=self._on_popup_ui_change
        )
        self.popup_toggle.grid(row=0, column=4, padx=(0, 12), pady=10, sticky="e")

        self._speed_ui_last_version = st.version
        self.after(600, self._sync_speed_ui)
        # Mirror
        mirror_row = ctk.CTkFrame(header, corner_radius=14)
        mirror_row.pack(fill="x", padx=16, pady=(0, 14))
        mirror_row.pack_forget()  # Legacy mirror controller preserved.
        mirror_row.grid_columnconfigure(1, weight=1)

        self.mirror_enabled_var = ctk.BooleanVar(value=False)

        self.mirror_toggle = ctk.CTkSwitch(
            mirror_row,
            text="Espelhar (Master: #1)",
            variable=self.mirror_enabled_var,
            command=self._on_mirror_ui_change
        )
        self.mirror_toggle.grid(row=0, column=0, padx=(12, 10), pady=10, sticky="w")

        ctk.CTkLabel(mirror_row, text="Controla todas as outras telas", text_color="#9CA3AF").grid(
            row=0, column=1, padx=(0, 12), pady=10, sticky="w"
        )

        quick_games_row = ctk.CTkFrame(header, corner_radius=14)
        quick_games_row.pack(fill="x", padx=16, pady=(0, 14))
        quick_games_row.pack_forget()  # Game shortcuts kept internally for future use.
        for col in range(QUICK_GAME_BUTTONS_COUNT):
            quick_games_row.grid_columnconfigure(col, weight=1)

        self.quick_game_buttons = []
        for button_index in range(QUICK_GAME_BUTTONS_COUNT):
            btn = ctk.CTkButton(
                quick_games_row,
                text="Vazio",
                height=34,
                corner_radius=10,
                command=lambda i=button_index: self._on_quick_game_button_click(i),
            )
            btn.grid(row=0, column=button_index, padx=(6 if button_index else 10, 6 if button_index < QUICK_GAME_BUTTONS_COUNT - 1 else 10), pady=10, sticky="ew")
            btn.bind("<Button-3>", lambda event, i=button_index: self._on_quick_game_button_right_click(event, i))
            self.quick_game_buttons.append(btn)

        body = ctk.CTkFrame(self, corner_radius=18)
        body.pack(fill="both", expand=True, padx=16, pady=(0, 12))

        body.grid_columnconfigure(0, weight=3)
        body.grid_columnconfigure(1, weight=2)
        body.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(body, corner_radius=18)
        left.grid(row=0, column=0, sticky="nsew", padx=(14, 8), pady=14)

        right = ctk.CTkFrame(body, corner_radius=18)
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 14), pady=14)

        ctk.CTkLabel(left, text="Configurações", font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w", padx=16, pady=(16, 4))
        ctk.CTkLabel(left, text="Informe os links, o modo do dispositivo e o intervalo.", text_color="#9CA3AF").pack(anchor="w", padx=16, pady=(0, 12))

        self.urls_var = ctk.StringVar()
        ctk.CTkLabel(left, text="URLs (separadas por vírgula)").pack(anchor="w", padx=16)
        self.urls_entry = ctk.CTkEntry(left, textvariable=self.urls_var, height=38, corner_radius=12)
        self.urls_entry.pack(fill="x", padx=16, pady=(6, 12))

        row1 = ctk.CTkFrame(left, fg_color="transparent")
        row1.pack(fill="x", padx=16, pady=(0, 12))
        row1.grid_columnconfigure((0, 1, 2), weight=1)
        row1.grid_columnconfigure(0, weight=2)  # Dar espaço aos nomes de dispositivo.

        self.mode_var = ctk.StringVar()
        ctk.CTkLabel(row1, text="Modo").grid(row=0, column=0, sticky="w")
        self.mode_combo = ctk.CTkOptionMenu(row1, variable=self.mode_var, values=list(MODE_LABELS.values()), width=250, height=36, corner_radius=12)
        self.mode_combo.grid(row=1, column=0, sticky="ew", pady=(6, 0), padx=(0, 10))

        self.count_var = ctk.StringVar(value="1")
        ctk.CTkLabel(row1, text="Quantidade por link").grid(row=0, column=1, sticky="w")
        self.count_entry = ctk.CTkEntry(row1, textvariable=self.count_var, height=36, corner_radius=12)
        self.count_entry.grid(row=1, column=1, sticky="ew", pady=(6, 0), padx=(0, 10))

        self.delay_var = ctk.StringVar(value="1.0")
        ctk.CTkLabel(row1, text="Delay (s)").grid(row=0, column=2, sticky="w")
        self.delay_entry = ctk.CTkEntry(row1, textvariable=self.delay_var, height=36, corner_radius=12)
        self.delay_entry.grid(row=1, column=2, sticky="ew", pady=(6, 0))

        self.headless_var = ctk.BooleanVar(value=False)
        self.keep_profiles_var = ctk.BooleanVar(value=False)
        self.same_identity_var = ctk.BooleanVar(value=False)
        self.generated_cpf_var = ctk.BooleanVar(value=True)

        flags = ctk.CTkFrame(left, corner_radius=14)
        flags.pack(fill="x", padx=16, pady=(0, 12))
        flags.pack_forget()  # Hidden legacy identity/game/profile controls.
        flags.grid_columnconfigure((0, 1), weight=1)

        self.chk_headless = ctk.CTkCheckBox(flags, text="Rodar invisível (Headless)", variable=self.headless_var)
        self.chk_keep = ctk.CTkCheckBox(flags, text="Manter perfis (__profiles)", variable=self.keep_profiles_var)
        self.chk_same_identity = ctk.CTkCheckBox(
            flags,
            text="Reutilizar dados",
            variable=self.same_identity_var,
        )
        self.chk_generated_cpf = ctk.CTkCheckBox(
            flags,
            text="Usar CPF Aleatorio",
            variable=self.generated_cpf_var,
        )

        self.start_game_enabled_var = ctk.BooleanVar(value=False)
        self.start_game_name_var = ctk.StringVar()
        self.start_game_id_var = ctk.StringVar()

        self.chk_same_identity.grid(row=0, column=0, sticky="w", padx=16, pady=12)
        self.chk_generated_cpf.grid(row=3, column=0, columnspan=2, sticky="w", padx=16, pady=(0, 12))

        self.chk_start_game = ctk.CTkCheckBox(
            flags,
            text="Iniciar jogo",
            variable=self.start_game_enabled_var,
            command=self._on_start_game_ui_change,
        )
        self.chk_start_game.grid(row=4, column=0, sticky="w", padx=16, pady=(0, 8))

        self.start_game_form = ctk.CTkFrame(flags, fg_color="transparent")
        self.start_game_form.grid(row=2, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 8))
        self.start_game_form.grid_columnconfigure((0, 1), weight=1)

        ctk.CTkLabel(self.start_game_form, text="Nome do jogo").grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(self.start_game_form, text="ID do jogo").grid(row=0, column=1, sticky="w", padx=(10, 0))

        self.start_game_name_entry = ctk.CTkEntry(self.start_game_form, textvariable=self.start_game_name_var, height=34, corner_radius=12)
        self.start_game_name_entry.grid(row=1, column=0, sticky="ew", pady=(6, 0), padx=(0, 5))

        self.start_game_id_entry = ctk.CTkEntry(self.start_game_form, textvariable=self.start_game_id_var, height=34, corner_radius=12)
        self.start_game_id_entry.grid(row=1, column=1, sticky="ew", pady=(6, 0), padx=(5, 0))
        self.start_game_form.grid_remove()
        autopix_card = ctk.CTkFrame(left, corner_radius=14)
        autopix_card.pack(fill="x", padx=16, pady=(0, 16))
        autopix_card.grid_columnconfigure(1, weight=1)
        autopix_card.grid_columnconfigure(2, weight=1)

        self.autopix_enabled_var = ctk.BooleanVar(value=False)
        self.autochave_enabled_var = ctk.BooleanVar(value=False)
        self.verify_phone_var = ctk.BooleanVar(value=False)
        self.autopix_bank_var = ctk.StringVar(value=get_default_bank())
        self.deposit_amount_var = ctk.StringVar(value="10")
        self.withdraw_pin_var = ctk.StringVar(value=DEFAULT_WITHDRAW_PIN)
        self.proxy_enabled_var = ctk.BooleanVar(value=True)


        self.chk_autopix = ctk.CTkCheckBox(
            autopix_card,
            text="AutoPIX",
            variable=self.autopix_enabled_var,
            command=self._on_autopix_ui_change,
        )
        self.chk_autopix.grid(row=0, column=0, padx=(16, 12), pady=(12, 6), sticky="w")

        self.chk_autochave = ctk.CTkCheckBox(
            autopix_card,
            text="AutoChave",
            variable=self.autochave_enabled_var,
            command=self._on_autopix_ui_change,
        )
        self.chk_autochave.grid(row=1, column=0, padx=(16, 12), pady=(0, 6), sticky="w")

        self.chk_verify_phone = ctk.CTkCheckBox(
            autopix_card,
            text="Verificar telefone",
            variable=self.verify_phone_var,
            command=self._on_autopix_ui_change,
        )
        self.chk_verify_phone.grid(row=2, column=0, padx=(16, 12), pady=(0, 12), sticky="w")

        ctk.CTkLabel(autopix_card, text="Valor depósito").grid(
            row=0, column=1, padx=(16, 12), pady=(12, 6), sticky="e"
        )
        self.deposit_amount_entry = ctk.CTkEntry(
            autopix_card,
            textvariable=self.deposit_amount_var,
            height=34,
            corner_radius=12,
        )
        self.deposit_amount_entry.grid(
            row=0, column=2, padx=(16, 12), pady=(12, 6), sticky="ew"
        )
        # Módulo Pix retirado da interface, preservado para possível retorno.
        autopix_card.pack_forget()

        # Controle exclusivo de saque/Pix temporariamente oculto.

        self.withdraw_pin_entry = ctk.CTkEntry(
            right,
            textvariable=self.withdraw_pin_var,
            height=38,
            corner_radius=12,
        )
        self.withdraw_pin_entry.pack_forget()


        self.proxy_var = ctk.StringVar()
        self.smartproxy_fixed_ip_var = ctk.BooleanVar(value=False)

        self.proxy_label = ctk.CTkLabel(left, text="Proxy (ip:porta:user:senha ou ip:porta)")
        self.proxy_label.pack(anchor="w", padx=16)
        self.proxy_label.pack_forget()
        self.proxy_entry = ctk.CTkEntry(left, textvariable=self.proxy_var, height=38, corner_radius=12)
        self.proxy_entry.pack(fill="x", padx=16, pady=(6, 8))
        self.proxy_entry.pack_forget()

        self.chk_smartproxy_fixed_ip = ctk.CTkCheckBox(
            left,
            text="IP Fixo SmartProxy",
            variable=self.smartproxy_fixed_ip_var,
        )
        self.chk_smartproxy_fixed_ip.pack(anchor="w", padx=16, pady=(0, 12))
        self.chk_smartproxy_fixed_ip.pack_forget()

        self.proxy_toggle_button = ctk.CTkButton(
            left,
            text="Proxy Ligado",
            command=self._toggle_live_proxy,
            height=38,
            corner_radius=12,
        )
        self.proxy_toggle_button.pack(fill="x", padx=16, pady=(0, 12))
        self.proxy_toggle_button.pack_forget()  # Proxy settings reserved for a future shared area.


        ctk.CTkLabel(right, text="Ações", font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w", padx=16, pady=(16, 12))

        self.btn_start_manual = ctk.CTkButton(
            right, text="Abrir Navegadores", height=42, corner_radius=14,
            fg_color="#111827", hover_color="#1F2937",
            command=self.start_browsers
        )
        self.btn_start_manual.pack(fill="x", padx=16, pady=(0, 10))

        self.btn_withdraw = ctk.CTkButton(
            right, text="Iniciar saque", height=42, corner_radius=14,
            command=self.request_withdrawal
        )
        self.btn_withdraw.pack_forget()

        self.btn_verify_phone = ctk.CTkButton(
            right, text="Verificar Numero", height=42, corner_radius=14,
            command=self.request_phone_verification
        )
        self.btn_verify_phone.pack(fill="x", padx=16, pady=(0, 10))
        self.btn_verify_phone.pack_forget()

        self.btn_stop = ctk.CTkButton(
            right, text="PARAR", height=42, corner_radius=14,
            fg_color="#DC2626", hover_color="#B91C1C",
            command=self.stop_run, state="disabled"
        )
        self.btn_stop.pack(fill="x", padx=16, pady=(0, 14))

        tools = ctk.CTkFrame(right, corner_radius=16)
        tools.pack(fill="x", padx=16, pady=(0, 14))
        tools.pack_forget()  # Position and instance-selection actions remain internal.

        ctk.CTkButton(
            tools,
            text="Salvar posição das telas",
            height=38,
            corner_radius=12,
            command=self.save_window_position
        ).pack(fill="x", padx=12, pady=(12, 8))

        ctk.CTkButton(
            tools,
            text="Selecionar instâncias",
            height=38,
            corner_radius=12,
            command=self._open_instance_selector,
        ).pack(fill="x", padx=12, pady=(0, 8))

        ctk.CTkLabel(
            tools,
            textvariable=self.instance_targets_var,
            text_color="#9CA3AF",
        ).pack(anchor="w", padx=14, pady=(0, 12))

        logs_card = ctk.CTkFrame(self, corner_radius=18)
        logs_card.pack(fill="both", expand=True, padx=16, pady=(0, 16))

        top_logs = ctk.CTkFrame(logs_card, fg_color="transparent")
        top_logs.pack(fill="x", padx=16, pady=(14, 8))

        ctk.CTkLabel(top_logs, text="Logs ao vivo", font=ctk.CTkFont(size=14, weight="bold")).pack(side="left")
        self.status_var = ctk.StringVar(value="")
        ctk.CTkLabel(top_logs, textvariable=self.status_var, text_color="#9CA3AF").pack(side="right")

        self.log_text = ctk.CTkTextbox(logs_card, corner_radius=14)
        self.log_text.pack(fill="both", expand=True, padx=16, pady=(0, 16))
        self.log_text.configure(state="disabled")

    def _device_mode_for_launch(self):
        return resolve_mode(self.mode_var.get())

    def _load_settings(self):
        st = load_settings()
        invite_links = self._read_invite_links_json()
        if invite_links:
            self.urls_var.set(", ".join(invite_links))
        else:
            self.urls_var.set(st.get("url", "https://google.com"))

        self.mode_var.set(mode_label(st.get("mode", "desktop")))
        self.count_var.set(str(st.get("count", 1)))
        self.delay_var.set(str(st.get("delay_seconds", 1.0)))
        self.headless_var.set(bool(st.get("headless", False)))
        self.proxy_var.set(st.get("proxy_raw", ""))
        self.smartproxy_fixed_ip_var.set(
            bool(st.get(_scope_key("smartproxy_fixed_ip"), st.get("smartproxy_fixed_ip", False)))
        )
        self.keep_profiles_var.set(bool(st.get("keep_profiles", False)))
        self.start_game_enabled_var.set(bool(st.get(_scope_key("start_game_enabled"), False)))
        self.start_game_name_var.set((st.get(_scope_key("start_game_name"), "") or "").strip())
        self.start_game_id_var.set(str(st.get(_scope_key("start_game_id"), "") or "").strip())
        self.quick_game_buttons_data = _normalize_quick_game_buttons(st.get(_scope_key("quick_game_buttons")))
        self._refresh_quick_game_buttons()
        self._on_start_game_ui_change()
        self.same_identity_var.set(bool(st.get(_scope_key("same_identity_enabled"), st.get("same_identity_enabled", False))))
        self.generated_cpf_var.set(bool(st.get(_scope_key("use_generated_cpf"), True)))
        self.auto_close_popups_var.set(bool(st.get(_scope_key("auto_close_popups"), False)))
        self.autopix_enabled_var.set(False)  # Pix pausado, inclusive em configurações antigas.
        self.autochave_enabled_var.set(False)  # Evita iniciar sessões Pix ocultas.
        self.verify_phone_var.set(bool(st.get(_scope_key("verify_phone_enabled"), False)))
        self.withdraw_pin_var.set(
            _clean_withdraw_pin(st.get(_scope_key("withdraw_pin"), st.get("withdraw_pin", DEFAULT_WITHDRAW_PIN)))
        )

        autopix_bank = (
            st.get(_scope_key("autopix_bank"))
            or st.get("autopix_bank")
            or get_default_bank()
        ).strip()
        if autopix_bank not in BANK_URLS:
            autopix_bank = get_default_bank()
        self.autopix_bank_var.set(autopix_bank)
        self._on_autopix_ui_change()
        self.proxy_enabled_var.set(bool(st.get(_scope_key("proxy_enabled"), True)))
        self._refresh_proxy_toggle_button()


    def save_settings(self):
        count = self._get_int(self.count_var, default=1, min_v=1, max_v=50)
        delay_seconds = self._get_float(self.delay_var, default=1.0, min_v=0.0, max_v=9999.0)

        st = load_settings()
        st.update({
            "url": self.urls_var.get().strip(),
            "mode": self._device_mode_for_launch(),
            "count": count,
            "delay_seconds": delay_seconds,
            "headless": bool(self.headless_var.get()),
            "proxy_raw": self.proxy_var.get().strip(),
            "auto_goto": True,
            "keep_profiles": bool(self.keep_profiles_var.get()),
            _scope_key("start_game_enabled"): bool(self.start_game_enabled_var.get()),
            _scope_key("start_game_name"): self.start_game_name_var.get().strip(),
            _scope_key("start_game_id"): self.start_game_id_var.get().strip(),
            _scope_key("quick_game_buttons"): _normalize_quick_game_buttons(self.quick_game_buttons_data),
            _scope_key("same_identity_enabled"): bool(self.same_identity_var.get()),
            _scope_key("auto_close_popups"): bool(self.auto_close_popups_var.get()),
            _scope_key("use_generated_cpf"): bool(self.generated_cpf_var.get()),
            _scope_key("autopix_enabled"): bool(self.autopix_enabled_var.get()),
            _scope_key("autochave_enabled"): bool(self.autochave_enabled_var.get()),
            _scope_key("autopix_bank"): (self.autopix_bank_var.get().strip() or get_default_bank()),
            _scope_key("verify_phone_enabled"): bool(self.verify_phone_var.get()),
            _scope_key("deposit_amount"): (self.deposit_amount_var.get().strip().replace(",", ".") or "10"),
            _scope_key("withdraw_pin"): _clean_withdraw_pin(self.withdraw_pin_var.get()),
            _scope_key("smartproxy_fixed_ip"): bool(self.smartproxy_fixed_ip_var.get()),
            _scope_key("proxy_enabled"): bool(self.proxy_enabled_var.get()),

        })
        write_settings(st)
        self._set_status("Configurações salvas.")
        self._log(f"[UI] Config salva ({AREA_PIX_SCOPE}).")

    def _on_autopix_ui_change(self):
        try:
            enabled = bool(self.autopix_enabled_var.get())
        except Exception:
            enabled = False

        if hasattr(self, "autopix_bank_combo") and self.autopix_bank_combo is not None:
            self.autopix_bank_combo.configure(state=("normal" if enabled else "disabled"))

        if hasattr(self, "deposit_amount_entry") and self.deposit_amount_entry is not None:
            self.deposit_amount_entry.configure(state=("normal" if enabled else "normal"))
        self.deposit_amount_entry.configure(state=("normal" if enabled else "normal"))

    def _validate_proxy(self) -> Optional[ProxyConfig]:
        # A selected proxy in the Proxy workspace overrides the legacy
        # text field for NEW browsers. Existing sessions are not altered.
        try:
            from nexora_accounts.proxy_store import ProxyStore, active_proxy_config
            # Once Proxy tab selection has been used, disabling it must mean
            # DIRECT routing, not fallback to an invisible legacy proxy_raw.
            if ProxyStore().is_managed():
                return active_proxy_config()
        except Exception:
            self._set_status("Não foi possível ler os proxies salvos.")
            self._log("[UI] Falha ao consultar o gerenciador de proxies.")
            raise

        raw = self.proxy_var.get().strip()
        if not raw:
            return None
        try:
            return parse_proxy_string(raw)
        except Exception:
            self._set_status("Proxy inválido.")
            self._log("[UI] Proxy inválido.")
            return None

    def _set_running_state(self, running: bool):
        state = "disabled" if running else "normal"
        self.urls_entry.configure(state=state)
        self.proxy_entry.configure(state=state)
        self.chk_smartproxy_fixed_ip.configure(state=state)
        self.mode_combo.configure(state=state)
        self.count_entry.configure(state=state)
        self.delay_entry.configure(state=state)
        self.chk_headless.configure(state=state)
        self.chk_keep.configure(state=state)
        self.chk_start_game.configure(state=state)
        self.start_game_name_entry.configure(state=state)
        self.start_game_id_entry.configure(state=state)
        self.chk_same_identity.configure(state=state)
        self.chk_generated_cpf.configure(state=state)
        self.chk_autopix.configure(state=state)
        self.chk_autochave.configure(state=state)
        if hasattr(self, "autopix_bank_combo") and self.autopix_bank_combo is not None:
            self.autopix_bank_combo.configure(
                state=(state if bool(self.autopix_enabled_var.get()) else "disabled")
            )

        if hasattr(self, "deposit_amount_entry") and self.deposit_amount_entry is not None:
            self.deposit_amount_entry.configure(
                state=(state if bool(self.autopix_enabled_var.get()) else "disabled")
            )
        self.deposit_amount_entry.configure(state=(state if bool(self.autopix_enabled_var.get()) else "disabled"))

        self.btn_start_manual.configure(state=("disabled" if running else "normal"))
        self.btn_verify_phone.configure(state=("normal" if running else "disabled"))
        self.btn_stop.configure(state=("normal" if running else "disabled"))
        self.chk_verify_phone.configure(state=state)
        for btn in self.quick_game_buttons:
            btn.configure(state="normal")
        
    def start_browsers(self):
        """Launch isolated Chromium windows without creating accounts."""
        self._start(False)

    def _start(self, run_auto: bool):
        # Legacy callers cannot re-enable the removed account automation.
        if run_auto or (self.worker_thread and self.worker_thread.is_alive()):
            return

        try:
            normalized_urls = normalize_url_list(self.urls_var.get())
        except ValueError as exc:
            self._set_status(str(exc))
            self._log(f"[UI] URL inválida: {exc}")
            return
        urls = ", ".join(normalized_urls)
        self.urls_var.set(urls)
        try:
            mode = self._device_mode_for_launch()
        except ValueError as exc:
            self._set_status(str(exc))
            self._log(f"[UI] {exc}")
            return
        count = self._get_int(self.count_var, default=1, min_v=1, max_v=50)
        self._log(f"[Abrir Navegadores] Filha: {count} janela(s) por link; "
                  f"{len(normalized_urls)} link(s).")

        url_list = [u.strip() for u in urls.split(",") if u.strip()]
        total_urls = len(url_list) if url_list else 1
        self.available_instance_ids = list(range(1, (total_urls * count) + 1))
        self._update_instance_target_label()

        headless = bool(self.headless_var.get())
        keep_profiles = bool(self.keep_profiles_var.get())
        use_same_identity = bool(self.same_identity_var.get()) and run_auto
        start_game_enabled = bool(self.start_game_enabled_var.get()) and run_auto
        start_game_name = self.start_game_name_var.get().strip()
        start_game_id = self.start_game_id_var.get().strip()

        proxy = self._validate_proxy()
        if self.proxy_var.get().strip() and proxy is None:
            return

        # Launches only browsers, without JSON accounts or generated identities.
        launch_plans = None

        self.save_settings()

        if use_same_identity:
            self._log("[UI] Mesmo nick/email ativo: a 1ª ocorrência de cada site entra no grupo compartilhado; repetidos ficam individuais.")

        autopix_enabled = bool(self.autopix_enabled_var.get()) and run_auto
        autochave_enabled = bool(self.autochave_enabled_var.get()) and run_auto
        autopix_bank = (self.autopix_bank_var.get().strip() or get_default_bank())
        if autopix_bank not in BANK_URLS:
            autopix_bank = get_default_bank()

        if autopix_enabled or autochave_enabled:
            modos = []
            if autopix_enabled:
                modos.append("AutoPIX")
            if autochave_enabled:
                modos.append("AutoChave")
            self._log(f"[Área Pix] {' + '.join(modos)} ativo(s) para {autopix_bank} no escopo '{AREA_PIX_SCOPE}'.")

        self.stop_event.clear()
        self._set_running_state(True)
        self._set_status("Executando...")

        # evita salvar posição automaticamente ao iniciar (limpa pedidos pendentes)
        try:
            self.winpos_controller.reset()
        except Exception:
            pass

        try:
            self.verify_phone_controller.reset()
        except Exception:
            pass

        def log_threadsafe(m: str):
            self.after(0, lambda: self._log(m))

        def worker():
            try:
                delay_seconds = self._get_float(self.delay_var, default=1.0, min_v=0.0, max_v=9999.0)

                if autopix_enabled or autochave_enabled:
                    log_threadsafe(f"[Área Pix] Garantindo sessão compartilhada de {autopix_bank} para o escopo '{AREA_PIX_SCOPE}'...")
                    ok = ensure_bank_session(
                        autopix_bank,
                        new_session=False,
                        log=log_threadsafe,
                        wait_ready=False,
                    )
                    if ok:
                        log_threadsafe(f"[Área Pix] Sessão de {autopix_bank} disponível para o escopo '{AREA_PIX_SCOPE}'.")
                    else:
                        log_threadsafe(f"[Área Pix] Não foi possível iniciar a sessão de {autopix_bank}.")

                run_all(
                    urls_string=urls,
                    mode=mode,
                    count_per_url=count,
                    headless=headless,
                    proxy=proxy,
                    auto_goto=bool(url_list),
                    keep_profiles=True,
                    run_auto=run_auto,
                    start_game_enabled=start_game_enabled,
                    start_game_name=start_game_name,
                    start_game_id=start_game_id,
                    stop_event=self.stop_event,
                    winpos_controller=self.winpos_controller,
                    speed_controller=self.speed_controller,
                    verify_phone_controller=self.verify_phone_controller,
                    mirror=self.mirror,
                    withdraw_controller=self.withdraw_controller,
                    game_launch_controller=self.game_launch_controller,
                    log=log_threadsafe,
                    delay_seconds=delay_seconds,
                    use_same_identity=use_same_identity,
                    instance_target_controller=self.instance_target_controller,
                    automation_callback=None,
                    instance_plans=launch_plans,
                )
            except Exception as e:
                log_threadsafe(f"[ERRO] Worker: {e}")
            finally:
                self.after(0, lambda: self._set_running_state(False))
                self.after(0, lambda: self._set_status("Pronto."))

        self.worker_thread = threading.Thread(target=worker, daemon=True)
        self.worker_thread.start()

    def stop_run(self):
        if not (self.worker_thread and self.worker_thread.is_alive()):
            return
        self.stop_event.set()
        self._set_status("Parando...")
        self._log("[UI] ⏹️ Parada solicitada.")


def run_standalone():
    root = ctk.CTk()
    root.title("Criar Filha")
    root.geometry("1040x720")
    root.minsize(980, 660)
    FilhaTab(root).pack(fill="both", expand=True)
    root.mainloop()