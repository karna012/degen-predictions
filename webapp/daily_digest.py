#!/usr/bin/env python3
"""Daily Top-3 digest: build, persist, draft, and optionally auto-post.

Morning job (systemd / Task Scheduler):

  cd webapp && python daily_digest.py
  # or: bash deploy/daily_distribute.sh

Always writes digest JSON + channel drafts for admin review. Telegram / X
are **admin-only** unless ``CHANNEL_AUTO_POST=1``. Idempotent via
data/daily_digest/state.json (same calendar day in Asia/Kolkata).

Env vars are documented in deploy/production.env.example and
docs/DAILY_DISTRIBUTION.md.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import io
import json
import logging
import os
import smtplib
import sys
import time
import traceback
import urllib.parse
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

WEBAPP = Path(__file__).resolve().parent
REPO = WEBAPP.parent
DIGEST_DIR = REPO / "data" / "daily_digest"
DRAFTS_DIR = REPO / "output_tables" / "daily_drafts"
CARDS_DIR = DIGEST_DIR / "cards"
STATE_PATH = DIGEST_DIR / "state.json"
LATEST_PATH = DIGEST_DIR / "latest.json"
LAST_TELEGRAM_SEND_PATH = REPO / "data" / "feedback" / "last_telegram_send.json"
TELEGRAM_DIGEST_RESULTS_PATH = REPO / "data" / "feedback" / "telegram_digest_results.json"
DIGEST_RESULT_LOOKBACK_DAYS = 2
SITE_URL = os.environ.get("SITE_URL", "https://degenpredictions.com").rstrip("/")
DIGEST_PATH = "/todays-picks/digest"
# Public invite for on-site CTAs only (not a bot chat_id). Env can override.
DEFAULT_TELEGRAM_CHANNEL_URL = "https://t.me/+JVbaUkQnvGE1NWNl"
DISCLAIMER = (
    "Educational ML estimates only — not betting advice. "
    "Track record: {site}/accuracy"
).format(site=SITE_URL)

log = logging.getLogger("daily_digest")


def channel_auto_post_enabled() -> bool:
    """Morning job and post-event never auto-send unless explicitly enabled."""
    try:
        import source_settings
        return bool(source_settings.telegram_credentials().get("auto_post"))
    except Exception:
        pass
    return (os.environ.get("CHANNEL_AUTO_POST") or "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def telegram_channel_url() -> str:
    try:
        import source_settings
        url = (source_settings.telegram_credentials().get("channel_url") or "").strip()
        if url:
            return url
    except Exception:
        pass
    return (
        (os.environ.get("TELEGRAM_CHANNEL_URL") or "").strip()
        or DEFAULT_TELEGRAM_CHANNEL_URL
    )


def telegram_bot_credentials() -> tuple[str, str]:
    """Return (bot_token, chat_id) from admin settings, else env."""
    try:
        import source_settings
        creds = source_settings.telegram_credentials()
        token = (creds.get("bot_token") or "").strip()
        chat_id = normalize_telegram_chat_id(creds.get("chat_id") or "")
        if token and chat_id:
            return token, chat_id
        if token or chat_id:
            # Partial admin override still merges with env for the missing half.
            token = token or (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
            chat_id = chat_id or normalize_telegram_chat_id(
                os.environ.get("TELEGRAM_CHAT_ID") or ""
            )
            return token, chat_id
    except Exception:
        pass
    return (
        (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip(),
        normalize_telegram_chat_id(os.environ.get("TELEGRAM_CHAT_ID") or ""),
    )


# ---------------------------------------------------------------------------
# Date / IO helpers
# ---------------------------------------------------------------------------

def digest_timezone():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("Asia/Kolkata")
    except Exception:
        return timezone(timedelta(hours=5, minutes=30))


def today_key(now: datetime | None = None) -> str:
    now = now or datetime.now(digest_timezone())
    if now.tzinfo is None:
        now = now.replace(tzinfo=digest_timezone())
    return now.astimezone(digest_timezone()).strftime("%Y-%m-%d")


def yesterday_key(now: datetime | None = None) -> str:
    now = now or datetime.now(digest_timezone())
    if now.tzinfo is None:
        now = now.replace(tzinfo=digest_timezone())
    return today_key(now - timedelta(days=1))


def recent_digest_days(n: int | None = None, now: datetime | None = None) -> list[str]:
    """IST calendar days to auto-post RESULT cards for, oldest first."""
    n = DIGEST_RESULT_LOOKBACK_DAYS if n is None else max(1, int(n))
    now = now or datetime.now(digest_timezone())
    if now.tzinfo is None:
        now = now.replace(tzinfo=digest_timezone())
    now = now.astimezone(digest_timezone())
    return [today_key(now - timedelta(days=i)) for i in range(n - 1, -1, -1)]


def _ensure_dirs() -> None:
    DIGEST_DIR.mkdir(parents=True, exist_ok=True)
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    CARDS_DIR.mkdir(parents=True, exist_ok=True)


def load_state() -> dict:
    if not STATE_PATH.is_file():
        return {}
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    _ensure_dirs()
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
        f.write("\n")


def load_latest_digest() -> dict | None:
    for path in (LATEST_PATH, DIGEST_DIR / f"{today_key()}.json"):
        if not path.is_file():
            continue
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
    return None


def load_digest_for_date(day: str) -> dict | None:
    path = DIGEST_DIR / f"{day}.json"
    if not path.is_file():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


# ---------------------------------------------------------------------------
# Top-3 selection
# ---------------------------------------------------------------------------

def _leg_public(leg: dict) -> dict:
    conf = leg.get("confidence")
    conf_f = float(conf) if conf is not None else None
    pct = int(round(conf_f * 100)) if conf_f is not None else None
    league_id = leg.get("league_id") or ""
    sport_key = leg.get("sport_key") or ""
    url = leg.get("url") or ""
    if not url and sport_key and league_id:
        url = f"/todays-picks/{sport_key}/{league_id}"
    out = {
        "matchup": leg.get("matchup") or (
            f"{leg.get('home', '?')} vs {leg.get('away', '?')}"
            if (leg.get("home") or leg.get("away"))
            else f"{leg.get('away', '?')} vs {leg.get('home', '?')}"
        ),
        "pick": leg.get("pick") or "",
        "market": leg.get("market") or "",
        "confidence": conf_f,
        "confidence_pct": pct,
        "league_id": league_id,
        "league_label": leg.get("league_label") or league_id.upper() or "League",
        "sport_key": sport_key,
        "sport_label": leg.get("sport_label") or sport_key.title() or "Sport",
        "when": leg.get("when") or "",
        "kickoff_iso": leg.get("kickoff_iso") or "",
        "status": leg.get("status") or "upcoming",
        "url": url,
        "away": leg.get("away") or "",
        "home": leg.get("home") or "",
    }
    # Admin digest may show %; Telegram PNG/captions recompute without %.
    out["selection_line"] = _selection_line(out, include_pct=True)
    return out


def _sport_league_label(row: dict | None) -> str:
    """Human label like 'Soccer · MLS' so readers know the sport."""
    row = row or {}
    sport = (row.get("sport_label") or row.get("sport_key") or "").strip()
    league = (row.get("league_label") or row.get("league_id") or "").strip()
    if sport and league:
        # Avoid "MLS · MLS" when labels collide.
        if sport.lower() == league.lower():
            return sport
        return f"{sport} · {league}"
    return sport or league or "Sport"


def _leg_is_finished(leg: dict | None) -> bool:
    try:
        import today_picks
        sport = (leg or {}).get("sport_key") or ""
        if today_picks.fixture_is_finished(leg or {}, sport_key=sport):
            return True
        if today_picks.fixture_off_public_board(leg or {}, sport_key=sport):
            return True
        kickoff = (leg or {}).get("kickoff_iso") or (leg or {}).get("date") or ""
        if (kickoff or (leg or {}).get("time")) and not today_picks.fixture_is_upcoming_or_live(
            leg or {}, sport_key=sport
        ):
            return True
        return False
    except Exception:
        return (leg or {}).get("status") == "final"


def _leg_board_key(leg: dict | None) -> str:
    leg = leg or {}
    return "|".join([
        (leg.get("league_id") or "").lower(),
        (leg.get("away") or "").lower(),
        (leg.get("home") or "").lower(),
        (leg.get("matchup") or "").lower(),
    ])


def select_top3(legs: list[dict], *, n: int = 3) -> list[dict]:
    """Best high-confidence legs, diversified across leagues when possible."""
    eligible = []
    for leg in legs or []:
        if _leg_is_finished(leg):
            continue
        conf = leg.get("confidence")
        if conf is None:
            continue
        try:
            conf_f = float(conf)
        except (TypeError, ValueError):
            continue
        pick = (leg.get("pick") or "").strip()
        if not pick or pick in ("No rating yet", "No strong lean"):
            continue
        market = (leg.get("market") or "").strip()
        # Prefer true high-prob tips over labelled upset leans.
        upset = market.lower() == "upset lean"
        try:
            import today_picks as _tp
            fragile = 1 if _tp._leg_is_fragile_total(leg) else 0
            cover = 1 if _tp._leg_is_favorite_cover(leg) else 0
        except Exception:
            fragile = cover = 0
        eligible.append((conf_f, 1 if upset else 0, fragile, cover, leg))

    # Pass 1: one per league, highest conf first among non-upsets then upsets.
    # Fragile Unders and favorite-cover props sort last.
    eligible.sort(key=lambda row: (row[1], row[2], row[3], -row[0]))
    chosen: list[dict] = []
    seen_league: set[str] = set()
    seen_match: set[str] = set()

    def match_key(leg: dict) -> str:
        return "|".join([
            (leg.get("league_id") or "").lower(),
            (leg.get("away") or "").lower(),
            (leg.get("home") or "").lower(),
            (leg.get("matchup") or "").lower(),
        ])

    # Pass 1: one per league, highest conf first among non-upsets then upsets.
    for _conf, _upset, _fragile, _cover, leg in eligible:
        if len(chosen) >= n:
            break
        lid = (leg.get("league_id") or leg.get("sport_key") or "x").lower()
        mk = match_key(leg)
        if lid in seen_league or mk in seen_match:
            continue
        chosen.append(_leg_public(leg))
        seen_league.add(lid)
        seen_match.add(mk)

    # Pass 2: fill remaining by confidence (still unique matches).
    if len(chosen) < n:
        for _conf, _upset, _fragile, _cover, leg in eligible:
            if len(chosen) >= n:
                break
            mk = match_key(leg)
            if mk in seen_match:
                continue
            chosen.append(_leg_public(leg))
            seen_match.add(mk)

    return chosen[:n]


def _league_urls_from_legs(legs: list[dict] | None) -> list[dict]:
    """Unique Today's Picks league boards that currently have legs."""
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for leg in legs or []:
        sport = str(leg.get("sport_key") or "")
        league = str(leg.get("league_id") or "")
        if not sport or not league:
            continue
        key = (sport, league)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "sport_key": sport,
            "league_id": league,
            "league_label": leg.get("league_label") or league,
            "sport_label": leg.get("sport_label") or sport.title(),
            "path": f"/todays-picks/{sport}/{league}",
            "url": f"{SITE_URL}/todays-picks/{sport}/{league}",
        })
    out.sort(key=lambda r: (r["sport_label"], r["league_label"]))
    return out


DIGEST_COMBOS_PER_BUCKET = 2
MIXED_PREFER_KINDS = ("Cross-sport", "Cross-league")


def _combo_count(combos: dict | None) -> int:
    if not combos:
        return 0
    return sum(len(combos.get(k) or []) for k in (2, 3, "mixed_2", "mixed_3"))


def _combo_bucket(combos: dict | None, key: int | str) -> list:
    """Return list for int or str keys (pickle uses ints; JSON may stringify)."""
    if not combos:
        return []
    for k in (key, str(key), int(key) if str(key).isdigit() else None):
        if k is None:
            continue
        rows = combos.get(k)
        if rows:
            return list(rows)
    return []


def _leg_fingerprint(leg: dict | None) -> str:
    """Stable leg identity for slip dedupe (fixture + selection)."""
    leg = leg or {}
    return "|".join([
        (leg.get("sport_key") or "").strip().lower(),
        (leg.get("league_id") or "").strip().lower(),
        (leg.get("matchup") or "").strip().lower(),
        (leg.get("pick") or "").strip().lower(),
        (leg.get("market") or "").strip().lower(),
    ])


def _combo_fingerprint(combo: dict | None) -> str:
    """Dedupe key: explicit id when present, else sorted leg fingerprints."""
    combo = combo or {}
    cid = combo.get("id") or combo.get("combo_id")
    if cid is not None and str(cid).strip():
        return f"id:{str(cid).strip()}"
    parts = sorted(
        _leg_fingerprint(leg)
        for leg in (combo.get("legs") or [])
        if isinstance(leg, dict)
    )
    return "legs:" + "||".join(parts)


def _event_name(leg: dict | None) -> str:
    """Full fixture label — always ``Home vs Away`` when both sides exist."""
    leg = leg or {}
    home = (leg.get("home") or "").strip()
    away = (leg.get("away") or "").strip()
    if home and away:
        return f"{home} vs {away}"
    matchup = (leg.get("matchup") or "").strip()
    if matchup:
        # Normalize common "Away @ Home" → "Home vs Away" when we can split.
        if " @ " in matchup and not home and not away:
            left, right = [p.strip() for p in matchup.split(" @ ", 1)]
            if left and right:
                return f"{right} vs {left}"
        if " vs " in matchup.lower():
            # Prefer keeping existing vs order only as last resort without home/away.
            return matchup
        return matchup
    return home or away or "Fixture"


def _parse_score_pair(note: str | None) -> tuple[str, str] | None:
    """Pull the first home/away score pair from an actual_note string."""
    import re
    text = (note or "").strip()
    if not text or text.lower().startswith("admin:"):
        return None
    # "5-4", "5–4", "181.0–186.0", "12–5 sets 2-0"
    m = re.search(r"(\d+(?:\.\d+)?)\s*[–\-]\s*(\d+(?:\.\d+)?)", text)
    if not m:
        return None
    a, b = m.group(1), m.group(2)

    def _clean(x: str) -> str:
        try:
            f = float(x)
            return str(int(f)) if f == int(f) else str(f)
        except ValueError:
            return x

    return _clean(a), _clean(b)


def _score_line(leg: dict | None) -> str:
    """``Home score - Away score`` for RESULT slips (empty if unknown)."""
    leg = leg or {}
    hs = leg.get("home_score")
    aws = leg.get("away_score")
    if hs is not None and aws is not None and str(hs).strip() != "" and str(aws).strip() != "":
        try:
            hf = float(hs)
            af = float(aws)
            hs_s = str(int(hf)) if hf == int(hf) else str(hf)
            aws_s = str(int(af)) if af == int(af) else str(af)
            return f"{hs_s} - {aws_s}"
        except (TypeError, ValueError):
            return f"{hs} - {aws}"
    parsed = _parse_score_pair(leg.get("actual_note"))
    if parsed:
        return f"{parsed[0]} - {parsed[1]}"
    return ""


def _short_market(market: str) -> str:
    """Compress verbose market labels for scannable slips."""
    raw = (market or "").strip()
    if not raw:
        return ""
    lower = raw.lower()
    aliases = {
        "player to win a set": "Win a set",
        "player to win set": "Win a set",
        "match winner": "Match winner",
        "moneyline": "Moneyline",
        "handicap": "Handicap",
        "run line": "Run line",
        "total sets": "Total sets",
        "total runs": "Total runs",
        "total goals": "Total goals",
        "draw no bet": "Draw no bet",
        "both teams to score": "BTTS",
    }
    if lower in aliases:
        return aliases[lower]
    if lower.startswith("player to "):
        rest = raw[10:].strip()
        return rest[:1].upper() + rest[1:] if rest else raw
    return raw


