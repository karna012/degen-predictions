"""Live/upcoming MLB fixtures via ESPN's public scoreboard endpoint
(site.api.espn.com) -- the same "unofficial but widely used, read-only,
no-auth" category as fotmob_lineup.py already uses in this codebase, not a
documented partner API. If ESPN ever blocks/changes it, this degrades to an
empty fixture list rather than crashing the site.

Bonus over the tennis/football fixture feeds: ESPN's MLB scoreboard also
includes each team's probable starting pitcher (name, current win-loss
record, ERA) right on the fixture, which is used as a live prediction input
alongside the Elo/H2H model -- no separate pitcher data source needed.
"""
import datetime
import time

import requests

ESPN_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard"
CACHE_TTL_SECONDS = 15 * 60  # no live game in the cached data -- daily-slate refresh cadence
LIVE_CACHE_TTL_SECONDS = 25  # a live game is in the cached data -- refresh fast enough that the
                             # 30s client-side poll (mlb_fixtures.html) actually gets new scores
_cache = {}


def _has_live_event(data):
    for event in data.get("events", []):
        try:
            if event["competitions"][0].get("status", {}).get("type", {}).get("state") == "in":
                return True
        except (KeyError, IndexError):
            continue
    return False


def _fetch_scoreboard(date_range):
    now = time.time()
    cached = _cache.get(date_range)
    if cached:
        ttl = LIVE_CACHE_TTL_SECONDS if _has_live_event(cached["data"]) else CACHE_TTL_SECONDS
        if now - cached["fetched_at"] < ttl:
            return cached["data"]
    try:
        resp = requests.get(ESPN_SCOREBOARD_URL, params={"dates": date_range}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        _cache[date_range] = {"data": data, "fetched_at": now}
        return data
    except Exception:
        if cached:
            return cached["data"]
        return {"events": []}


def _probable_pitcher(competitor):
    for p in competitor.get("probables", []):
        athlete = p.get("athlete") or {}
        stats = {s["name"]: s.get("displayValue") for s in p.get("statistics", [])}
        return {
            "name": athlete.get("displayName"),
            "wins": stats.get("wins"),
            "losses": stats.get("losses"),
            "era": stats.get("ERA"),
        }
    return None


def get_fixtures(days_back=3, days_ahead=10):
    """Every MLB game ESPN reports in the given window, sorted chronologically."""
    today = datetime.datetime.now(datetime.timezone.utc).date()
    start = today - datetime.timedelta(days=days_back)
    end = today + datetime.timedelta(days=days_ahead)
    date_range = f"{start.strftime('%Y%m%d')}-{end.strftime('%Y%m%d')}"

    data = _fetch_scoreboard(date_range)
    fixtures = []
    for event in data.get("events", []):
        try:
            comp = event["competitions"][0]
            competitors = comp["competitors"]
            home = next(c for c in competitors if c["homeAway"] == "home")
            away = next(c for c in competitors if c["homeAway"] == "away")
            status = event.get("status", {}).get("type", {})
            completed = bool(status.get("completed"))

            fixtures.append({
                "date": event.get("date"),
                "home_team": home["team"].get("displayName"),
                "home_abbr": home["team"].get("abbreviation"),
                "away_team": away["team"].get("displayName"),
                "away_abbr": away["team"].get("abbreviation"),
                "home_pitcher": _probable_pitcher(home),
                "away_pitcher": _probable_pitcher(away),
                "status": status.get("state"),
                "status_detail": status.get("shortDetail"),
                "completed": completed,
                "home_score": home.get("score") if completed or status.get("state") == "in" else None,
                "away_score": away.get("score") if completed or status.get("state") == "in" else None,
            })
        except (KeyError, IndexError, StopIteration):
            continue

    fixtures.sort(key=lambda m: m["date"] or "")
    return fixtures
