/**
 * Page-context helper: fetch X queue JSON + slip PNGs (uses admin basic-auth session).
 */
(function () {
  async function blobToBase64(blob) {
    const buf = await blob.arrayBuffer();
    const bytes = new Uint8Array(buf);
    let binary = "";
    const chunk = 0x8000;
    for (let i = 0; i < bytes.length; i += chunk) {
      binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
    }
    return btoa(binary);
  }

  async function imageFromUrl(imageUrl) {
    if (!imageUrl) return { imageBase64: "", imageMime: "image/png" };
    const abs = new URL(imageUrl, location.origin).href;
    const resp = await fetch(abs, { credentials: "same-origin" });
    if (!resp.ok) throw new Error("image " + resp.status);
    const blob = await resp.blob();
    return {
      imageBase64: await blobToBase64(blob),
      imageMime: blob.type || "image/png",
    };
  }

  async function postJsonToItem(post) {
    const text = (post.text || "").trim();
    if (!text) return null;
    const imageName = post.image_download_name || "degen-slip.png";
    let imageBase64 = "";
    let imageMime = "image/png";
    if (post.image_url) {
      const img = await imageFromUrl(post.image_url);
      imageBase64 = img.imageBase64;
      imageMime = img.imageMime;
    }
    return { text, imageBase64, imageMime, imageName };
  }

  window.__degenXFetchQueue = async function (preferTelegram) {
    const q = preferTelegram ? "prefer=telegram" : "source=digest";
    const resp = await fetch("/admin/x-queue.json?" + q, { credentials: "same-origin" });
    if (!resp.ok) throw new Error("queue " + resp.status);
    const data = await resp.json();
    const items = [];
    for (const post of data.posts || []) {
      const item = await postJsonToItem(post);
      if (item) items.push(item);
    }
    return items;
  };
})();