def _short_pick(pick: str, event: str, *, market: str = "") -> str:
    """Shorten player names when the full name already sits on the event line.

    Team names stay intact (e.g. "Sunrisers Leeds"). Only player markets
    (set / player-to) drop First Last → Last.
    """
    pick = (pick or "").strip()
    if not pick:
        return ""
    # Keep lines / totals as-is (e.g. "Boston Red Sox +1.5", "Under 2.5").
    if any(ch.isdigit() for ch in pick) or pick.lower().startswith(("over ", "under ")):
        return pick
    market_l = (market or "").lower()
    # Only shorten on explicit player markets — never chop team names.
    if "player" not in market_l and "set" not in market_l:
        return pick
    event_l = (event or "").lower()
    parts = pick.split()
    if len(parts) == 2 and pick.lower() in event_l and all(t.isalpha() for t in parts):
        return parts[-1]
    return pick


def _selection_line(leg: dict | None, *, include_pct: bool = False) -> str:
    """Short pick · market (optional · %) — never nests the fixture again.

    Telegram slip PNGs/captions use include_pct=False.
    Admin digest JSON may pass include_pct=True when % is wanted.
    """
    leg = leg or {}
    event = _event_name(leg)
    market_raw = leg.get("market") or ""
    pick = _short_pick(leg.get("pick") or "", event, market=market_raw)
    market = _short_market(market_raw)
    bits = []
    if pick:
        bits.append(pick)
    if market and market.lower() not in pick.lower():
        bits.append(market)
    head = " · ".join(bits) if bits else "Pick"
    if not include_pct:
        return head
    pct = leg.get("confidence_pct")
    pct_s = f"{int(pct)}%" if pct is not None else ""
    return f"{head} · {pct_s}" if pct_s else head


def _joint_pct_label(combo: dict | None) -> str:
    """Integer joint % for slip titles/captions (e.g. Best 2-leg · 67%)."""
    combo = combo or {}
    pct = combo.get("joint_pct")
    if pct is None:
        joint = combo.get("joint_prob")
        try:
            pct = round(float(joint) * 100, 1) if joint is not None else None
        except (TypeError, ValueError):
            pct = None
    if pct is None:
        return "—"
    return f"{int(round(float(pct)))}%"

def _combo_public(combo: dict) -> dict:
    """JSON-safe combo slip for digest / Telegram (same fields as Best Combos UI)."""
    joint = combo.get("joint_prob")
    try:
        joint_f = float(joint) if joint is not None else None
    except (TypeError, ValueError):
        joint_f = None
    joint_pct = round(joint_f * 100, 1) if joint_f is not None else None
    legs_out = []
    for leg in combo.get("legs") or []:
        if isinstance(leg, dict):
            legs_out.append(_leg_public(leg))
    score = combo.get("score")
    try:
        score_f = float(score) if score is not None else None
    except (TypeError, ValueError):
        score_f = None
    pub = {
        "size": int(combo.get("size") or len(legs_out) or 0),
        "joint_prob": joint_f,
        "joint_pct": joint_pct,
        "joint_formula": combo.get("joint_formula") or "",
        "mix_label": combo.get("mix_label") or "",
        "mix_kind": combo.get("mix_kind") or "",
        "n_sports": combo.get("n_sports"),
        "n_leagues": combo.get("n_leagues"),
        "is_fallback": bool(combo.get("is_fallback")),
        "is_soft_fill": bool(combo.get("is_soft_fill")),
        "corr_label": combo.get("corr_label") or "",
        "corr_note": combo.get("corr_note") or "",
        "score": score_f,
        "legs": legs_out,
    }
    cid = combo.get("id") or combo.get("combo_id")
    if cid is not None and str(cid).strip():
        pub["id"] = str(cid).strip()
    pub["fingerprint"] = _combo_fingerprint(pub)
    return pub


def select_top_combos(
    combos: dict | None,
    key: int | str,
    *,
    n: int = DIGEST_COMBOS_PER_BUCKET,
    exclude_fps: set[str] | None = None,
    exclude_fixtures: set | None = None,
    prefer_kinds: tuple[str, ...] | None = None,
) -> list[dict]:
    """Top N unique slips from a Best Combos bucket (already score-ranked).

    Skips fingerprints in ``exclude_fps`` and any slip that shares a fixture
    key with ``exclude_fixtures`` (cross-bucket anti-reshuffle). When
    ``prefer_kinds`` is set (mixed boards), walk Cross-sport then Cross-league
    before other kinds so true mixed slips win over same-league copies.
    """
    import today_picks as _tp

    rows = _combo_bucket(combos, key)
    if prefer_kinds:
        ordered: list = []
        seen_row: set[int] = set()
        for kind in prefer_kinds:
            for i, combo in enumerate(rows):
                if i in seen_row or not isinstance(combo, dict):
                    continue
                if (combo.get("mix_kind") or "") == kind:
                    ordered.append(combo)
                    seen_row.add(i)
        for i, combo in enumerate(rows):
            if i in seen_row or not isinstance(combo, dict):
                continue
            ordered.append(combo)
            seen_row.add(i)
    else:
        ordered = [c for c in rows if isinstance(c, dict)]

    blocked = set(exclude_fps or ())
    blocked_fx = set(exclude_fixtures or ())
    out: list[dict] = []
    for combo in ordered:
        pub = _combo_public(combo)
        legs = [lg for lg in (pub.get("legs") or []) if isinstance(lg, dict)]
        if not legs:
            continue
        fx_keys = {_tp._leg_match_key(leg) for leg in legs}
        if fx_keys & blocked_fx:
            continue
        fp = pub.get("fingerprint") or _combo_fingerprint(pub)
        if fp in blocked:
            continue
        blocked.add(fp)
        blocked_fx |= fx_keys
        out.append(pub)
        if len(out) >= n:
            break
    return out


def _format_leg_lines(leg: dict, *, indent: str = "") -> list[str]:
    """Sport · League / event / selection — three short lines."""
    return [
        f"{indent}{_sport_league_label(leg)}",
        f"{indent}{_event_name(leg)}",
        f"{indent}{_selection_line(leg)}",
    ]


def _format_combo_block(title: str, slips: list[dict]) -> list[str]:
    """Sparse combo blocks for share text — one slip at a time, minimal header."""
    if not slips:
        return []
    lines: list[str] = []
    for c in slips:
        lines.append(title)
        for leg in c.get("legs") or []:
            lines.extend(_format_leg_lines(leg))
            lines.append("")
        if lines and lines[-1] == "":
            lines.pop()
        lines.append("")
    while lines and lines[-1] == "":
        lines.pop()
    return lines


def _format_picks_message(picks: list[dict], *, day: str) -> str:
    """Today's top singles — one short Telegram message."""
    lines = [f"Today's Picks · {day}", ""]
    if not picks:
        lines.append("No high-confidence picks today.")
        lines.extend(["", _telegram_board_url("picks")])
        return "\n".join(lines)
    for pick in picks:
        lines.extend(_format_leg_lines(pick))
        lines.append("")
    while lines and lines[-1] == "":
        lines.pop()
    lines.extend(["", _telegram_board_url("picks")])
    return "\n".join(lines)


def _format_combo_slip_message(
    combo: dict,
    *,
    bucket_title: str,
    index: int,
) -> str:
    """ONE combo slip = ONE Telegram message. Sparse header + legs only (no %)."""
    # Example:
    #   Best 2-leg
    #
    #   Tennis · ATP (Men)
    #   Luciano Darderi vs Brandon Nakashima
    #   Nakashima · Win a set
    _ = index  # index unused in copy — each message is already one slip
    lines = [bucket_title, ""]
    for leg in combo.get("legs") or []:
        lines.extend(_format_leg_lines(leg))
        lines.append("")
    while lines and lines[-1] == "":
        lines.pop()
    lines.extend(["", _telegram_board_url("combo")])
    return "\n".join(lines)


def site_utm(path: str, *, campaign: str = "daily", source: str = "telegram") -> str:
    """Canonical site URL with UTM so Telegram / social posts send traffic home."""
    path = path if str(path).startswith("/") else f"/{path}"
    src = (source or "telegram").strip() or "telegram"
    medium = "social" if src != "telegram" else "channel"
    return (
        f"{SITE_URL}{path}"
        f"?utm_source={src}&utm_medium={medium}&utm_campaign={campaign}"
    )


def _telegram_header_url(digest: dict | None = None) -> str:
    """Public site board in Telegram headers — never the locked digest page."""
    _ = digest
    return site_utm("/todays-picks")


def _telegram_board_url(kind: str = "picks") -> str:
    if kind == "combo":
        return site_utm("/todays-picks/combos")
    return site_utm("/todays-picks")


def _with_site_link(caption: str, *, kind: str = "picks") -> str:
    text = (caption or "").strip()
    url = _telegram_board_url(kind)
    if url not in text:
        text = f"{text}\n{url}" if text else url
    return text[:1024]


def build_telegram_messages(digest: dict) -> list[str]:
    """1 short header + 1 Today's Picks + 1 message per unique combo slip."""
    day = digest.get("date") or today_key()
    header_url = _telegram_header_url(digest)

    header = "\n".join([
        "Degen Predictions",
        day,
        header_url,
    ])
    messages = [
        header,
        _format_picks_message(list(digest.get("picks") or []), day=day),
    ]

    for bucket_title, key in (
        ("Best 2-leg", "combos_2"),
        ("Best 3-leg", "combos_3"),
        ("Mixed 2-leg", "mixed_2"),
        ("Mixed 3-leg", "mixed_3"),
    ):
        for i, combo in enumerate(digest.get(key) or [], 1):
            messages.append(
                _format_combo_slip_message(
                    combo, bucket_title=bucket_title, index=i,
                )
            )

    return messages


def build_digest_payload(
    picks: list[dict],
    *,
    day: str | None = None,
    legs: list[dict] | None = None,
    combos: dict | None = None,
) -> dict:
    """Share payload over existing Today's Picks cards (+ Best Combos slips)."""
    day = day or today_key()
    digest_url = f"{SITE_URL}{DIGEST_PATH}"
    combos_url = f"{SITE_URL}/todays-picks/combos"
    picks_hub = f"{SITE_URL}/todays-picks"
    league_urls = _league_urls_from_legs(legs)
    # Also derive leagues from the top picks themselves when legs omitted.
    if not league_urls and picks:
        league_urls = _league_urls_from_legs(picks)

    # Best buckets first; later buckets skip fingerprints AND shared fixtures.
    import today_picks as _tp

    used_fps: set[str] = set()
    used_fixtures: set = set()

    def _take(key, *, prefer_kinds=None):
        nonlocal used_fps, used_fixtures
        picked = select_top_combos(
            combos,
            key,
            n=DIGEST_COMBOS_PER_BUCKET,
            exclude_fps=used_fps,
            exclude_fixtures=used_fixtures,
            prefer_kinds=prefer_kinds,
        )
        for c in picked:
            used_fps.add(c.get("fingerprint") or _combo_fingerprint(c))
            for leg in c.get("legs") or []:
                if isinstance(leg, dict):
                    used_fixtures.add(_tp._leg_match_key(leg))
        return picked

    combos_2 = _take(2)
    combos_3 = _take(3)
    mixed_2 = _take("mixed_2", prefer_kinds=MIXED_PREFER_KINDS)
    mixed_3 = _take("mixed_3", prefer_kinds=MIXED_PREFER_KINDS)
    featured_combo_count = (
        len(combos_2) + len(combos_3) + len(mixed_2) + len(mixed_3)
    )
    has_combos = featured_combo_count > 0 or _combo_count(combos) > 0

    lines = []
    for i, p in enumerate(picks, 1):
        lines.append(f"{i}.")
        lines.extend(_format_leg_lines(p))
        lines.append("")
    while lines and lines[-1] == "":
        lines.pop()

    body_lines = list(lines) if lines else [
        "No high-confidence picks cleared the bar today.",
    ]
    combo_blocks: list[str] = []
    for title, slips in (
        ("Best 2-leg", combos_2),
        ("Best 3-leg", combos_3),
        ("Mixed 2-leg", mixed_2),
        ("Mixed 3-leg", mixed_3),
    ):
        block = _format_combo_block(title, slips)
        if block:
            combo_blocks.extend(["", *block])

    tg_channel = telegram_channel_url()
    share_bits = [
        f"Today's 3 best picks — Degen Predictions ({day})",
        "",
        *body_lines,
        *combo_blocks,
        "",
        f"Telegram: {tg_channel}",
        f"Today's Picks: {picks_hub}",
        f"Best Combos: {combos_url}",
    ]
    if league_urls:
        share_bits.append("League boards with picks today:")
        for row in league_urls[:4]:
            share_bits.append(f"  · {row['league_label']}: {row['url']}")
    share_bits += ["", DISCLAIMER]
    share_text = "\n".join(share_bits)

    # Aggregate text kept for drafts; live Telegram posts one
    # message per slip via telegram_messages (see build_telegram_messages).
    tg_bits = [
        f"Degen Predictions — Daily picks ({day})",
        "",
        *body_lines,
        *combo_blocks,
        "",
        f"Today's Picks: {picks_hub}",
        f"Best Combos: {combos_url}",
        f"Telegram: {tg_channel}",
        "",
        DISCLAIMER,
    ]
    telegram_text = "\n".join(tg_bits)

    payload = {
        "date": day,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "headline": "Today's 3 best picks — Degen Predictions",
        "digest_url": digest_url,
        "combos_url": combos_url,
        "picks_hub_url": picks_hub,
        "has_combos": has_combos,
        "combo_count": _combo_count(combos),
        "featured_combo_count": featured_combo_count,
        "combos_2": combos_2,
        "combos_3": combos_3,
        "mixed_2": mixed_2,
        "mixed_3": mixed_3,
        "league_urls": league_urls,
        "disclaimer": DISCLAIMER,
        "pick_count": len(picks),
        "picks": picks,
        "share_text": share_text,
        "telegram_text": telegram_text,
        "source": "today_picks",
        "telegram_channel_url": telegram_channel_url() or None,
    }
    payload["telegram_messages"] = build_telegram_messages(payload)
    return payload


def write_digest(digest: dict) -> Path:
    _ensure_dirs()
    day = digest.get("date") or today_key()
    day_path = DIGEST_DIR / f"{day}.json"
    payload = json.dumps(digest, indent=2, ensure_ascii=False) + "\n"
    day_path.write_text(payload, encoding="utf-8")
    LATEST_PATH.write_text(payload, encoding="utf-8")
    return day_path


# ---------------------------------------------------------------------------
# Slip card PNGs (Pillow optional — skip cleanly if missing)
# Product: ONE image per slip (Today's Picks + each combo) for Telegram / X.
# ---------------------------------------------------------------------------

BRAND_LOCKUP = "Degen Predictions™"
BRAND_SITE = "degenpredictions.com"
_SLIP_COLORS = {
    "bg": (7, 12, 22),
    "card": (18, 26, 43),
    "panel": (12, 18, 32),
    "accent": (45, 212, 191),
    "text": (232, 238, 248),
    "muted": (154, 171, 199),
    "won": (34, 197, 94),
    "lost": (248, 113, 113),
    "void": (148, 163, 184),
}


def _slip_logo_path() -> Path | None:
    for name in ("logo-nav.png", "logo.png", "favicon-96.png", "brand-1024.png"):
        path = WEBAPP / "static" / name
        if path.is_file():
            return path
    return None


