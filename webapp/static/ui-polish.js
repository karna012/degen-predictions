(function () {
  "use strict";

  window.__predictInFlight = 0;

  /** Soft live refresh: longer interval, restore scroll, skip while predicting. */
  window.scheduleSoftReload = function (opts) {
    opts = opts || {};
    var liveMs = opts.liveMs != null ? opts.liveMs : 180000;
    var idleMs = opts.idleMs != null ? opts.idleMs : 900000;
    var hasLive =
      opts.hasLive != null
        ? opts.hasLive
        : document.querySelector(".confidence-alert, [data-live='1']") !== null;
    var delay = hasLive ? liveMs : idleMs;
    window.setTimeout(function () {
      if (window.__predictInFlight > 0) {
        window.scheduleSoftReload({ liveMs: 30000, idleMs: 30000, hasLive: hasLive });
        return;
      }
      try {
        sessionStorage.setItem("dp_scroll_y", String(window.scrollY || 0));
      } catch (e) {}
      window.location.reload();
    }, delay);
  };

  function syncDensityToggle() {
    var btn = document.getElementById("density-toggle");
    if (!btn) return;
    var compact = document.documentElement.classList.contains("density-compact");
    btn.setAttribute("aria-pressed", compact ? "true" : "false");
    btn.textContent = compact ? "Compact" : "Comfort";
  }

  document.addEventListener("DOMContentLoaded", function () {
    try {
      var y = sessionStorage.getItem("dp_scroll_y");
      if (y != null) {
        sessionStorage.removeItem("dp_scroll_y");
        var top = parseInt(y, 10);
        if (!isNaN(top) && top > 0) {
          window.scrollTo(0, top);
        }
      }
    } catch (e) {}

    syncDensityToggle();
    var btn = document.getElementById("density-toggle");
    if (btn) {
      btn.addEventListener("click", function () {
        var root = document.documentElement;
        var next = !root.classList.contains("density-compact");
        root.classList.toggle("density-compact", next);
        try {
          localStorage.setItem("dp-density", next ? "compact" : "comfortable");
        } catch (e) {}
        syncDensityToggle();
      });
    }

    function promptUnlockFirst() {
      if (typeof showToast === "function") {
        showToast("Unlock it first", "info");
      } else {
        window.alert("Unlock it first");
      }
    }

    function cardNeedsUnlock(card) {
      if (!card || card.classList.contains("is-predicted")) return false;
      if (card.getAttribute("data-lock") === "1") return true;
      if (card.querySelector(".js-unlock-slate:not([disabled])")) return true;
      var btn = card.querySelector(".predict-btn");
      if (!btn || btn.disabled || btn.classList.contains("is-unlocked")) return false;
      var label = (btn.textContent || "").toLowerCase();
      return label.indexOf("get prediction") !== -1 || label.indexOf("unlock") !== -1;
    }

    function isUnlockControl(el) {
      return el && el.closest(".js-unlock-slate, .predict-btn");
    }

    function revealSlateCard(card, btn) {
      var empty = card.querySelector(".fixture-empty");
      var outcomes = card.querySelector(".fixture-outcomes");
      if (empty) {
        empty.hidden = true;
        empty.setAttribute("hidden", "");
      }
      if (outcomes) {
        outcomes.hidden = false;
        outcomes.removeAttribute("hidden");
        outcomes.classList.add("is-revealed");
      }
      card.querySelectorAll("[data-unlock-hide]").forEach(function (el) {
        el.hidden = false;
        el.removeAttribute("hidden");
      });
      card.classList.add("is-predicted");
      card.removeAttribute("data-lock");
      if (btn) {
        btn.textContent = "Unlocked";
        btn.classList.add("is-unlocked");
        btn.disabled = true;
      }
    }

    // Unlock Today's Picks / Best Combos cards (same pattern as Get Prediction).
    document.addEventListener("click", function (e) {
      var btn = e.target.closest(".js-unlock-slate");
      if (!btn || btn.disabled) return;
      e.preventDefault();
      e.stopImmediatePropagation();
      var card = btn.closest(".fixture-card");
      if (!card || card.classList.contains("is-predicted")) return;
      revealSlateCard(card, btn);
      var kind = (card.getAttribute("data-unlock-kind") || "pick").replace(/[^a-z]/g, "");
      if (kind !== "combo" && kind !== "pick") kind = "pick";
      var eventName = (card.getAttribute("data-unlock-event") || "").slice(0, 180);
      var url = "/api/unlock/" + kind + "?event=" + encodeURIComponent(eventName);
      try {
        fetch(url, { method: "GET", keepalive: true, credentials: "omit", cache: "no-store" });
      } catch (err) {}
    }, true);

    // Event / card click while locked: do not open the match — ask to unlock first.
    document.addEventListener("click", function (e) {
      if (isUnlockControl(e.target)) return;
      var card = e.target.closest(".fixture-card");
      if (!card || !cardNeedsUnlock(card)) return;
      e.preventDefault();
      e.stopImmediatePropagation();
      promptUnlockFirst();
    }, true);

    document.addEventListener("click", function (e) {
      var card = e.target.closest(".fixture-card[data-href]");
      if (!card) return;
      if (e.target.closest("a, button, input, select, textarea, label")) return;
      if (cardNeedsUnlock(card)) {
        e.preventDefault();
        promptUnlockFirst();
        return;
      }
      var href = card.getAttribute("data-href");
      if (href) window.location.href = href;
    });
    document.addEventListener("keydown", function (e) {
      if (e.key !== "Enter" && e.key !== " ") return;
      var card = e.target.closest(".fixture-card[data-href]");
      if (!card || e.target !== card) return;
      e.preventDefault();
      if (cardNeedsUnlock(card)) {
        promptUnlockFirst();
        return;
      }
      var href = card.getAttribute("data-href");
      if (href) window.location.href = href;
    });

    function syncHeaderHeight() {
      var header = document.querySelector(".site-header");
      if (!header) return;
      var h = Math.ceil(header.getBoundingClientRect().height);
      if (h > 0) {
        document.documentElement.style.setProperty("--header-h", h + "px");
      }
    }
    syncHeaderHeight();
    window.addEventListener("resize", syncHeaderHeight, { passive: true });

    document.querySelectorAll("[data-copy-share]").forEach(function (el) {
      el.addEventListener("click", function () {
        var text = el.getAttribute("data-copy-share") || "";
        if (!text) return;
        var done = function () {
          var prev = el.textContent;
          el.textContent = "Copied";
          setTimeout(function () {
            el.textContent = prev;
          }, 1400);
        };
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text).then(done).catch(function () {
            window.prompt("Copy prediction", text);
          });
        } else {
          window.prompt("Copy prediction", text);
        }
      });
    });

    document.querySelectorAll("[data-bookmark-hint]").forEach(function (el) {
      el.addEventListener("click", function () {
        var isMac = /Mac|iPhone|iPad|iPod/i.test(navigator.platform || "") ||
          /Mac OS/i.test(navigator.userAgent || "");
        var msg = isMac
          ? "Press ⌘+D to bookmark this page — then reopen it for daily picks."
          : "Press Ctrl+D to bookmark this page — then reopen it for daily picks.";
        if (typeof showToast === "function") {
          showToast(msg, "info");
        } else {
          window.alert(msg);
        }
      });
    });

    document.querySelectorAll("[data-print-share]").forEach(function (el) {
      el.addEventListener("click", function () {
        window.print();
      });
    });

    function drawPickShareCard(card) {
      var canvas = document.createElement("canvas");
      var W = 1080;
      var H = 1080;
      canvas.width = W;
      canvas.height = H;
      var ctx = canvas.getContext("2d");
      if (!ctx) return null;
      ctx.fillStyle = "#070c16";
      ctx.fillRect(0, 0, W, H);
      ctx.fillStyle = "#121a2b";
      roundRect(ctx, 48, 48, W - 96, H - 96, 36);
      ctx.fill();
      ctx.fillStyle = "#2dd4bf";
      ctx.fillRect(48, 48, W - 96, 10);
      var matchup = card.getAttribute("data-matchup") || "";
      var pick = card.getAttribute("data-pick") || "";
      var market = card.getAttribute("data-market") || "";
      var pct = card.getAttribute("data-pct") || "";
      var league = card.getAttribute("data-league") || "";
      ctx.fillStyle = "#2dd4bf";
      ctx.font = "700 36px Bai Jamjuree, Segoe UI, sans-serif";
      ctx.fillText("Degen Predictions™", 100, 160);
      ctx.fillStyle = "#9aabc7";
      ctx.font = "600 28px Manrope, Segoe UI, sans-serif";
      ctx.fillText(String(league).toUpperCase(), 100, 220);
      ctx.fillStyle = "#e8eef8";
      ctx.font = "700 52px Bai Jamjuree, Segoe UI, sans-serif";
      wrapText(ctx, matchup, 100, 320, W - 200, 60);
      ctx.fillStyle = "#2dd4bf";
      ctx.font = "700 64px Bai Jamjuree, Segoe UI, sans-serif";
      wrapText(ctx, pick, 100, 520, W - 200, 72);
      ctx.fillStyle = "#9aabc7";
      ctx.font = "600 34px Manrope, Segoe UI, sans-serif";
      var meta = market + (pct ? "  ·  " + pct + "%" : "");
      ctx.fillText(meta, 100, 700);
      ctx.fillStyle = "#2dd4bf";
      ctx.font = "700 28px Manrope, Segoe UI, sans-serif";
      ctx.fillText("Degen Predictions™", 100, 880);
      ctx.fillStyle = "#2dd4bf";
      ctx.font = "600 28px Manrope, Segoe UI, sans-serif";
      ctx.fillText("degenpredictions.com", 100, 920);
      ctx.fillStyle = "#9aabc7";
      ctx.font = "500 24px Manrope, Segoe UI, sans-serif";
      ctx.fillText("Educational ML estimates — not betting advice", 100, 970);
      return canvas;
    }

    function roundRect(ctx, x, y, w, h, r) {
      ctx.beginPath();
      ctx.moveTo(x + r, y);
      ctx.arcTo(x + w, y, x + w, y + h, r);
      ctx.arcTo(x + w, y + h, x, y + h, r);
      ctx.arcTo(x, y + h, x, y, r);
      ctx.arcTo(x, y, x + w, y, r);
      ctx.closePath();
    }

    function wrapText(ctx, text, x, y, maxWidth, lineHeight) {
      var words = String(text || "").split(/\s+/);
      var line = "";
      var yy = y;
      for (var i = 0; i < words.length; i++) {
        var test = line ? line + " " + words[i] : words[i];
        if (ctx.measureText(test).width > maxWidth && line) {
          ctx.fillText(line, x, yy);
          line = words[i];
          yy += lineHeight;
        } else {
          line = test;
        }
      }
      if (line) ctx.fillText(line, x, yy);
    }

    document.querySelectorAll("[data-export-share-card]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var card = btn.closest("[data-share-card]");
        if (!card) return;
        var canvas = drawPickShareCard(card);
        if (!canvas) return;
        canvas.toBlob(function (blob) {
          if (!blob) return;
          var fileName = "degen-pick.png";
          if (navigator.canShare) {
            try {
              var file = new File([blob], fileName, { type: "image/png" });
              if (navigator.canShare({ files: [file] })) {
                navigator.share({
                  files: [file],
                  title: "Degen Predictions pick",
                  text: card.getAttribute("data-pick") || "",
                }).catch(function () {});
                return;
              }
            } catch (e) {}
          }
          var a = document.createElement("a");
          a.href = URL.createObjectURL(blob);
          a.download = fileName;
          document.body.appendChild(a);
          a.click();
          a.remove();
          setTimeout(function () { URL.revokeObjectURL(a.href); }, 1500);
          if (typeof showToast === "function") showToast("Share image downloaded", "info");
        }, "image/png");
      });
    });

    document.querySelectorAll("[data-native-share]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var title = btn.getAttribute("data-share-title") || "Degen Predictions";
        var text = btn.getAttribute("data-share-text") || "";
        if (navigator.share) {
          navigator.share({ title: title, text: text }).catch(function () {});
          return;
        }
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text).then(function () {
            if (typeof showToast === "function") showToast("Copied share text", "info");
          });
        } else {
          window.prompt("Copy share text", text);
        }
      });
    });

    // Brief skeleton flash — skip on narrow / reduced-motion (feels laggy)
    var narrow = window.matchMedia("(max-width: 640px)").matches;
    var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (!narrow && !reduceMotion) {
      var grids = document.querySelectorAll(".cricket-fixture-grid");
      grids.forEach(function (grid) {
        var wrap = grid.parentElement;
        if (!wrap || wrap.querySelector(".skeleton-grid")) return;
        if (!grid.children.length) return;
        wrap.classList.add("is-loading");
        var sk = document.createElement("div");
        sk.className = "skeleton-grid";
        sk.setAttribute("aria-hidden", "true");
        for (var i = 0; i < Math.min(grid.children.length, 4); i++) {
          var card = document.createElement("div");
          card.className = "skeleton-card";
          sk.appendChild(card);
        }
        wrap.insertBefore(sk, grid);
        window.setTimeout(function () {
          wrap.classList.remove("is-loading");
          sk.remove();
        }, 280);
      });
    }
  });
})();
