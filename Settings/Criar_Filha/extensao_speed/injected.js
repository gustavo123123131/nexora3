(() => {
  const CHANNEL = "__UHSH__";
  if (window.__UHSH_HOOKED__) return;
  window.__UHSH_HOOKED__ = true;

  // --- Reais (originais) ---
  const realPerfNow = performance.now.bind(performance);
  const realDateNow = Date.now.bind(Date);
  const realSetTimeout = window.setTimeout.bind(window);
  const realSetInterval = window.setInterval.bind(window);
  const realRAF = window.requestAnimationFrame.bind(window);
  const realCancelRAF = window.cancelAnimationFrame?.bind(window);

  // --- Config ---
  let cfg = {
    speed: 1.0,
    perfNow: true,
    dateNow: true,
    setTimeout: true,
    setInterval: true,
    raf: true
  };

  function asNumber(x, fallback) {
    const n = Number(x);
    return Number.isFinite(n) ? n : fallback;
  }

  function clamp(n, min, max) {
    return Math.min(max, Math.max(min, n));
  }

  // --- Linha do tempo virtual ---
  let baseRealPerf = realPerfNow();
  let baseVirtualPerf = baseRealPerf;

  let baseRealDate = realDateNow();
  let baseVirtualDate = baseRealDate;

  function getSpeed() {
    // evite 0 / negativo
    return clamp(asNumber(cfg.speed, 1.0), 0.01, 100);
  }

  function freezeVirtualClocks() {
    const nowRealPerf = realPerfNow();
    const nowRealDate = realDateNow();
    const speed = getSpeed();

    // "congela" o virtual no tempo atual antes de mexer em base
    baseVirtualPerf = baseVirtualPerf + (nowRealPerf - baseRealPerf) * speed;
    baseRealPerf = nowRealPerf;

    baseVirtualDate = baseVirtualDate + (nowRealDate - baseRealDate) * speed;
    baseRealDate = nowRealDate;
  }

  function setConfig(next) {
    freezeVirtualClocks();
    cfg = { ...cfg, ...next };
  }

  // --- Hooks de now() ---
  Object.defineProperty(performance, "now", {
    configurable: true,
    value: function () {
      if (!cfg.perfNow) return realPerfNow();
      const nowReal = realPerfNow();
      return baseVirtualPerf + (nowReal - baseRealPerf) * getSpeed();
    }
  });

  Date.now = function () {
    if (!cfg.dateNow) return realDateNow();
    const nowReal = realDateNow();
    return baseVirtualDate + (nowReal - baseRealDate) * getSpeed();
  };

  // --- Hooks de setTimeout / setInterval ---
  function scaleDelay(delay) {
    // Se speed > 1, o tempo "passa mais rápido", então queremos delays menores
    // delay_scaled = delay / speed
    const d = asNumber(delay, 0);
    return d / getSpeed();
  }

  window.setTimeout = function (fn, delay, ...rest) {
    if (!cfg.setTimeout) return realSetTimeout(fn, delay, ...rest);
    return realSetTimeout(fn, scaleDelay(delay), ...rest);
  };

  window.setInterval = function (fn, delay, ...rest) {
    if (!cfg.setInterval) return realSetInterval(fn, delay, ...rest);
    return realSetInterval(fn, scaleDelay(delay), ...rest);
  };

  // --- Hook de requestAnimationFrame ---
  // A maioria dos códigos usa o "timestamp" do RAF para calcular delta.
  // Vamos escalar esse timestamp para simular frames mais "adiantados".
  window.requestAnimationFrame = function (cb) {
    if (!cfg.raf) return realRAF(cb);

    return realRAF(function (ts) {
      // ts é baseado em performance.now() "real" do navegador.
      // Vamos converter para um virtual coerente.
      const virtualTs = (cfg.perfNow ? performance.now() : ts);
      cb(virtualTs);
    });
  };

  if (realCancelRAF) {
    window.cancelAnimationFrame = function (id) {
      return realCancelRAF(id);
    };
  }

  // --- Recebe config do content script/popup ---
  window.addEventListener("message", (ev) => {
    if (ev.source !== window) return;
    const msg = ev.data;
    if (!msg || msg.channel !== CHANNEL) return;

    if (msg.type === "SET_CONFIG") {
      setConfig(msg.cfg || {});
    } else if (msg.type === "PATCH_CONFIG") {
      setConfig(msg.cfg || {});
    } else if (msg.type === "RESET") {
      setConfig({
        speed: 1.0,
        perfNow: true,
        dateNow: true,
        setTimeout: true,
        setInterval: true,
        raf: true
      });
    }
  });

})();
