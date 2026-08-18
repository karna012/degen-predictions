"""Live/upcoming NHL fixtures.

Primary: the NHL's own official public API (api-web.nhle.com) -- free,
keyless, verified live. Its schedule endpoint returns a 7-day week per
call, so days_back/days_ahead is covered by fetching however many weekly
windows overlap the requested range. Fallback: ESPN's site API (needs a
generic UA here, same gotcha as NBA/Liga MX's ESPN fetches). Unlike MLB/
NBA/NFL, ESPN has no CDN scoreboard for NHL at all (verified: 404 across
every URL variant tried) -- no CDN fallback branch exists here on purpose.
"""
from __future__ import annotations

import datetime
import time

NHL_SCHEDULE_URL = "https://api-web.nhle.com/v1/schedule/{date}"
ESPN_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard"
GENERIC_UA = {"User-Agent": "python-requests/2.31.0"}

# The NHL's own API abbreviations differ from ESPN's for a handful of teams
# -- same class of mismatch MLB already handles (AZ -> ARI). Elo ratings
# are keyed on the NHL's own codes (the primary source's native format),
# so only the ESPN fallback path needs to translate back to them.
ESPN_TO_NHL_ABBR = {
    "LA": "LAK",
    "NJ": "NJD",
    "SJ": "SJS",
    "TB": "TBL",
    "UTAH": "UTA",
}

CACHE_TTL_SECONDS = 2 * 60
LIVE_CACHE_TTL_SECONDS = 25
MAX_FALLBACK_DAYS = 8

_cache = {}


def _has_live_fixtures(fixtures: list[dict]) -> bool:
    return any((m.get("status") or "").lower() == "in" for m in fixtures)


def _fetch_json(url, *, name, params=None, timeout=8, ttl=CACHE_TTL_SECONDS, retries=2,
                headers=None, count_items=None, min_items=1):
    import feed_store
    return feed_store.get_json(
        name, url, params=params, timeout=timeout, retries=retries, ttl=ttl,
        headers=headers, count_items=count_items, min_items=min_items,
    )


def _nhl_status(game_state: str) -> tuple[str, bool]:
    state = (game_state or "").upper()
    if state == "LIVE" or state == "CRIT":
        return "in", False
    if state in ("FINAL", "OFF"):
        return "post", True
    return "pre", False


def _fixtures_from_nhl_week(payload) -> list[dict]:
    fixtures = []
    for day in (payload or {}).get("gameWeek") or []:
        for g in day.get("games") or []:
            try:
                if g.get("gameType") not in (2, 3):  # regular season, playoffs
                    continue
                home = g.get("homeTeam") or {}
                away = g.get("awayTeam") or {}
                state, completed = _nhl_status(g.get("gameState"))
                home_score = home.get("score")
                away_score = away.get("score")
                has_score = home_score is not None and away_score is not None
                fixtures.append({
                    "date": g.get("startTimeUTC"),
                    "home_team": (home.get("placeName") or {}).get("default", "") + " " +
                                 (home.get("commonName") or {}).get("default", ""),
                    "home_abbr": (home.get("abbrev") or "").strip().upper(),
                    "away_team": (away.get("placeName") or {}).get("default", "") + " " +
                                 (away.get("commonName") or {}).get("default", ""),
                    "away_abbr": (away.get("abbrev") or "").strip().upper(),
                    "status": state,
                    "status_detail": g.get("gameState"),
                    "completed": bool(completed and has_score),
                    "home_score": home_score if (completed or state == "in") and has_score else None,
                    "away_score": away_score if (completed or state == "in") and has_score else None,
                })
            except (KeyError, TypeError):
                continue
    fixtures.sort(key=lambda m: m.get("date") or "")
    return fixtures


def _fetch_nhl_primary(start: datetime.date, end: datetime.date, ttl: float) -> list[dict]:
    seen_ids = set()
    fixtures = []
    cursor = start
    calls = 0
    while cursor <= end and calls < 4:  # each call covers a 7-day window
        try:
            payload = _fetch_json(
                NHL_SCHEDULE_URL.format(date=cursor.isoformat()),
                name=f"nhl:{cursor.isoformat()}",
                timeout=6,
                retries=1,
                ttl=ttl,
                count_items=lambda d: sum(
                    len(day.get("games") or []) for day in (d or {}).get("gameWeek") or []
                ),
                min_items=0,
            )
            for m in _fixtures_from_nhl_week(payload):
                key = (m["date"], m["home_abbr"], m["away_abbr"])
                if key in seen_ids:
                    continue
                seen_ids.add(key)
                day = (m.get("date") or "")[:10]
                if start.isoformat() <= day <= end.isoformat():
                    fixtures.append(m)
        except Exception:
            pass
        cursor += datetime.timedelta(days=7)
        calls += 1
    fixtures.sort(key=lambda m: m.get("date") or "")
    return fixtures


