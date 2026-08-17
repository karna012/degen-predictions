/**
 * X compose filler — top frame only, one image per pending id.
 * Does NOT auto-run on load (background triggers DEGEN_TRY_FILL once).
 */
(function () {
  if (window !== window.top) return;
  if (window.__degenXPosterBound) return;
  window.__degenXPosterBound = true;

  const MAX_WAIT_MS = 45000;
  let filling = false;
  let attachedForId = "";

  function normalizeText(s) {
    return String(s || "").replace(/\u00a0/g, " ").replace(/\s+/g, " ").trim();
  }

  function sleep(ms) {
    return new Promise((r) => setTimeout(r, ms));
  }

  function visible(el) {
    if (!el) return false;
    const st = window.getComputedStyle(el);
    if (st.display === "none" || st.visibility === "hidden") return false;
    const r = el.getBoundingClientRect();
    return r.width > 8 && r.height > 8;
  }

  async function waitFor(fn, timeout = MAX_WAIT_MS) {
    const start = Date.now();
    while (Date.now() - start < timeout) {
      try {
        const v = fn();
        if (v) return v;
      } catch (_) {}
      await sleep(200);
    }
    return null;
  }

  function findTextbox() {
    const nodes = [
      ...document.querySelectorAll('[data-testid="tweetTextarea_0"]'),
      ...document.querySelectorAll('[role="textbox"][contenteditable="true"]'),
    ];
    return nodes.find(visible) || null;
  }

  function countMediaTiles() {
    const imgs = document.querySelectorAll(
      '[data-testid="attachments"] img, [data-testid="previewInterstitial"] img, [data-testid="tweetPhoto"] img'
    );
    return imgs.length;
  }

  function findComposeFileInput(textbox) {
    const dialog = textbox && textbox.closest('[role="dialog"]');
    const scope = dialog || document;
    const inputs = [...scope.querySelectorAll('input[data-testid="fileInput"]')];
    const visibleInput = inputs.find((el) => {
      if (el.type !== "file") return false;
      const r = el.getBoundingClientRect();
      return r.width > 0 || r.height > 0 || el.multiple !== undefined;
    });
    return visibleInput || inputs[0] || null;
  }

  function findPostButton() {
    const candidates = [
      ...document.querySelectorAll('[data-testid="tweetButton"]'),
      ...document.querySelectorAll('[data-testid="tweetButtonInline"]'),
    ];
    return candidates.find((el) => {
      if (!visible(el)) return false;
      if (el.getAttribute("aria-disabled") === "true") return false;
      if (el.disabled) return false;
      return true;
    }) || null;
  }

  async function setText(box, text) {
    box.focus();
    await sleep(120);
    const target = normalizeText(text);
    try {
      document.execCommand("selectAll", false, null);
      document.execCommand("insertText", false, text);
    } catch (_) {}
    let cur = normalizeText(box.innerText || box.textContent || "");
    if (cur !== target) {
      box.textContent = "";
      box.dispatchEvent(new InputEvent("beforeinput", { bubbles: true, data: text, inputType: "insertText" }));
      box.textContent = text;
      box.dispatchEvent(new InputEvent("input", { bubbles: true, data: text, inputType: "insertText" }));
      box.dispatchEvent(new Event("change", { bubbles: true }));
      cur = normalizeText(box.innerText || box.textContent || "");
    }
    return cur === target;
  }

  function base64ToFile(base64, mime, name) {
    const bin = atob(base64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return new File([bytes], name || "degen-slip.png", { type: mime || "image/png" });
  }

  async function attachFileOnce(file, pendingId) {
    if (attachedForId === pendingId) return countMediaTiles() >= 1;
    if (countMediaTiles() >= 1) {
      attachedForId = pendingId;
      return true;
    }

    const box = findTextbox();
    const input = findComposeFileInput(box);
    if (!input) return false;

    attachedForId = pendingId;
    const dt = new DataTransfer();
    dt.items.add(file);
    input.files = dt.files;
    input.dispatchEvent(new Event("change", { bubbles: true }));

    await waitFor(() => countMediaTiles() >= 1, 15000);

    // If X somehow stacked two previews, stop — do not attach again.
    if (countMediaTiles() > 1) {
      console.warn("[degen-x-poster] multiple media tiles after attach:", countMediaTiles());
    }
    try {
      await chrome.runtime.sendMessage({ type: "DEGEN_IMAGE_ATTACHED", pendingId });
    } catch (_) {}
    return countMediaTiles() >= 1;
  }

  async function fillOnce(expectedId) {
    if (filling) return;
    filling = true;
    try {
      const claim = await chrome.runtime.sendMessage({
        type: "DEGEN_CLAIM_PENDING",
        payload: { frameId: 0 },
      });
      const pending = claim && claim.pending;
      if (!pending || !pending.text) return;
      if (expectedId && pending.id !== expectedId) return;

      const box = await waitFor(findTextbox);
      if (!box) return;

      await sleep(pending.delayMs || 1000);
      const textOk = await setText(box, pending.text);
      if (!textOk) {
        await chrome.runtime.sendMessage({
          type: "DEGEN_RELEASE_PENDING",
          payload: { id: pending.id, retry: true, reason: "text-not-set" },
        });
        return;
      }

      if (pending.imageBase64 && !pending.imageAttached) {
        const file = base64ToFile(pending.imageBase64, pending.imageMime, pending.imageName);
        await attachFileOnce(file, pending.id);
      }

      const btn = await waitFor(findPostButton, 20000);
      if (!btn) {
        await chrome.runtime.sendMessage({
          type: "DEGEN_RELEASE_PENDING",
          payload: { id: pending.id, retry: true, reason: "post-button-missing" },
        });
        return;
      }
      await sleep(800);
      btn.click();
      await sleep(4000);

      await chrome.runtime.sendMessage({
        type: "DEGEN_POST_DONE",
        payload: { id: pending.id, queueIndex: pending.queueIndex },
      });
    } catch (err) {
      console.warn("[degen-x-poster] fill failed", err);
    } finally {
      filling = false;
    }
  }

  chrome.runtime.onMessage.addListener((msg) => {
    if (msg && msg.type === "DEGEN_TRY_FILL") {
      fillOnce(msg.pendingId || "");
    }
  });
})();
