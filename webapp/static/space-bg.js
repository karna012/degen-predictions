/*! Photorealistic space background.
 * Body textures: Solar System Scope (CC BY 4.0) + NASA GSFC solar disk (public domain).
 * Sprites live in /static/space/*.webp
 */
!function () {
  // Query bust when textures change (Cloudflare caches /static with immutable max-age).
  const V = "20260810r2";
  const ASSETS = {
    sun: "/static/space/sun.webp?v=" + V,
    moon: "/static/space/moon.webp?v=" + V,
    saturn: "/static/space/saturn.webp?v=" + V,
    jupiter: "/static/space/jupiter.webp?v=" + V,
    mars: "/static/space/mars.webp?v=" + V,
    earth: "/static/space/earth.webp?v=" + V,
    comet: "/static/space/comet.webp?v=" + V
  };

  function loadImage(src) {
    return new Promise(function (resolve) {
      const img = new Image();
      img.decoding = "async";
      img.onload = function () { resolve(img); };
      img.onerror = function () { resolve(null); };
      img.src = src;
    });
  }

  function makeComet(w, h, seeded) {
    const ang = 0.12 * Math.PI + Math.random() * 0.18 * Math.PI;
    const speed = 1.8 + 2.6 * Math.random();
    return {
      x: seeded ? Math.random() * w : -120 - 220 * Math.random(),
      y: seeded ? Math.random() * h * 0.55 : -80 - 180 * Math.random(),
      vx: Math.cos(ang) * speed,
      vy: Math.sin(ang) * speed,
      scale: 0.55 + 0.75 * Math.random(),
      alpha: 0.45 + 0.4 * Math.random(),
      rot: ang + Math.PI,
      dust: 0.7 + 0.5 * Math.random()
    };
  }

  function boltPath(x0, y0, x1, y1, depth, jag) {
    if (depth <= 0) return [[x0, y0], [x1, y1]];
    const mx = (x0 + x1) / 2 + (Math.random() - 0.5) * jag;
    const my = (y0 + y1) / 2 + (Math.random() - 0.5) * jag * 0.28;
    const a = boltPath(x0, y0, mx, my, depth - 1, jag * 0.55);
    const b = boltPath(mx, my, x1, y1, depth - 1, jag * 0.55);
    return a.concat(b.slice(1));
  }

  function makeStorm(w, h) {
    const x = w * (0.15 + 0.7 * Math.random());
    const y = h * (0.45 + 0.4 * Math.random());
    const main = boltPath(x, 0, x + (Math.random() - 0.5) * w * 0.22, y, 6, 85);
    const branches = [];
    const n = 2 + Math.floor(2 * Math.random());
    for (let i = 0; i < n; i++) {
      const idx = Math.floor(main.length * (0.25 + 0.45 * Math.random()));
      const [bx, by] = main[idx];
      const ex = bx + (Math.random() - 0.5) * w * 0.16;
      const ey = by + (y - by) * (0.35 + 0.4 * Math.random());
      branches.push(boltPath(bx, by, ex, ey, 4, 40));
    }
    return { main: main, branches: branches };
  }

  function drawSprite(ctx, img, x, y, radius, rot, alpha, clipCircle) {
    if (!img) return;
    const fit = (2 * radius) / Math.min(img.width, img.height);
    const dw = img.width * fit;
    const dh = img.height * fit;
    ctx.save();
    ctx.globalAlpha = alpha;
    ctx.translate(x, y);
    ctx.rotate(rot);
    if (clipCircle) {
      ctx.beginPath();
      ctx.arc(0, 0, radius, 0, Math.PI * 2);
      ctx.clip();
    }
    ctx.drawImage(img, -dw / 2, -dh / 2, dw, dh);
    ctx.restore();
  }

  function drawCometSprite(ctx, img, c) {
    if (!img) return;
    const w = img.width * c.scale * 0.42;
    const h = img.height * c.scale * 0.42;
    ctx.save();
    ctx.globalCompositeOperation = "lighter";
    ctx.globalAlpha = c.alpha;
    ctx.translate(c.x, c.y);
    ctx.rotate(c.rot);
    ctx.drawImage(img, -w * 0.72, -h * 0.55, w, h);
    // soft photographic dust bloom behind nucleus
    const g = ctx.createRadialGradient(0, 0, 0, 0, 0, 18 * c.scale);
    g.addColorStop(0, "rgba(255,245,230," + (0.35 * c.alpha) + ")");
    g.addColorStop(1, "rgba(180,210,255,0)");
    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.arc(0, 0, 18 * c.scale, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
  }

  function start(root, opts) {
    opts = opts || {};
    const lite = !!opts.lite;
    const canvas = document.createElement("canvas");
    canvas.className = "bg-canvas";
    canvas.setAttribute("aria-hidden", "true");
    root.appendChild(canvas);
    if (lite) root.setAttribute("data-space-bg", "lite");

    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const ctx = canvas.getContext("2d", { alpha: true });
    let W, H, dpr;
    let comets = [];
    let stars = [];
    let nebulae = [];
    let planets = [];
    let milky = null;
    let sun = null;
    let moon = null;
    let visible = true;
    let raf = null;
    let frame = 0;
    const imgs = {};
    let storm = null;
    let nextStorm = performance.now() + (lite ? 22000 : 8000) + (lite ? 18000 : 12000) * Math.random();
    let last = 0;
    // Desktop ~28fps; mobile lite ~20fps to protect battery / Main-Thread.
    const FRAME_MS = 1000 / (lite ? 20 : 28);
    const DPR_CAP = lite ? 1 : 1.25;
    const SIZE_SCALE = lite ? 0.72 : 1;

    function resize() {
      dpr = Math.min(window.devicePixelRatio || 1, DPR_CAP);
      W = window.innerWidth;
      H = window.innerHeight;
      canvas.width = W * dpr;
      canvas.height = H * dpr;
      canvas.style.width = W + "px";
      canvas.style.height = H + "px";
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

      const area = W * H;
      const cometN = lite ? 1 : Math.max(2, Math.round(area / 520000));
      comets = Array.from({ length: cometN }, function () { return makeComet(W, H, true); });

      const starN = lite
        ? Math.max(22, Math.round(area / 32000))
        : Math.max(40, Math.round(area / 16000));
      stars = Array.from({ length: starN }, function () {
        return {
          x: Math.random() * W,
          y: Math.random() * H,
          r: 0.5 + 1.4 * Math.random(),
          phase: Math.random() * Math.PI * 2,
          speed: 0.4 + 0.8 * Math.random()
        };
      });

      const hues = ["45, 212, 191", "126, 182, 255", "240, 193, 75"];
      nebulae = Array.from({ length: lite ? 1 : 2 }, function (_, i) {
        return {
          x: (0.2 + 0.6 * Math.random()) * W,
          y: (0.15 + 0.6 * Math.random()) * H,
          r: Math.max(W, H) * (0.28 + 0.16 * Math.random()),
          hue: hues[i % hues.length],
          driftX: 0.04 * (Math.random() - 0.5),
          driftY: 0.04 * (Math.random() - 0.5),
          alpha: 0.045 + 0.035 * Math.random()
        };
      });

      sun = {
        x: 0.88 * W,
        y: 0.12 * H,
        r: 0.05 * Math.min(W, H) * SIZE_SCALE,
        phase: 0,
        rot: 0
      };
      moon = {
        x: 0.08 * W,
        y: 0.14 * H,
        r: 0.032 * Math.min(W, H) * SIZE_SCALE,
        rot: 0
      };

      const span = 1.3 * Math.hypot(W, H);
      const milkyN = lite
        ? Math.max(36, Math.round(area / 22000))
        : Math.max(80, Math.round(area / 9000));
      milky = {
        cx: 0.5 * W,
        cy: 0.55 * H,
        angle: -0.55,
        coreR: 0.22 * Math.min(W, H),
        stars: Array.from({ length: milkyN }, function () {
          const along = (Math.random() - 0.5) * span;
          const across = 2 * (Math.random() - 0.5);
          const band = 0.16 * Math.min(W, H) * (1 - 0.6 * Math.pow(Math.abs(along) / (span / 2), 2));
          return {
            along: along,
            across: across * Math.max(band, 12),
            r: 0.4 + 1.1 * Math.random(),
            phase: Math.random() * Math.PI * 2
          };
        })
      };

      // Mobile: sun + moon + 2–3 planets (skip earth) to keep draw cost down.
      const keys = lite ? ["jupiter", "saturn", "mars"] : ["jupiter", "saturn", "mars", "earth"];
      planets = keys.map(function (key, i) {
        const base = (key === "saturn" ? 34 : 24) + 18 * Math.random();
        return {
          key: key,
          x: (i / keys.length) * W + 100 * Math.random(),
          y: (0.18 + 0.55 * Math.random()) * H,
          r: base * SIZE_SCALE,
          speed: 0.02 + 0.04 * Math.random(),
          rot: Math.random() * Math.PI * 2,
          spin: 0.0008 + 0.0012 * Math.random()
        };
      });
    }

    function spawnStorm(now) {
      const flashes = [];
      let t = 0;
      const n = 2 + Math.floor(3 * Math.random());
      for (let i = 0; i < n; i++) {
        flashes.push({ t: t, dur: 55 + 65 * Math.random() });
        t += 70 + 90 * Math.random();
      }
      storm = {
        bolts: [makeStorm(W, H), Math.random() < 0.35 ? makeStorm(W, H) : null].filter(Boolean),
        flashes: flashes,
        totalDur: t + 120,
        startedAt: now
      };
      nextStorm = now + 15000 + 25000 * Math.random();
    }

    function strokePoly(pts) {
      ctx.beginPath();
      pts.forEach(function (p, i) {
        if (i === 0) ctx.moveTo(p[0], p[1]);
        else ctx.lineTo(p[0], p[1]);
      });
      ctx.stroke();
    }

    function tick(now) {
      if (!visible) return;
      raf = requestAnimationFrame(tick);
      now = now || performance.now();
      if (now - last < FRAME_MS) return;
      last = now;
      frame += 1;
      ctx.clearRect(0, 0, W, H);

      // Storms are expensive (gradients + bolts); rarer / skipped on lite.
      if (!lite && now >= nextStorm) spawnStorm(now);

      // Nebula wash
      ctx.globalCompositeOperation = "lighter";
      for (let i = 0; i < nebulae.length; i++) {
        const n = nebulae[i];
        n.x += n.driftX;
        n.y += n.driftY;
        const g = ctx.createRadialGradient(n.x, n.y, 0, n.x, n.y, n.r);
        g.addColorStop(0, "rgba(" + n.hue + ", " + n.alpha + ")");
        g.addColorStop(1, "rgba(" + n.hue + ", 0)");
        ctx.fillStyle = g;
        ctx.beginPath();
        ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2);
        ctx.fill();
      }

      // Milky band
      ctx.globalCompositeOperation = "source-over";
      if (milky) {
        ctx.save();
        ctx.translate(milky.cx, milky.cy);
        ctx.rotate(milky.angle);
        ctx.globalCompositeOperation = "lighter";
        const band = ctx.createRadialGradient(0, 0, 0, 0, 0, milky.coreR);
        band.addColorStop(0, "rgba(160, 200, 210, 0.08)");
        band.addColorStop(0.5, "rgba(90, 160, 170, 0.035)");
        band.addColorStop(1, "rgba(90, 160, 170, 0)");
        ctx.beginPath();
        ctx.ellipse(0, 0, milky.coreR * 1.6, milky.coreR * 0.45, 0, 0, Math.PI * 2);
        ctx.fillStyle = band;
        ctx.fill();
        ctx.globalCompositeOperation = "source-over";
        for (let i = 0; i < milky.stars.length; i++) {
          const s = milky.stars[i];
          const tw = 0.5 + 0.5 * Math.abs(Math.sin(0.006 * frame + s.phase));
          ctx.beginPath();
          ctx.arc(s.along, s.across, s.r, 0, Math.PI * 2);
          ctx.fillStyle = "rgba(225, 225, 245, " + (0.32 * tw) + ")";
          ctx.fill();
        }
        ctx.restore();
      }

      // Field stars
      for (let i = 0; i < stars.length; i++) {
        const s = stars[i];
        const tw = 0.4 + 0.6 * Math.abs(Math.sin(0.01 * frame * s.speed + s.phase));
        ctx.beginPath();
        ctx.arc(s.x, s.y, s.r, 0, Math.PI * 2);
        ctx.fillStyle = "rgba(255, 255, 255, " + (0.48 * tw) + ")";
        ctx.fill();
      }

      // Sun
      if (sun && imgs.sun) {
        sun.phase += 0.005;
        sun.rot += 0.00045;
        const pulse = 1 + 0.025 * Math.sin(sun.phase);
        const glow = ctx.createRadialGradient(sun.x, sun.y, 0, sun.x, sun.y, sun.r * 2.8 * pulse);
        glow.addColorStop(0, "rgba(255, 180, 90, 0.18)");
        glow.addColorStop(0.45, "rgba(255, 120, 40, 0.05)");
        glow.addColorStop(1, "rgba(255, 120, 40, 0)");
        ctx.beginPath();
        ctx.arc(sun.x, sun.y, sun.r * 2.8 * pulse, 0, Math.PI * 2);
        ctx.fillStyle = glow;
        ctx.fill();
        drawSprite(ctx, imgs.sun, sun.x, sun.y, sun.r * pulse, sun.rot, 0.92, false);
      }

      // Moon
      if (moon && imgs.moon) {
        moon.rot += 0.00025;
        const glow = ctx.createRadialGradient(moon.x, moon.y, 0, moon.x, moon.y, moon.r * 1.9);
        glow.addColorStop(0, "rgba(220, 225, 240, 0.09)");
        glow.addColorStop(1, "rgba(220, 225, 240, 0)");
        ctx.beginPath();
        ctx.arc(moon.x, moon.y, moon.r * 1.9, 0, Math.PI * 2);
        ctx.fillStyle = glow;
        ctx.fill();
        drawSprite(ctx, imgs.moon, moon.x, moon.y, moon.r, moon.rot, 0.88, false);
      }

      // Planets
      for (let i = 0; i < planets.length; i++) {
        const p = planets[i];
        p.x += p.speed;
        p.rot += p.spin;
        if (p.x > W + p.r * 4) p.x = -p.r * 4;
        const img = imgs[p.key];
        // Saturn keeps rings — no circular clip; others use soft image alpha already
        drawSprite(ctx, img, p.x, p.y, p.r, p.rot, 0.9, false);
      }

      // Comets (photographic sprites)
      for (let i = comets.length - 1; i >= 0; i--) {
        const c = comets[i];
        c.x += c.vx;
        c.y += c.vy;
        drawCometSprite(ctx, imgs.comet, c);
        if (c.x - 200 > W + 120 || c.y - 120 > H + 120) {
          comets[i] = makeComet(W, H, false);
        }
      }

      // Lightning — cool white/blue, subtle (not cartoon yellow)
      if (storm) {
        const age = now - storm.startedAt;
        if (age > storm.totalDur) {
          storm = null;
        } else {
          let flash = 0;
          for (let i = 0; i < storm.flashes.length; i++) {
            const f = storm.flashes[i];
            if (age >= f.t && age <= f.t + f.dur) {
              const u = (age - f.t) / f.dur;
              flash = Math.max(flash, u < 0.18 ? u / 0.18 : 1 - (u - 0.18) / 0.82);
            }
          }
          if (flash > 0) {
            const sky = ctx.createRadialGradient(0.5 * W, 0, 0, 0.5 * W, 0, 0.9 * Math.max(W, H));
            sky.addColorStop(0, "rgba(210, 220, 255, " + (0.28 * flash) + ")");
            sky.addColorStop(1, "rgba(170, 190, 255, " + (0.06 * flash) + ")");
            ctx.fillStyle = sky;
            ctx.fillRect(0, 0, W, H);

            for (let b = 0; b < storm.bolts.length; b++) {
              const bolt = storm.bolts[b];
              ctx.save();
              ctx.lineCap = "round";
              ctx.lineJoin = "round";
              ctx.strokeStyle = "rgba(170, 190, 255, " + (0.28 * flash) + ")";
              ctx.lineWidth = 7;
              ctx.shadowColor = "rgba(180, 200, 255, 0.55)";
              ctx.shadowBlur = 18;
              strokePoly(bolt.main);
              for (let k = 0; k < bolt.branches.length; k++) strokePoly(bolt.branches[k]);
              ctx.strokeStyle = "rgba(245, 248, 255, " + Math.min(1, 1.15 * flash) + ")";
              ctx.lineWidth = 1.4;
              ctx.shadowBlur = 8;
              strokePoly(bolt.main);
              ctx.lineWidth = 1;
              for (let k = 0; k < bolt.branches.length; k++) strokePoly(bolt.branches[k]);
              ctx.restore();
            }
          }
        }
      }
    }

    resize();
    window.addEventListener("resize", resize);
    document.addEventListener("visibilitychange", function () {
      visible = !document.hidden;
      if (visible && !reduced) {
        last = 0;
        if (raf === null) raf = requestAnimationFrame(tick);
      } else if (raf !== null) {
        cancelAnimationFrame(raf);
        raf = null;
      }
    });

    // Defer image decode; animation can start with stars alone
    if (!reduced) raf = requestAnimationFrame(tick);
    const assetKeys = lite
      ? ["sun", "moon", "jupiter", "saturn", "mars", "comet"]
      : Object.keys(ASSETS);
    Promise.all(
      assetKeys.map(function (key) {
        return loadImage(ASSETS[key]).then(function (img) {
          if (img) imgs[key] = img;
        });
      })
    ).then(function () {
      if (visible && !reduced && raf === null) raf = requestAnimationFrame(tick);
    });
  }

  const root = document.getElementById("react-bg-root");
  if (!root) return;

  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const saveData = !!(navigator.connection && navigator.connection.saveData);
  // Still skip animation for accessibility / Save-Data — not for mobile itself.
  if (reduced || saveData) {
    root.setAttribute("data-space-bg", "static");
    return;
  }

  const lite = window.matchMedia("(max-width: 900px), (pointer: coarse)").matches;
  const idleTimeout = lite ? 3200 : 1800;
  const fallbackMs = lite ? 480 : 200;
  const boot = function () { start(root, { lite: lite }); };
  if ("requestIdleCallback" in window) {
    requestIdleCallback(boot, { timeout: idleTimeout });
  } else {
    setTimeout(boot, fallbackMs);
  }
}();