def _paste_slip_logo(img, *, x: int = 80, y: int = 62, height: int = 56) -> int:
    """Paste brand mark; returns width used (0 if logo missing)."""
    try:
        from PIL import Image
    except ImportError:
        return 0
    logo_path = _slip_logo_path()
    if logo_path is None:
        return 0
    try:
        logo = Image.open(logo_path).convert("RGBA")
    except OSError:
        return 0
    if logo.height < 1:
        return 0
    scale = height / float(logo.height)
    w = max(1, int(round(logo.width * scale)))
    try:
        resample = Image.Resampling.LANCZOS
    except AttributeError:
        resample = Image.LANCZOS
    logo = logo.resize((w, height), resample)
    img.paste(logo, (x, y), logo)
    return w


def _pil_font(size: int, bold: bool = False):
    from PIL import ImageFont

    candidates = [
        "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    for path in candidates:
        if os.path.isfile(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _wrap_draw_text(draw, x: int, yy: int, raw: str, fnt, fill, *, max_w: int = 860,
                    max_lines: int = 2) -> int:
    """Draw wrapped text; returns y after last line."""
    words = str(raw or "").split()
    if not words:
        return yy

    def _width(s: str) -> float:
        try:
            return float(draw.textlength(s, font=fnt))
        except Exception:
            box = draw.textbbox((0, 0), s, font=fnt)
            return float(box[2] - box[0])

    lines_out: list[str] = []
    cur = words[0]
    for w in words[1:]:
        trial = f"{cur} {w}"
        if _width(trial) <= max_w:
            cur = trial
        else:
            lines_out.append(cur)
            cur = w
            if len(lines_out) >= max_lines:
                break
    else:
        lines_out.append(cur)
        cur = ""
    if cur and len(lines_out) < max_lines:
        lines_out.append(cur)
    for ln in lines_out[:max_lines]:
        draw.text((x, yy), ln, fill=fill, font=fnt)
        yy += int(getattr(fnt, "size", 22) + 6)
    return yy


def render_slip_card_png(
    *,
    title: str,
    legs: list[dict],
    out_path: Path,
    day: str | None = None,
    result_status: str | None = None,
    telegram_serial: int | None = None,
    result_serial: int | None = None,
) -> Path | None:
    """Sparse bet-slip image with Degen Predictions™ lockup (Telegram + X)."""
    payload = render_slip_card_payload(
        title=title, legs=legs, out_path=out_path, day=day,
        result_status=result_status,
        telegram_serial=telegram_serial,
        result_serial=result_serial,
    )
    if not payload:
        return None
    path = payload.get("path")
    return Path(path) if path else None


def _result_status_label(raw: str | None) -> str:
    s = (raw or "").strip().lower()
    return {
        "won": "WON",
        "lost": "LOST",
        "void": "VOID",
        "push": "PUSH",
        "pending": "PENDING",
    }.get(s, (raw or "").strip().upper() or "RESULT")


def _result_status_color(raw: str | None):
    s = (raw or "").strip().lower()
    c = _SLIP_COLORS
    if s == "won":
        return c["won"]
    if s == "lost":
        return c["lost"]
    if s in ("void", "push"):
        return c["void"]
    return c["accent"]


def _serial_tags(telegram_serial=None, result_serial=None) -> tuple[str, str]:
    try:
        import combo_results
        return (
            combo_results.format_telegram_serial(telegram_serial),
            combo_results.format_result_serial(result_serial),
        )
    except Exception:
        return "", ""


def _next_telegram_serial() -> int:
    try:
        import combo_results
        return int(combo_results.allocate_channel_serial("telegram") or 0)
    except Exception as exc:
        log.warning("telegram serial allocate failed: %s", exc)
        return 0


def render_slip_card_payload(
    *,
    title: str,
    legs: list[dict],
    out_path: Path,
    day: str | None = None,
    result_status: str | None = None,
    telegram_serial: int | None = None,
    result_serial: int | None = None,
) -> dict | None:
    """Render a slip card. Always returns png_bytes when Pillow works.

    ``path`` is set only if the PNG could be written to disk. Telegram send
    uses png_bytes so a non-writable cards/ directory cannot force text-only.

    When ``result_status`` is set (won/lost/void/…), the card is a RESULT
    reveal: status accent + per-leg outcome + actual note (no %).
    """
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        log.info("skipped slip PNG (Pillow not installed)")
        return None

    out_path = Path(out_path)
    try:
        _ensure_dirs()
    except OSError as exc:
        log.warning("slip PNG ensure dirs failed: %s", exc)

    legs = [leg for leg in (legs or []) if isinstance(leg, dict)]
    n_legs = max(1, len(legs))
    is_result = bool((result_status or "").strip())
    W = 1080
    footer_h = 150
    box_h = 220 if is_result else 168
    H = min(max(560 + n_legs * (box_h + 16), 760), 2200)
    c = _SLIP_COLORS
    accent = _result_status_color(result_status) if is_result else c["accent"]

    img = Image.new("RGB", (W, H), c["bg"])
    draw = ImageDraw.Draw(img)
    title_f = _pil_font(40, bold=True)
    body_f = _pil_font(28)
    brand_f = _pil_font(30, bold=True)
    small_f = _pil_font(24)
    tiny_f = _pil_font(20)
    badge_f = _pil_font(26, bold=True)

    draw.rounded_rectangle((40, 40, W - 40, H - 40), radius=36, fill=c["card"])
    draw.rectangle((40, 40, W - 40, 48), fill=accent)

    logo_w = _paste_slip_logo(img, x=80, y=64, height=56)
    brand_x = 80 + (logo_w + 18 if logo_w else 0)
    draw.text((brand_x, 72), BRAND_LOCKUP, fill=c["accent"], font=brand_f)
    tg_tag, rs_tag = _serial_tags(telegram_serial, result_serial)
    serial_line = rs_tag or tg_tag
    if serial_line:
        bbox = draw.textbbox((0, 0), serial_line, font=brand_f)
        tw = bbox[2] - bbox[0]
        draw.text((W - 80 - tw, 72), serial_line, fill=accent, font=brand_f)

    headline = (title or "Slip").strip() or "Slip"
    if is_result:
        label = _result_status_label(result_status)
        headline = f"RESULT · {label}"
        yy = _wrap_draw_text(draw, 80, 136, headline, title_f, accent, max_w=900, max_lines=1)
        sub = (title or "Slip").strip() or "Slip"
        if tg_tag:
            sub = f"{tg_tag}  {sub}"
        if day:
            sub = f"{sub} · {day}"
        yy = _wrap_draw_text(draw, 80, yy + 6, sub, small_f, c["muted"], max_w=900, max_lines=2)
        yy += 12
    else:
        yy = _wrap_draw_text(draw, 80, 136, headline, title_f, c["text"], max_w=900, max_lines=2)
        if day:
            draw.text((80, yy + 8), day, fill=c["muted"], font=small_f)
            yy += 44
        else:
            yy += 20

    y = max(yy + 16, 240)
    for i, leg in enumerate(legs, 1):
        if y + box_h > H - footer_h:
            break
        draw.rounded_rectangle((80, y, W - 80, y + box_h), radius=16, fill=c["panel"])
        draw.text((110, y + 16), f"#{i}", fill=accent, font=small_f)
        _wrap_draw_text(
            draw, 160, y + 16, _sport_league_label(leg), small_f, accent,
            max_w=780, max_lines=1,
        )
        ey = _wrap_draw_text(
            draw, 110, y + 52, _event_name(leg), body_f, c["text"],
            max_w=860, max_lines=2,
        )
        sel_y = max(ey, y + 100)
        if is_result:
            score = _score_line(leg)
            if score:
                _wrap_draw_text(
                    draw, 110, sel_y, score, body_f, c["text"],
                    max_w=860, max_lines=1,
                )
                sel_y += 34
            _wrap_draw_text(
                draw, 110, sel_y,
                _selection_line(leg, include_pct=False), small_f, c["muted"],
                max_w=860, max_lines=1,
            )
            leg_status = (leg.get("status") or result_status or "").strip().lower()
            leg_label = _result_status_label(leg_status)
            leg_color = _result_status_color(leg_status)
            _wrap_draw_text(
                draw, 110, sel_y + 34, leg_label, badge_f, leg_color,
                max_w=860, max_lines=1,
            )
        else:
            _wrap_draw_text(
                draw, 110, sel_y,
                _selection_line(leg, include_pct=False), small_f, c["muted"],
                max_w=860, max_lines=1,
            )
        y += box_h + 16

    if not legs:
        draw.text((110, y), "No picks on this slip.", fill=c["muted"], font=body_f)

    draw.text((80, H - 128), BRAND_LOCKUP, fill=c["accent"], font=small_f)
    draw.text((80, H - 98), BRAND_SITE, fill=c["muted"], font=tiny_f)
    draw.text(
        (80, H - 72),
        "Educational ML estimates — not betting advice",
        fill=c["muted"],
        font=tiny_f,
    )
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    png_bytes = buf.getvalue()
    path: Path | None = None
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(png_bytes)
        path = out_path
    except OSError as exc:
        log.warning("slip PNG disk write failed (%s): %s", out_path, exc)
    return {"path": path, "png_bytes": png_bytes}


def iter_featured_combo_slips(digest: dict) -> list[tuple[str, dict]]:
    """Ordered unique featured combos: Best 2, Best 3, Mixed 2, Mixed 3."""
    out: list[tuple[str, dict]] = []
    for bucket_title, key in (
        ("Best 2-leg", "combos_2"),
        ("Best 3-leg", "combos_3"),
        ("Mixed 2-leg", "mixed_2"),
        ("Mixed 3-leg", "mixed_3"),
    ):
        for combo in digest.get(key) or []:
            if isinstance(combo, dict) and (combo.get("legs") or []):
                out.append((bucket_title, combo))
    return out


def _slip_dict_from_payload(
    payload: dict | None,
    *,
    caption: str,
    kind: str,
    extra: dict | None = None,
) -> dict | None:
    if not payload or not payload.get("png_bytes"):
        return None
    slip = {
        "caption": caption,
        "kind": kind,
        "png_bytes": payload["png_bytes"],
    }
    if payload.get("path"):
        slip["path"] = Path(payload["path"])
    if extra:
        slip.update(extra)
    return slip


def build_telegram_slip_images(digest: dict) -> list[dict]:
    """Build 1 Today's Picks image + 1 image per combo slip.

    Returns list of ``{"caption": str, "path": Path, "kind": str}``.
    Empty list if Pillow missing or every render fails.
    """
    day = digest.get("date") or today_key()
    day_dir = CARDS_DIR / day
    slips: list[dict] = []

    picks = [p for p in (digest.get("picks") or []) if isinstance(p, dict)]
    # Never ship a blank "Today's Picks" card ("No picks on this slip.") to
    # Telegram. Morning gather can be empty at 09:00 IST; posting that used
    # to lock the day as posted_telegram and block the real slate later.
    if picks:
        picks_path = day_dir / "todays-picks.png"
        serial = _next_telegram_serial()
        tg_tag, _ = _serial_tags(serial)
        try:
            payload = render_slip_card_payload(
                title="Today's Picks",
                legs=picks,
                out_path=picks_path,
                day=day,
                telegram_serial=serial,
            )
        except Exception as exc:
            log.warning("Today's Picks slip PNG failed: %s", exc)
            payload = None
        cap = f"Today's Picks · {day}"
        if tg_tag:
            cap = f"{tg_tag} {cap}"
        slip = _slip_dict_from_payload(
            payload,
            caption=_with_site_link(cap, kind="picks"),
            kind="picks",
            extra={"title": "Today's Picks", "legs": picks, "telegram_serial": serial},
        )
        if slip:
            slips.append(slip)

    for i, (bucket_title, combo) in enumerate(iter_featured_combo_slips(digest), 1):
        # Channel captions/titles: bucket only — never joint %.
        caption = str(bucket_title or "Combo").strip() or "Combo"
        serial = _next_telegram_serial()
        tg_tag, _ = _serial_tags(serial)
        out_path = day_dir / f"combo-{i:02d}.png"
        try:
            payload = render_slip_card_payload(
                title=caption,
                legs=list(combo.get("legs") or []),
                out_path=out_path,
                day=day,
                telegram_serial=serial,
            )
        except Exception as exc:
            log.warning("combo slip PNG %s failed: %s", i, exc)
            payload = None
        cap = caption
        if tg_tag:
            cap = f"{tg_tag} {caption}"
        slip = _slip_dict_from_payload(
            payload,
            caption=_with_site_link(cap, kind="combo"),
            kind="combo",
            extra={
                "bucket": bucket_title,
                "title": caption,
                "legs": list(combo.get("legs") or []),
                "telegram_serial": serial,
            },
        )
        if slip:
            slips.append(slip)

    return slips


def build_manual_slip_images(
    *,
    picks: list[dict] | None = None,
    combos: list[tuple[str, dict]] | None = None,
    picks_title: str | None = None,
) -> list[dict]:
    """Admin-selected slips for Telegram. Captions/titles never include %."""
    day = today_key()
    day_dir = CARDS_DIR / day / "manual"
    try:
        day_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.warning("manual slip card dir failed: %s", exc)
    stamp = datetime.now(timezone.utc).strftime("%H%M%S")
    slips: list[dict] = []

    picks = [p for p in (picks or []) if isinstance(p, dict)]
    if picks:
        n = len(picks)
        title = (picks_title or "").strip() or (
            f"{n}-leg slip" if n != 3 else "Today's Picks"
        )
        serial = _next_telegram_serial()
        tg_tag, _ = _serial_tags(serial)
        out_path = day_dir / f"picks-{stamp}.png"
        try:
            payload = render_slip_card_payload(
                title=title,
                legs=picks,
                out_path=out_path,
                day=day,
                telegram_serial=serial,
            )
        except Exception as exc:
            log.warning("manual picks slip PNG failed: %s", exc)
            payload = None
        cap = f"{title} · {day}"
        if tg_tag:
            cap = f"{tg_tag} {cap}"
        slip = _slip_dict_from_payload(
            payload,
            caption=cap,
            kind="picks",
            extra={"title": title, "legs": picks, "telegram_serial": serial},
        )
        if slip:
            slips.append(slip)

    for i, item in enumerate(combos or [], 1):
        if not isinstance(item, (tuple, list)) or len(item) != 2:
            continue
        bucket_title, combo = item
        if not isinstance(combo, dict):
            continue
        legs = [leg for leg in (combo.get("legs") or []) if isinstance(leg, dict)]
        if not legs:
            continue
        caption = str(bucket_title or "Combo").strip() or "Combo"
        serial = _next_telegram_serial()
        tg_tag, _ = _serial_tags(serial)
        out_path = day_dir / f"combo-{stamp}-{i:02d}.png"
        try:
            payload = render_slip_card_payload(
                title=caption,
                legs=legs,
                out_path=out_path,
                day=day,
                telegram_serial=serial,
            )
        except Exception as exc:
            log.warning("manual combo slip PNG %s failed: %s", i, exc)
            payload = None
        cap = caption
        if tg_tag:
            cap = f"{tg_tag} {caption}"
        slip = _slip_dict_from_payload(
            payload,
            caption=cap,
            kind="combo",
            extra={
                "bucket": caption, "title": caption, "legs": legs,
                "telegram_serial": serial,
            },
        )
        if slip:
            slips.append(slip)

    return slips


# ---------------------------------------------------------------------------
# X / Twitter queue — same slips as Telegram, one short post each
# ---------------------------------------------------------------------------

TWITTER_LIMIT = 280
TWITTER_URL_WEIGHT = 23
LAST_TELEGRAM_SEND_MAX_SLIPS = 40

_HASHTAG_BY_LEAGUE = {
    "mlb": ("#MLB",),
    "npb": ("#NPB",),
    "nfl": ("#NFL",),
    "nba": ("#NBA",),
    "nhl": ("#NHL",),
    "atp": ("#ATP", "#Tennis"),
    "wta": ("#WTA", "#Tennis"),
    "hundred": ("#TheHundred",),
    "cpl": ("#CPL",),
    "ipl": ("#IPL",),
    "bbl": ("#BBL",),
    "mlc": ("#MLC",),
    "premier_league": ("#PremierLeague",),
    "mls": ("#MLS",),
    "la_liga": ("#LaLiga",),
    "bundesliga": ("#Bundesliga",),
    "serie_a": ("#SerieA",),
    "ligue_1": ("#Ligue1",),
    "primeira_liga": ("#PrimeiraLiga",),
    "liga_mx": ("#LigaMX",),
    "eredivisie": ("#Eredivisie",),
    "championship": ("#EFL",),
}

_HASHTAG_BY_SPORT = {
    "soccer": ("#Soccer",),
    "baseball": ("#Baseball",),
    "cricket": ("#Cricket",),
    "tennis": ("#Tennis",),
    "american_football": ("#NFL", "#NFLFootball"),
    "basketball": ("#NBA", "#Basketball"),
    "hockey": ("#NHL", "#IceHockey"),
}


def _leg_social_public(leg: dict | None) -> dict:
    leg = leg or {}
    keys = (
        "matchup", "pick", "market", "sport_key", "sport_label",
        "league_id", "league_label", "away", "home", "selection_line",
        "kickoff_iso", "status", "when",
    )
    return {k: (leg.get(k) or "") for k in keys}


def _slip_social_public(slip: dict | None) -> dict:
    slip = slip or {}
    legs = [
        _leg_social_public(leg)
        for leg in (slip.get("legs") or [])
        if isinstance(leg, dict)
    ]
    title = (
        (slip.get("title") or "").strip()
        or (slip.get("bucket") or "").strip()
        or (slip.get("caption") or "").strip().split("\n")[0]
        or "Pick"
    )
    # Drop site URLs accidentally stored in captions.
    if title.startswith("http"):
        title = "Pick"
    image_path = ""
    raw_path = slip.get("path") or slip.get("image_path") or ""
    if raw_path:
        try:
            p = Path(raw_path)
            if p.is_file():
                image_path = str(p.resolve())
        except OSError:
            image_path = ""
    return {
        "title": title[:80],
        "kind": (slip.get("kind") or "picks").strip() or "picks",
        "legs": legs,
        "image_path": image_path,
        "status": (slip.get("status") or "").strip(),
        "slate_date": (slip.get("slate_date") or "").strip(),
        "telegram_serial": slip.get("telegram_serial"),
        "result_serial": slip.get("result_serial"),
    }


def _x_queue_dir(day: str | None = None) -> Path:
    day = day or today_key()
    out = CARDS_DIR / day / "x_queue"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _copy_slip_image(src: Path | str | None, dest: Path) -> bool:
    if not src:
        return False
    try:
        src_path = Path(src)
        if not src_path.is_file():
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src_path.resolve() != dest.resolve():
            dest.write_bytes(src_path.read_bytes())
        return dest.is_file()
    except OSError:
        return False


def _slip_image_key(item: dict) -> str:
    """Stable short id for a slip's image, independent of its position in
    whatever list it's currently rendered in -- the X queue accumulates
    across sends and gets filtered as games finish, so list position drifts
    and can't be used to address a specific slip's image reliably."""
    fp = _legs_only_fingerprint(item) or (item.get("title") or "")
    return hashlib.sha1(fp.encode("utf-8")).hexdigest()[:16]


def ensure_twitter_slip_images(
    slips: list[dict],
    *,
    day: str | None = None,
) -> list[dict]:
    """Always re-render slip PNGs so the trademark lockup stays current."""
    day = day or today_key()
    out_dir = _x_queue_dir(day)
    ready: list[dict] = []
    for i, slip in enumerate(slips or [], 1):
        if not isinstance(slip, dict):
            continue
        pub = _slip_social_public(slip)
        if not pub.get("legs"):
            continue
        key = _slip_image_key(pub)
        dest = out_dir / f"{key}.png"
        ok = False
        try:
            rendered = render_slip_card_png(
                title=pub["title"],
                legs=pub["legs"],
                out_path=dest,
                day=day,
            )
            ok = bool(rendered and Path(rendered).is_file())
        except Exception as exc:
            log.warning("X queue PNG %s failed: %s", i, exc)
            ok = _copy_slip_image(pub.get("image_path") or slip.get("path"), dest)
        pub["image_path"] = str(dest.resolve()) if ok else ""
        pub["image_index"] = i
        pub["slip_key"] = key
        ready.append(pub)
    return ready


def save_last_telegram_send(slips: list[dict], *, day: str | None = None) -> Path:
    """Remember slips just posted to Telegram, accumulating across separate
    sends (rather than overwriting) so every slip sent stays available in
    the X queue -- not just the most recent send. De-duped by content key
    (a re-send of the same slip refreshes it, not a duplicate entry);
    capped at LAST_TELEGRAM_SEND_MAX_SLIPS, oldest dropped on overflow.
    Stale slips age out via twitter_queue()'s "still upcoming" filter at
    read time, not here -- this only remembers what was sent.
    """
    _ensure_dirs()
    LAST_TELEGRAM_SEND_PATH.parent.mkdir(parents=True, exist_ok=True)
    day = day or today_key()
    new_rows = ensure_twitter_slip_images(
        [s for s in (slips or []) if isinstance(s, dict)],
        day=day,
    )

    existing = load_last_telegram_send()
    prior_rows = list(existing.get("slips") or []) if existing else []

    seen: set[str] = set()
    rows: list[dict] = []
    for row in new_rows + prior_rows:
        key = row.get("slip_key") or _slip_image_key(row)
        if key in seen:
            continue
        seen.add(key)
        rows.append(row)
    rows = rows[:LAST_TELEGRAM_SEND_MAX_SLIPS]

    payload = {
        "date": day,
        "sent_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "slips": rows,
    }
    LAST_TELEGRAM_SEND_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return LAST_TELEGRAM_SEND_PATH


def load_last_telegram_send() -> dict | None:
    if not LAST_TELEGRAM_SEND_PATH.is_file():
        return None
    try:
        data = json.loads(LAST_TELEGRAM_SEND_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    slips = data.get("slips") or []
    if not slips:
        return None
    return data


def slips_from_digest(digest: dict | None) -> list[dict]:
    digest = digest or {}
    slips: list[dict] = []
    picks = [p for p in (digest.get("picks") or []) if isinstance(p, dict)]
    if picks:
        slips.append({"title": "Today's Picks", "kind": "picks", "legs": picks})
    for title, combo in iter_featured_combo_slips(digest):
        slips.append({
            "title": title,
            "kind": "combo",
            "legs": list(combo.get("legs") or []),
        })
    return [_slip_social_public(s) for s in slips if s.get("legs")]


def _hashtags_for_legs(legs: list[dict]) -> list[str]:
    tags: list[str] = ["#DegenPredictions"]
    seen = {tags[0].lower()}
    for leg in legs:
        league = (leg.get("league_id") or "").strip().lower()
        sport = (leg.get("sport_key") or "").strip().lower()
        extras = list(_HASHTAG_BY_LEAGUE.get(league) or ())
        if not extras:
            extras = list(_HASHTAG_BY_SPORT.get(sport) or ())
        for tag in extras:
            key = tag.lower()
            if key in seen:
                continue
            seen.add(key)
            tags.append(tag)
            if len(tags) >= 4:
                return tags
    return tags


def _tweet_len(text: str, url: str) -> int:
    if url and url in text:
        return len(text) - len(url) + TWITTER_URL_WEIGHT
    return len(text)


def _twitter_intent_url(text: str) -> str:
    return "https://twitter.com/intent/tweet?" + urllib.parse.urlencode({"text": text})


def _format_twitter_text(slip: dict, *, day: str | None = None) -> str:
    """Caption for one slip — always includes every leg (never '+N more')."""
    slip = _slip_social_public(slip)
    legs = slip.get("legs") or []
    kind = slip.get("kind") or "picks"
    title = slip.get("title") or ("Combo" if kind == "combo" else "Today's Picks")
    url = site_utm(
        "/todays-picks/combos" if kind == "combo" else "/todays-picks",
        campaign="x_queue",
        source="twitter",
    )
    tags = " ".join(_hashtags_for_legs(legs))

    def pack(blocks: list[str], *, head: str, footer: str) -> str:
        mid = "\n\n".join(b for b in blocks if b)
        if mid:
            return f"{head}\n\n{mid}\n\n{footer}".strip()
        return f"{head}\n\n{footer}".strip()

    # Prefer richest layout; always keep every leg — never truncate with "+N more".
    layouts = [
        [
            f"{_sport_league_label(leg)}\n{_event_name(leg)}\n{_selection_line(leg, include_pct=False)}"
            for leg in legs
        ],
        [
            f"{_event_name(leg)}\n{_selection_line(leg, include_pct=False)}"
            for leg in legs
        ],
        [
            f"{_event_name(leg)} · {_selection_line(leg, include_pct=False)}"
            for leg in legs
        ],
    ]
    tg_tag, _ = _serial_tags(slip.get("telegram_serial"))
    if tg_tag:
        title = f"{tg_tag} {title}"
    heads = [
        f"{BRAND_LOCKUP}\n{title}\n{day}" if day else f"{BRAND_LOCKUP}\n{title}",
        f"{title}\n{day}" if day else title,
        title,
    ]
    footers = [
        f"{url}\n\n{tags}\nNot betting advice",
        f"{url}\n{tags}",
        url,
    ]

    best = pack(layouts[-1], head=heads[-1], footer=footers[-1])
    for blocks in layouts:
        for head in heads:
            for footer in footers:
                text = pack(blocks, head=head, footer=footer)
                if _tweet_len(text, url) <= TWITTER_LIMIT:
                    return text
                # Prefer still-complete captions that are shorter even if over limit.
                if _tweet_len(text, url) < _tweet_len(best, url):
                    best = text
    return best


def _format_twitter_result_text(item: dict, *, prefix: str = "") -> str:
    """Short RESULT tweet — PNG carries the legs; keep WON/LOST + link."""
    status = (item.get("status") or "").strip().lower()
    label = _result_status_label(status)
    mark = {"won": "✅", "lost": "❌", "void": "➖", "push": "➖"}.get(status, "•")
    title = (item.get("title") or "").strip() or (
        "Single" if (item.get("kind") or "") == "single" else "Slip"
    )
    day = (item.get("slate_date") or "").strip()
    url = site_utm(
        "/todays-picks/combos",
        campaign="x_result",
        source="twitter",
    )
    tags = " ".join(_hashtags_for_legs(item.get("legs") or []))
    tg_tag, rs_tag = _serial_tags(
        item.get("telegram_serial"), item.get("result_serial"),
    )
    head = f"{mark} RESULT · {label}"
    if rs_tag:
        head = f"{rs_tag} {head}"
    if (prefix or "").strip():
        head = f"{prefix.strip()}\n{head}"
    sub = f"{title} · {day}" if day else title
    if tg_tag:
        sub = f"{tg_tag} {sub}"
    candidates = [
        f"{head}\n{sub}\n\n{url}\n\n{tags}\nNot betting advice",
        f"{head}\n{sub}\n\n{url}\n\n{tags}",
        f"{head}\n{sub}\n{url}\n{tags}",
        f"{head}\n{sub}\n{url}",
        f"{head}\n{url}",
    ]
    for text in candidates:
        if _tweet_len(text, url) <= TWITTER_LIMIT:
            return text
    return candidates[-1][:TWITTER_LIMIT]


def build_twitter_posts(
    slips: list[dict] | None = None,
    *,
    day: str | None = None,
) -> list[dict]:
    """One X-ready post per Telegram slip. No confidence %. Includes slip PNG."""
    day = day or today_key()
    slips = ensure_twitter_slip_images(list(slips or []), day=day)
    posts = []
    for i, slip in enumerate(slips, 1):
        if not isinstance(slip, dict):
            continue
        pub = _slip_social_public(slip)
        if not pub.get("legs"):
            continue
        if (pub.get("kind") or "") == "result" or (pub.get("status") or "").lower() in (
            "won", "lost", "void", "push",
        ):
            text = _format_twitter_result_text(pub, prefix="")
        else:
            text = _format_twitter_text(pub, day=day)
        board = site_utm(
            "/todays-picks/combos" if pub["kind"] == "combo" else "/todays-picks",
            campaign="x_queue",
            source="twitter",
        )
        image_path = (slip.get("image_path") or pub.get("image_path") or "").strip()
        has_image = bool(image_path and Path(image_path).is_file())
        image_url = ""
        slip_key = slip.get("slip_key") or _slip_image_key(pub)
        if has_image:
            image_url = f"/admin/twitter-image/{slip_key}.png?day={urllib.parse.quote(day)}"
        posts.append({
            "index": i,
            "title": pub["title"],
            "kind": pub["kind"],
            "leg_count": len(pub["legs"]),
            "text": text,
            "char_count": _tweet_len(text, board),
            "intent_url": _twitter_intent_url(text),
            "has_image": has_image,
            "image_url": image_url,
            "image_download_name": f"degen-{day}-{i:02d}.png",
        })
    return posts


def filter_digest_upcoming(digest: dict | None) -> dict:
    """Drop finished legs from a digest so admin/X show remaining fixtures."""
    digest = dict(digest or {})
    picks = [p for p in (digest.get("picks") or []) if isinstance(p, dict) and not _leg_is_finished(p)]
    digest["picks"] = picks
    featured = 0
    for key in ("combos_2", "combos_3", "mixed_2", "mixed_3"):
        kept = []
        for combo in digest.get(key) or []:
            if not isinstance(combo, dict):
                continue
            legs = [lg for lg in (combo.get("legs") or []) if isinstance(lg, dict)]
            if not legs or any(_leg_is_finished(lg) for lg in legs):
                continue
            kept.append(combo)
        digest[key] = kept
        featured += len(kept)
    digest["featured_combo_count"] = featured
    return digest


def verify_digest_fixtures(digest: dict | None, *, fixtures=None) -> dict:
    """Keep only legs whose match is on today's live fixture feeds."""
    import today_picks

    digest = dict(digest or {})
    live = list(fixtures) if fixtures is not None else today_picks.todays_live_fixtures()
    dropped: list[str] = []

    def _ok(leg: dict) -> bool:
        if today_picks.leg_on_todays_live_board(leg, live):
            return True
        dropped.append(
            f"{leg.get('league_id') or '?'} {leg.get('matchup') or leg.get('home') or '?'}"
        )
        return False

    digest["picks"] = [
        p for p in (digest.get("picks") or [])
        if isinstance(p, dict) and _ok(p)
    ]
    featured = 0
    for key in ("combos_2", "combos_3", "mixed_2", "mixed_3"):
        kept = []
        for combo in digest.get(key) or []:
            if not isinstance(combo, dict):
                continue
            legs = [lg for lg in (combo.get("legs") or []) if isinstance(lg, dict)]
            if not legs or not all(_ok(lg) for lg in legs):
                continue
            kept.append(combo)
        digest[key] = kept
        featured += len(kept)
    digest["featured_combo_count"] = featured
    digest["fixture_verify"] = {
        "live_count": len(live),
        "dropped": dropped[:20],
        "dropped_n": len(dropped),
        "picks": len(digest.get("picks") or []),
        "combos": featured,
    }
    if dropped:
        log.warning(
            "telegram fixture verify dropped %d legs (live fixtures=%d): %s",
            len(dropped), len(live), "; ".join(dropped[:8]),
        )
    return digest


def _upcoming_board_keys(digest: dict | None) -> set[str]:
    keys: set[str] = set()
    digest = digest or {}
    for pick in digest.get("picks") or []:
        if isinstance(pick, dict) and not _leg_is_finished(pick):
            keys.add(_leg_board_key(pick))
    for _title, combo in iter_featured_combo_slips(digest):
        for leg in combo.get("legs") or []:
            if isinstance(leg, dict) and not _leg_is_finished(leg):
                keys.add(_leg_board_key(leg))
    return keys


def _slip_still_upcoming(slip: dict | None, upcoming_keys: set[str] | None) -> bool:
    legs = [lg for lg in ((slip or {}).get("legs") or []) if isinstance(lg, dict)]
    if not legs:
        return False
    for leg in legs:
        if _leg_is_finished(leg):
            return False
        # Free-text custom/OCR legs (no league_id, no home/away) were never
        # on today's priced board to begin with, so they can never match a
        # board key -- checking upcoming_keys for them would drop every
        # custom slip from the X queue unconditionally. _leg_is_finished
        # above already covers staleness for these via kickoff_iso when
        # it's set.
        is_board_leg = bool(
            (leg.get("league_id") or "").strip() or leg.get("home") or leg.get("away")
        )
        if is_board_leg and upcoming_keys is not None and _leg_board_key(leg) not in upcoming_keys:
            return False
    return True


def twitter_queue(
    *,
    digest: dict | None = None,
    prefer_telegram: bool = True,
    board_legs: list[dict] | None = None,
) -> dict:
    """Admin queue: last Telegram send if those games are still upcoming, else live digest."""
    source = "digest"
    sent_at = None
    live = filter_digest_upcoming(digest or {})
    day = live.get("date") or today_key()
    slips: list[dict] = []
    if board_legs is not None:
        upcoming_keys = {
            _leg_board_key(leg)
            for leg in board_legs
            if isinstance(leg, dict) and not _leg_is_finished(leg)
        }
    else:
        upcoming_keys = _upcoming_board_keys(live) or None
    last = load_last_telegram_send() if prefer_telegram else None
    if last and last.get("slips"):
        kept = [
            s for s in last["slips"]
            if isinstance(s, dict) and _slip_still_upcoming(s, upcoming_keys)
        ]
        if kept:
            slips = kept
            source = "telegram"
            sent_at = last.get("sent_at")
            day = last.get("date") or day
    if not slips:
        slips = slips_from_digest(live)
        source = "digest"
        sent_at = None
        day = live.get("date") or today_key()
    posts = build_twitter_posts(slips, day=day)
    return {
        "date": day,
        "source": source,
        "sent_at": sent_at,
        "posts": posts,
        "count": len(posts),
    }


def twitter_image_path(key: str, *, day: str | None = None) -> Path | None:
    """Resolve a slip's PNG for an admin image route by its stable content
    key (see _slip_image_key) -- not by list position, which drifts as the
    X queue accumulates across sends and items drop out as games finish."""
    key = (key or "").strip()
    if not key:
        return None

    last = load_last_telegram_send()
    if last:
        for slip in last.get("slips") or []:
            if (slip.get("slip_key") or "") != key:
                continue
            raw = (slip.get("image_path") or "").strip()
            if raw:
                p = Path(raw)
                if p.is_file():
                    return p

    candidates = []
    if day:
        candidates.append(day)
    if last and last.get("date"):
        candidates.append(last["date"])
    candidates.append(today_key())
    seen = set()
    for d in candidates:
        if not d or d in seen:
            continue
        seen.add(d)
        path = _x_queue_dir(d) / f"{key}.png"
        if path.is_file():
            return path
    return None


def render_digest_card_png(digest: dict, out_path: Path | None = None) -> Path | None:
    """Legacy aggregate share card (web/drafts). Telegram uses per-slip images."""
    try:
        from PIL import Image
    except ImportError:
        log.info("skipped PNG share card (Pillow not installed)")
        return None

    day = digest.get("date") or today_key()
    out_path = out_path or (CARDS_DIR / f"{day}-top3.png")
    _ensure_dirs()

    slip_imgs = build_telegram_slip_images(digest)
    if slip_imgs:
        panels = []
        for row in slip_imgs:
            try:
                panels.append(Image.open(row["path"]).convert("RGB"))
            except OSError:
                continue
        if panels:
            W = max(p.width for p in panels)
            gap = 24
            H = sum(p.height for p in panels) + gap * (len(panels) - 1) + 48
            mega = Image.new("RGB", (W, H), _SLIP_COLORS["bg"])
            y = 24
            for p in panels:
                mega.paste(p, (0, y))
                y += p.height + gap
            mega.save(out_path, format="PNG", optimize=True)
            return out_path

    return render_slip_card_png(
        title="Today's picks + best combos",
        legs=list(digest.get("picks") or []),
        out_path=out_path,
        day=day,
    )


# ---------------------------------------------------------------------------
# Drafts (X / Quora / Shorts) — always written
# ---------------------------------------------------------------------------

def write_channel_drafts(digest: dict) -> Path:
    _ensure_dirs()
    day = digest.get("date") or today_key()
    path = DRAFTS_DIR / f"{day}.md"
    # Public drafts lead with the site board (UTM) plus the Telegram invite.
    url = site_utm("/todays-picks", campaign="social_draft")
    tg_join = (
        (digest.get("telegram_channel_url") or "").strip()
        or telegram_channel_url()
    )
    accuracy = f"{SITE_URL}/accuracy"
    picks = digest.get("picks") or []

    share = digest.get("share_text") or ""
    tg_messages = list(digest.get("telegram_messages") or [])
    if not tg_messages:
        tg_messages = build_telegram_messages(digest)
    tg_text = "\n\n———\n\n".join(tg_messages) if tg_messages else (
        digest.get("telegram_text") or share
    )
    combos_url = digest.get("combos_url") or f"{SITE_URL}/todays-picks/combos"
    combos_line = f"\nBest Combos (same board): {combos_url}\n"
    combo_summary = []
    for label, key in (
        ("Best 2-leg", "combos_2"),
        ("Best 3-leg", "combos_3"),
        ("Mixed 2-leg", "mixed_2"),
        ("Mixed 3-leg", "mixed_3"),
    ):
        n = len(digest.get(key) or [])
        if n:
            combo_summary.append(f"- {label}: {n} slip(s)")
    combo_summary_md = ("\n".join(combo_summary) + "\n") if combo_summary else ""
    league_lines = ""
    if digest.get("league_urls"):
        league_lines = "\nLeague Today's Picks:\n" + "\n".join(
            f"- {r['league_label']}: {r['url']}" for r in digest["league_urls"][:6]
        ) + "\n"

    content = f"""# Daily distribution drafts — {day}

Auto-generated from **existing** Today's Picks / Best Combos (not a parallel model).
Review before manual post. Honest disclaimer must stay on every channel.

> {DISCLAIMER}
{league_lines}{combos_line}{combo_summary_md}
---

## X / Twitter (ready to paste)

```
{share}
```

Thread alt (if character limit bites):
1/ Today's 3 best picks — Degen Predictions
{chr(10).join(f"{i}. {_sport_league_label(p)}: {p.get('pick')} @ {p.get('confidence_pct')}%" for i, p in enumerate(picks, 1)) or "(empty slate)"}
2/ Full board + methodology: {url}
3/ Best Combos (2-leg / 3-leg / mixed): {combos_url}
4/ Daily slips on Telegram: {tg_join or url}
Not betting advice — educational ML. Ledger: {accuracy}

---

## Quora-style answer outline

**Question patterns:** "How accurate are sports prediction sites?" / "Best free ML sports picks?"

1. Lead with honesty: full-slate vs selective tiers; cite the live ledger.
2. Link {accuracy} and name the relevant league (e.g. ATP / MLB / IPL).
3. Show today's board as an example of the selective filter: {url}
4. Mention Best Combos (incl. mixed cross-sport / cross-league): {combos_url}
5. Close with disclaimer: educational ML, not betting advice.

---

## YouTube Shorts / Reels script (30–45s) — Model pick vs result

**Hook (0–3s):** "Yesterday the model said X. Here's what happened."
**Setup (3–12s):** Show one settled pick from the results ledger (league + market + %).
**Reveal (12–28s):** Cut to actual result; green check or miss — no fake win.
**CTA (28–40s):** "Today's 3 best picks are live — link in bio." Show {url}
**Super:** "Educational ML · not betting advice · {accuracy}"

(Video generation is out of scope — use this script with screen recordings.)

---

## Telegram (1 header + 1 Today's Picks + 1 msg per combo)

Auto-post sends **{len(tg_messages)}** separate messages (picks together; each combo alone).

```
{tg_text}
```
"""
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Channel posters
# ---------------------------------------------------------------------------

def normalize_telegram_chat_id(raw: str) -> str:
    """Accept numeric chat id or @channelusername. Invite links are NOT chat ids."""
    val = (raw or "").strip()
    if not val:
        return ""
    lower = val.lower()
    # Private invite links (t.me/+HASH) cannot be used as chat_id.
    if "/+" in lower or lower.startswith("+") or "t.me/+" in lower:
        return ""
    for prefix in ("https://t.me/", "http://t.me/", "t.me/"):
        if lower.startswith(prefix):
            slug = val[len(prefix):].split("?")[0].split("/")[0].strip()
            if not slug or slug.startswith("+"):
                return ""
            return f"@{slug.lstrip('@')}"
    if val.startswith("@"):
        return val
    if val.replace("-", "").isalnum() and not val.lstrip("-").isdigit():
        return f"@{val}"
    return val  # numeric chat id (often -100... for channels)


def post_telegram(
    digest: dict,
    image_path: Path | None = None,
    slip_images: list[dict] | None = None,
    *,
    rebuild_images: bool | None = None,
) -> tuple[bool, str]:
    """Primary distribution channel. Posts when bot token + chat id are set.

    Product: optional tiny header text, then ONE sendPhoto per slip image
    (1 Today's Picks card + one photo per featured combo). Captions stay
    short titles (no %). Never dumps multi-slip text walls.

    ``slip_images``:
      - ``None`` → build from digest (morning job) unless rebuild_images=False
      - list (even empty) → use as-is; do **not** silently swap in the morning
        digest (admin manual send must post the selected slip only)
    """
    token, chat_id = telegram_bot_credentials()
    if not token or not chat_id:
        return False, "skipped Telegram (set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID)"

    import requests

    day = digest.get("date") or today_key()
    header_url = _telegram_header_url(digest)
    header = "\n".join([
        "Degen Predictions",
        day,
        header_url,
    ])

    if slip_images is None:
        do_rebuild = True if rebuild_images is None else bool(rebuild_images)
        slips: list[dict] = []
        if do_rebuild:
            try:
                slips = build_telegram_slip_images(digest)
            except Exception as exc:
                log.warning("slip image build failed: %s", exc)
                slips = []
    else:
        slips = list(slip_images)

    def _slip_png_bytes(slip: dict) -> bytes | None:
        raw = slip.get("png_bytes")
        if isinstance(raw, (bytes, bytearray)) and raw:
            return bytes(raw)
        path = slip.get("path")
        if path and Path(path).is_file():
            try:
                return Path(path).read_bytes()
            except OSError:
                return None
        return None

    # Keep slips that have PNG bytes in memory or a readable file.
    slips = [
        s for s in slips
        if isinstance(s, dict) and _slip_png_bytes(s)
    ]

    picks = [p for p in (digest.get("picks") or []) if isinstance(p, dict)]
    featured = int(digest.get("featured_combo_count") or 0)
    if not slips and not picks and not featured:
        messages = list(digest.get("telegram_messages") or [])
        bodies = messages[1:] if len(messages) > 1 else []
        meaningful = [
            b for b in bodies
            if (b or "").strip() and "No high-confidence picks today." not in (b or "")
        ]
        if not meaningful:
            return False, "skipped Telegram (empty slate; no slip images)"

    def _send_message(body: str, *, preview: bool = False):
        return requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": body[:4096],
                "disable_web_page_preview": not preview,
            },
            timeout=30,
        )

    def _send_photo(png_bytes: bytes, caption: str, *, filename: str = "slip.png"):
        # Explicit filename + image/png: a bare file handle is often sent as
        # application/octet-stream and Telegram sendPhoto then rejects it.
        name = filename if str(filename).lower().endswith(".png") else "slip.png"
        payload = {"chat_id": chat_id, "caption": (caption or "")[:1024]}
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendPhoto",
            data=payload,
            files={"photo": (name, png_bytes, "image/png")},
            timeout=45,
        )
        if resp.status_code < 400:
            return resp
        log.warning(
            "sendPhoto failed (%s): %s — retrying sendDocument",
            resp.status_code, (resp.text or "")[:200],
        )
        return requests.post(
            f"https://api.telegram.org/bot{token}/sendDocument",
            data=payload,
            files={"document": (name, png_bytes, "image/png")},
            timeout=45,
        )

    sent = 0
    photos_sent = 0
    header_message_id = None
    try:
        # Tiny header text (OK per product spec).
        resp = _send_message(header, preview=True)
        if resp.status_code >= 400:
            return False, f"Telegram header error {resp.status_code}: {resp.text[:300]}"
        sent += 1
        try:
            header_message_id = (resp.json().get("result") or {}).get("message_id")
        except Exception:
            header_message_id = None

        if slips:
            for i, slip in enumerate(slips):
                time.sleep(0.35)
                png_bytes = _slip_png_bytes(slip)
                if not png_bytes:
                    return False, (
                        f"Telegram slip photo {i + 1}/{len(slips)} missing PNG "
                        f"after {photos_sent} photos"
                    )
                caption = _with_site_link(
                    str(slip.get("caption") or ""),
                    kind="combo" if slip.get("kind") == "combo" else "picks",
                )
                fname = "slip.png"
                path = slip.get("path")
                if path:
                    fname = Path(path).name or fname
                resp = _send_photo(png_bytes, caption, filename=fname)
                if resp.status_code >= 400:
                    return False, (
                        f"Telegram slip photo {i + 1}/{len(slips)} failed "
                        f"after {photos_sent} photos: {resp.status_code}: "
                        f"{resp.text[:300]}"
                    )
                photos_sent += 1
                sent += 1
                try:
                    mid = (resp.json().get("result") or {}).get("message_id")
                except Exception:
                    mid = None
                if mid is not None:
                    slip["telegram_message_id"] = mid
                    ids = list(slip.get("telegram_message_ids") or [])
                    ids.append(mid)
                    if header_message_id is not None and header_message_id not in ids:
                        # Keep header id once on the first slip only for audit.
                        pass
                    slip["telegram_message_ids"] = ids
            n_picks = sum(1 for s in slips if s.get("kind") == "picks")
            n_combos = len(slips) - n_picks
            return True, (
                f"posted Telegram slip images ({photos_sent} photos: "
                f"{n_picks} picks + {n_combos} combos, +1 header) → {chat_id}"
            )

        # Hard fallback only if Pillow/render unavailable — still split messages.
        log.warning("no slip PNGs available; falling back to text slips")
        messages = list(digest.get("telegram_messages") or []) or build_telegram_messages(digest)
        bodies = messages[1:] if len(messages) > 1 else messages
        _ = image_path  # legacy mega-card intentionally unused for channel posts
        for i, body in enumerate(bodies):
            time.sleep(0.35)
            resp = _send_message(body, preview=False)
            if resp.status_code >= 400:
                return False, (
                    f"Telegram text slip {i + 1}/{len(bodies)} failed after "
                    f"{sent} ok: {resp.status_code}: {resp.text[:300]}"
                )
            sent += 1
        return True, (
            f"posted Telegram TEXT FALLBACK ({sent} messages, 0 photos) → {chat_id}"
        )
    except Exception as exc:
        return False, f"Telegram failed after {sent} messages ({photos_sent} photos): {exc}"


