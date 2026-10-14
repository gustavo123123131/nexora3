const DEFAULTS = {
  speed: 1.0,
  perfNow: true,
  dateNow: false,     // mais seguro
  setTimeout: true,
  setInterval: true,
  raf: true,
  mediaRate: false,
  injectIframes: true // ✅ NOVO: injeta iframes depois do load
};

function installHooks(cfg) {
  // Já instalado: só atualiza cfg
  if (window.__UHSH_INSTALLED__) {
    window.__UHSH_applyPatch__?.(cfg || {});
    return;
  }
  window.__UHSH_INSTALLED__ = true;

  let C = { ...(cfg || {}) };

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

  function speed() {
    return clamp(asNum(C.speed, 1.0), 0.01, 100);
  }

  function freeze() {
    const s = speed();
    const rp = realPerfNow();
    const rd = realDateNow();

    baseVirtualPerf = baseVirtualPerf + (rp - baseRealPerf) * s;
    baseRealPerf = rp;

    baseVirtualDate = baseVirtualDate + (rd - baseRealDate) * s;
    baseRealDate = rd;
  }

  function applyPatch(patch) {
    freeze();
    C = { ...C, ...(patch || {}) };
  }
  window.__UHSH_applyPatch__ = applyPatch;

  // performance.now (tenta mais compatível)
  try {
    const hookedPerfNow = function () {
      if (!C.perfNow) return realPerfNow();
      const rp = realPerfNow();
      return baseVirtualPerf + (rp - baseRealPerf) * speed();
    };

    const d1 = Object.getOwnPropertyDescriptor(performance, "now");
    if (d1?.configurable) {
      Object.defineProperty(performance, "now", { configurable: true, value: hookedPerfNow });
    } else {
      const proto = performance?.constructor?.prototype;
      const d2 = proto && Object.getOwnPropertyDescriptor(proto, "now");
      if (d2?.configurable) {
        Object.defineProperty(proto, "now", { configurable: true, value: hookedPerfNow });
      } else {
        performance.now = hookedPerfNow;
      }
    }
  } catch {}

  // Date.now
  try {
    Date.now = function () {
      if (!C.dateNow) return realDateNow();
      const rd = realDateNow();
      return baseVirtualDate + (rd - baseRealDate) * speed();
    };
  } catch {}

  // timers
  const scaleDelay = (d) => asNum(d, 0) / speed();

  try {
    window.setTimeout = function (fn, delay, ...rest) {
      if (!C.setTimeout) return realSetTimeout(fn, delay, ...rest);
      return realSetTimeout(fn, scaleDelay(delay), ...rest);
    };
  } catch {}

  try {
    window.setInterval = function (fn, delay, ...rest) {
      if (!C.setInterval) return realSetInterval(fn, delay, ...rest);
      return realSetInterval(fn, scaleDelay(delay), ...rest);
    };
  } catch {}

  // RAF
  try {
    window.requestAnimationFrame = function (cb) {
      if (!C.raf) return realRAF(cb);
      return realRAF(() => cb(performance.now()));
    };
  } catch {}

  // media playbackRate (opcional)
  function applyMediaRate() {
    if (!C.mediaRate) return;
    const s = speed();
    document.querySelectorAll("video,audio").forEach((m) => {
      try { m.playbackRate = s; } catch {}
    });
  }
  realSetInterval(applyMediaRate, 1000);
}

// filtra frames ruins (onde dava “export”/quebra)
function isInjectableFrameUrl(url) {
  if (!url || typeof url !== "string") return false;
  if (url.startsWith("http://") || url.startsWith("https://")) return true;
  return false; // bloqueia about:, chrome-extension:, data:, blob:, file:
}

async function injectIntoFrames(tabId, frameIds, cfg) {
  if (!frameIds.length) return;
  await chrome.scripting.executeScript({
    target: { tabId, frameIds },
    world: "MAIN",
    func: installHooks,
    args: [cfg]
  });
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  (async () => {
    if (!msg || !msg.type) return;

    const tabId = sender.tab?.id || msg.tabId;
    if (!tabId) return sendResponse?.({ ok: false, error: "no_tab" });

    const cfg = msg.cfg || DEFAULTS;

    if (msg.type === "INJECT_PHASE1") {
      // ✅ Fase 1: cedo, só frame 0 (não trava boot, evita cache)
      try {
        await injectIntoFrames(tabId, [0], cfg);
        return sendResponse({ ok: true });
      } catch (e) {
        return sendResponse({ ok: false, error: String(e) });
      }
    }

    if (msg.type === "INJECT_PHASE2") {
      // ✅ Fase 2: depois do load, injeta nos iframes válidos
      if (!cfg.injectIframes) return sendResponse({ ok: true, skipped: true });

      try {
        const frames = await chrome.webNavigation.getAllFrames({ tabId });
        const wanted = frames
          .filter(f => f.frameId !== 0)
          .filter(f => isInjectableFrameUrl(f.url))
          .map(f => f.frameId);

        // também aplica patch no frame 0 (pra garantir cfg atualizada)
        const targets = [0, ...wanted];

        await injectIntoFrames(tabId, targets, cfg);
        return sendResponse({ ok: true, injectedFrames: targets.length });
      } catch (e) {
        return sendResponse({ ok: false, error: String(e) });
      }
    }

    if (msg.type === "PATCH_CONFIG") {
      // patch leve: atualiza frame 0 e (se habilitado) iframes também
      try {
        const frames = await chrome.webNavigation.getAllFrames({ tabId });
        const wanted = (cfg.injectIframes ? frames.filter(f => isInjectableFrameUrl(f.url)).map(f => f.frameId) : [0]);
        const unique = [...new Set(wanted)];
        await injectIntoFrames(tabId, unique, cfg);
        return sendResponse({ ok: true });
      } catch (e) {
        return sendResponse({ ok: false, error: String(e) });
      }
    }
  })();

  return true;
});

chrome.runtime.onInstalled.addListener(() => {
  chrome.storage.local.get(DEFAULTS, (cur) => {
    chrome.storage.local.set({ ...DEFAULTS, ...cur });
  });
});
