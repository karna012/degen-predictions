"""Live/upcoming fixtures for the Caribbean Premier League, sourced from
Wikipedia's "<year> Caribbean Premier League" article via the public
MediaWiki API (same fail-soft pattern as hundred_fixtures.py).

fixturedownload.com has no CPL feed. The season article uses the same
{{Single-innings cricket match}} template as The Hundred, but team fields
are wikilinks (not {{cr-ENG|…}} codes).

2026 REBRANDS / NAME VARIANTS (display -> Cricsheet/Elo identity):
  Jamaica Kingsmen -> Jamaica Tallawahs
  Barbados Tridents -> Barbados Royals (also historical Cricsheet name)
  Antigua & Barbuda Falcons -> Antigua and Barbuda Falcons
  St Kitts & Nevis Patriots -> St Kitts and Nevis Patriots
"""
from __future__ import annotations

import datetime
import logging
import re
import time

import requests

logger = logging.getLogger(__name__)

WIKI_API = "https://en.wikipedia.org/w/api.php"
CACHE_TTL_SECONDS = 20 * 60
WIKI_HEADERS = {
    "User-Agent": "DegenPredictions/1.0 (https://degenpredictions.com; contact via site) requests",
}

# Wikipedia display name -> Elo / Cricsheet identity (training history).
REBRAND_ALIAS = {
    "Jamaica Kingsmen": "Jamaica Tallawahs",
    "Barbados Tridents": "Barbados Royals",
    "Antigua & Barbuda Falcons": "Antigua and Barbuda Falcons",
    "Antigua and Barbuda Falcons": "Antigua and Barbuda Falcons",
    "St Kitts & Nevis Patriots": "St Kitts and Nevis Patriots",
    "St. Kitts and Nevis Patriots": "St Kitts and Nevis Patriots",
    "St Kitts and Nevis Patriots": "St Kitts and Nevis Patriots",
    "Saint Lucia Kings": "Saint Lucia Kings",
    "St Lucia Kings": "Saint Lucia Kings",
    "Guyana Amazon Warriors": "Guyana Amazon Warriors",
    "Trinbago Knight Riders": "Trinbago Knight Riders",
    "Barbados Royals": "Barbados Royals",
    "Jamaica Tallawahs": "Jamaica Tallawahs",
}

MATCH_BLOCK_RE = re.compile(r"\{\{Single-innings cricket match(.*?)\n\}\}", re.DOTALL)
FIELD_RE = re.compile(r"^\|[ \t]*(\w+)[ \t]*=[ \t]*(.*)$", re.MULTILINE)
WIKILINK_RE = re.compile(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]")

_cache = {}


def _strip_wikilinks(text: str) -> str:
    return WIKILINK_RE.sub(r"\1", text or "").strip()


def _team_from_field(raw: str) -> str | None:
    name = _strip_wikilinks(raw)
    name = re.sub(r"\s+", " ", name).strip()
    return name or None


def _fetch_page_wikitext(year: int) -> str | None:
    title = f"{year} Caribbean Premier League"
    search_resp = requests.get(
        WIKI_API,
        params={
            "action": "query",
            "list": "search",
            "srsearch": title,
            "format": "json",
        },
        timeout=20,
        headers=WIKI_HEADERS,
    )
    search_resp.raise_for_status()
    results = search_resp.json().get("query", {}).get("search", [])
    match = next((r for r in results if r["title"] == title), None)
    if not match:
        return None

    wt_resp = requests.get(
        WIKI_API,
        params={
            "action": "parse",
            "pageid": match["pageid"],
            "format": "json",
            "prop": "wikitext",
        },
        timeout=25,
        headers=WIKI_HEADERS,
    )
    wt_resp.raise_for_status()
    return wt_resp.json().get("parse", {}).get("wikitext", {}).get("*")


def _parse_date(date_str: str, time_str: str):
    try:
        dt = datetime.datetime.strptime(date_str.strip(), "%d %B %Y")
        if time_str and time_str.strip():
            hh, mm = time_str.strip().split(":")[:2]
            dt = dt.replace(hour=int(hh), minute=int(mm))
        return dt
    except Exception:
        return None


def _parse_fixtures(wikitext: str) -> list:
    fixtures = []
    for block in MATCH_BLOCK_RE.findall(wikitext or ""):
        fields = {m.group(1): m.group(2).strip() for m in FIELD_RE.finditer(block)}
        team1 = _team_from_field(fields.get("team1", ""))
        team2 = _team_from_field(fields.get("team2", ""))
        if not team1 or not team2:
            continue
        match_dt = _parse_date(fields.get("date", ""), fields.get("time", ""))
        result = _strip_wikilinks(fields.get("result", ""))
        score1 = fields.get("score1", "").strip()
        score2 = fields.get("score2", "").strip()
        venue = _strip_wikilinks(fields.get("venue", ""))
        fixtures.append({
            "team1": team1,
            "team2": team2,
            "team1_elo_name": REBRAND_ALIAS.get(team1, team1),
            "team2_elo_name": REBRAND_ALIAS.get(team2, team2),
            "date": match_dt.strftime("%Y-%m-%d") if match_dt else fields.get("date", ""),
            "time": (fields.get("time") or "").strip(),
            "venue": venue,
            "completed": bool(result) or bool(score1 and score2),
            "result": result or None,
            "score1": score1 or None,
            "score2": score2 or None,
            "source": "wikipedia",
        })
    return fixtures


def get_cpl_fixtures() -> list:
    """All fixtures for the current CPL season, or [] on fetch/parse failure."""
    year = datetime.datetime.now().year
    cache_key = year
    now = time.time()
    cached = _cache.get(cache_key)
    from live_cache_bust import is_fresh
    if cached and is_fresh(cached["fetched_at"], CACHE_TTL_SECONDS):
        return cached["data"]

    try:
        wikitext = _fetch_page_wikitext(year)
        fixtures = _parse_fixtures(wikitext) if wikitext else []
    except Exception as exc:
        logger.warning("CPL fixtures fetch/parse failed: %s", exc)
        fixtures = cached["data"] if cached else []

    _cache[cache_key] = {"data": fixtures, "fetched_at": now}
    return fixtures


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def slug_for_fixture(fixture: dict) -> str:
    return f"{_slugify(fixture['team1'])}-vs-{_slugify(fixture['team2'])}-{fixture['date']}"


def get_fixture_by_slug(slug: str):
    for f in get_cpl_fixtures():
        if slug_for_fixture(f) == slug:
            return f
    return None


def get_upcoming_and_live(fixtures=None) -> list:
    fixtures = fixtures if fixtures is not None else get_cpl_fixtures()
    import today_picks
    upcoming = [
        f for f in fixtures
        if today_picks.fixture_is_upcoming_or_live(f, sport_key="cricket")
    ]
    upcoming.sort(key=lambda f: (f["date"], f.get("time") or ""))
    return upcoming