def build_telegram_result_image(item: dict) -> dict | None:
    """Render a RESULT slip PNG for a settled channel item."""
    legs = [leg for leg in (item.get("legs") or []) if isinstance(leg, dict)]
    if not legs:
        return None
    day = (item.get("slate_date") or today_key()).strip() or today_key()
    title = (item.get("title") or "").strip() or (
        "Single" if (item.get("kind") or "") == "single" else "Slip"
    )
    status = (item.get("status") or "").strip().lower() or "pending"
    tg_serial = item.get("telegram_serial")
    rs_serial = item.get("result_serial")
    tg_tag, rs_tag = _serial_tags(tg_serial, rs_serial)
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in title)[:40] or "slip"
    out_path = CARDS_DIR / day / "results" / f"{safe}-{status}.png"
    try:
        payload = render_slip_card_payload(
            title=title,
            legs=legs,
            out_path=out_path,
            day=day,
            result_status=status,
            telegram_serial=tg_serial,
            result_serial=rs_serial,
        )
    except Exception as exc:
        log.warning("result slip PNG failed: %s", exc)
        return None
    if not payload or not payload.get("png_bytes"):
        return None
    label = _result_status_label(status)
    caption = _with_site_link(
        f"RESULT · {label}\n{title} · {day}",
        kind="combo" if (item.get("kind") or "") == "combo" else "picks",
    )
    # Prefer results board for settled posts.
    try:
        cap_head = f"RESULT · {label}"
        if rs_tag:
            cap_head = f"{rs_tag} {cap_head}"
        cap_sub = f"{title} · {day}"
        if tg_tag:
            cap_sub = f"{tg_tag} {cap_sub}"
        caption = (
            f"{cap_head}\n{cap_sub}\n"
            f"{site_utm('/todays-picks/combos', campaign='tg_result')}"
        )
    except Exception:
        pass
    return {
        "caption": caption[:1024],
        "png_bytes": payload["png_bytes"],
        "path": payload.get("path"),
        "kind": "result",
        "title": title,
        "status": status,
        "legs": legs,
        "slate_date": day,
        "telegram_serial": tg_serial,
        "result_serial": rs_serial,
    }


