/* Space background: a self-contained canvas animation mounted once into
   #react-bg-root at page load and never torn down (it lives for the page's
   whole lifetime, so there's no unmount case to handle). Was previously a
   React component for no functional reason -- plain DOM/canvas setup is
   simpler and lighter for a mount-once, never-rerender effect like this.
   Renders: nebula, Milky Way, stars, sun, moon, planets, comets. */
(function () {
    /* Real astronomical photos (NASA / public domain, via Wikimedia Commons) --
       used instead of drawn shapes so bodies read as photographic, not cartoon.
       Self-hosted under static/space/ (not fetched from upload.wikimedia.org
       directly) -- the original hotlinked URLs sent uncached, uncompressed
       full-res PNGs (3.3MB combined) on every visit, since we don't control
       Wikimedia's cache headers. Self-hosting puts them under this site's own
       1-year immutable static cache instead. */
    const TEXTURES = {
        sun: "/static/space/sun.webp",
        moon: "/static/space/moon.webp",
        saturn: "/static/space/saturn.webp",
        jupiter: "/static/space/jupiter.webp",
        mars: "/static/space/mars.webp",
    };

    function loadImage(src) {
        return new Promise((resolve) => {
            const img = new Image();
            img.crossOrigin = "anonymous";
            img.onload = () => resolve(img);
            img.onerror = () => resolve(null);
            img.src = src;
        });
    }

    function makeComet(w, h, initial) {
        const angle = (Math.PI * 0.15) + Math.random() * (Math.PI * 0.15);
        const speed = 2.2 + Math.random() * 3.2;
        const startX = initial ? Math.random() * w : -80 - Math.random() * 200;
        const startY = initial ? Math.random() * h * 0.6 : -80 - Math.random() * 200;
        return {
            x: startX,
            y: startY,
            vx: Math.cos(angle) * speed,
            vy: Math.sin(angle) * speed,
            len: 90 + Math.random() * 140,
            r: 1.6 + Math.random() * 1.8,
            alpha: 0.5 + Math.random() * 0.4,
        };
    }

    function initSpaceBackground(root) {
        const canvas = document.createElement("canvas");
        canvas.className = "bg-canvas";
        canvas.setAttribute("aria-hidden", "true");
        root.appendChild(canvas);

        const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
            const ctx = canvas.getContext("2d");

            let w, h, dpr;
            let comets = [], stars = [], nebulae = [], planets = [], sun = null, moon = null, milkyWay = null;
            let running = true;
            let rafId = null;
            let t = 0;
            const images = {};

            /* ---- occasional lightning: a fractal, forking bolt flash ---- */
            /* spaced minutes apart, silent (no audio). */
            let lightning = null; // { bolts: [[[x,y],...], ...], flashes: [{t, dur}], age }
            let nextThunderAt = performance.now() + 8000 + Math.random() * 12000;

            /* Jagged, forking bolt path: recursively displaces the midpoint of each
               segment (a "midpoint displacement" fractal), then spins off shorter
               sub-branches partway down -- much closer to a real strike's geometry
               than a single zig-zag polyline. */
            function jaggedPath(x1, y1, x2, y2, depth, wobble) {
                if (depth <= 0) return [[x1, y1], [x2, y2]];
                const mx = (x1 + x2) / 2 + (Math.random() - 0.5) * wobble;
                const my = (y1 + y2) / 2 + (Math.random() - 0.5) * wobble * 0.3;
                const left = jaggedPath(x1, y1, mx, my, depth - 1, wobble * 0.55);
                const right = jaggedPath(mx, my, x2, y2, depth - 1, wobble * 0.55);
                return left.concat(right.slice(1));
            }

            function makeBolt() {
                const startX = w * (0.15 + Math.random() * 0.7);
                const endY = h * (0.5 + Math.random() * 0.4);
                const endX = startX + (Math.random() - 0.5) * w * 0.25;
                const main = jaggedPath(startX, 0, endX, endY, 6, 90);

                const branches = [];
                const branchCount = 2 + Math.floor(Math.random() * 3);
                for (let i = 0; i < branchCount; i++) {
                    const forkIdx = Math.floor(main.length * (0.25 + Math.random() * 0.45));
                    const [fx, fy] = main[forkIdx];
                    const bx = fx + (Math.random() - 0.5) * w * 0.18;
                    const by = fy + (endY - fy) * (0.35 + Math.random() * 0.4);
                    branches.push(jaggedPath(fx, fy, bx, by, 4, 45));
                }
                return { main, branches };
            }

            function triggerThunder() {
                /* real strikes usually flicker as 2-4 rapid sub-flashes over ~300ms */
                const flashCount = 2 + Math.floor(Math.random() * 3);
                const flashes = [];
                let cursor = 0;
                for (let i = 0; i < flashCount; i++) {
                    flashes.push({ t: cursor, dur: 60 + Math.random() * 70 });
                    cursor += 70 + Math.random() * 90;
                }
                lightning = {
                    bolts: [makeBolt(), Math.random() < 0.4 ? makeBolt() : null].filter(Boolean),
                    flashes,
                    totalDur: cursor + 120,
                    age: 0,
                };
            }

            function seed() {
                const cometCount = Math.max(3, Math.round((w * h) / 380000));
                comets = Array.from({ length: cometCount }, () => makeComet(w, h, true));

                const starCount = Math.max(60, Math.round((w * h) / 9000));
                stars = Array.from({ length: starCount }, () => ({
                    x: Math.random() * w,
                    y: Math.random() * h,
                    r: 0.5 + Math.random() * 1.4,
                    phase: Math.random() * Math.PI * 2,
                    speed: 0.4 + Math.random() * 0.8,
                }));

                const nebulaHues = ["82, 60, 220", "19, 79, 246", "255, 90, 190"];
                nebulae = Array.from({ length: 3 }, (_, i) => ({
                    x: (0.2 + Math.random() * 0.6) * w,
                    y: (0.15 + Math.random() * 0.6) * h,
                    r: Math.max(w, h) * (0.28 + Math.random() * 0.16),
                    hue: nebulaHues[i % nebulaHues.length],
                    driftX: (Math.random() - 0.5) * 0.04,
                    driftY: (Math.random() - 0.5) * 0.04,
                    alpha: 0.05 + Math.random() * 0.04,
                }));

                sun = { x: w * 0.88, y: h * 0.12, r: Math.min(w, h) * 0.045, phase: 0, rot: 0 };

                moon = { x: w * 0.08, y: h * 0.14, r: Math.min(w, h) * 0.028, rot: 0 };

                const bandLen = Math.hypot(w, h) * 1.3;
                milkyWay = {
                    cx: w * 0.5, cy: h * 0.55,
                    angle: -0.55,
                    coreR: Math.min(w, h) * 0.22,
                    stars: Array.from({ length: Math.max(220, Math.round((w * h) / 2600)) }, () => {
                        const along = (Math.random() - 0.5) * bandLen;
                        const spread = (Math.random() - 0.5) * 2;
                        const width = Math.min(w, h) * 0.16 * (1 - Math.pow(Math.abs(along) / (bandLen / 2), 2) * 0.6);
                        return {
                            along, across: spread * Math.max(width, 12),
                            r: 0.4 + Math.random() * 1.1,
                            phase: Math.random() * Math.PI * 2,
                        };
                    }),
                };

                const planetKeys = ["jupiter", "saturn", "mars"];
                planets = planetKeys.map((key, i) => ({
                    key,
                    x: (i / planetKeys.length) * w + Math.random() * 120,
                    y: (0.15 + Math.random() * 0.6) * h,
                    r: 26 + Math.random() * 20,
                    speed: 0.025 + Math.random() * 0.045,
                    rot: Math.random() * Math.PI * 2,
                }));
            }

            function drawSphereImage(img, x, y, r, rotation, alpha, circularClip) {
                const size = r * 2;
                const scale = size / Math.min(img.width, img.height);
                const dw = img.width * scale;
                const dh = img.height * scale;
                ctx.save();
                ctx.globalAlpha = alpha;
                ctx.translate(x, y);
                ctx.rotate(rotation);
                if (circularClip) {
                    ctx.beginPath();
                    ctx.arc(0, 0, r, 0, Math.PI * 2);
                    ctx.clip();
                }
                ctx.drawImage(img, -dw / 2, -dh / 2, dw, dh);
                ctx.restore();
            }

            function resize() {
                dpr = Math.min(window.devicePixelRatio || 1, 3);
                w = window.innerWidth;
                h = window.innerHeight;
                canvas.width = w * dpr;
                canvas.height = h * dpr;
                canvas.style.width = `${w}px`;
                canvas.style.height = `${h}px`;
                ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
                seed();
            }

            function step() {
                ctx.clearRect(0, 0, w, h);
                t += 1;

                const now = performance.now();
                if (now >= nextThunderAt) {
                    triggerThunder();
                    lightning.startedAt = now;
                    nextThunderAt = now + 15000 + Math.random() * 25000;
                }

                ctx.globalCompositeOperation = "lighter";
                for (const neb of nebulae) {
                    neb.x += neb.driftX;
                    neb.y += neb.driftY;
                    const grad = ctx.createRadialGradient(neb.x, neb.y, 0, neb.x, neb.y, neb.r);
                    grad.addColorStop(0, `rgba(${neb.hue}, ${neb.alpha})`);
                    grad.addColorStop(1, `rgba(${neb.hue}, 0)`);
                    ctx.fillStyle = grad;
                    ctx.beginPath();
                    ctx.arc(neb.x, neb.y, neb.r, 0, Math.PI * 2);
                    ctx.fill();
                }
                ctx.globalCompositeOperation = "source-over";

                if (milkyWay) {
                    ctx.save();
                    ctx.translate(milkyWay.cx, milkyWay.cy);
                    ctx.rotate(milkyWay.angle);

                    ctx.globalCompositeOperation = "lighter";
                    const coreGlow = ctx.createRadialGradient(0, 0, 0, 0, 0, milkyWay.coreR);
                    coreGlow.addColorStop(0, "rgba(180, 190, 255, 0.10)");
                    coreGlow.addColorStop(0.5, "rgba(140, 130, 220, 0.05)");
                    coreGlow.addColorStop(1, "rgba(140, 130, 220, 0)");
                    ctx.beginPath();
                    ctx.ellipse(0, 0, milkyWay.coreR * 1.6, milkyWay.coreR * 0.45, 0, 0, Math.PI * 2);
                    ctx.fillStyle = coreGlow;
                    ctx.fill();
                    ctx.globalCompositeOperation = "source-over";

                    for (const ms of milkyWay.stars) {
                        const twinkle = 0.5 + 0.5 * Math.abs(Math.sin(t * 0.006 + ms.phase));
                        ctx.beginPath();
                        ctx.arc(ms.along, ms.across, ms.r, 0, Math.PI * 2);
                        ctx.fillStyle = `rgba(225, 225, 245, ${0.35 * twinkle})`;
                        ctx.fill();
                    }
                    ctx.restore();
                }

                for (const s of stars) {
                    const twinkle = 0.4 + 0.6 * Math.abs(Math.sin(t * 0.01 * s.speed + s.phase));
                    ctx.beginPath();
                    ctx.arc(s.x, s.y, s.r, 0, Math.PI * 2);
                    ctx.fillStyle = `rgba(255, 255, 255, ${0.5 * twinkle})`;
                    ctx.fill();
                }

                if (sun && images.sun) {
                    sun.phase += 0.006;
                    sun.rot += 0.0006;
                    const pulse = 1 + Math.sin(sun.phase) * 0.03;
                    const glow = ctx.createRadialGradient(sun.x, sun.y, 0, sun.x, sun.y, sun.r * 2.6 * pulse);
                    glow.addColorStop(0, "rgba(255, 200, 120, 0.16)");
                    glow.addColorStop(0.5, "rgba(255, 150, 80, 0.05)");
                    glow.addColorStop(1, "rgba(255, 150, 80, 0)");
                    ctx.beginPath();
                    ctx.arc(sun.x, sun.y, sun.r * 2.6 * pulse, 0, Math.PI * 2);
                    ctx.fillStyle = glow;
                    ctx.fill();
                    drawSphereImage(images.sun, sun.x, sun.y, sun.r * pulse, sun.rot, 0.55, true);
                }

                if (moon && images.moon) {
                    moon.rot += 0.0003;
                    const glow = ctx.createRadialGradient(moon.x, moon.y, 0, moon.x, moon.y, moon.r * 1.8);
                    glow.addColorStop(0, "rgba(220, 225, 240, 0.10)");
                    glow.addColorStop(1, "rgba(220, 225, 240, 0)");
                    ctx.beginPath();
                    ctx.arc(moon.x, moon.y, moon.r * 1.8, 0, Math.PI * 2);
                    ctx.fillStyle = glow;
                    ctx.fill();
                    drawSphereImage(images.moon, moon.x, moon.y, moon.r, moon.rot, 0.5, true);
                }

                for (const p of planets) {
                    p.x += p.speed;
                    p.rot += 0.0015;
                    if (p.x > w + p.r * 4) p.x = -p.r * 4;
                    const img = images[p.key];
                    /* Saturn's rings extend past its sphere, so it's the one
                       planet drawn uncropped -- everything else is circle-clipped. */
                    if (img) drawSphereImage(img, p.x, p.y, p.r, p.rot, 0.85, p.key !== "saturn");
                }

                for (let i = comets.length - 1; i >= 0; i--) {
                    const c = comets[i];
                    c.x += c.vx;
                    c.y += c.vy;
                    const angle = Math.atan2(c.vy, c.vx);

                    const tailX = c.x - Math.cos(angle) * c.len;
                    const tailY = c.y - Math.sin(angle) * c.len;
                    const grad = ctx.createLinearGradient(c.x, c.y, tailX, tailY);
                    grad.addColorStop(0, `rgba(255, 255, 255, ${c.alpha})`);
                    grad.addColorStop(0.25, `rgba(180, 210, 255, ${c.alpha * 0.5})`);
                    grad.addColorStop(1, "rgba(120, 170, 255, 0)");
                    ctx.strokeStyle = grad;
                    ctx.lineWidth = c.r;
                    ctx.lineCap = "round";
                    ctx.beginPath();
                    ctx.moveTo(c.x, c.y);
                    ctx.lineTo(tailX, tailY);
                    ctx.stroke();

                    ctx.beginPath();
                    ctx.arc(c.x, c.y, c.r * 1.4, 0, Math.PI * 2);
                    ctx.fillStyle = `rgba(255, 255, 255, ${Math.min(1, c.alpha + 0.3)})`;
                    ctx.shadowColor = "rgba(180, 210, 255, 0.9)";
                    ctx.shadowBlur = 12;
                    ctx.fill();
                    ctx.shadowBlur = 0;

                    if (c.x - c.len > w + 100 || c.y - c.len > h + 100) {
                        comets[i] = makeComet(w, h, false);
                    }
                }

                /* lightning: multi-flicker sky flash + forked, fractal bolt(s) */
                if (lightning) {
                    const age = now - lightning.startedAt;
                    if (age > lightning.totalDur) {
                        lightning = null;
                    } else {
                        let flashEnvelope = 0;
                        for (const f of lightning.flashes) {
                            if (age >= f.t && age <= f.t + f.dur) {
                                const local = (age - f.t) / f.dur;
                                /* fast attack, slower decay -- real flash shape */
                                flashEnvelope = Math.max(flashEnvelope, local < 0.2 ? local / 0.2 : 1 - (local - 0.2) / 0.8);
                            }
                        }

                        if (flashEnvelope > 0) {
                            const skyGrad = ctx.createRadialGradient(w * 0.5, 0, 0, w * 0.5, 0, Math.max(w, h) * 0.9);
                            skyGrad.addColorStop(0, `rgba(225, 232, 255, ${flashEnvelope * 0.4})`);
                            skyGrad.addColorStop(1, `rgba(200, 210, 255, ${flashEnvelope * 0.12})`);
                            ctx.fillStyle = skyGrad;
                            ctx.fillRect(0, 0, w, h);

                            const drawBoltPath = (path, widthMul) => {
                                ctx.beginPath();
                                path.forEach(([bx, by], idx) => {
                                    if (idx === 0) ctx.moveTo(bx, by);
                                    else ctx.lineTo(bx, by);
                                });
                                ctx.stroke();
                            };

                            for (const bolt of lightning.bolts) {
                                ctx.save();
                                /* outer soft glow pass */
                                ctx.strokeStyle = `rgba(190, 205, 255, ${flashEnvelope * 0.5})`;
                                ctx.lineWidth = 10;
                                ctx.lineCap = "round";
                                ctx.lineJoin = "round";
                                ctx.shadowColor = "rgba(190, 210, 255, 0.9)";
                                ctx.shadowBlur = 30;
                                drawBoltPath(bolt.main, 1);
                                for (const b of bolt.branches) drawBoltPath(b, 1);

                                /* bright hot core pass */
                                ctx.strokeStyle = `rgba(255, 255, 255, ${Math.min(1, flashEnvelope * 1.3)})`;
                                ctx.lineWidth = 2;
                                ctx.shadowBlur = 12;
                                drawBoltPath(bolt.main, 1);
                                ctx.lineWidth = 1.2;
                                for (const b of bolt.branches) drawBoltPath(b, 1);
                                ctx.restore();
                            }
                        }
                    }
                }

                if (running) rafId = requestAnimationFrame(step);
            }

            function handleVisibility() {
                running = !document.hidden;
                if (running && !reduceMotion) rafId = requestAnimationFrame(step);
            }

            resize();
            window.addEventListener("resize", resize);
            document.addEventListener("visibilitychange", handleVisibility);

            Promise.all(
                Object.entries(TEXTURES).map(([key, src]) =>
                    loadImage(src).then((img) => { if (img) images[key] = img; })
                )
            ).then(() => {
                if (!running) return;
                if (rafId === null) rafId = requestAnimationFrame(step);
            });

            if (!reduceMotion) rafId = requestAnimationFrame(step);
    }

    const root = document.getElementById("react-bg-root");
    if (root) {
        initSpaceBackground(root);
    }
})();
