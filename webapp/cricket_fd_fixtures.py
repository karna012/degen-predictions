"""Shared fixturedownload.com feed parser for IPL / BBL upcoming fixtures.

Uses the same free JSON feed soccer already relies on
(https://fixturedownload.com/feed/json/{slug}). Fail-soft: returns [] on
any network/parse error. Season slug tries current year then previous year
so off-season boards still show the last completed schedule for results
context when needed.
"""
from __future__ import annotations

import datetime
import logging
import re
import time

logger = logging.getLogger(__name__)

FEED_URL = "https://fixturedownload.com/feed/json/{slug}"
CACHE_TTL_SECONDS = 20 * 60
HEADERS = {"User-Agent": "DegenPredictions/1.0 (https://degenpredictions.com)"}

# Short venue names from fixturedownload -> Cricsheet venue strings (best effort).
VENUE_ALIAS = {
    "SCG": "Sydney Cricket Ground",
    "MCG": "Melbourne Cricket Ground",
    "The Gabba": "Brisbane Cricket Ground, Woolloongabba",
    "Gabba": "Brisbane Cricket Ground, Woolloongabba",
    "ENGIE Stadium": "Sydney Showground Stadium",
    "Marvel Stadium": "Docklands Stadium",
    "GMHBA Stadium": "GMHBA Stadium",
    "Perth Stadium": "Perth Stadium",
    "Optus Stadium": "Perth Stadium",
    "Adelaide Oval": "Adelaide Oval",
    "Bellerive Oval": "Bellerive Oval",
    "Ninja Stadium": "Bellerive Oval",
    "Manuka Oval": "Manuka Oval",
    "Wankhede Stadium": "Wankhede Stadium",
    "Eden Gardens": "Eden Gardens",
    "M Chinnaswamy Stadium": "M Chinnaswamy Stadium",
    "MA Chidambaram Stadium": "MA Chidambaram Stadium, Chepauk",
    "Arun Jaitley Stadium": "Arun Jaitley Stadium",
    "Narendra Modi Stadium": "Narendra Modi Stadium, Ahmedabad",
    "Rajiv Gandhi International Stadium": "Rajiv Gandhi International Stadium, Uppal",
    "Sawai Mansingh Stadium": "Sawai Mansingh Stadium",
    "PCA Stadium": "Punjab Cricket Association IS Bindra Stadium, Mohali",
    "Ekana Cricket Stadium": "Bharat Ratna Shri Atal Bihari Vajpayee Ekana Cricket Stadium, Lucknow",
}

_cache: dict = {}


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def slug_for_fixture(fixture: dict) -> str:
    return f"{_slugify(fixture['team1'])}-vs-{_slugify(fixture['team2'])}-{fixture['date']}"


def _normalize_venue(raw: str) -> str:
    if not raw:
        return ""
    if raw in VENUE_ALIAS:
        return VENUE_ALIAS[raw]
    for key, full in VENUE_ALIAS.items():
        if key.lower() in raw.lower() or raw.lower() in key.lower():
            return full
    return raw


def _parse_date_utc(date_utc: str):
    if not date_utc:
        return None, "", ""
    try:
        # "2026-12-12 09:10:00Z"
        dt = datetime.datetime.strptime(date_utc.replace("Z", ""), "%Y-%m-%d %H:%M:%S")
        return dt, dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M")
    except Exception:
        return None, date_utc[:10], ""


def _fetch_season(slug: str) -> list:
    import feed_store
    data = feed_store.get_json(
        f"cricket:{slug}",
        FEED_URL.format(slug=slug),
        headers=HEADERS,
        timeout=12,
        retries=3,
        ttl=CACHE_TTL_SECONDS,
        count_items=lambda d: len(d) if isinstance(d, list) else 0,
    )
    if isinstance(data, list):
        return _parse_season_rows(data, slug)
    return []


def _parse_season_rows(raw: list, slug: str) -> list:
    out = []
    for row in raw:
        home = (row.get("HomeTeam") or "").strip()
        away = (row.get("AwayTeam") or "").strip()
        if not home or not away:
            continue
        dt, day, time_str = _parse_date_utc(row.get("DateUtc") or "")
        winner = (row.get("Winner") or "").strip()
        home_score = row.get("HomeTeamScore")
        away_score = row.get("AwayTeamScore")
        completed = bool(winner) or (home_score is not None and away_score is not None)
        venue = _normalize_venue((row.get("Location") or "").strip())
        out.append({
            "team1": home,
            "team2": away,
            "team1_elo_name": home,
            "team2_elo_name": away,
            "date": day,
            "time": time_str,
            "venue": venue,
            "completed": completed,
            "result": winner or None,
            "score1": str(home_score) if home_score is not None else None,
            "score2": str(away_score) if away_score is not None else None,
            "round": row.get("RoundNumber"),
            "source": "fixturedownload",
            "source_slug": slug,
        })
    return out


def get_competition_fixtures(base_slug: str, years: list[int] | None = None) -> list:
    """Try year slugs newest-first; cache successful payload."""
    years = years or [datetime.datetime.now().year, datetime.datetime.now().year - 1]
    cache_key = (base_slug, tuple(years))
    now = time.time()
    cached = _cache.get(cache_key)
    from live_cache_bust import is_fresh
    if cached and is_fresh(cached["fetched_at"], CACHE_TTL_SECONDS):
        return cached["data"]

    fixtures = []
    for year in years:
        slug = f"{base_slug}-{year}"
        try:
            fixtures = _fetch_season(slug)
            if fixtures:
                break
        except Exception as exc:
            logger.warning("fixturedownload %s failed: %s", slug, exc)
            fixtures = []

    if not fixtures and cached and cached.get("data"):
        return cached["data"]
    _cache[cache_key] = {"data": fixtures, "fetched_at": now}
    return fixtures


def get_upcoming(base_slug: str, years: list[int] | None = None) -> list:
    fixtures = get_competition_fixtures(base_slug, years=years)
    import today_picks
    upcoming = [
        f for f in fixtures
        if today_picks.fixture_is_upcoming_or_live(f, sport_key="cricket")
    ]
    upcoming.sort(key=lambda f: (f["date"], f.get("time") or ""))
    return upcoming


def get_fixture_by_slug(base_slug: str, slug: str, years: list[int] | None = None):
    for f in get_competition_fixtures(base_slug, years=years):
        if slug_for_fixture(f) == slug:
            return f
    return None