def _telegram_retry_after(resp) -> int | None:
    if resp is None or getattr(resp, "status_code", 0) != 429:
        return None
    try:
        payload = resp.json() or {}
        wait = int(((payload.get("parameters") or {}).get("retry_after") or 0))
    except (TypeError, ValueError, AttributeError):
        wait = 0
    if wait <= 0:
        return 20
    return min(wait + 1, 60)


def _telegram_post_with_retry(url: str, **kwargs):
    import requests

    resp = requests.post(url, **kwargs)
    wait = _telegram_retry_after(resp)
    if wait:
        log.warning("Telegram 429 — sleeping %ss then retry", wait)
        time.sleep(wait)
        resp = requests.post(url, **kwargs)
    return resp


def post_telegram_result_reply(
    text: str,
    *,
    reply_to_message_id: int | None = None,
    png_bytes: bytes | None = None,
    caption: str | None = None,
) -> tuple[bool, str]:
    """Reply (or post) a settled-slip result into the Telegram channel.

    Prefers ``sendPhoto`` when ``png_bytes`` is provided; falls back to text.
    """
    token, chat_id = telegram_bot_credentials()
    if not token or not chat_id:
        return False, "skipped Telegram (set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID)"

    import requests

    reply_payload: dict = {}
    if reply_to_message_id is not None:
        try:
            reply_payload["reply_to_message_id"] = int(reply_to_message_id)
            reply_payload["allow_sending_without_reply"] = True
        except (TypeError, ValueError):
            pass

    if png_bytes:
        cap = (caption or text or "").strip()[:1024]
        data = {"chat_id": chat_id, "caption": cap, **reply_payload}
        try:
            resp = _telegram_post_with_retry(
                f"https://api.telegram.org/bot{token}/sendPhoto",
                data=data,
                files={"photo": ("result.png", png_bytes, "image/png")},
                timeout=45,
            )
            if resp.status_code >= 400:
                log.warning(
                    "result sendPhoto failed (%s): %s — retrying sendDocument",
                    resp.status_code, (resp.text or "")[:200],
                )
                resp = _telegram_post_with_retry(
                    f"https://api.telegram.org/bot{token}/sendDocument",
                    data=data,
                    files={"document": ("result.png", png_bytes, "image/png")},
                    timeout=45,
                )
        except Exception as exc:
            return False, f"Telegram result photo failed: {exc}"
        if resp.status_code >= 400:
            return False, (
                f"Telegram result photo error {resp.status_code}: {resp.text[:300]}"
            )
        return True, f"posted Telegram result photo → {chat_id}"

    body = (text or "").strip()
    if not body:
        return False, "empty result text"
    payload: dict = {
        "chat_id": chat_id,
        "text": body[:4096],
        "disable_web_page_preview": True,
        **reply_payload,
    }
    try:
        resp = _telegram_post_with_retry(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json=payload,
            timeout=30,
        )
    except Exception as exc:
        return False, f"Telegram result reply failed: {exc}"
    if resp.status_code >= 400:
        return False, f"Telegram result reply error {resp.status_code}: {resp.text[:300]}"
    return True, f"posted Telegram result reply → {chat_id}"


