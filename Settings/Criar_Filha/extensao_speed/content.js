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

// ✅ Fase 1: document_start (instala hooks cedo no frame 0)
chrome.storage.local.get(DEFAULTS, (cfg) => {
  chrome.runtime.sendMessage({ type: "INJECT_PHASE1", cfg });
});

// ✅ Fase 2: depois do load (injeta nos iframes do emulador)
window.addEventListener("load", () => {
  chrome.storage.local.get(DEFAULTS, (cfg) => {
    chrome.runtime.sendMessage({ type: "INJECT_PHASE2", cfg });
  });
});

// Patch quando mexe no popup
chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== "local") return;

  chrome.storage.local.get(DEFAULTS, (cfg) => {
    chrome.runtime.sendMessage({ type: "PATCH_CONFIG", cfg });
  });
});
