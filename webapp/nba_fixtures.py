"""Live/upcoming NBA fixtures.

Primary: ESPN's CDN scoreboard (works with any User-Agent, verified live).
Fallback: ESPN's site API, which is UA-sensitive here -- a generic UA like
requests' default succeeds while this project's own identifying UA gets a
403, the same gotcha already worked around for soccer's Liga MX/ESPN fetch
(see soccer_fixtures.py's _fetch_espn()). Degrades to an empty list rather
than crashing the site.

No MLB-Stats-API-equivalent primary exists for NBA (no official free
keyless scoreboard), so this is CDN -> site API only.
"""
from __future__ import annotations

import datetime
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

ESPN_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
ESPN_CDN_SCOREBOARD_URL = "https://cdn.espn.com/core/nba/scoreboard"
GENERIC_UA = {"User-Agent": "python-requests/2.31.0"}

CACHE_TTL_SECONDS = 2 * 60
LIVE_CACHE_TTL_SECONDS = 25
MAX_FALLBACK_DAYS = 8

_cache = {}


def _has_live_fixtures(fixtures: list[dict]) -> bool:
    return any((m.get("status") or "").lower() == "in" for m in fixtures)


def _unwrap_scoreboard(payload):
    """Normalize site.api or CDN xhr payloads to ``{"events": [...]}``."""
    if not isinstance(payload, dict):
        return {"events": []}
    if isinstance(payload.get("events"), list):
        return payload
    content = payload.get("content")
    if isinstance(content, dict):
        sb = content.get("sbData")
        if isinstance(sb, dict) and isinstance(sb.get("events"), list):
            return sb
    return {"events": []}


def _fetch_json(url, *, name, params=None, timeout=8, ttl=CACHE_TTL_SECONDS, retries=2,
                headers=None, count_items=None, min_items=1):
    import feed_store
    return feed_store.get_json(
        name, url, params=params, timeout=timeout, retries=retries, ttl=ttl,
        headers=headers, count_items=count_items, min_items=min_items,
    )


def _merge_events(*event_lists):
    events = []
    seen = set()
    for group in event_lists:
        for ev in group or []:
            eid = ev.get("id") or ev.get("uid") or ev.get("date")
            if eid in seen:
                continue
            seen.add(eid)
            events.append(ev)
    return events


def _fetch_cdn_days(days, ttl):
    if not days:
        return {"events": []}

    def _one(day: str):
        try:
            raw = _fetch_json(
                ESPN_CDN_SCOREBOARD_URL,
                name=f"nbacdn:{day}",
                params={"xhr": "1", "date": day},
                timeout=5,
                retries=1,
                ttl=ttl,
                count_items=lambda d: len(_unwrap_scoreboard(d).get("events") or []),
                min_items=1,
            )
            return _unwrap_scoreboard(raw)
        except Exception:
            return {"events": []}

    event_lists = []
    workers = min(6, max(1, len(days)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_one, day): day for day in days}
        for fut in as_completed(futures):
            event_lists.append(fut.result().get("events") or [])
    return {"events": _merge_events(*event_lists)}


def _fixtures_from_espn(data) -> list[dict]:
    fixtures = []
    for event in (data or {}).get("events") or []:
        try:
            comp = event["competitions"][0]
            competitors = comp["competitors"]
            home = next(c for c in competitors if c["homeAway"] == "home")
            away = next(c for c in competitors if c["homeAway"] == "away")
            status = event.get("status", {}).get("type", {}) or {}
            if not status:
                status = (comp.get("status") or {}).get("type") or {}
            state = (status.get("state") or "").strip().lower()
            home_score = home.get("score")
            away_score = away.get("score")
            has_score = home_score not in (None, "") and away_score not in (None, "")
            completed = (
                bool(status.get("completed") or state in ("post", "final"))
                and has_score
            )
            fixtures.append({
                "date": event.get("date"),
                "home_team": home["team"].get("displayName"),
                "home_abbr": (home["team"].get("abbreviation") or "").strip().upper(),
                "away_team": away["team"].get("displayName"),
                "away_abbr": (away["team"].get("abbreviation") or "").strip().upper(),
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


def _fetch_espn_site_fallback(start: datetime.date, end: datetime.date, ttl: float) -> list[dict]:
    date_range = f"{start.strftime('%Y%m%d')}-{end.strftime('%Y%m%d')}"
    try:
        raw = _fetch_json(
            ESPN_SCOREBOARD_URL,
            name=f"nba:{date_range}",
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


def _fetch_cdn(start: datetime.date, end: datetime.date, ttl: float) -> list[dict]:
    days = []
    cur = start
    while cur <= end and len(days) < MAX_FALLBACK_DAYS:
        days.append(cur.strftime("%Y%m%d"))
        cur += datetime.timedelta(days=1)
    data = _fetch_cdn_days(days, ttl)
    return _fixtures_from_espn(data)


def get_fixtures(days_back=2, days_ahead=7):
    """Every NBA game in the window, sorted chronologically."""
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
        pref = source_settings.feed_preference("nba")
        enabled = bool(pref.get("enabled", True))
        order = source_settings.provider_order("nba", kind="fixtures")
    except Exception:
        order = []

    if not enabled:
        return list(cached["data"]) if cached else []

    if not order:
        order = ["espn_cdn", "espn_site"]

    fixtures = []
    for name in order:
        try:
            if name == "espn_cdn":
                fixtures = _fetch_cdn(start, end, ttl)
            elif name == "espn_site":
                fixtures = _fetch_espn_site_fallback(start, end, ttl)
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
