document.addEventListener("DOMContentLoaded", () => {
  const delayMs = document.getElementById("delayMs");
  const gapMs = document.getElementById("gapMs");
  const status = document.getElementById("status");

  chrome.storage.sync.get({ delayMs: 1200, gapMs: 14000, autoSubmit: true }, (cfg) => {
    delayMs.value = String(cfg.delayMs || 1200);
    gapMs.value = String(cfg.gapMs || 14000);
  });

  document.getElementById("save").addEventListener("click", () => {
    chrome.storage.sync.set({
      autoSubmit: true,
      delayMs: Math.max(500, Math.min(8000, Number(delayMs.value) || 1200)),
      gapMs: Math.max(5000, Math.min(60000, Number(gapMs.value) || 14000)),
    }, () => {
      status.hidden = false;
      setTimeout(() => { status.hidden = true; }, 1200);
    });
  });
});
