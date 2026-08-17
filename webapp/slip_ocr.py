"""Parse a bookmaker bet-slip screenshot into structured single- or multi-leg fields.

Supports:
  - Single-leg (Bet365-style Bet details, corners Team N Over/Under, etc.)
  - N-leg multi / parlay (Stake "2 Leg Multi", "3 Leg", Acca, …)

OCR priority (first that works wins):
  1. Pillow + pytesseract (if both installed and tesseract binary available)
  2. Anthropic / OpenAI vision HTTP if ANTHROPIC_API_KEY / CLAUDE_API_KEY /
     OPENAI_API_KEY is set in the environment (no new paid SDK deps)
  3. Caller-supplied OCR text (paste fallback)

Privacy: image bytes are processed in memory only (BytesIO). This module never
writes slip screenshots to disk, cache, or a database.

Never logs or returns API keys. Optional deps fail soft.
"""
from __future__ import annotations

import base64
import io
import logging
import os
import re
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_ENV_LOADED = False


def _ensure_production_env() -> None:
    """Load deploy/production.env into os.environ if keys are missing.

    Gunicorn usually gets EnvironmentFile= already; this is a safe fallback
    that never overwrites existing env values and never logs secrets.
    """
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    _ENV_LOADED = True
    try:
        repo = Path(__file__).resolve().parents[1]
        env_path = repo / "deploy" / "production.env"
        if not env_path.is_file():
            return
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v
    except Exception:
        log.debug("slip_ocr: could not load production.env", exc_info=True)


SPORT_ALIASES = {
    "football": ("soccer", "Soccer"),
    "soccer": ("soccer", "Soccer"),
    "futbol": ("soccer", "Soccer"),
    "fútbol": ("soccer", "Soccer"),
    "baseball": ("baseball", "Baseball"),
    "mlb": ("baseball", "Baseball"),
    "cricket": ("cricket", "Cricket"),
    "tennis": ("tennis", "Tennis"),
    "basketball": ("basketball", "Basketball"),
    "nba": ("basketball", "Basketball"),
    "hockey": ("hockey", "Hockey"),
    "nhl": ("hockey", "Hockey"),
    "american football": ("american_football", "American Football"),
    "nfl": ("american_football", "American Football"),
}

SPORT_LINE_RE = re.compile(
    r"^(football|soccer|futbol|fútbol|baseball|cricket|tennis|"
    r"basketball|hockey|american\s+football|mlb|nba|nhl|nfl)\b",
    re.I,
)

MATCHUP_RE = re.compile(
    r"^(.+?)\s+(?:v(?:s\.?)?|@|\u2013|\u2014|-)\s+(.+)$",
    re.I,
)

# Stake / bookie slip type headers
SLIP_TYPE_RE = re.compile(
    r"(?:^|\b)(?:(\d+)\s*[- ]?\s*leg(?:s)?\s*(?:multi(?:ple)?|parlay|acca)?|"
    r"(\d+)\s*[- ]?\s*(?:fold|teamers?)|"
    r"(parlay|acca|accumulator|multiple|multi)|"
    r"(single(?:\s*leg)?))\b",
    re.I,
)

NOISE_LINE_RE = re.compile(
    r"^(bet\s*details|bet\s*slip|my\s*bets|"
    r"returns?|odds?|potential\s+returns?|to\s+return|"
    r"cash\s*out|place\s*bet|confirm|edit|back|close|"
    r"selection|selections|live|in-play|@\s*\d|"
    r"total\s*odds|combined|payout|profit|"
    r"\d+\.\d{2}\s*$|£|\$|€|₹|"
    r"leg\s*\d+\s*$)",
    re.I,
)

# "Stake $10" / amount lines — keep brand-only "Stake" out of legs
STAKE_AMOUNT_RE = re.compile(r"^stake\b.*\d", re.I)

ODDS_ONLY_RE = re.compile(r"^[@]?\s*\d+[.,]\d{2}\s*$")
TOTAL_TEAM_LINE_RE = re.compile(
    r"^total\s*([12])\s*[\(\[]?\s*(\d+(?:\.\d+)?)\s*[\)\]]?\s*$",
    re.I,
)
TEAM_N_RE = re.compile(r"^team\s*([12])\b", re.I)
OVER_UNDER_RE = re.compile(r"\b(over|under)\b", re.I)
LINE_NUM_RE = re.compile(r"(\d+(?:\.\d+)?)")
DATE_TIME_RE = re.compile(r"\d{1,2}:\d{2}|\d{1,2}/\d{1,2}")

