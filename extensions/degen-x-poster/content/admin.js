/**
 * Admin UI: status only — X auto-post starts from background after Telegram send.
 */
(function () {
  const MARK = "data-degen-x-ext";

  function setGlobalStatus(msg) {
    const note = document.getElementById("tw-ext-note");
    if (note) {
      note.textContent = msg;
      note.classList.add("tw-ext-ready");
    }
    document.querySelectorAll(".tw-card .tw-status").forEach((el) => {
      el.textContent = msg;
    });
  }

  async function stopAll() {
    try {
      await chrome.runtime.sendMessage({ type: "DEGEN_STOP_QUEUE" });
      setGlobalStatus("Auto-post stopped.");
    } catch (_) {
      setGlobalStatus("Could not stop (reload extension).");
    }
  }

  function ensureToolbar() {
    const toolbar = document.querySelector(".tw-toolbar");
    if (!toolbar || toolbar.getAttribute(MARK)) return;
    toolbar.setAttribute(MARK, "1");

    const stop = document.createElement("button");
    stop.type = "button";
    stop.className = "btn btn-sm tw-ext-stop-all";
    stop.textContent = "Stop auto-post";
    toolbar.appendChild(stop);
    stop.addEventListener("click", (e) => {
      e.preventDefault();
      stopAll();
    });
  }

  function wirePageButtons() {
    document.querySelectorAll(".tw-ext-post-page").forEach((btn) => {
      if (btn.getAttribute(MARK)) return;
      btn.setAttribute(MARK, "1");
      btn.hidden = true;
    });
  }

  chrome.runtime.onMessage.addListener((msg) => {
    if (msg && msg.type === "DEGEN_STATUS" && msg.msg) {
      setGlobalStatus(msg.msg);
    }
  });

  document.documentElement.setAttribute("data-degen-x-ext-ready", "1");
  const note = document.getElementById("tw-ext-note");
  if (note) {
    note.textContent = "Extension ready — post from this queue when you want X. Nothing auto-posts.";
    note.classList.add("tw-ext-ready");
  }

  ensureToolbar();
  wirePageButtons();

  const root = document.getElementById("tw-queue") || document.body;
  new MutationObserver(() => {
    ensureToolbar();
    wirePageButtons();
  }).observe(root, { childList: true, subtree: true });
})();
