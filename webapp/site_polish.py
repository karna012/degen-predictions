"""Public-site polish helpers: home teaser, OG cards, watchlist, feed stale flags."""
from __future__ import annotations

import hashlib
import io
import json
import logging
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

log = logging.getLogger(__name__)

REPO = Path(__file__).resolve().parents[1]
WATCH_COOKIE = "dp_watch"
WATCH_MAX = 24


def build_home_now() -> dict[str, Any]:
    """Compact homepage strip: pick teaser, combo teaser, markets teasers."""
    out: dict[str, Any] = {
        "pick": None,
        "combo": None,
        "crypto": None,
        "stock": None,
        "track": None,
    }
    try:
        out["pick"], out["combo"] = _pick_and_combo_teasers()
    except Exception:
        log.exception("home_now picks/combos failed")
    try:
        out["crypto"], out["stock"] = _market_teasers()
    except Exception:
        log.exception("home_now markets failed")
    try:
        out["track"] = _public_track_snapshot()
    except Exception:
        log.exception("home_now track failed")
    return out


def _pick_and_combo_teasers() -> tuple[dict | None, dict | None]:
    import daily_digest
    digest = daily_digest.load_latest_digest() or {}
    digest = daily_digest.filter_digest_upcoming(digest) if digest else {}
    pick = None
    picks = digest.get("picks") or []
    if picks:
        p = picks[0]
        pick = {
            "matchup": p.get("matchup") or f"{p.get('home')} vs {p.get('away')}",
            "market": p.get("market") or p.get("pick_label") or "Pick",
            "pick": p.get("pick") or p.get("selection") or "",
            "league": p.get("league_label") or p.get("league") or "",
            "href": "/todays-picks",
            "confidence": p.get("confidence"),
        }
    combo = None
    combos = digest.get("combos") or {}
    # combos may be {2: [...], 3: [...]} or nested featured
    card = None
    if isinstance(combos, dict):
        for key in (2, 3, "2", "3", "mixed_2", "mixed_3"):
            bucket = combos.get(key) or []
            if isinstance(bucket, list) and bucket:
                card = bucket[0]
                break
    if not card:
        featured = digest.get("featured_combos") or []
        if featured:
            card = featured[0]
    if isinstance(card, dict):
        legs = card.get("legs") or []
        labels = []
        for leg in legs[:3]:
            if not isinstance(leg, dict):
                continue
            labels.append(
                f"{leg.get('pick') or leg.get('selection') or ''} "
                f"({leg.get('matchup') or ''})".strip()
            )
        combo = {
            "title": card.get("title") or f"{len(legs)}-leg combo",
            "legs_preview": " · ".join(x for x in labels if x)[:160],
            "joint": card.get("joint_prob") or card.get("combined_prob"),
            "href": "/todays-picks/combos",
            "n_legs": len(legs),
        }
    return pick, combo


def _market_teasers() -> tuple[dict | None, dict | None]:
    import investment_sources
    if not investment_sources.markets_enabled("crypto") and not investment_sources.markets_enabled("stocks"):
        return None, None
    import pandas as pd
    path = investment_sources.CSV_PATH
    if not path.is_file():
        return None, None
    df = pd.read_csv(path)
    if df.empty or "ticker" not in df.columns:
        return None, None
    crypto = stock = None
    if investment_sources.markets_enabled("crypto") and "asset_class" in df.columns:
        c = df[df["asset_class"].astype(str) == "crypto"]
        if not c.empty:
            # Prefer 6m horizon when present
            if "horizon_months" in c.columns:
                c6 = c[pd.to_numeric(c["horizon_months"], errors="coerce") == 6]
                if not c6.empty:
                    c = c6
            if "rank_within_asset_class" in c.columns:
                c = c.sort_values("rank_within_asset_class")
            row = c.iloc[0]
            ticker = str(row["ticker"])
            crypto = {
                "ticker": ticker,
                "href": f"/crypto/asset/{quote(ticker, safe='')}",
                "rank": int(row["rank_within_asset_class"]) if "rank_within_asset_class" in c.columns and pd.notna(row.get("rank_within_asset_class")) else None,
                "board_href": "/crypto",
            }
    if investment_sources.markets_enabled("stocks") and "asset_class" in df.columns:
        s = df[df["asset_class"].astype(str) == "us_stocks"]
        if s.empty:
            s = df[df["asset_class"].astype(str).str.endswith("stocks")]
        if not s.empty:
            if "horizon_months" in s.columns:
                s6 = s[pd.to_numeric(s["horizon_months"], errors="coerce") == 6]
                if not s6.empty:
                    s = s6
            if "rank_within_asset_class" in s.columns:
                s = s.sort_values("rank_within_asset_class")
            row = s.iloc[0]
            ticker = str(row["ticker"])
            market = "us" if str(row.get("asset_class")) == "us_stocks" else "india"
            stock = {
                "ticker": ticker,
                "href": f"/stocks/{market}/asset/{quote(ticker, safe='')}",
                "rank": int(row["rank_within_asset_class"]) if "rank_within_asset_class" in s.columns and pd.notna(row.get("rank_within_asset_class")) else None,
                "board_href": f"/stocks/{market}",
                "market": market,
            }
    return crypto, stock


