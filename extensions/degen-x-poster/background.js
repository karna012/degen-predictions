/**
 * Service worker: auto-start X queue after Telegram send; one slip at a time on compose.
 */
const COMPOSE_URL = "https://x.com/compose/post";
const AUTO_SESSION_KEY = "degenXAutoSession";

let lastFillKey = "";
/** In-memory — storage claim alone can race two frames/messages. */
const activeClaims = new Set();

chrome.runtime.onInstalled.addListener(() => {
  chrome.storage.sync.set({
    autoSubmit: true,
    delayMs: 1200,
    gapMs: 14000,
  });
});

/** Admin sends X from the queue. Do not auto-start after Telegram. */
chrome.webNavigation.onCompleted.addListener((details) => {
  if (details.frameId !== 0) return;
  try {
    const url = new URL(details.url);
    if (!/\/admin/.test(url.pathname)) return;
    const tg = url.searchParams.get("tg") || "";
    if (!tg.startsWith("ok_") || url.searchParams.get("xauto") !== "1") return;
    maybeAutoStartFromAdminTab(details.tabId, tg).catch((err) => {
      console.warn("[degen-x-poster] auto-start", err);
    });
  } catch (_) {
    /* ignore */
  }
});

/** Fire fill exactly once when compose finishes loading. */
chrome.tabs.onUpdated.addListener((tabId, info) => {
  if (info.status !== "complete") return;
  maybeTriggerFill(tabId).catch(() => {});
});

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (!msg || !msg.type) return;

  if (msg.type === "DEGEN_START_QUEUE") {
    startQueue(msg.payload)
      .then((ok) => sendResponse({ ok: true, ...ok }))
      .catch((err) => sendResponse({ ok: false, error: String(err && err.message || err) }));
    return true;
  }

  if (msg.type === "DEGEN_CLAIM_PENDING") {
    claimPending(msg.payload || {})
      .then((pending) => sendResponse({ ok: true, pending }))
      .catch((err) => sendResponse({ ok: false, pending: null, error: String(err) }));
    return true;
  }

  if (msg.type === "DEGEN_IMAGE_ATTACHED") {
    markImageAttached((msg.payload && msg.payload.pendingId) || msg.pendingId || "")
      .then(() => sendResponse({ ok: true }))
      .catch(() => sendResponse({ ok: false }));
    return true;
  }

  if (msg.type === "DEGEN_POST_DONE") {
    onPostDone(msg.payload || {})
      .then((ok) => sendResponse({ ok: true, ...ok }))
      .catch((err) => sendResponse({ ok: false, error: String(err && err.message || err) }));
    return true;
  }

  if (msg.type === "DEGEN_RELEASE_PENDING") {
    releasePending(msg.payload || {})
      .then((ok) => sendResponse({ ok: true, ...ok }))
      .catch((err) => sendResponse({ ok: false, error: String(err && err.message || err) }));
    return true;
  }

  if (msg.type === "DEGEN_GET_STATUS") {
    chrome.storage.local.get(["runState"], (data) => {
      sendResponse({ ok: true, state: data.runState || null });
    });
    return true;
  }

  if (msg.type === "DEGEN_STOP_QUEUE") {
    lastFillKey = "";
    activeClaims.clear();
    chrome.storage.local.set({
      runState: null,
      pendingPost: null,
      claimLock: null,
    }, () => sendResponse({ ok: true }));
    return true;
  }

  if (msg.type === "DEGEN_PING") {
    sendResponse({ ok: true, version: chrome.runtime.getManifest().version });
    return false;
  }
});

function normalizeItem(payload) {
  return {
    text: String((payload && payload.text) || "").trim(),
    imageName: String((payload && payload.imageName) || "degen-slip.png"),
    imageBase64: payload && payload.imageBase64 ? String(payload.imageBase64) : "",
    imageMime: String((payload && payload.imageMime) || "image/png"),
  };
}