def _unwrap_scoreboard(payload):
    if not isinstance(payload, dict):
        return {"events": []}
    if isinstance(payload.get("events"), list):
        return payload
    return {"events": []}


def _fixtures_from_espn(data) -> list[dict]:
    fixtures = []
    for event in (data or {}).get("events") or []:
        try:
            comp = event["competitions"][0]
            competitors = comp["competitors"]
            home = next(c for c in competitors if c["homeAway"] == "home")
            away = next(c for c in competitors if c["homeAway"] == "away")
            status = event.get("status", {}).get("type", {}) or {}
            state = (status.get("state") or "").strip().lower()
            home_score = home.get("score")
            away_score = away.get("score")
            has_score = home_score not in (None, "") and away_score not in (None, "")
            completed = (
                bool(status.get("completed") or state in ("post", "final"))
                and has_score
            )
            home_abbr = (home["team"].get("abbreviation") or "").strip().upper()
            away_abbr = (away["team"].get("abbreviation") or "").strip().upper()
            fixtures.append({
                "date": event.get("date"),
                "home_team": home["team"].get("displayName"),
                "home_abbr": ESPN_TO_NHL_ABBR.get(home_abbr, home_abbr),
                "away_team": away["team"].get("displayName"),
                "away_abbr": ESPN_TO_NHL_ABBR.get(away_abbr, away_abbr),
                "status": status.get("state"),
                "status_detail": status.get("shortDetail"),
                "completed": completed,
                "home_score": home_score if completed or state == "in" else None,
                "away_score": away_score if completed or state == "in" else None,
            })
        except (KeyError, IndexError, StopIteration):
            continue
    fixtures.sort(key=lambda m: m.get("date") or "")
    return fixtures


def _fetch_espn_fallback(start: datetime.date, end: datetime.date, ttl: float) -> list[dict]:
    date_range = f"{start.strftime('%Y%m%d')}-{end.strftime('%Y%m%d')}"
    try:
        raw = _fetch_json(
            ESPN_SCOREBOARD_URL,
            name=f"nhl:{date_range}",
            params={"dates": date_range},
            timeout=4,
            retries=1,
            ttl=ttl,
            headers=GENERIC_UA,
            count_items=lambda d: len(_unwrap_scoreboard(d).get("events") or []),
            min_items=1,
        )
        data = _unwrap_scoreboard(raw)
    except Exception:
        data = {"events": []}
    return _fixtures_from_espn(data)


def get_fixtures(days_back=2, days_ahead=7):
    """Every NHL game in the window, sorted chronologically."""
    today = datetime.datetime.now(datetime.timezone.utc).date()
    start = today - datetime.timedelta(days=days_back)
    end = today + datetime.timedelta(days=days_ahead)
    cache_key = f"{start.isoformat()}_{end.isoformat()}"

    cached = _cache.get(cache_key)
    if cached:
        ttl = LIVE_CACHE_TTL_SECONDS if _has_live_fixtures(cached["data"]) else CACHE_TTL_SECONDS
        from live_cache_bust import is_fresh
        if is_fresh(cached["fetched_at"], ttl):
            return list(cached["data"])

    ttl = LIVE_CACHE_TTL_SECONDS if (cached and _has_live_fixtures(cached["data"])) else CACHE_TTL_SECONDS

    enabled = True
    order = []
    try:
        import source_settings
        pref = source_settings.feed_preference("nhl")
        enabled = bool(pref.get("enabled", True))
        order = source_settings.provider_order("nhl", kind="fixtures")
    except Exception:
        order = []

    if not enabled:
        return list(cached["data"]) if cached else []

    if not order:
        order = ["nhl_api", "espn_site"]

    fixtures = []
    for name in order:
        try:
            if name == "nhl_api":
                fixtures = _fetch_nhl_primary(start, end, ttl)
            elif name == "espn_site":
                fixtures = _fetch_espn_fallback(start, end, ttl)
            else:
                continue
        except Exception:
            fixtures = []
        if fixtures:
            break

    if not fixtures and cached:
        return list(cached["data"])

    _cache[cache_key] = {"data": fixtures, "fetched_at": time.time()}
    return fixtures