def _public_track_snapshot() -> dict[str, Any] | None:
    import combo_results
    team = combo_results.degen_team_summary(days=30)
    combo = None
    try:
        # Prefer published Best Combos board ledger when available
        if hasattr(combo_results, "public_board_summary"):
            combo = combo_results.public_board_summary(days=30)
        else:
            combo = team.get("combos")
    except Exception:
        combo = team.get("combos")
    hit = team.get("hit_rate")
    decided = team.get("decided") or 0
    if decided < 3 and not (combo and combo.get("decided")):
        return None
    return {
        "days": 30,
        "hit_rate": hit,
        "decided": decided,
        "won": team.get("won"),
        "lost": team.get("lost"),
        "combo_hit_rate": (combo or {}).get("hit_rate"),
        "combo_decided": (combo or {}).get("decided"),
        "href": "/accuracy",
        "combos_href": "/todays-picks/combos/results",
    }


def feed_stale_banner(*feed_prefixes: str) -> dict[str, Any] | None:
    """Return a visitor-facing banner when named feeds are degraded/down."""
    try:
        import feed_store
        snap = feed_store.snapshot() or {}
    except Exception:
        return None
    bad = []
    for name, row in snap.items():
        if feed_prefixes and not any(str(name).startswith(p) for p in feed_prefixes):
            continue
        if row.get("hard_fail") or row.get("degraded"):
            bad.append({
                "name": name,
                "reason": row.get("reason") or "delayed",
            })
    if not bad:
        return None
    reasons = ", ".join(f"{b['name']} ({b['reason']})" for b in bad[:3])
    return {
        "level": "warn",
        "title": "Schedule feed delayed",
        "detail": f"Live times/scores may lag ({reasons}). Showing last good snapshot when available.",
    }


def parse_watch_cookie(raw: str | None) -> list[dict[str, str]]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    out = []
    for item in data[:WATCH_MAX]:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip()
        key = str(item.get("key") or "").strip()
        label = str(item.get("label") or key).strip()
        href = str(item.get("href") or "").strip()
        if kind and key and href.startswith("/"):
            out.append({"kind": kind, "key": key, "label": label[:80], "href": href[:200]})
    return out


def dump_watch_cookie(items: list[dict[str, str]]) -> str:
    return json.dumps(items[:WATCH_MAX], separators=(",", ":"))


def render_og_card(
    *,
    title: str,
    subtitle: str = "",
    footer: str = "degenpredictions.com",
    accent: tuple[int, int, int] = (45, 212, 191),
) -> bytes:
    """Simple 1200×630 share card PNG."""
    from PIL import Image, ImageDraw, ImageFont

    W, H = 1200, 630
    bg = (7, 12, 22)
    img = Image.new("RGB", (W, H), bg)
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, 18, H), fill=accent)
    draw.rectangle((0, H - 10, W, H), fill=accent)

    try:
        font_lg = ImageFont.truetype("DejaVuSans-Bold.ttf", 54)
        font_md = ImageFont.truetype("DejaVuSans.ttf", 32)
        font_sm = ImageFont.truetype("DejaVuSans.ttf", 24)
    except OSError:
        font_lg = ImageFont.load_default()
        font_md = font_lg
        font_sm = font_lg

    def _wrap(text: str, font, max_w: int) -> list[str]:
        words = (text or "").split()
        lines, cur = [], ""
        for w in words:
            trial = (cur + " " + w).strip()
            if draw.textlength(trial, font=font) <= max_w:
                cur = trial
            else:
                if cur:
                    lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        return lines[:4] or [""]

    y = 90
    draw.text((64, 40), "DEGEN PREDICTIONS", fill=(148, 163, 184), font=font_sm)
    for line in _wrap(title[:140], font_lg, W - 120):
        draw.text((64, y), line, fill=(248, 250, 252), font=font_lg)
        y += 64
    y += 12
    for line in _wrap(subtitle[:200], font_md, W - 120):
        draw.text((64, y), line, fill=(203, 213, 225), font=font_md)
        y += 40
    draw.text((64, H - 70), footer[:80], fill=(100, 116, 139), font=font_sm)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def og_cache_key(**parts: str) -> str:
    raw = "|".join(f"{k}={parts[k]}" for k in sorted(parts))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]