async function getSync() {
  return chrome.storage.sync.get({ autoSubmit: true, delayMs: 1200, gapMs: 14000 });
}

function todayKey() {
  return new Date().toISOString().slice(0, 10);
}

async function autoStartSeen(token) {
  const data = await chrome.storage.session.get([AUTO_SESSION_KEY]);
  return data[AUTO_SESSION_KEY] === token;
}

async function markAutoStart(token) {
  await chrome.storage.session.set({ [AUTO_SESSION_KEY]: token });
}

async function notifyAdminTab(tabId, msg) {
  if (tabId == null) return;
  try {
    await chrome.tabs.sendMessage(tabId, { type: "DEGEN_STATUS", msg });
  } catch (_) {
    /* admin content script may not be ready */
  }
}

async function fetchQueueItemsInTab(tabId, preferTelegram) {
  await chrome.scripting.executeScript({
    target: { tabId, allFrames: false },
    files: ["content/queue-api.js"],
  });
  const [{ result: items }] = await chrome.scripting.executeScript({
    target: { tabId, allFrames: false },
    func: async (prefer) => window.__degenXFetchQueue(prefer),
    args: [preferTelegram],
  });
  return items || [];
}

async function maybeAutoStartFromAdminTab(tabId, tg) {
  const token = "tg:" + tg + ":" + todayKey();
  if (await autoStartSeen(token)) return;

  const data = await chrome.storage.local.get(["runState"]);
  if (data.runState && data.runState.status === "running") return;

  await markAutoStart(token);
  await notifyAdminTab(tabId, "Telegram sent — starting X auto-post…");

  const items = await fetchQueueItemsInTab(tabId, true);
  if (!items.length) {
    await notifyAdminTab(tabId, "No slips to post to X.");
    return;
  }

  await notifyAdminTab(
    tabId,
    "Auto-posting " + items.length + " slip(s) to X — leave Chrome open."
  );
  await startQueue({ items, autoSubmit: true });
}

async function startQueue(payload) {
  const items = ((payload && payload.items) || [])
    .map(normalizeItem)
    .filter((it) => it.text);
  if (!items.length) throw new Error("No slips to post");

  lastFillKey = "";
  activeClaims.clear();
  const settings = await getSync();
  const runState = {
    items,
    index: 0,
    total: items.length,
    autoSubmit: true,
    delayMs: Number(settings.delayMs) || 1200,
    gapMs: Number(settings.gapMs) || 14000,
    tabId: null,
    startedAt: Date.now(),
    status: "running",
  };

  await chrome.storage.local.set({ runState, pendingPost: null, claimLock: null });
  await armCurrentItem(runState);
  runState.tabId = await openOrReuseCompose(runState.tabId);
  await chrome.storage.local.set({ runState });
  await maybeTriggerFill(runState.tabId);
  return { total: items.length, tabId: runState.tabId };
}

async function openOrReuseCompose(tabId) {
  if (tabId != null) {
    try {
      await chrome.tabs.update(tabId, { url: COMPOSE_URL, active: true });
      return tabId;
    } catch (_) {
      /* fall through */
    }
  }
  const tab = await chrome.tabs.create({ url: COMPOSE_URL, active: true });
  return tab.id;
}

async function armCurrentItem(runState) {
  const item = runState.items[runState.index];
  if (!item) return;
  const pending = {
    id: `${runState.startedAt}-${runState.index}`,
    text: item.text,
    imageName: item.imageName,
    imageBase64: item.imageBase64,
    imageMime: item.imageMime,
    autoSubmit: true,
    delayMs: runState.delayMs,
    createdAt: Date.now(),
    queueIndex: runState.index,
    queueTotal: runState.total,
    claimed: false,
    imageAttached: false,
  };
  await chrome.storage.local.set({ pendingPost: pending, claimLock: null });
}

