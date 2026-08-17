"""Build photorealistic celestial sprites for space-bg.
Textures: Solar System Scope (CC BY 4.0) + NASA public-domain solar disk.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

SRC = Path(__file__).resolve().parent / "_src"
OUT = Path(__file__).resolve().parent


def soft_disk_alpha(size: int, edge: float = 0.012) -> np.ndarray:
    y, x = np.ogrid[:size, :size]
    cx = cy = (size - 1) / 2.0
    r = np.sqrt((x - cx) ** 2 + (y - cy) ** 2) / (size / 2.0)
    a = np.clip((1.0 - r) / edge, 0, 1)
    a = a * a * (3 - 2 * a)
    return (a * 255).astype(np.uint8)


def render_sphere(
    tex_path: Path,
    size: int = 720,
    light_dir: tuple[float, float, float] = (-0.35, -0.15, 0.92),
    ambient: float = 0.22,
    contrast: float = 1.08,
) -> Image.Image:
    tex = Image.open(tex_path).convert("RGB")
    tw, th = tex.size
    tex_np = np.asarray(tex, dtype=np.float32)
    yy, xx = np.mgrid[0:size, 0:size]
    nx = (xx + 0.5) / size * 2 - 1
    ny = (yy + 0.5) / size * 2 - 1
    r2 = nx * nx + ny * ny
    mask = r2 <= 1.0
    nz = np.zeros_like(nx)
    nz[mask] = np.sqrt(1.0 - r2[mask])

    lon = np.arctan2(nx, nz)
    lat = np.arcsin(np.clip(ny, -1, 1))
    u = ((lon / (2 * np.pi)) + 0.5) * (tw - 1)
    v = (0.5 - lat / np.pi) * (th - 1)
    ui = np.clip(u.astype(np.int32), 0, tw - 1)
    vi = np.clip(v.astype(np.int32), 0, th - 1)

    rgb = np.zeros((size, size, 3), dtype=np.float32)
    rgb[mask] = tex_np[vi[mask], ui[mask]]

    lx, ly, lz = light_dir
    ln = np.sqrt(lx * lx + ly * ly + lz * lz)
    lx, ly, lz = lx / ln, ly / ln, lz / ln
    ndotl = np.clip(nx * lx + ny * ly + nz * lz, 0, 1)
    shade = ambient + (1 - ambient) * ndotl
    limb = 0.55 + 0.45 * nz
    shade = shade * limb
    rgb = np.clip(rgb * shade[..., None] * contrast, 0, 255).astype(np.uint8)

    alpha = soft_disk_alpha(size)
    alpha[~mask] = 0
    return Image.fromarray(np.dstack([rgb, alpha]), "RGBA")


def key_black_to_alpha(img: Image.Image, threshold: float = 28, soft: float = 18) -> Image.Image:
    arr = np.asarray(img.convert("RGB"), dtype=np.float32)
    lum = arr.mean(axis=2)
    a = np.clip((lum - threshold) / soft, 0, 1)
    a = a * a * (3 - 2 * a)
    rgba = np.dstack([arr.astype(np.uint8), (a * 255).astype(np.uint8)])
    return Image.fromarray(rgba, "RGBA")


def make_sun(size: int = 768) -> Image.Image:
    # NASA archive solar disk (saved as comet.jpg from GSFC archive download)
    src = Image.open(SRC / "comet.jpg").convert("RGB")
    w, h = src.size
    side = min(w, h)
    src = src.crop(((w - side) // 2, (h - side) // 2, (w + side) // 2, (h + side) // 2))
    src = src.resize((size, size), Image.LANCZOS)
    keyed = key_black_to_alpha(src, threshold=18, soft=22)
    a = soft_disk_alpha(size, edge=0.03)
    arr = np.asarray(keyed).astype(np.float32)
    arr[..., 3] = np.minimum(arr[..., 3], a)
    return Image.fromarray(arr.astype(np.uint8), "RGBA")


def make_saturn(size: int = 900) -> Image.Image:
    body_size = int(size * 0.52)
    body = render_sphere(
        SRC / "saturn.jpg",
        size=body_size,
        light_dir=(-0.4, -0.2, 0.9),
        ambient=0.28,
    )
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    bx = (size - body.width) // 2
    by = (size - body.height) // 2

    ring = Image.open(SRC / "saturn_ring.png").convert("RGBA")
    profile = np.asarray(ring).mean(axis=0)  # (W, 4)
    pw = profile.shape[0]

    yy, xx = np.mgrid[0:size, 0:size]
    cx = cy = (size - 1) / 2.0
    # Elliptical ring plane (perspective flatten + slight tilt)
    nx = (xx - cx) / (size * 0.48)
    ny = (yy - cy) / (size * 0.14)
    # mild shear for tilt
    nx = nx + 0.12 * ny
    rr = np.sqrt(nx * nx + ny * ny)

    inner, outer = 0.55, 1.0
    valid = (rr >= inner) & (rr <= outer)
    u = np.clip(((rr - inner) / (outer - inner)) * (pw - 1), 0, pw - 1).astype(np.int32)
    dens = profile[u, 0] / 255.0
    pa = profile[u, 3] / 255.0
    edge = np.clip((rr - inner) / 0.04, 0, 1) * np.clip((outer - rr) / 0.04, 0, 1)
    alpha = np.clip(pa * (0.35 + 0.65 * dens) * edge * 0.92, 0, 1)
    alpha[~valid] = 0

    ring_rgba = np.zeros((size, size, 4), dtype=np.float32)
    ring_rgba[..., 0] = 215
    ring_rgba[..., 1] = 195
    ring_rgba[..., 2] = 155
    # faint bluish outer ring tint
    ring_rgba[..., 0] = np.where(rr > 0.85, 190, 215)
    ring_rgba[..., 1] = np.where(rr > 0.85, 185, 195)
    ring_rgba[..., 2] = np.where(rr > 0.85, 170, 155)
    ring_rgba[..., 3] = alpha * 255

    # Occlusion: hide ring pixels that fall behind the planet disk
    body_r = body_size / 2.0 * 0.98
    behind = ((xx - cx) ** 2 + (yy - cy) ** 2) <= body_r ** 2
    # Far half (above equator in screen space) stays behind planet
    far = ring_rgba.copy()
    near = ring_rgba.copy()
    far[yy >= cy, 3] = 0
    near[yy < cy, 3] = 0
    far[behind & (yy < cy), 3] = 0  # occluded by body

    canvas.alpha_composite(Image.fromarray(np.clip(far, 0, 255).astype(np.uint8), "RGBA"))
    canvas.alpha_composite(body, (bx, by))
    # Near rings in front, but punch out the planet disk so they don't cover body
    near[behind, 3] = 0
    canvas.alpha_composite(Image.fromarray(np.clip(near, 0, 255).astype(np.uint8), "RGBA"))
    return canvas


def make_comet(size: int = 640) -> Image.Image:
    """Synthetic photorealistic comet: bright coma + dust + ion tails."""
    arr = np.zeros((size, size, 4), dtype=np.float32)
    hx, hy = size * 0.72, size * 0.55
    yy, xx = np.mgrid[0:size, 0:size]
    dx, dy = xx - hx, yy - hy

    coma = np.exp(-(dx * dx + dy * dy) / (2 * (size * 0.028) ** 2))

    tx, ty = -0.96, -0.22
    along = dx * tx + dy * ty
    across = -dx * ty + dy * tx
    dust = np.zeros_like(coma)
    m = along > 0
    dust[m] = np.exp(-across[m] ** 2 / (2 * (size * 0.018 + along[m] * 0.045) ** 2)) * np.exp(
        -along[m] / (size * 0.55)
    )

    tx2, ty2 = -0.99, -0.08
    along2 = dx * tx2 + dy * ty2
    across2 = -dx * ty2 + dy * tx2
    ion = np.zeros_like(coma)
    m2 = along2 > 0
    ion[m2] = np.exp(-across2[m2] ** 2 / (2 * (size * 0.008 + along2[m2] * 0.02) ** 2)) * np.exp(
        -along2[m2] / (size * 0.7)
    )

    arr[..., 0] += coma * 255 + dust * 210
    arr[..., 1] += coma * 245 + dust * 200
    arr[..., 2] += coma * 230 + dust * 170
    arr[..., 0] += ion * 90
    arr[..., 1] += ion * 170
    arr[..., 2] += ion * 255
    alpha = np.clip(coma * 1.1 + dust * 0.75 + ion * 0.55, 0, 1)

    rng = np.random.default_rng(7)
    grain = rng.normal(0, 6, arr.shape[:2])
    for c in range(3):
        arr[..., c] = np.clip(arr[..., c] + grain * alpha, 0, 255)
    arr[..., 3] = alpha * 255
    img = Image.fromarray(arr.astype(np.uint8), "RGBA")
    return img.filter(ImageFilter.GaussianBlur(radius=0.6))


def save_webp(img: Image.Image, name: str, max_side: int = 768, quality: int = 82) -> Path:
    im = img.convert("RGBA")
    w, h = im.size
    scale = min(1.0, max_side / max(w, h))
    if scale < 1:
        im = im.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    path = OUT / name
    im.save(path, "WEBP", quality=quality, method=6)
    print(name, im.size, path.stat().st_size)
    return path


def main() -> None:
    print("Rendering celestial sprites...")
    save_webp(make_sun(800), "sun.webp", max_side=720, quality=80)
    save_webp(
        render_sphere(SRC / "moon.jpg", 720, light_dir=(-0.25, -0.1, 0.96), ambient=0.18, contrast=1.12),
        "moon.webp",
        640,
        82,
    )
    save_webp(render_sphere(SRC / "jupiter.jpg", 720, ambient=0.25, contrast=1.1), "jupiter.webp", 640, 80)
    save_webp(render_sphere(SRC / "mars.jpg", 700, ambient=0.2, contrast=1.15), "mars.webp", 600, 80)
    save_webp(make_saturn(900), "saturn.webp", 720, 80)
    save_webp(render_sphere(SRC / "earth.jpg", 700, ambient=0.2, contrast=1.12), "earth.webp", 600, 80)
    save_webp(make_comet(640), "comet.webp", 512, 78)
    print("Done")


if __name__ == "__main__":
    main()