# Channel RESULT audience filter: never flood the channel with misses.
TELEGRAM_RESULT_MAX_LOSSES = 2


def _mark_result_channels(item: dict, *, telegram: bool = False, twitter: bool = False) -> None:
    import combo_results

    kind = item.get("kind") or "single"
    try:
        row_id = int(item["id"])
    except (KeyError, TypeError, ValueError):
        return
    if telegram:
        try:
            combo_results.mark_telegram_result_posted(kind, row_id)
        except Exception as exc:
            log.warning("mark telegram result posted failed: %s", exc)
    if twitter:
        try:
            combo_results.mark_twitter_result_posted(kind, row_id)
        except Exception as exc:
            log.warning("mark twitter result posted failed: %s", exc)


def _post_result_to_channels(
    item: dict,
    *,
    text: str,
    caption: str,
    png_bytes: bytes | None,
    reply_to: int | None = None,
    do_telegram: bool = True,
    do_twitter: bool = True,
    twitter_prefix: str = "",
) -> dict:
    """Post one RESULT slip to Telegram and/or X. Channels are independent."""
    out = {
        "telegram_ok": None,
        "telegram_msg": "",
        "twitter_ok": None,
        "twitter_msg": "",
    }
    if do_telegram:
        ok, msg = post_telegram_result_reply(
            text,
            reply_to_message_id=reply_to,
            png_bytes=png_bytes,
            caption=caption,
        )
        out["telegram_ok"] = ok
        out["telegram_msg"] = msg
        if not ok:
            log.warning("telegram result reply failed: %s", msg)
    if do_twitter:
        ok, msg = post_twitter_result(
            item,
            png_bytes=png_bytes,
            prefix=twitter_prefix,
        )
        out["twitter_ok"] = ok
        out["twitter_msg"] = msg
        if not ok and not _is_twitter_skip(msg):
            log.warning("twitter result post failed: %s", msg)
    return out


def _is_twitter_skip(msg: str | None) -> bool:
    return (msg or "").lower().startswith("skipped")


def _empty_result_post_stats(**extra: Any) -> dict:
    out = {
        "checked": 0,
        "posted": 0,
        "skipped": 0,
        "failed": 0,
        "twitter_posted": 0,
        "twitter_failed": 0,
        "twitter_skipped": 0,
        "twitter_queued": 0,
        "errors": [],
    }
    out.update(extra)
    return out


def _tally_channel_result(stats: dict, posted: dict) -> None:
    tg_ok = posted.get("telegram_ok")
    if tg_ok is True:
        stats["posted"] = int(stats.get("posted") or 0) + 1
    elif tg_ok is False:
        stats["failed"] = int(stats.get("failed") or 0) + 1
        msg = posted.get("telegram_msg") or ""
        if msg:
            stats.setdefault("errors", []).append(msg)
    x_ok = posted.get("twitter_ok")
    x_msg = posted.get("twitter_msg") or ""
    if x_ok is True:
        stats["twitter_posted"] = int(stats.get("twitter_posted") or 0) + 1
    elif x_ok is False and _is_twitter_skip(x_msg):
        stats["twitter_skipped"] = int(stats.get("twitter_skipped") or 0) + 1
    elif x_ok is False:
        stats["twitter_failed"] = int(stats.get("twitter_failed") or 0) + 1
        if x_msg:
            stats.setdefault("errors", []).append(x_msg)
    stats["errors"] = list(stats.get("errors") or [])[:8]


def post_due_telegram_results(
    *,
    limit: int = 40,
    max_losses: int | None = None,
    do_telegram: bool = True,
    do_twitter: bool = True,
    only_keys: list | None = None,
) -> dict:
    """Send RESULT photos for settled channel slips not yet posted.

    Admin chooses Telegram and/or X. Audience rule: post **all WON**
    slips; post at most ``max_losses`` LOST slips per slate day (default 2).
    Extra losses / voids are marked posted without sending so the queue
    stays clean. Telegram and X are tracked independently so a failed X
    post retries without double-posting Telegram. When X API creds are
    missing, RESULT cards are queued for the admin X page.

    ``only_keys`` is an optional list of ``(kind, id)`` pairs — when set,
    only those selected slips are considered, fetched directly (ignoring
    the "already posted" / win-loss-void curation used by the automatic
    queue below) so an admin can explicitly resend or send a VOID result.
    Empty selection from admin = send nothing (do not fall back to all due).
    """
    import combo_results

    max_losses = TELEGRAM_RESULT_MAX_LOSSES if max_losses is None else int(max_losses)
    explicit = only_keys is not None

    if explicit:
        key_set = set()
        for item in only_keys:
            try:
                kind, row_id = item[0], int(item[1])
            except (TypeError, ValueError, IndexError):
                continue
            kind = (kind or "").strip().lower()
            if kind in ("combo", "single"):
                key_set.add((kind, row_id))
        explicit_items = combo_results.get_results_by_keys(list(key_set))
        tg_items = list(explicit_items) if do_telegram else []
        x_items = list(explicit_items) if do_twitter else []
    else:
        tg_items = (
            combo_results.list_telegram_results_due(limit=limit, channel="telegram")
            if do_telegram else []
        )
        x_items = (
            combo_results.list_telegram_results_due(limit=limit, channel="twitter")
            if do_twitter else []
        )

    stats = _empty_result_post_stats(max_losses=max_losses)
    stats["checked"] = len(tg_items)

    def _by_day(rows: list[dict]) -> dict[str, list[dict]]:
        grouped: dict[str, list[dict]] = {}
        for item in rows:
            day = (item.get("slate_date") or combo_results.slate_date_utc()).strip()
            grouped.setdefault(day, []).append(item)
        return grouped

    seen: set[tuple[str, int]] = set()
    skipped = 0
    x_queue_slips: list[dict] = []

    def _queue_x_slip(item: dict, image: dict | None) -> None:
        if not do_twitter:
            return
        legs = list((image or {}).get("legs") or item.get("legs") or [])
        if not legs:
            return
        x_queue_slips.append({
            "title": (
                (item.get("title") or "")
                or (image or {}).get("title")
                or "RESULT"
            ),
            "kind": "result",
            "status": item.get("status") or (image or {}).get("status") or "",
            "legs": legs,
            "png_bytes": (image or {}).get("png_bytes"),
            "path": (image or {}).get("path"),
            "slate_date": item.get("slate_date") or (image or {}).get("slate_date") or "",
            "telegram_serial": item.get("telegram_serial") or (image or {}).get("telegram_serial"),
            "result_serial": item.get("result_serial") or (image or {}).get("result_serial"),
        })

    for day, day_items in _by_day(tg_items).items():
        if explicit:
            # Admin picked these exact rows -- send all of them, no
            # win/loss curation and no VOID/PUSH skip.
            to_post, to_skip = day_items, []
        else:
            already = combo_results.count_telegram_loss_results_posted(day=day)
            to_post, to_skip = combo_results.select_telegram_results_for_channel(
                day_items,
                max_losses=max_losses,
                losses_already_posted=already,
            )
        for item in to_skip:
            _mark_result_channels(item, telegram=True, twitter=True)
            skipped += 1
            seen.add(((item.get("kind") or "single"), int(item["id"])))
            log.info(
                "telegram result skipped (%s) id=%s title=%s",
                item.get("status"),
                item.get("id"),
                (item.get("title") or "")[:40],
            )
        for item in to_post:
            item = combo_results.stamp_result_serial(item)
            kind_id = ((item.get("kind") or "single"), int(item["id"]))
            seen.add(kind_id)
            text = combo_results.format_telegram_result_text(item)
            ids = combo_results.parse_telegram_message_ids(item.get("telegram_message_ids"))
            reply_to = ids[0] if ids else None
            image = None
            try:
                image = build_telegram_result_image(item)
            except Exception as exc:
                log.warning("build telegram result image failed: %s", exc)
            need_x = bool(do_twitter) and not (
                item.get("twitter_result_posted_at") or ""
            ).strip()
            posted = _post_result_to_channels(
                item,
                text=text,
                caption=(image or {}).get("caption") or text,
                png_bytes=(image or {}).get("png_bytes"),
                reply_to=reply_to,
                do_telegram=True,
                do_twitter=need_x,
            )
            _mark_result_channels(
                item,
                telegram=bool(posted.get("telegram_ok")),
                twitter=bool(posted.get("twitter_ok")),
            )
            _tally_channel_result(stats, posted)
            if need_x and not posted.get("twitter_ok"):
                _queue_x_slip(item, image)
            time.sleep(0.35)

    # Ledger rows whose Telegram RESULT already went out still need X.
    remaining_x = []
    for item in x_items:
        kind_id = ((item.get("kind") or "single"), int(item["id"]))
        if kind_id in seen:
            continue
        remaining_x.append(item)
    stats["checked"] = int(stats.get("checked") or 0) + len(remaining_x)

    for day, day_items in _by_day(remaining_x).items():
        if explicit:
            to_post, to_skip = day_items, []
        else:
            already = combo_results.count_twitter_loss_results_posted(day=day)
            to_post, to_skip = combo_results.select_telegram_results_for_channel(
                day_items,
                max_losses=max_losses,
                losses_already_posted=already,
            )
        for item in to_skip:
            _mark_result_channels(item, twitter=True)
            skipped += 1
        for item in to_post:
            item = combo_results.stamp_result_serial(item)
            image = None
            try:
                image = build_telegram_result_image(item)
            except Exception as exc:
                log.warning("build twitter result image failed: %s", exc)
            posted = _post_result_to_channels(
                item,
                text=combo_results.format_telegram_result_text(item),
                caption=(image or {}).get("caption") or "",
                png_bytes=(image or {}).get("png_bytes"),
                do_telegram=False,
                do_twitter=True,
            )
            _mark_result_channels(item, twitter=bool(posted.get("twitter_ok")))
            _tally_channel_result(stats, posted)
            if not posted.get("twitter_ok"):
                _queue_x_slip(item, image)
            time.sleep(0.35)

    if do_twitter and x_queue_slips:
        try:
            save_last_telegram_send(x_queue_slips, day=today_key())
            stats["twitter_queued"] = len(x_queue_slips)
            log.info("queued %d RESULT slip(s) for X admin", len(x_queue_slips))
        except Exception as exc:
            log.warning("queue X result slips failed: %s", exc)

    stats["skipped"] = skipped
    stats["errors"] = list(stats.get("errors") or [])[:5]
    return stats