MARKET_HINTS = (
    "corners", "cards", "goals", "shots", "fouls", "offsides",
    "throw", "booking", "btts", "both teams", "asian", "handicap",
    "moneyline", "match odds", "1x2", "draw no bet", "double chance",
    "total", "spread", "runs", "wickets", "aces", "games", "sets",
    "hits", "strikeouts", "player", "team total", "result",
    "winner", "correct score", "clean sheet",
)

BTTS_RE = re.compile(r"both\s*teams?\s*to\s*score|btts", re.I)
YES_NO_RE = re.compile(r"^(yes|no)$", re.I)
ONE_X_TWO_RE = re.compile(r"^(1\s*[x×]\s*2|1x2|match\s*result|full\s*time\s*result|match\s*odds)$", re.I)


def _normalize_ws(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u00a0", " ")
    return text


def _clean_lines(text: str) -> list[str]:
    lines = []
    for raw in _normalize_ws(text).split("\n"):
        line = re.sub(r"\s+", " ", raw).strip(" ·•|-")
        if not line:
            continue
        if ODDS_ONLY_RE.match(line):
            continue
        if STAKE_AMOUNT_RE.match(line):
            continue
        # Keep slip-type headers and matchups even if they look noisy
        if SLIP_TYPE_RE.search(line) and not MATCHUP_RE.match(line):
            lines.append(line)
            continue
        if NOISE_LINE_RE.match(line) and not MATCHUP_RE.match(line):
            low = line.lower()
            if not any(h in low for h in MARKET_HINTS):
                continue
        lines.append(line)
    return lines


def _map_sport(token: str) -> tuple[str, str]:
    key = re.sub(r"\s+", " ", (token or "").strip().lower())
    if key in SPORT_ALIASES:
        return SPORT_ALIASES[key]
    for alias, mapped in SPORT_ALIASES.items():
        if key.startswith(alias):
            return mapped
    label = (token or "").strip().title() or "Soccer"
    slug = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_") or "custom"
    return slug, label


def _looks_like_league(line: str) -> bool:
    low = line.lower()
    if MATCHUP_RE.match(line):
        return False
    if SPORT_LINE_RE.match(line):
        return False
    if YES_NO_RE.match(line):
        return False
    if OVER_UNDER_RE.search(line) and TOTAL_TEAM_LINE_RE.search(line):
        return False
    league_tokens = (
        "league", "liga", "serie", "cup", "uefa", "champions", "europa",
        "conference", "premier", "bundesliga", "ligue", "eredivisie",
        "mls", "mlb", "nba", "nhl", "nfl", "atp", "wta", "ipl", "bbl",
        "world cup", "nations", "friendl", "qualif", "division",
    )
    return any(t in low for t in league_tokens)


def _looks_like_market(line: str) -> bool:
    low = line.lower().strip()
    if MATCHUP_RE.match(line):
        return False
    if YES_NO_RE.match(line):
        return False
    if TOTAL_TEAM_LINE_RE.match(line):
        return True
    if ONE_X_TWO_RE.match(line):
        return True
    if BTTS_RE.search(line):
        return True
    if OVER_UNDER_RE.search(low) and LINE_NUM_RE.search(low):
        return True
    return any(h in low for h in MARKET_HINTS)


def _detect_slip_type(lines: list[str], matchup_count: int) -> tuple[str, int]:
    """Return (slip_mode, slip_n). Prefer explicit header; else matchup count."""
    for line in lines[:12]:
        m = SLIP_TYPE_RE.search(line)
        if not m:
            continue
        if m.group(4):  # single
            return "single", 1
        if m.group(1):
            n = max(2, min(12, int(m.group(1))))
            return "multi", n
        if m.group(2):
            n = max(2, min(12, int(m.group(2))))
            return "multi", n
        if m.group(3):  # parlay/acca/multiple without number
            n = matchup_count if matchup_count >= 2 else 2
            return "multi", max(2, min(12, n))
    if matchup_count >= 2:
        return "multi", min(12, matchup_count)
    return "single", 1


def _format_corners_team_total(
    team_n: str, line: str, side: str, matchup: str
) -> tuple[str, str]:
    market = "Corners"
    pick = f"Team {team_n} {side.title()} {line} corners"
    m = MATCHUP_RE.match(matchup or "")
    if m:
        home, away = m.group(1).strip(), m.group(2).strip()
        team_name = home if team_n == "1" else away
        if team_name:
            pick = f"{team_name} {side.title()} {line} corners"
    return market, pick


def _parse_market_block(lines: list[str], start: int, matchup: str) -> tuple[str, str, int]:
    """Parse market / selection starting at `start`. Returns market, pick, next_idx."""
    chunk = lines[start: start + 6]
    if not chunk:
        return "", "", start
    joined = " | ".join(chunk)

    total_idx = None
    for i, line in enumerate(chunk):
        if TOTAL_TEAM_LINE_RE.match(line):
            total_idx = i
            break
    if total_idx is not None:
        tm = TOTAL_TEAM_LINE_RE.match(chunk[total_idx])
        team_n, line_val = tm.group(1), tm.group(2)
        side = "Over"
        for line in chunk[total_idx:]:
            ou = OVER_UNDER_RE.search(line)
            if ou:
                side = ou.group(1).title()
                break
        head = chunk[0] if total_idx > 0 else "Corners"
        if "corner" in head.lower() or total_idx == 0:
            market, pick = _format_corners_team_total(team_n, line_val, side, matchup)
            return market, pick, start + max(total_idx + 1, 2)
        return head, f"Team {team_n} {side} {line_val}", start + max(total_idx + 1, 2)

    m = re.search(
        r"(corners?).*(?:team\s*([12])|total\s*([12])).*\b(over|under)\b.*?(\d+(?:\.\d+)?)",
        joined, re.I,
    )
    if m:
        team_n = m.group(2) or m.group(3) or "2"
        side = m.group(4).title()
        line_val = m.group(5)
        market, pick = _format_corners_team_total(team_n, line_val, side, matchup)
        return market, pick, start + 1

    # BTTS + Yes/No
    for i, line in enumerate(chunk):
        if BTTS_RE.search(line):
            market = "Both Teams to Score"
            pick = "Yes"
            for j in range(i + 1, min(i + 3, len(chunk))):
                if YES_NO_RE.match(chunk[j]):
                    pick = chunk[j].title()
                    return market, pick, start + j + 1
            return market, pick, start + i + 1

    # 1x2 / match result + team name selection
    for i, line in enumerate(chunk):
        if ONE_X_TWO_RE.match(line):
            market = "1x2"
            pick = ""
            for j in range(i + 1, min(i + 3, len(chunk))):
                if MATCHUP_RE.match(chunk[j]) or SPORT_LINE_RE.match(chunk[j]):
                    break
                if DATE_TIME_RE.search(chunk[j]):
                    continue
                pick = chunk[j]
                return market, pick, start + j + 1
            return market, pick or "1x2", start + i + 1

    market = chunk[0]
    pick = ""
    consumed = 1
    if len(chunk) >= 2:
        if (
            OVER_UNDER_RE.search(chunk[1])
            or TEAM_N_RE.match(chunk[1])
            or YES_NO_RE.match(chunk[1])
        ):
            pick = chunk[1]
            consumed = 2
            if len(chunk) >= 3 and LINE_NUM_RE.search(chunk[2]) and not MATCHUP_RE.match(chunk[2]):
                if chunk[2].lower() not in pick.lower():
                    pick = f"{pick} {chunk[2]}".strip()
                    consumed = 3
        elif not MATCHUP_RE.match(chunk[1]) and not SPORT_LINE_RE.match(chunk[1]):
            pick = chunk[1]
            consumed = 2
    if not pick:
        parts = re.split(r"\s+[·|]\s+|\s+-\s+", market, maxsplit=1)
        if len(parts) == 2:
            market, pick = parts[0].strip(), parts[1].strip()
        else:
            pick = market
            market = "Market"
    return market.strip(), pick.strip(), start + consumed


def _empty_leg() -> dict[str, Any]:
    return {
        "sport_key": "soccer",
        "sport": "Soccer",
        "league": "",
        "event": "",
        "home": "",
        "away": "",
        "market": "",
        "pick": "",
        "selection": "",
    }


def _leg_from_matchup_window(
    lines: list[str],
    start: int,
    match_idx: int,
    end: int,
) -> dict[str, Any]:
    """Build one leg from lines around a matchup.

    Stake-style (common): Sport → Market → Selection → Event
    Bet365-style: Sport → League → Event → Market → Selection
    """
    leg = _empty_leg()
    m = MATCHUP_RE.match(lines[match_idx])
    if not m:
        return leg
    home, away = m.group(1).strip(), m.group(2).strip()
    leg["home"] = home
    leg["away"] = away
    leg["event"] = f"{home} vs {away}"

    before = lines[start:match_idx]
    after = lines[match_idx + 1:end]

    # Sport / league from before
    for line in before:
        sm = SPORT_LINE_RE.match(line)
        if sm:
            leg["sport_key"], leg["sport"] = _map_sport(sm.group(1))
            rest = line[sm.end():].strip(" ·|-")
            if rest and _looks_like_league(rest):
                leg["league"] = rest
            continue
        if _looks_like_league(line) and not _looks_like_market(line):
            leg["league"] = line

    # Prefer market/pick from BEFORE matchup (Stake: selection then market then event)
    pre_markets = [i for i, line in enumerate(before) if _looks_like_market(line)]
    if pre_markets:
        bi = pre_markets[-1]  # market closest to the event
        market, pick, _ = _parse_market_block(before, bi, leg["event"])

        # Stake order is often: Selection → Market → odds → Event
        # e.g. "Olympique Lyonnais" then "1x2", or "Yes" then "Both Teams to Score"
        before_market = before[:bi]
        if before_market:
            prev = before_market[-1]
            if YES_NO_RE.match(prev):
                pick = prev.title()
            elif (
                not ODDS_ONLY_RE.match(prev)
                and not SPORT_LINE_RE.match(prev)
                and not _looks_like_league(prev)
                and not _looks_like_market(prev)
                and not DATE_TIME_RE.search(prev)
            ):
                # Team / selection name sitting above the market label
                if ONE_X_TWO_RE.match(market) or not pick or pick.lower() == (market or "").lower():
                    pick = prev

        if not pick:
            for line in reversed(before_market):
                if (
                    SPORT_LINE_RE.match(line)
                    or _looks_like_league(line)
                    or _looks_like_market(line)
                    or ODDS_ONLY_RE.match(line)
                    or DATE_TIME_RE.search(line)
                ):
                    continue
                pick = line
                break

        leg["market"] = market
        leg["pick"] = pick
        leg["selection"] = pick
    elif after:
        # Bet365-style: markets after event
        ai = 0
        while ai < len(after) and DATE_TIME_RE.search(after[ai]) and not _looks_like_market(after[ai]):
            ai += 1
        if ai < len(after):
            market, pick, _ = _parse_market_block(after, ai, leg["event"])
            leg["market"] = market
            leg["pick"] = pick
            leg["selection"] = pick
    else:
        # Selection-only lines before event (e.g. Yes + BTTS split badly)
        for i, line in enumerate(before):
            if YES_NO_RE.match(line) or OVER_UNDER_RE.search(line):
                # look backward for market
                market = "Market"
                for j in range(i - 1, -1, -1):
                    if _looks_like_market(before[j]) or BTTS_RE.search(before[j]):
                        market = before[j]
                        break
                if BTTS_RE.search(market) or (i and BTTS_RE.search(before[max(0, i - 1)])):
                    market = "Both Teams to Score"
                leg["market"] = market
                leg["pick"] = line.title() if YES_NO_RE.match(line) else line
                leg["selection"] = leg["pick"]
                break

    # If 1x2 and pick empty, try last non-meta before line as team selection
    if ONE_X_TWO_RE.match(leg.get("market") or "") and not leg.get("pick"):
        for line in reversed(before):
            if (
                SPORT_LINE_RE.match(line)
                or _looks_like_league(line)
                or _looks_like_market(line)
                or YES_NO_RE.match(line)
            ):
                continue
            leg["pick"] = line
            leg["selection"] = line
            break

    # Normalize BTTS market label
    if BTTS_RE.search(leg.get("market") or ""):
        leg["market"] = "Both Teams to Score"
    if ONE_X_TWO_RE.match(leg.get("market") or ""):
        leg["market"] = "1x2"

    return leg


def _parse_single_fallback(lines: list[str]) -> dict[str, Any]:
    """Original single-leg path when only one (or zero) matchups found."""
    leg = _empty_leg()
    sport_idx = None
    for i, line in enumerate(lines):
        m = SPORT_LINE_RE.match(line)
        if m:
            leg["sport_key"], leg["sport"] = _map_sport(m.group(1))
            sport_idx = i
            rest = line[m.end():].strip(" ·|-")
            if rest and _looks_like_league(rest):
                leg["league"] = rest
            break

    match_idx = None
    scan_from = (sport_idx + 1) if sport_idx is not None else 0
    for i in range(scan_from, len(lines)):
        m = MATCHUP_RE.match(lines[i])
        if m:
            leg["home"], leg["away"] = m.group(1).strip(), m.group(2).strip()
            leg["event"] = f"{leg['home']} vs {leg['away']}"
            match_idx = i
            break

    if match_idx is not None:
        for i in range(scan_from, match_idx):
            if _looks_like_league(lines[i]):
                leg["league"] = lines[i]
                break
        # Stake order: market before event
        before = lines[scan_from:match_idx]
        pre_markets = [i for i, line in enumerate(before) if _looks_like_market(line)]
        if pre_markets:
            market, pick, _ = _parse_market_block(before, pre_markets[0], leg["event"])
            if not pick:
                for line in before[pre_markets[0] + 1:]:
                    if not (SPORT_LINE_RE.match(line) or _looks_like_market(line)):
                        pick = line
                        break
            leg["market"], leg["pick"], leg["selection"] = market, pick, pick
        else:
            mkt_start = match_idx + 1
            while mkt_start < len(lines) and DATE_TIME_RE.search(lines[mkt_start]) and not _looks_like_market(lines[mkt_start]):
                mkt_start += 1
            if mkt_start < len(lines):
                market, pick, _ = _parse_market_block(lines, mkt_start, leg["event"])
                leg["market"], leg["pick"], leg["selection"] = market, pick, pick
    else:
        for i, line in enumerate(lines):
            if _looks_like_market(line):
                market, pick, _ = _parse_market_block(lines, i, "")
                leg["market"], leg["pick"], leg["selection"] = market, pick, pick
                break

    if BTTS_RE.search(leg.get("market") or ""):
        leg["market"] = "Both Teams to Score"
    if ONE_X_TWO_RE.match(leg.get("market") or ""):
        leg["market"] = "1x2"
    return leg


def parse_slip_text(text: str) -> dict[str, Any]:
    """Turn OCR / pasted bet-details text into structured slip fields (+ legs)."""
    lines = _clean_lines(text or "")
    match_idxs = [i for i, line in enumerate(lines) if MATCHUP_RE.match(line)]
    slip_mode, slip_n = _detect_slip_type(lines, len(match_idxs))

    legs: list[dict[str, Any]] = []
    if len(match_idxs) >= 2 or (slip_mode == "multi" and len(match_idxs) >= 1):
        for mi, match_idx in enumerate(match_idxs):
            start = 0
            if mi == 0:
                # skip slip-type header lines
                start = 0
                for j, line in enumerate(lines[:match_idx]):
                    if SLIP_TYPE_RE.search(line) and not MATCHUP_RE.match(line):
                        start = j + 1
                # also bump past leading noise after header
                if mi > 0:
                    start = match_idxs[mi - 1] + 1
                else:
                    # previous matchup end — for first leg, use header end
                    prev = 0
                    for j, line in enumerate(lines[:match_idx]):
                        if SLIP_TYPE_RE.search(line):
                            prev = j + 1
                    start = prev
            else:
                start = match_idxs[mi - 1] + 1
            # Don't steal next leg's sport line: end at next matchup
            end = match_idxs[mi + 1] if mi + 1 < len(match_idxs) else len(lines)
            # For next-leg boundary, include lines before next matchup only
            leg = _leg_from_matchup_window(lines, start, match_idx, end)
            if leg.get("event"):
                legs.append(leg)
        # If header said N but we found fewer matchups, keep what we have
        if slip_mode == "multi" and legs:
            slip_n = max(slip_n, len(legs)) if slip_n >= 2 else len(legs)
            slip_n = min(12, max(2, len(legs)))
            slip_mode = "multi"
    else:
        leg = _parse_single_fallback(lines)
        if leg.get("event") or leg.get("pick"):
            legs = [leg]
        slip_mode, slip_n = "single", 1

    # Confidence
    conf = 0.15 if slip_mode else 0.0
    if legs:
        conf += 0.2
    complete = sum(1 for lg in legs if lg.get("event") and lg.get("pick"))
    if legs:
        conf += 0.5 * (complete / len(legs))
    if any(lg.get("market") for lg in legs):
        conf += 0.15

    first = legs[0] if legs else _empty_leg()
    return {
        "slip_mode": slip_mode,
        "slip_n": slip_n if slip_mode == "multi" else 1,
        "legs": legs,
        "sport_key": first.get("sport_key") or "soccer",
        "sport": first.get("sport") or "Soccer",
        "league": first.get("league") or "",
        "event": first.get("event") or "",
        "home": first.get("home") or "",
        "away": first.get("away") or "",
        "market": first.get("market") or "",
        "pick": first.get("pick") or "",
        "selection": first.get("selection") or first.get("pick") or "",
        "confidence": round(min(conf, 1.0), 2),
        "raw_text": "\n".join(lines),
    }


def _ocr_tesseract(image_bytes: bytes) -> str | None:
    try:
        from PIL import Image, ImageOps, ImageFilter
    except ImportError:
        log.info("slip_ocr: Pillow not installed")
        return None
    try:
        import pytesseract
    except ImportError:
        log.info("slip_ocr: pytesseract not installed")
        return None

    try:
        img = Image.open(io.BytesIO(image_bytes))
        img = ImageOps.exif_transpose(img)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        w, h = img.size
        if max(w, h) < 1400:
            scale = 1400 / max(w, h)
            img = img.resize((int(w * scale), int(h * scale)))
        gray = ImageOps.grayscale(img)
        gray = ImageOps.autocontrast(gray)
        # Dark bookie UIs (Stake): invert so text is dark-on-light for Tesseract
        hist = gray.histogram()
        dark = sum(hist[:64])
        light = sum(hist[192:])
        if dark > light * 1.15:
            gray = ImageOps.invert(gray)
            gray = ImageOps.autocontrast(gray)
        gray = gray.filter(ImageFilter.SHARPEN)
        text = (pytesseract.image_to_string(gray) or "").strip()
        return text or None
    except Exception:
        log.exception("slip_ocr: tesseract failed")
        return None


_VISION_PROMPT = (
    "Extract bet slip details from this bookmaker screenshot. "
    "It may be a single bet or a multi/parlay (e.g. '2 Leg Multi'). "
    "Return plain text lines only:\n"
    "First line: slip type (Single, or 'N Leg Multi').\n"
    "Then for EACH leg, in order:\n"
    "Sport\nLeague (or blank line if unknown)\nMarket name\nSelection\nHome vs Away\n"
    "Separate legs with a blank line. No odds, stake, or commentary."
)


def _ocr_anthropic(image_bytes: bytes, mime: str) -> str | None:
    api_key = (
        os.environ.get("ANTHROPIC_API_KEY")
        or os.environ.get("CLAUDE_API_KEY")
        or ""
    ).strip()
    if not api_key:
        return None
    try:
        import requests
    except ImportError:
        return None

    b64 = base64.standard_b64encode(image_bytes).decode("ascii")
    model = (
        os.environ.get("ANTHROPIC_VISION_MODEL")
        or os.environ.get("CLAUDE_VISION_MODEL")
        or "claude-3-5-haiku-latest"
    ).strip()
    payload = {
        "model": model,
        "max_tokens": 800,
        "messages": [{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": mime,
                        "data": b64,
                    },
                },
                {"type": "text", "text": _VISION_PROMPT},
            ],
        }],
    }
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=payload,
            timeout=60,
        )
        if resp.status_code >= 400:
            log.warning("slip_ocr: anthropic HTTP %s", resp.status_code)
            return None
        data = resp.json()
        parts = []
        for block in data.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text") or "")
        text = "\n".join(parts).strip()
        return text or None
    except Exception:
        log.exception("slip_ocr: anthropic vision failed")
        return None