async function claimPending(meta) {
  const frameId = meta.frameId != null ? meta.frameId : 0;
  if (frameId !== 0) return null;

  const data = await chrome.storage.local.get(["pendingPost"]);
  const pending = data.pendingPost;
  if (!pending || !pending.text) return null;
  if (pending.claimed || pending.imageAttached) return null;
  if (activeClaims.has(pending.id)) return null;

  activeClaims.add(pending.id);
  pending.claimed = true;
  pending.claimedAt = Date.now();
  await chrome.storage.local.set({ pendingPost: pending, claimLock: pending.id });
  return pending;
}

async function markImageAttached(pendingId) {
  const data = await chrome.storage.local.get(["pendingPost"]);
  const pending = data.pendingPost;
  if (!pending || pending.id !== pendingId) return;
  pending.imageAttached = true;
  await chrome.storage.local.set({ pendingPost: pending });
}

async function maybeTriggerFill(tabId) {
  const data = await chrome.storage.local.get(["runState", "pendingPost"]);
  const runState = data.runState;
  const pending = data.pendingPost;
  if (!runState || runState.status !== "running" || runState.tabId !== tabId) return;
  if (!pending || pending.claimed) return;

  const key = `${tabId}:${pending.id}`;
  if (lastFillKey === key) return;
  lastFillKey = key;

  try {
    await chrome.tabs.sendMessage(tabId, { type: "DEGEN_TRY_FILL", pendingId: pending.id }, { frameId: 0 });
  } catch (_) {
    try {
      await chrome.scripting.executeScript({
        target: { tabId, allFrames: false },
        files: ["content/x.js"],
      });
      await chrome.tabs.sendMessage(tabId, { type: "DEGEN_TRY_FILL", pendingId: pending.id }, { frameId: 0 });
    } catch (err) {
      console.warn("[degen-x-poster] trigger fill failed", err);
      lastFillKey = "";
    }
  }
}

async function releasePending(payload) {
  const id = String((payload && payload.id) || "");
  const retry = !!(payload && payload.retry);
  if (!id) return { released: false };

  const data = await chrome.storage.local.get(["runState", "pendingPost"]);
  const pending = data.pendingPost;
  if (!pending || pending.id !== id) return { released: false };

  activeClaims.delete(id);
  lastFillKey = "";
  pending.claimed = false;
  pending.claimedAt = null;
  await chrome.storage.local.set({ pendingPost: pending, claimLock: null });

  if (retry && data.runState && data.runState.status === "running" && data.runState.tabId != null) {
    await new Promise((r) => setTimeout(r, 1000));
    await maybeTriggerFill(data.runState.tabId);
  }
  return { released: true, retry };
}

async function onPostDone(payload) {
  lastFillKey = "";
  if (payload && payload.id) activeClaims.delete(payload.id);
  await chrome.storage.local.set({ pendingPost: null, claimLock: null });

  const data = await chrome.storage.local.get(["runState"]);
  const runState = data.runState;
  if (!runState || runState.status !== "running") {
    return { done: true, remaining: 0 };
  }

  const nextIndex = (runState.index || 0) + 1;
  if (nextIndex >= runState.total) {
    runState.status = "done";
    runState.index = nextIndex;
    await chrome.storage.local.set({ runState });
    return { done: true, remaining: 0 };
  }

  runState.index = nextIndex;
  await chrome.storage.local.set({ runState });

  const gap = Number(runState.gapMs) || 14000;
  await new Promise((r) => setTimeout(r, gap));

  const fresh = (await chrome.storage.local.get(["runState"])).runState;
  if (!fresh || fresh.status !== "running") {
    return { done: true, remaining: 0, stopped: true };
  }

  await armCurrentItem(fresh);
  fresh.tabId = await openOrReuseCompose(fresh.tabId);
  await chrome.storage.local.set({ runState: fresh });
  await maybeTriggerFill(fresh.tabId);
  return { done: false, remaining: fresh.total - fresh.index, nextIndex: fresh.index };
}