def digest_result_fingerprint(item: dict) -> str:
    """Stable id for a digest RESULT card so we do not post it twice."""
    title = (item.get("title") or "").strip().lower()
    status = (item.get("status") or "").strip().lower()
    return f"{title}::{status}::{_legs_only_fingerprint(item)}"


def _legs_only_fingerprint(item: dict) -> str:
    parts: list[str] = []
    for leg in item.get("legs") or []:
        if not isinstance(leg, dict):
            continue
        parts.append("|".join([
            (leg.get("matchup") or "").strip().lower(),
            (leg.get("pick") or "").strip().lower(),
            (leg.get("market") or "").strip().lower(),
        ]))
    return ";;".join(sorted(parts))


def _merge_result_slips(items: list[dict]) -> list[dict]:
    """Drop duplicate RESULT cards that share the same legs."""
    seen: set[str] = set()
    out: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        fp = _legs_only_fingerprint(item)
        if not fp or fp in seen:
            continue
        seen.add(fp)
        out.append(item)
    return out


def _digest_channel_flags(meta: dict | None) -> tuple[bool, bool, bool]:
    """Return (telegram_done, twitter_done, skipped) for a digest RESULT row.

    Older telegram_digest_results.json entries only stored ``at`` after a
    Telegram send — treat those as Telegram-done, X still due.
    """
    if not isinstance(meta, dict):
        return False, False, False
    if meta.get("skipped"):
        return True, True, True
    # Explicit False must not count as posted. Legacy rows only stored ``at``
    # after a successful Telegram send (no telegram/twitter keys).
    if "telegram" in meta:
        done_tg = bool(meta.get("telegram") or meta.get("seed"))
    else:
        done_tg = bool(meta.get("at") or meta.get("seed"))
    done_x = bool(meta.get("twitter"))
    return done_tg, done_x, False


def _load_digest_results_state() -> dict:
    if not TELEGRAM_DIGEST_RESULTS_PATH.is_file():
        return {}
    try:
        return json.loads(TELEGRAM_DIGEST_RESULTS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_digest_results_state(state: dict) -> None:
    TELEGRAM_DIGEST_RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state, indent=2, ensure_ascii=True) + "\n"
    tmp = TELEGRAM_DIGEST_RESULTS_PATH.with_name(
        TELEGRAM_DIGEST_RESULTS_PATH.name + ".tmp"
    )
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(TELEGRAM_DIGEST_RESULTS_PATH)


def _grade_digest_slip_legs(legs: list[dict] | None) -> tuple[list[dict], str]:
    import combo_results

    out: list[dict] = []
    for leg in legs or []:
        if not isinstance(leg, dict):
            continue
        score = combo_results.lookup_score_for_leg(leg) or {}
        status, note = combo_results.settle_leg_against_score(
            leg.get("market") or "",
            leg.get("pick") or "",
            score,
        )
        row = dict(leg)
        row["status"] = status
        row["actual_note"] = note
        out.append(row)
    slip_status = combo_results._roll_up_slip_status([r["status"] for r in out])
    return out, slip_status


def collect_board_won_combo_slips(day: str) -> list[dict]:
    """Every WON published combo on the public board for ``day``."""
    import combo_results

    day = (day or "").strip()
    if not day:
        return []
    try:
        rows = combo_results.list_slips(
            days=21, status=combo_results.WON, limit=200,
        )
    except Exception as exc:
        log.warning("list won board slips failed: %s", exc)
        return []
    out: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if (row.get("slate_date") or "").strip() != day:
            continue
        legs = [leg for leg in (row.get("legs") or []) if isinstance(leg, dict)]
        if not legs:
            continue
        bucket = (row.get("bucket") or "best").strip() or "best"
        try:
            size = int(row.get("size") or len(legs) or 2)
        except (TypeError, ValueError):
            size = len(legs)
        title = (row.get("title") or "").strip() or (
            f"{bucket.replace('_', ' ').title()} {size}-leg"
        )
        out.append({
            "kind": "combo",
            "title": title,
            "slate_date": day,
            "status": combo_results.WON,
            "legs": legs,
        })
    return out


def collect_digest_result_slips(day: str) -> list[dict]:
    """RESULT cards for the same slips the morning job posted.

    One Today's Picks card + the featured combo cards. Not the rest of the
    public board — those were never sent to the channel.
    """
    path = DIGEST_DIR / f"{day}.json"
    slips: list[dict] = []
    digest: dict = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                digest = loaded
        except (OSError, json.JSONDecodeError):
            digest = {}
    digest["date"] = day

    picks = [p for p in (digest.get("picks") or []) if isinstance(p, dict)]
    if picks:
        graded, status = _grade_digest_slip_legs(picks)
        slips.append({
            "kind": "combo",
            "title": "Today's Picks",
            "slate_date": day,
            "status": status,
            "legs": graded,
        })
    for title, combo in iter_featured_combo_slips(digest):
        graded, status = _grade_digest_slip_legs(list(combo.get("legs") or []))
        if not graded:
            continue
        slips.append({
            "kind": "combo",
            "title": title,
            "slate_date": day,
            "status": status,
            "legs": graded,
        })
    return _merge_result_slips(slips)


def post_digest_day_telegram_results(
    day: str,
    *,
    max_losses: int | None = None,
    seed_posted: list[dict] | None = None,
) -> dict:
    """Post RESULT photos for a digest day that was sent to the channel.

    Used when ledger ``telegram_message_ids`` were never stored (the usual
    case for morning digest posts). Posts Telegram and X. Idempotent via
    telegram_digest_results.json (Telegram and X tracked separately).
    """
    import combo_results

    day = (day or "").strip()
    max_losses = TELEGRAM_RESULT_MAX_LOSSES if max_losses is None else int(max_losses)
    if not day:
        return _empty_result_post_stats(day=day, pending=0)

    slips = collect_digest_result_slips(day)
    state = _load_digest_results_state()
    day_state = dict(state.get(day) or {})
    now = datetime.now(timezone.utc).isoformat()
    for item in seed_posted or []:
        if not isinstance(item, dict):
            continue
        fp = digest_result_fingerprint(item)
        day_state.setdefault(fp, {
            "at": now,
            "status": (item.get("status") or "").strip().lower(),
            "title": (item.get("title") or "").strip(),
            "seed": True,
            "telegram": True,
        })

    pending = [s for s in slips if (s.get("status") or "") == combo_results.PENDING]
    new_items: list[dict] = []
    x_only: list[dict] = []
    for item in slips:
        if (item.get("status") or "") == combo_results.PENDING:
            continue
        fp = digest_result_fingerprint(item)
        item = dict(item)
        item["_fp"] = fp
        done_tg, done_x, skipped_row = _digest_channel_flags(day_state.get(fp))
        if skipped_row:
            continue
        item["_had_tg"] = done_tg
        item["_had_x"] = done_x
        if not done_tg:
            new_items.append(item)
        elif not done_x:
            x_only.append(item)

    already_losses = 0
    for meta in day_state.values():
        if not isinstance(meta, dict):
            continue
        if (meta.get("status") or "").strip().lower() != combo_results.LOST:
            continue
        done_tg, done_x, skipped_row = _digest_channel_flags(meta)
        if done_tg or skipped_row:
            already_losses += 1

    to_post, to_skip = combo_results.select_telegram_results_for_channel(
        new_items,
        max_losses=max_losses,
        losses_already_posted=already_losses,
    )

    stats = _empty_result_post_stats(
        day=day,
        pending=len(pending),
        max_losses=max_losses,
        already_losses=already_losses,
        checked=len(slips),
    )
    skipped = 0
    for item in to_skip:
        day_state[item["_fp"]] = {
            "at": now,
            "status": (item.get("status") or "").strip().lower(),
            "title": (item.get("title") or "").strip(),
            "skipped": True,
            "telegram": True,
            "twitter": True,
        }
        skipped += 1

    is_yesterday = day < today_key()
    prefix = f"Yesterday ({day})" if is_yesterday else ""

    def _persist() -> bool:
        state[day] = day_state
        try:
            _save_digest_results_state(state)
            return True
        except OSError as exc:
            log.error(
                "save digest telegram result state failed: %s — stopping so Telegram is not re-sent",
                exc,
            )
            stats.setdefault("errors", []).append(f"state save failed: {exc}")
            return False

    def _save_item_state(item: dict, posted: dict, *, had_tg: bool, had_x: bool) -> None:
        tg_ok = posted.get("telegram_ok")
        x_ok = posted.get("twitter_ok")
        day_state[item["_fp"]] = {
            "at": now,
            "status": (item.get("status") or "").strip().lower(),
            "title": (item.get("title") or "").strip(),
            "telegram": bool(had_tg or tg_ok),
            "twitter": bool(had_x or x_ok),
        }

    if to_skip and not _persist():
        stats["skipped"] = skipped
        stats["errors"] = list(stats.get("errors") or [])[:5]
        return stats

    for item in to_post:
        text = combo_results.format_telegram_result_text(item)
        if prefix:
            text = f"{prefix}\n{text}"
        image = None
        try:
            image = build_telegram_result_image(item)
        except Exception as exc:
            log.warning("build digest result image failed: %s", exc)
        cap = (image or {}).get("caption") or text
        if prefix and image and image.get("caption"):
            cap = f"{prefix}\n{image['caption']}"
        posted = _post_result_to_channels(
            item,
            text=text,
            caption=cap,
            png_bytes=(image or {}).get("png_bytes"),
            do_telegram=True,
            do_twitter=not item.get("_had_x"),
            twitter_prefix=prefix,
        )
        _save_item_state(
            item, posted,
            had_tg=bool(item.get("_had_tg")),
            had_x=bool(item.get("_had_x")),
        )
        _tally_channel_result(stats, posted)
        if not _persist():
            stats["skipped"] = skipped
            stats["errors"] = list(stats.get("errors") or [])[:5]
            return stats
        time.sleep(0.8)

    already_x_losses = 0
    for meta in day_state.values():
        if not isinstance(meta, dict):
            continue
        if (meta.get("status") or "").strip().lower() != combo_results.LOST:
            continue
        _done_tg, done_x, skipped_row = _digest_channel_flags(meta)
        if done_x or skipped_row:
            already_x_losses += 1
    x_post, x_skip = combo_results.select_telegram_results_for_channel(
        x_only,
        max_losses=max_losses,
        losses_already_posted=already_x_losses,
    )
    for item in x_skip:
        meta = dict(day_state.get(item["_fp"]) or {})
        meta.update({"at": now, "twitter": True, "skipped": True})
        day_state[item["_fp"]] = meta
        skipped += 1
    if x_skip and not _persist():
        stats["skipped"] = skipped
        stats["errors"] = list(stats.get("errors") or [])[:5]
        return stats
    for item in x_post:
        image = None
        try:
            image = build_telegram_result_image(item)
        except Exception as exc:
            log.warning("build digest twitter result image failed: %s", exc)
        posted = _post_result_to_channels(
            item,
            text=combo_results.format_telegram_result_text(item),
            caption=(image or {}).get("caption") or "",
            png_bytes=(image or {}).get("png_bytes"),
            do_telegram=False,
            do_twitter=True,
            twitter_prefix=prefix,
        )
        _save_item_state(item, posted, had_tg=True, had_x=False)
        _tally_channel_result(stats, posted)
        if not _persist():
            break
        time.sleep(0.8)

    stats["skipped"] = skipped
    if not _persist():
        stats["errors"] = list(stats.get("errors") or [])[:5]
        return stats

    stats["errors"] = list(stats.get("errors") or [])[:5]
    return stats


def post_recent_digest_telegram_results(
    *,
    max_losses: int | None = None,
    days: int | None = None,
) -> dict:
    """Post digest RESULT cards for the last few IST days as games settle.

    Lookback (default 2) so yesterday's settled slips still post once
    without re-sending older board results.
    """
    posted = failed = skipped = pending = 0
    twitter_posted = twitter_failed = twitter_skipped = 0
    days_out: list[dict] = []
    errors: list[str] = []
    for day in recent_digest_days(days):
        try:
            part = post_digest_day_telegram_results(day, max_losses=max_losses)
        except Exception as exc:
            log.warning("digest telegram results %s failed: %s", day, exc)
            part = _empty_result_post_stats(
                day=day, failed=1, errors=[str(exc)[:240]],
            )
        days_out.append(part)
        posted += int(part.get("posted") or 0)
        failed += int(part.get("failed") or 0)
        skipped += int(part.get("skipped") or 0)
        pending += int(part.get("pending") or 0)
        twitter_posted += int(part.get("twitter_posted") or 0)
        twitter_failed += int(part.get("twitter_failed") or 0)
        twitter_skipped += int(part.get("twitter_skipped") or 0)
        errors.extend(part.get("errors") or [])
    return {
        "posted": posted,
        "failed": failed,
        "skipped": skipped,
        "pending": pending,
        "twitter_posted": twitter_posted,
        "twitter_failed": twitter_failed,
        "twitter_skipped": twitter_skipped,
        "errors": errors[:5],
        "days": days_out,
    }


def _oauth1_header(method: str, url: str, params: dict, *, key: str, secret: str,
                   token: str, token_secret: str) -> str:
    """Minimal OAuth 1.0a signing for Twitter API v1.1/v2."""
    nonce = base64.b64encode(os.urandom(24)).decode("ascii").translate(
        str.maketrans("", "", "+/=")
    )[:32]
    ts = str(int(time.time()))
    oauth = {
        "oauth_consumer_key": key,
        "oauth_nonce": nonce,
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": ts,
        "oauth_token": token,
        "oauth_version": "1.0",
    }
    all_params = {**params, **oauth}
    encoded = "&".join(
        f"{urllib.parse.quote(str(k), safe='~')}={urllib.parse.quote(str(v), safe='~')}"
        for k, v in sorted(all_params.items())
    )
    base = "&".join([
        method.upper(),
        urllib.parse.quote(url, safe="~"),
        urllib.parse.quote(encoded, safe="~"),
    ])
    signing_key = (
        f"{urllib.parse.quote(secret, safe='~')}&"
        f"{urllib.parse.quote(token_secret, safe='~')}"
    )
    sig = base64.b64encode(
        hmac.new(signing_key.encode(), base.encode(), hashlib.sha1).digest()
    ).decode()
    oauth["oauth_signature"] = sig
    return "OAuth " + ", ".join(
        f'{urllib.parse.quote(k, safe="~")}="{urllib.parse.quote(str(v), safe="~")}"'
        for k, v in sorted(oauth.items())
    )


def _twitter_credentials() -> dict | None:
    api_key = (os.environ.get("TWITTER_API_KEY") or os.environ.get("X_API_KEY") or "").strip()
    api_secret = (
        os.environ.get("TWITTER_API_SECRET")
        or os.environ.get("TWITTER_API_KEY_SECRET")
        or os.environ.get("X_API_SECRET")
        or ""
    ).strip()
    access_token = (
        os.environ.get("TWITTER_ACCESS_TOKEN") or os.environ.get("X_ACCESS_TOKEN") or ""
    ).strip()
    access_secret = (
        os.environ.get("TWITTER_ACCESS_TOKEN_SECRET")
        or os.environ.get("X_ACCESS_TOKEN_SECRET")
        or ""
    ).strip()
    if not all([api_key, api_secret, access_token, access_secret]):
        return None
    return {
        "key": api_key,
        "secret": api_secret,
        "token": access_token,
        "token_secret": access_secret,
    }