def _ocr_openai(image_bytes: bytes, mime: str) -> str | None:
    api_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if not api_key:
        return None
    try:
        import requests
    except ImportError:
        return None

    b64 = base64.standard_b64encode(image_bytes).decode("ascii")
    model = (os.environ.get("OPENAI_VISION_MODEL") or "gpt-4o-mini").strip()
    payload = {
        "model": model,
        "max_tokens": 800,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": _VISION_PROMPT},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{b64}"},
                },
            ],
        }],
    }
    try:
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "content-type": "application/json",
            },
            json=payload,
            timeout=60,
        )
        if resp.status_code >= 400:
            log.warning("slip_ocr: openai HTTP %s", resp.status_code)
            return None
        data = resp.json()
        text = (
            ((data.get("choices") or [{}])[0].get("message") or {}).get("content")
            or ""
        ).strip()
        return text or None
    except Exception:
        log.exception("slip_ocr: openai vision failed")
        return None


def _guess_mime(filename: str, declared: str | None) -> str:
    declared = (declared or "").split(";")[0].strip().lower()
    if declared in ("image/png", "image/jpeg", "image/jpg", "image/webp", "image/gif"):
        return "image/jpeg" if declared == "image/jpg" else declared
    ext = (filename or "").rsplit(".", 1)[-1].lower()
    return {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "webp": "image/webp",
        "gif": "image/gif",
    }.get(ext, "image/jpeg")


