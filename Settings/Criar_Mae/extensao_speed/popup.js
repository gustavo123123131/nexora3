const DEFAULTS = {
  speed: 1.0,
  perfNow: true,
  dateNow: false,
  setTimeout: true,
  setInterval: true,
  raf: true,
  mediaRate: false,
  injectIframes: true
};

const $ = (id) => document.getElementById(id);

function setUI(cfg) {
  $("speed").value = String(cfg.speed);
  $("speedValue").textContent = `${Number(cfg.speed).toFixed(1)}x`;
  for (const k of ["perfNow", "dateNow", "setTimeout", "setInterval", "raf", "mediaRate", "injectIframes"]) {
    $(k).checked = !!cfg[k];
  }
}

document.addEventListener("DOMContentLoaded", () => {
  chrome.storage.local.get(DEFAULTS, (cfg) => setUI(cfg));

  $("speed").addEventListener("input", () => {
    const v = Number($("speed").value);
    $("speedValue").textContent = `${v.toFixed(1)}x`;
    chrome.storage.local.set({ speed: v });
  });

  for (const k of ["perfNow", "dateNow", "setTimeout", "setInterval", "raf", "mediaRate", "injectIframes"]) {
    $(k).addEventListener("change", () => {
      chrome.storage.local.set({ [k]: $(k).checked });
    });
  }

  $("reset").addEventListener("click", () => {
    chrome.storage.local.set({ ...DEFAULTS }, () => setUI(DEFAULTS));
  });
});