def _upload_twitter_media(png_bytes: bytes, creds: dict) -> tuple[str | None, str]:
    """Upload a PNG via Twitter v1.1 media endpoint. Returns (media_id, error)."""
    import requests

    url = "https://upload.twitter.com/1.1/media/upload.json"
    try:
        auth = _oauth1_header(
            "POST", url, {},
            key=creds["key"], secret=creds["secret"],
            token=creds["token"], token_secret=creds["token_secret"],
        )
        resp = requests.post(
            url,
            headers={"Authorization": auth},
            files={"media": ("result.png", png_bytes, "image/png")},
            timeout=45,
        )
    except Exception as exc:
        return None, f"X media upload failed: {exc}"
    if resp.status_code >= 400:
        return None, f"X media upload error {resp.status_code}: {resp.text[:300]}"
    try:
        media_id = str((resp.json() or {}).get("media_id_string") or "").strip()
    except ValueError:
        media_id = ""
    if not media_id:
        return None, f"X media upload missing media_id: {resp.text[:200]}"
    return media_id, ""


def _post_twitter_tweet(
    text: str,
    creds: dict,
    *,
    media_id: str | None = None,
) -> tuple[bool, str]:
    import requests

    url = "https://api.twitter.com/2/tweets"
    payload: dict[str, Any] = {"text": (text or "")[:TWITTER_LIMIT]}
    if media_id:
        payload["media"] = {"media_ids": [str(media_id)]}
    body = json.dumps(payload)
    try:
        auth = _oauth1_header(
            "POST", url, {},
            key=creds["key"], secret=creds["secret"],
            token=creds["token"], token_secret=creds["token_secret"],
        )
        resp = requests.post(
            url,
            data=body,
            headers={
                "Authorization": auth,
                "Content-Type": "application/json",
            },
            timeout=30,
        )
        if resp.status_code >= 400:
            return False, f"X/Twitter error {resp.status_code}: {resp.text[:300]}"
        return True, "posted X/Twitter"
    except Exception as exc:
        return False, f"X/Twitter failed: {exc}"


def post_twitter_result(
    item: dict,
    *,
    png_bytes: bytes | None = None,
    prefix: str = "",
) -> tuple[bool, str]:
    """Post one RESULT slip to X (image when provided)."""
    creds = _twitter_credentials()
    if not creds:
        return False, (
            "skipped X/Twitter (missing TWITTER_API_KEY / SECRET / "
            "ACCESS_TOKEN / ACCESS_TOKEN_SECRET)"
        )
    text = _format_twitter_result_text(item, prefix=prefix)
    media_id = None
    if png_bytes:
        media_id, err = _upload_twitter_media(png_bytes, creds)
        if err:
            return False, err
    return _post_twitter_tweet(text, creds, media_id=media_id)


def post_twitter(digest: dict) -> tuple[bool, str]:
    creds = _twitter_credentials()
    if not creds:
        return False, "skipped X/Twitter (missing TWITTER_API_KEY / SECRET / ACCESS_TOKEN / ACCESS_TOKEN_SECRET)"

    text = (digest.get("share_text") or "")[:280]
    # Prefer a compact tweet if share_text is long.
    if len(digest.get("share_text") or "") > 280:
        picks = digest.get("picks") or []
        lines = [
            f"{i}. {_sport_league_label(p)}: {p.get('pick')} ({p.get('confidence_pct')}%)"
            for i, p in enumerate(picks, 1)
        ]
        text = (
            f"Today's 3 best picks — Degen Predictions\n"
            + "\n".join(lines)
            + f"\n{digest.get('digest_url')}\nNot betting advice."
        )[:280]
    return _post_twitter_tweet(text, creds)


def post_email(digest: dict) -> tuple[bool, str]:
    host = (os.environ.get("SMTP_HOST") or "").strip()
    to_addr = (os.environ.get("DIGEST_EMAIL_TO") or os.environ.get("SMTP_TO") or "").strip()
    user = (os.environ.get("SMTP_USER") or "").strip()
    password = (os.environ.get("SMTP_PASSWORD") or "").strip()
    from_addr = (os.environ.get("SMTP_FROM") or user or "").strip()
    if not host or not to_addr or not from_addr:
        return False, (
            "skipped Email (set SMTP_HOST + DIGEST_EMAIL_TO + SMTP_FROM; "
            "TODO: wire ESP if you outgrow raw SMTP)"
        )

    port = int(os.environ.get("SMTP_PORT") or "587")
    subject = digest.get("headline") or "Today's 3 best picks"
    body = digest.get("share_text") or subject
    msg = MIMEMultipart()
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    try:
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            smtp.ehlo()
            if os.environ.get("SMTP_STARTTLS", "1") != "0":
                smtp.starttls()
                smtp.ehlo()
            if user and password:
                smtp.login(user, password)
            smtp.sendmail(from_addr, [a.strip() for a in to_addr.split(",") if a.strip()], msg.as_string())
        return True, f"posted Email → {to_addr}"
    except Exception as exc:
        return False, f"Email failed: {exc}"


# ---------------------------------------------------------------------------
# Gather legs (disk cache → Flask rebuild)
# ---------------------------------------------------------------------------

def gather_legs(*, force_rebuild: bool = False) -> list[dict]:
    """Load today's combo legs; prefer disk cache unless force_rebuild."""
    legs, _combos = gather_board(force_rebuild=force_rebuild)
    return legs


def gather_board(*, force_rebuild: bool = False) -> tuple[list[dict], dict]:
    """Load today's legs + combos (same builders as the public site)."""
    sys.path.insert(0, str(WEBAPP))
    os.chdir(WEBAPP)

    if not force_rebuild:
        import pickle
        for name in ("combo_full.pkl", "combo_fast.pkl"):
            path = WEBAPP / name
            if not path.is_file():
                continue
            try:
                age = time.time() - path.stat().st_mtime
                # Morning job can use up to 3h-old cache; still better than empty.
                if age > 3 * 3600:
                    continue
                with open(path, "rb") as f:
                    bundle = pickle.load(f)
                if bundle and bundle[0]:
                    combos = bundle[1] if len(bundle) > 1 and isinstance(bundle[1], dict) else {}
                    log.info("loaded %s legs from %s (age %.0fs)", len(bundle[0]), name, age)
                    return list(bundle[0]), combos
            except Exception as exc:
                log.warning("ignore combo cache %s: %s", name, exc)

    log.info("rebuilding combo legs via Flask app (may take ~15s)…")
    import app as flask_app  # noqa: WPS433 — intentional runtime import

    with flask_app.app.app_context():
        legs, combos = flask_app._gather_combo_picks_today(
            include_live=True, allow_blocking_rebuild=True,
        )
    return list(legs or []), combos or {}


# ---------------------------------------------------------------------------
# Main job
# ---------------------------------------------------------------------------

def maybe_catch_up_channel_posts() -> dict:
    """Post today's slips if the 09:00 IST job sent nothing (empty slate).

    Disabled unless CHANNEL_AUTO_POST=1 — admin verifies and sends.
    """
    if not channel_auto_post_enabled():
        return {"skipped": True, "reason": "admin-only channel posts", "date": today_key()}
    day = today_key()
    state = load_state()
    day_state = dict(state.get(day) or {})
    if int(day_state.get("telegram_slip_count") or 0) > 0:
        return {"skipped": True, "reason": "already posted slips", "date": day}
    if day_state.get("posted_telegram"):
        return {"skipped": True, "reason": "already posted slips", "date": day}

    log.info("catch-up rebuilding live board before Telegram")
    return run_daily_job(force_rebuild=True, force_repost=False)


def run_daily_job(
    *,
    force_rebuild: bool = True,
    force_repost: bool = False,
    dry_run: bool = False,
    skip_posts: bool = False,
) -> dict[str, Any]:
    _ensure_dirs()
    day = today_key()
    state = load_state()
    day_state = dict(state.get(day) or {})

    legs, combos = gather_board(force_rebuild=force_rebuild)
    picks = select_top3(legs, n=3)
    digest = build_digest_payload(picks, day=day, legs=legs, combos=combos)
    digest = filter_digest_upcoming(digest)
    digest = verify_digest_fixtures(digest)
    picks = list(digest.get("picks") or [])
    digest_path = write_digest(digest)
    log.info(
        "wrote digest %s (%d picks, %d featured combos: 2=%d 3=%d mixed2=%d mixed3=%d)",
        digest_path,
        len(picks),
        digest.get("featured_combo_count") or 0,
        len(digest.get("combos_2") or []),
        len(digest.get("combos_3") or []),
        len(digest.get("mixed_2") or []),
        len(digest.get("mixed_3") or []),
    )

    drafts_path = write_channel_drafts(digest)
    log.info("wrote drafts %s", drafts_path)

    slip_images: list[dict] = []
    try:
        slip_images = build_telegram_slip_images(digest)
        log.info(
            "wrote %d slip PNGs (%d picks + %d combos)",
            len(slip_images),
            sum(1 for s in slip_images if s.get("kind") == "picks"),
            sum(1 for s in slip_images if s.get("kind") == "combo"),
        )
    except Exception as exc:
        log.warning("slip PNG render failed: %s", exc)

    image_path = None
    try:
        image_path = render_digest_card_png(digest)
        if image_path:
            log.info("wrote share card %s", image_path)
    except Exception as exc:
        log.warning("PNG render failed: %s", exc)

    results = {
        "date": day,
        "pick_count": len(picks),
        "featured_combo_count": digest.get("featured_combo_count") or 0,
        "combos_2": len(digest.get("combos_2") or []),
        "combos_3": len(digest.get("combos_3") or []),
        "mixed_2": len(digest.get("mixed_2") or []),
        "mixed_3": len(digest.get("mixed_3") or []),
        "digest_path": str(digest_path),
        "drafts_path": str(drafts_path),
        "image_path": str(image_path) if image_path else None,
        "slip_image_count": len(slip_images),
        "slip_captions": [s.get("caption") for s in slip_images],
        "fixture_verify": digest.get("fixture_verify") or {},
        "channels": {},
        "dry_run": dry_run,
    }

    auto_post = channel_auto_post_enabled()
    if dry_run or skip_posts or not auto_post:
        if dry_run or skip_posts:
            note = "skipped posts (dry-run or --skip-posts)"
        else:
            note = "admin-only: Telegram/X not auto-posted — send from /admin"
        log.info(note)
        results["channels"]["note"] = note
        results["channels"]["telegram"] = note
        day_state["digest_written"] = True
        day_state["drafts_written"] = True
        try:
            import indexnow
            results["indexnow"] = indexnow.notify_slate(digest)
            log.info("indexnow: %s", results["indexnow"])
        except Exception as exc:
            log.warning("indexnow failed: %s", exc)
            results["indexnow"] = {"ok": False, "error": str(exc)}
        state[day] = day_state
        save_state(state)
        return results

    posters = [
        (
            "telegram",
            lambda: post_telegram(digest, image_path, slip_images=slip_images),
            "posted_telegram",
        ),
        ("twitter", lambda: post_twitter(digest), "posted_twitter"),
        ("email", lambda: post_email(digest), "posted_email"),
    ]
    has_channel_content = bool(picks) or bool(digest.get("featured_combo_count"))
    telegram_slip_count = int(day_state.get("telegram_slip_count") or 0)
    for name, fn, flag in posters:
        already = bool(day_state.get(flag))
        # An empty 09:00 IST post must not lock the day — later catch-up can send
        # the real cards once the board fills.
        if name == "telegram" and telegram_slip_count <= 0:
            already = False
        if already and not force_repost:
            msg = f"skipped {name} (already posted {day}; use --force-repost)"
            log.info(msg)
            results["channels"][name] = msg
            continue
        # Empty 09:00 IST slate: do not post a blank slip / leftover PNG,
        # and do not mark posted so a later run can send the real cards.
        if not has_channel_content:
            msg = f"skipped {name} (empty slate; not marking posted)"
            log.info(msg)
            results["channels"][name] = msg
            continue
        ok, msg = fn()
        log.info("%s", msg)
        results["channels"][name] = msg
        if ok:
            day_state[flag] = True
            if name == "telegram":
                day_state["telegram_slip_count"] = len(slip_images)
                telegram_slip_count = len(slip_images)
                if slip_images:
                    try:
                        save_last_telegram_send(slip_images, day=day)
                    except Exception as exc:
                        log.warning("save last telegram send failed: %s", exc)
                    try:
                        import combo_results
                        combo_results.record_degen_team_from_telegram_slips(
                            slip_images, slate_date=day,
                        )
                    except Exception as exc:
                        log.warning("record telegram channel slips failed: %s", exc)
        # "skipped …" is not a failure — leave flag false so creds later still post.

    day_state["digest_written"] = True
    day_state["drafts_written"] = True
    day_state["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        import indexnow
        results["indexnow"] = indexnow.notify_slate(digest)
        log.info("indexnow: %s", results["indexnow"])
        if results["indexnow"].get("ok"):
            day_state["posted_indexnow"] = True
    except Exception as exc:
        log.warning("indexnow failed: %s", exc)
        results["indexnow"] = {"ok": False, "error": str(exc)}
    state[day] = day_state
    # Keep state file small.
    if len(state) > 60:
        for old in sorted(k for k in state if k != day)[:-45]:
            state.pop(old, None)
    save_state(state)
    return results


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    parser = argparse.ArgumentParser(description="Daily Top-3 digest + optional auto-post")
    parser.add_argument("--dry-run", action="store_true", help="Write digest/drafts only")
    parser.add_argument("--skip-posts", action="store_true", help="Same as dry-run for channels")
    parser.add_argument("--force-rebuild", action="store_true", default=True,
                        help="Rebuild combo legs via Flask (default)")
    parser.add_argument("--use-cache", action="store_true",
                        help="Prefer on-disk combo cache instead of rebuild")
    parser.add_argument("--force-repost", action="store_true",
                        help="Ignore same-day posted flags")
    parser.add_argument("--print-share-text", action="store_true",
                        help="Print share text to stdout after run")
    args = parser.parse_args(argv)

    # Load production.env if present (EC2 oneshot units also EnvironmentFile= it).
    env_path = REPO / "deploy" / "production.env"
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v

    try:
        result = run_daily_job(
            force_rebuild=not args.use_cache,
            force_repost=args.force_repost,
            dry_run=args.dry_run,
            skip_posts=args.skip_posts,
        )
    except Exception:
        traceback.print_exc()
        return 1

    if not args.dry_run and not args.skip_posts and channel_auto_post_enabled():
        try:
            result["digest_results"] = post_recent_digest_telegram_results()
            log.info("morning digest results %s", result["digest_results"])
        except Exception as exc:
            log.warning("morning digest results failed: %s", exc)
            result["digest_results"] = {"ok": False, "error": str(exc)[:240]}

    if args.print_share_text or args.dry_run:
        digest = load_latest_digest() or {}
        print("\n----- SHARE TEXT -----\n")
        print(digest.get("share_text") or "(empty)")
        print("\n----------------------\n")

    print(json.dumps({
        "date": result.get("date"),
        "pick_count": result.get("pick_count"),
        "featured_combo_count": result.get("featured_combo_count"),
        "combos_2": result.get("combos_2"),
        "combos_3": result.get("combos_3"),
        "mixed_2": result.get("mixed_2"),
        "mixed_3": result.get("mixed_3"),
        "channels": result.get("channels"),
        "indexnow": result.get("indexnow"),
        "digest_results": result.get("digest_results"),
        "digest_url": f"{SITE_URL}{DIGEST_PATH}",
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