def extract_text_from_image(
    image_bytes: bytes,
    filename: str = "",
    content_type: str | None = None,
) -> tuple[str | None, str]:
    """Return (text, engine_name). engine_name is '' when nothing worked."""
    _ensure_production_env()
    if not image_bytes:
        return None, ""
    mime = _guess_mime(filename, content_type)

    text = _ocr_tesseract(image_bytes)
    if text:
        return text, "tesseract"

    text = _ocr_anthropic(image_bytes, mime)
    if text:
        return text, "anthropic"

    text = _ocr_openai(image_bytes, mime)
    if text:
        return text, "openai"

    return None, ""


def available_engines() -> dict[str, bool]:
    _ensure_production_env()
    has_pil = False
    has_tess_mod = False
    try:
        from PIL import Image  # noqa: F401
        has_pil = True
    except ImportError:
        pass
    try:
        import pytesseract  # noqa: F401
        has_tess_mod = True
    except ImportError:
        pass
    return {
        "pillow": has_pil,
        "pytesseract": has_tess_mod,
        "anthropic": bool(
            (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_API_KEY") or "").strip()
        ),
        "openai": bool((os.environ.get("OPENAI_API_KEY") or "").strip()),
    }


def parse_slip_image(
    image_bytes: bytes,
    filename: str = "",
    content_type: str | None = None,
    ocr_text: str | None = None,
) -> dict[str, Any]:
    """Full pipeline: image (and/or pasted text) → structured slip fields."""
    engine = "pasted"
    text = (ocr_text or "").strip()
    if not text:
        text, engine = extract_text_from_image(image_bytes, filename, content_type)
        text = (text or "").strip()
    if not text:
        engines = available_engines()
        return {
            "ok": False,
            "error": "ocr_unavailable",
            "message": (
                "Could not read text from the image (OCR offline on server). "
                "Paste bet-details text in the fallback box, or ask admin to "
                "install tesseract / set a vision API key."
            ),
            "engines": engines,
            "slip_mode": "single",
            "slip_n": 1,
            "legs": [],
        }

    parsed = parse_slip_text(text)
    legs = parsed.get("legs") or []
    complete = [lg for lg in legs if lg.get("event") and lg.get("pick")]
    ok = bool(complete)
    return {
        "ok": ok,
        "error": None if ok else "parse_incomplete",
        "message": (
            None if ok
            else "Parsed some fields — edit legs before send."
        ),
        "engine": engine,
        "slip_mode": parsed.get("slip_mode") or "single",
        "slip_n": int(parsed.get("slip_n") or 1),
        "legs": legs,
        # First-leg convenience fields (single-leg UI / backward compat)
        "sport": parsed.get("sport") or "",
        "sport_key": parsed.get("sport_key") or "",
        "league": parsed.get("league") or "",
        "event": parsed.get("event") or "",
        "home": parsed.get("home") or "",
        "away": parsed.get("away") or "",
        "market": parsed.get("market") or "",
        "pick": parsed.get("pick") or "",
        "selection": parsed.get("selection") or "",
        "confidence": parsed.get("confidence") or 0,
        "raw_text": parsed.get("raw_text") or text,
    }
