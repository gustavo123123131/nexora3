import threading
import queue
import time
from dataclasses import dataclass
from typing import Dict, Any, Optional, Tuple


@dataclass
class State:
    enabled: bool = False
    master: int = 1  # master fixo: tela 1


class MirrorEasy:
    """
    Mirror 100% thread-safe com Playwright Sync:
    - o callback do binding NÃO chama Playwright (só enfileira)
    - cada instância aplica na própria page (na própria thread) via tick()

    ✅ Versão "jogo/iframe friendly" (SEM extensão):
    - captura eventos (pointer/mouse/wheel/key) dentro de TODOS os frames possíveis (via frame.evaluate)
    - replica por "entrada física" do Playwright (page.mouse / page.keyboard), NÃO por DOM/eventos JS
    - para frames: converte coordenada do frame -> coordenada absoluta usando bounding_box do iframe

    ⚠️ Observação:
    - se o jogo usar pointer lock / raw input, alguns movimentos podem ficar limitados,
      mas clicks/wheel/teclas e movimentos comuns normalmente funcionam.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self.state = State()
        self._inbox: Dict[int, "queue.Queue[dict]"] = {}

    # ---------- helpers ----------
    def _inject_frames(self, page: Any, js: str):
        try:
            page.evaluate(js)
        except Exception:
            pass

        try:
            for fr in page.frames:
                try:
                    fr.evaluate(js)
                except Exception:
                    pass
        except Exception:
            pass

    # ---------- UI state ----------
    def set(self, enabled: bool, master: int = 1):
        with self._lock:
            prev = bool(self.state.enabled)

            self.state.enabled = bool(enabled)
            self.state.master = 1  # ignora master diferente de 1

            # 🔥 evita espelhar ações antigas:
            # - ao ligar: limpa qualquer backlog antigo
            # - ao desligar: limpa também
            if prev != self.state.enabled:
                for _idx, q in self._inbox.items():
                    try:
                        while True:
                            q.get_nowait()
                    except Exception:
                        pass

    def get(self) -> State:
        with self._lock:
            return State(enabled=self.state.enabled, master=self.state.master)

    # ---------- registry ----------
    def register(self, idx: int):
        with self._lock:
            if idx not in self._inbox:
                self._inbox[idx] = queue.Queue()

    def unregister(self, idx: int):
        with self._lock:
            self._inbox.pop(idx, None)

    # ---------- receiver (survive goto/reload + frames dinâmicos) ----------
    def ensure_receiver(self, page: Any):
        # instala receiver em todos os frames (main + iframes), porque frames podem nascer depois
        self._inject_frames(page, self._receiver_js())

        st = self.get()

        try:
            page.evaluate("(en) => { window.__MIRROR_ENABLED__ = !!en; }", bool(st.enabled))
        except Exception:
            pass

        try:
            for fr in page.frames:
                try:
                    fr.evaluate("(en) => { window.__MIRROR_ENABLED__ = !!en; }", bool(st.enabled))
                except Exception:
                    pass
        except Exception:
            pass

    def install_receiver(self, page: Any):
        self.ensure_receiver(page)

    # ---------- master setup ----------
    def ensure_master(self, idx: int, page: Any):
        st = self.get()

        if not st.enabled or st.master != idx:
            # remove bolinha e reseta flags (main + frames)
            try:
                page.evaluate(
                    "() => { "
                    "try { document.getElementById('__mirror_dot__')?.remove(); } catch {} "
                    "try { window.__MIRROR_MASTER_READY__ = false; } catch {} "
                    "}"
                )
            except Exception:
                pass

            try:
                for fr in page.frames:
                    try:
                        fr.evaluate(
                            "() => { "
                            "try { document.getElementById('__mirror_dot__')?.remove(); } catch {} "
                            "try { window.__MIRROR_MASTER_READY__ = false; } catch {} "
                            "}"
                        )
                    except Exception:
                        pass
            except Exception:
                pass

            try:
                setattr(page, "_mirror_master_ready", False)
            except Exception:
                pass
            return

        # já instalado? valida se o binding ainda existe
        try:
            if getattr(page, "_mirror_master_ready", False):
                ok = page.evaluate("() => !!window.__MIRROR_MASTER__ && (typeof window.__MIRROR_SEND__ === 'function')")
                if ok:
                    try:
                        page.evaluate(self._dot_js())
                    except Exception:
                        pass

                    # frames podem nascer depois: reinjeta capture sempre
                    self._inject_frames(page, self._master_capture_js())

                    return

                try:
                    setattr(page, "_mirror_master_ready", False)
                except Exception:
                    pass
        except Exception:
            pass

        # binding: callback NÃO chama Playwright, só enfileira
        try:
            page.expose_binding(
                "__MIRROR_SEND__",
                lambda _src, payload: self._broadcast(idx, payload),
            )
        except Exception:
            pass

        # valida binding no JS (main frame)
        try:
            ok = page.evaluate("() => (typeof window.__MIRROR_SEND__ === 'function')")
            if not ok:
                try:
                    setattr(page, "_mirror_master_ready", False)
                except Exception:
                    pass
                return
        except Exception:
            return

        # instala captura + bolinha no main
        try:
            page.evaluate(self._master_capture_js())
            page.evaluate(self._dot_js())
            page.evaluate("() => { window.__MIRROR_MASTER_READY__ = true; }")
        except Exception:
            return

        # instala captura também nos frames (quando possível)
        self._inject_frames(page, self._master_capture_js())

        try:
            setattr(page, "_mirror_master_ready", True)
        except Exception:
            pass

    # ---------- per-instance tick ----------
    def tick(self, idx: int, page: Any):
        """
        Chame dentro do loop da instância (mesma thread da page):
        - garante receiver após navegações
        - instala/remove master automaticamente (#1)
        - aplica eventos pendentes por mouse/keyboard reais
        """
        self.ensure_receiver(page)

        st = self.get()

        if not st.enabled:
            try:
                setattr(page, "_mirror_master_ready", False)
            except Exception:
                pass

        self.ensure_master(idx, page)

        q = self._get_queue(idx)
        if q is None:
            return

        # ---- drenagem inteligente (evita backlog e desync) ----
        MAX_DRAIN = 800  # segurança: não deixa uma tela tocar backlog infinito

        drained = 0
        last_mm = None

        wheel_dx = 0
        wheel_dy = 0
        wheel_last = None

        batch: list[dict] = []

        while drained < MAX_DRAIN:
            try:
                msg = q.get_nowait()
            except Exception:
                break

            drained += 1

            try:
                t = msg.get("t")

                # compacta mousemove: guarda só o último
                if t == "mm":
                    last_mm = msg
                    continue

                # compacta wheel: soma tudo (mantém o "último" só pra copiar metadados)
                if t == "wheel":
                    try:
                        wheel_dx += int(msg.get("dx", 0) or 0)
                    except Exception:
                        pass
                    try:
                        wheel_dy += int(msg.get("dy", 0) or 0)
                    except Exception:
                        pass
                    wheel_last = msg
                    continue

                # outros eventos preserva ordem
                batch.append(msg)

            except Exception:
                pass

        # se tinha wheel acumulado, reconstroi 1 evento
        if wheel_last and (wheel_dx != 0 or wheel_dy != 0):
            w = dict(wheel_last)
            w["dx"] = wheel_dx
            w["dy"] = wheel_dy
            batch.append(w)

        # aplica o último mousemove no fim (posição mais recente = "tempo real")
        if last_mm:
            batch.append(last_mm)

        # se estourou o MAX_DRAIN, limpa o resto da fila pra não desincronizar
        if drained >= MAX_DRAIN:
            try:
                while True:
                    q.get_nowait()
            except Exception:
                pass

        # ---- processa a batch compactada ----
        for msg in batch:
            try:
                t = msg.get("t")

                if t == "reload":
                    try:
                        page.reload(wait_until="domcontentloaded", timeout=30000)
                    except Exception:
                        pass
                    try:
                        setattr(page, "_mirror_master_ready", False)
                    except Exception:
                        pass
                    continue

                if t == "pwkey":
                    self._apply_key(page, msg)
                    continue

                if t in ("md", "mu", "mm", "wheel", "click"):
                    self._apply_pointer(page, msg)
                    continue

            except Exception:
                pass

    # ---------- internal ----------
    def _get_queue(self, idx: int) -> Optional["queue.Queue[dict]"]:
        with self._lock:
            return self._inbox.get(idx)

    def _broadcast(self, source_idx: int, payload: dict):
        with self._lock:
            targets: Tuple[Tuple[int, "queue.Queue[dict]"], ...] = tuple(self._inbox.items())

        for idx, q in targets:
            if idx == source_idx:
                continue
            try:
                q.put(payload)
            except Exception:
                pass

    def _find_frame_by_url(self, page: Any, frame_url: Optional[str]):
        if not frame_url:
            return None
        best = None
        best_score = -1

        try:
            for fr in page.frames:
                try:
                    u = fr.url or ""
                except Exception:
                    continue

                if u == frame_url:
                    return fr

                # heurística: "contém" (caso o browser normalize urls)
                if frame_url and frame_url in u:
                    score = len(frame_url)
                    if score > best_score:
                        best_score = score
                        best = fr
        except Exception:
            pass

        return best

    def _frame_abs_offset(self, page: Any, frame_url: Optional[str]) -> Tuple[float, float]:
        """
        Retorna (ox, oy) para converter coordenadas (clientX/clientY) do frame em coords absolutas da página.
        - main frame => (0,0)
        - iframe => bounding_box do frame_element (x,y)
        """
        if not frame_url:
            return 0.0, 0.0

        fr = self._find_frame_by_url(page, frame_url)
        if fr is None:
            return 0.0, 0.0

        try:
            if fr == page.main_frame:
                return 0.0, 0.0
        except Exception:
            pass

        try:
            el = fr.frame_element()
            box = el.bounding_box()
            if box and "x" in box and "y" in box:
                return float(box["x"]), float(box["y"])
        except Exception:
            pass

        return 0.0, 0.0

    def _apply_pointer(self, page: Any, msg: dict):
        frame_url = msg.get("frame_url")
        ox, oy = self._frame_abs_offset(page, frame_url)

        try:
            x = float(msg.get("x", 0.0)) + ox
            y = float(msg.get("y", 0.0)) + oy
        except Exception:
            return

        t = msg.get("t")
        btn = int(msg.get("button", 0) or 0)
        button_map = {0: "left", 1: "middle", 2: "right"}
        pw_button = button_map.get(btn, "left")

        if t == "mm":
            try:
                page.mouse.move(x, y)
            except Exception:
                pass
            return

        if t == "md":
            try:
                page.mouse.move(x, y)
            except Exception:
                pass
            try:
                page.mouse.down(button=pw_button)
            except Exception:
                pass
            return

        if t == "mu":
            try:
                page.mouse.move(x, y)
            except Exception:
                pass
            try:
                page.mouse.up(button=pw_button)
            except Exception:
                pass
            return

        if t == "click":
            try:
                page.mouse.click(x, y, button=pw_button)
            except Exception:
                pass
            return

        if t == "wheel":
            try:
                dx = float(msg.get("dx", 0.0) or 0.0)
                dy = float(msg.get("dy", 0.0) or 0.0)
            except Exception:
                dx, dy = 0.0, 0.0

            # garante que o mouse está no lugar (alguns jogos dependem disso)
            try:
                page.mouse.move(x, y)
            except Exception:
                pass
            try:
                page.mouse.wheel(dx, dy)
            except Exception:
                pass
            return

    def _apply_key(self, page: Any, msg: dict):
        mods = []
        if msg.get("ctrl"):
            mods.append("Control")
        if msg.get("alt"):
            mods.append("Alt")
        if msg.get("shift"):
            mods.append("Shift")
        if msg.get("meta"):
            mods.append("Meta")

        key = str(msg.get("k") or "")
        code = str(msg.get("c") or "")

        def to_pw_key(k: str, c: str) -> str:
            if k == " ":
                return "Space"
            m = {
                "Enter": "Enter",
                "Backspace": "Backspace",
                "Tab": "Tab",
                "Escape": "Escape",
                "Esc": "Escape",
                "Delete": "Delete",
                "Insert": "Insert",
                "Home": "Home",
                "End": "End",
                "PageUp": "PageUp",
                "PageDown": "PageDown",
                "ArrowUp": "ArrowUp",
                "ArrowDown": "ArrowDown",
                "ArrowLeft": "ArrowLeft",
                "ArrowRight": "ArrowRight",
            }
            if k in m:
                return m[k]
            if k.startswith("F") and k[1:].isdigit():
                return k
            if len(k) == 1:
                return k
            if c.startswith("Key") and len(c) == 4:
                return c[-1]
            return k or c or ""

        base = to_pw_key(key, code)

        # caractere simples sem modificadores => type
        if not mods and len(base) == 1:
            try:
                page.keyboard.type(base)
            except Exception:
                pass
            return

        combo = "+".join(mods + [base]) if mods else base
        try:
            page.keyboard.press(combo)
        except Exception:
            pass

    # ---------- JS payload handlers ----------
    @staticmethod
    def _receiver_js() -> str:
        # receiver leve: só garante flags e evita duplicar
        return r"""
(() => {
  if (window.__MIRROR_RECEIVER__) return;
  window.__MIRROR_RECEIVER__ = true;
  window.__MIRROR_ENABLED__ = window.__MIRROR_ENABLED__ ?? false;
})();
"""

    @staticmethod
    def _master_capture_js() -> str:
        # captura "física" (pointer/mouse/wheel/key) e envia com frame_url + coords do frame
        # throttle no pointermove pra não travar
        return r"""
(() => {
  if (window.__MIRROR_MASTER__) return;
  window.__MIRROR_MASTER__ = true;

  function frameUrl(){
    try { return String(window.location.href || ""); } catch { return ""; }
  }

  function send(p){
    try {
      if (!window.__MIRROR_ENABLED__) return;

      p.frame_url = frameUrl();

      if (typeof window.__MIRROR_SEND__ === "function") {
        window.__MIRROR_SEND__(p);
      } else {
        // fallback pro top (bridge simples)
        try { window.top && window.top.__MIRROR_SEND__ && window.top.__MIRROR_SEND__(p); } catch {}
      }
    } catch {}
  }

  // CLICK "real" via down+up
  window.addEventListener("pointerdown", (e) => {
    send({ t:"md", x:e.clientX, y:e.clientY, button:e.button });
  }, true);

  window.addEventListener("pointerup", (e) => {
    send({ t:"mu", x:e.clientX, y:e.clientY, button:e.button });
  }, true);

  // pointermove com throttle
  let lastMove = 0;
  window.addEventListener("pointermove", (e) => {
    const now = performance.now();
    if (now - lastMove < 16) return; // ~60fps
    lastMove = now;
    send({ t:"mm", x:e.clientX, y:e.clientY, button:e.button });
  }, true);

  // wheel
  window.addEventListener("wheel", (e) => {
    send({ t:"wheel", x:e.clientX, y:e.clientY, dx:e.deltaX, dy:e.deltaY });
  }, { passive:true, capture:true });

  // teclas
  window.addEventListener("keydown", (e) => {
    if (e.repeat) return;

    // reload (F5 / Ctrl+R)
    if (e.key === "F5" || (e.ctrlKey && (e.key === "r" || e.key === "R"))) {
      send({ t:"reload" });
      return;
    }

    send({
      t:"pwkey",
      k:e.key,
      c:e.code,
      ctrl:!!e.ctrlKey,
      alt:!!e.altKey,
      shift:!!e.shiftKey,
      meta:!!e.metaKey
    });
  }, true);
})();
"""

    @staticmethod
    def _dot_js() -> str:
        return r"""
(() => {
  try { document.getElementById("__mirror_dot__")?.remove(); } catch {}
  const d = document.createElement("div");
  d.id = "__mirror_dot__";
  d.style.position = "fixed";
  d.style.left = "10px";
  d.style.top = "10px";
  d.style.width = "12px";
  d.style.height = "12px";
  d.style.borderRadius = "999px";
  d.style.background = "#ff2d2d";
  d.style.zIndex = "2147483647";
  d.style.boxShadow = "0 0 8px rgba(255,45,45,0.8)";
  d.style.pointerEvents = "none";
  document.documentElement.appendChild(d);
})();
"""