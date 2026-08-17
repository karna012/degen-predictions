"""Live/upcoming MLB fixtures.

Primary: official MLB Stats API (statsapi.mlb.com) — free, range queries,
works from cloud IPs. Fallback: ESPN CDN scoreboard when Stats API is empty
or unreachable. Degrades to an empty list rather than crashing the site.

Probable starters (name, W-L, ERA) come from Stats API people stats when
available; ESPN CDN also carries them on the fallback path.
"""
from __future__ import annotations

import datetime
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

MLB_STATS_SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"
MLB_STATS_PEOPLE_URL = "https://statsapi.mlb.com/api/v1/people/{person_id}"
ESPN_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard"
ESPN_CDN_SCOREBOARD_URL = "https://cdn.espn.com/core/mlb/scoreboard"

CACHE_TTL_SECONDS = 2 * 60
LIVE_CACHE_TTL_SECONDS = 25
MAX_FALLBACK_DAYS = 8
PITCHER_STATS_TTL_SECONDS = 6 * 60 * 60

# Stats API abbreviations that differ from ESPN (used by ESPN_TO_RETROSHEET).
MLB_TO_ESPN_ABBR = {
    "AZ": "ARI",
    "CWS": "CHW",
}

_cache = {}
_pitcher_cache = {}


def _normalize_abbr(abbr: str | None) -> str | None:
    if not abbr:
        return None
    text = str(abbr).strip().upper()
    return MLB_TO_ESPN_ABBR.get(text, text)


def _has_live_fixtures(fixtures: list[dict]) -> bool:
    return any((m.get("status") or "").lower() == "in" for m in fixtures)


def _has_live_event(data):
    for event in data.get("events", []):
        try:
            if event["competitions"][0].get("status", {}).get("type", {}).get("state") == "in":
                return True
        except (KeyError, IndexError):
            continue
    return False


def _unwrap_scoreboard(payload):
    """Normalize site.api or CDN xhr payloads to ``{\"events\": [...]}``."""
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
                count_items=None, min_items=1):
    import feed_store
    return feed_store.get_json(
        name,
        url,
        params=params,
        timeout=timeout,
        retries=retries,
        ttl=ttl,
        count_items=count_items,
        min_items=min_items,
    )


def _expand_date_range(date_range: str) -> list[str]:
    text = (date_range or "").strip()
    if not text:
        today = datetime.datetime.now(datetime.timezone.utc).date()
        return [today.strftime("%Y%m%d")]
    if "-" in text and len(text) >= 17:
        start_s, end_s = text.split("-", 1)
    else:
        start_s = end_s = text[:8]
    try:
        start = datetime.datetime.strptime(start_s[:8], "%Y%m%d").date()
        end = datetime.datetime.strptime(end_s[:8], "%Y%m%d").date()
    except ValueError:
        today = datetime.datetime.now(datetime.timezone.utc).date()
        return [today.strftime("%Y%m%d")]
    if end < start:
        start, end = end, start
    out = []
    cur = start
    while cur <= end and len(out) < 21:
        out.append(cur.strftime("%Y%m%d"))
        cur += datetime.timedelta(days=1)
    return out


def _pitcher_from_person(person_id: int | None, season: int) -> dict | None:
    if not person_id:
        return None
    key = f"{person_id}:{season}"
    cached = _pitcher_cache.get(key)
    if cached and (time.time() - cached["fetched_at"]) < PITCHER_STATS_TTL_SECONDS:
        return cached["data"]

    try:
        payload = _fetch_json(
            MLB_STATS_PEOPLE_URL.format(person_id=int(person_id)),
            name=f"mlbperson:{person_id}:{season}",
            params={
                "hydrate": f"stats(group=[pitching],type=[season],season={season})",
            },
            timeout=5,
            retries=1,
            ttl=PITCHER_STATS_TTL_SECONDS,
            count_items=lambda d: 1 if isinstance(d, dict) and (d.get("people") or []) else 0,
            min_items=1,
        )
    except Exception:
        payload = None

    people = (payload or {}).get("people") if isinstance(payload, dict) else None
    if not people:
        return None
    person = people[0] or {}
    wins = losses = era = None
    for block in person.get("stats") or []:
        if (block.get("group") or {}).get("displayName") != "pitching":
            continue
        for split in block.get("splits") or []:
            stat = split.get("stat") or {}
            wins = stat.get("wins")
            losses = stat.get("losses")
            era = stat.get("era")
            break
        if era is not None or wins is not None:
            break
    data = {
        "name": person.get("fullName"),
        "wins": wins,
        "losses": losses,
        "era": str(era) if era is not None else None,
    }
    if not data["name"]:
        return None
    _pitcher_cache[key] = {"data": data, "fetched_at": time.time()}
    return data


def _enrich_pitchers(fixtures: list[dict], season: int) -> None:
    """Fill W-L/ERA for probable starters; name-only is kept if stats fail."""
    needed = {}
    for m in fixtures:
        for key in ("home_pitcher", "away_pitcher"):
            p = m.get(key)
            if not isinstance(p, dict):
                continue
            pid = p.get("id")
            if not pid or p.get("era") is not None:
                continue
            needed[int(pid)] = key

    if not needed:
        return

    stats_by_id = {}
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(needed)))) as pool:
        futures = {
            pool.submit(_pitcher_from_person, pid, season): pid
            for pid in needed
        }
        for fut in as_completed(futures):
            pid = futures[fut]
            try:
                stats_by_id[pid] = fut.result()
            except Exception:
                stats_by_id[pid] = None

    for m in fixtures:
        for key in ("home_pitcher", "away_pitcher"):
            p = m.get(key)
            if not isinstance(p, dict) or not p.get("id"):
                continue
            extra = stats_by_id.get(int(p["id"]))
            if not extra:
                # Drop internal id so templates keep the old ESPN-shaped dict.
                m[key] = {
                    "name": p.get("name"),
                    "wins": p.get("wins"),
                    "losses": p.get("losses"),
                    "era": p.get("era"),
                }
                continue
            m[key] = {
                "name": extra.get("name") or p.get("name"),
                "wins": extra.get("wins"),
                "losses": extra.get("losses"),
                "era": extra.get("era"),
            }


def _statsapi_status(game: dict) -> tuple[str, str, bool]:
    status = game.get("status") or {}
    abstract = (status.get("abstractGameState") or "").strip()
    detail = status.get("detailedState") or status.get("codedGameState") or ""
    detail_l = detail.lower()
    if "postpon" in detail_l:
        return "post", detail, False
    if abstract == "Live":
        return "in", detail, False
    if abstract == "Final":
        return "post", detail, True
    return "pre", detail, False


def _fixtures_from_statsapi(payload) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    fixtures = []
    for day in payload.get("dates") or []:
        for game in day.get("games") or []:
            try:
                teams = game.get("teams") or {}
                home_side = teams.get("home") or {}
                away_side = teams.get("away") or {}
                home_team = home_side.get("team") or {}
                away_team = away_side.get("team") or {}
                state, detail, final_ok = _statsapi_status(game)
                home_score = home_side.get("score")
                away_score = away_side.get("score")
                has_score = home_score not in (None, "") and away_score not in (None, "")
                completed = bool(final_ok and has_score)

                home_pp = home_side.get("probablePitcher") or {}
                away_pp = away_side.get("probablePitcher") or {}
                home_pitcher = None
                away_pitcher = None
                if home_pp.get("fullName") or home_pp.get("id"):
                    home_pitcher = {
                        "id": home_pp.get("id"),
                        "name": home_pp.get("fullName"),
                        "wins": None,
                        "losses": None,
                        "era": None,
                    }
                if away_pp.get("fullName") or away_pp.get("id"):
                    away_pitcher = {
                        "id": away_pp.get("id"),
                        "name": away_pp.get("fullName"),
                        "wins": None,
                        "losses": None,
                        "era": None,
                    }

                fixtures.append({
                    "date": game.get("gameDate") or "",
                    "home_team": home_team.get("name"),
                    "home_abbr": _normalize_abbr(home_team.get("abbreviation")),
                    "away_team": away_team.get("name"),
                    "away_abbr": _normalize_abbr(away_team.get("abbreviation")),
                    "home_pitcher": home_pitcher,
                    "away_pitcher": away_pitcher,
                    "status": state,
                    "status_detail": detail,
                    "completed": completed,
                    "home_score": home_score if completed or state == "in" else None,
                    "away_score": away_score if completed or state == "in" else None,
                })
            except (KeyError, TypeError, AttributeError):
                continue
    fixtures.sort(key=lambda m: m.get("date") or "")
    return fixtures


def _fetch_statsapi_range(start: datetime.date, end: datetime.date, ttl: float) -> list[dict]:
    payload = _fetch_json(
        MLB_STATS_SCHEDULE_URL,
        name=f"mlbstats:{start.isoformat()}_{end.isoformat()}",
        params={
            "sportId": 1,
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "hydrate": "team,probablePitcher",
        },
        timeout=10,
        retries=2,
        ttl=ttl,
        count_items=lambda d: int((d or {}).get("totalGames") or 0)
        if isinstance(d, dict)
        else 0,
        min_items=1,
    )
    fixtures = _fixtures_from_statsapi(payload)
    if fixtures:
        # ERA/W-L only matter for live prediction inputs — skip finished games
        # and far-ahead slate to keep the fixtures page snappy.
        horizon = (
            datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=3)
        ).strftime("%Y-%m-%d")
        enrich = [
            m for m in fixtures
            if not m.get("completed") and (m.get("date") or "")[:10] <= horizon
        ]
        _enrich_pitchers(enrich, season=start.year)
        # Strip internal pitcher ids from the rest so templates stay ESPN-shaped.
        for m in fixtures:
            for key in ("home_pitcher", "away_pitcher"):
                p = m.get(key)
                if isinstance(p, dict) and "id" in p and p.get("era") is None:
                    m[key] = {
                        "name": p.get("name"),
                        "wins": p.get("wins"),
                        "losses": p.get("losses"),
                        "era": p.get("era"),
                    }
    return fixtures


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
                name=f"mlbcdn:{day}",
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


def _probable_pitcher_espn(competitor):
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
                "home_abbr": _normalize_abbr(home["team"].get("abbreviation")),
                "away_team": away["team"].get("displayName"),
                "away_abbr": _normalize_abbr(away["team"].get("abbreviation")),
                "home_pitcher": _probable_pitcher_espn(home),
                "away_pitcher": _probable_pitcher_espn(away),
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
    days = []
    cur = start
    while cur <= end and len(days) < MAX_FALLBACK_DAYS:
        days.append(cur.strftime("%Y%m%d"))
        cur += datetime.timedelta(days=1)
    data = _fetch_cdn_days(days, ttl)
    if not (data.get("events") or []):
        date_range = f"{start.strftime('%Y%m%d')}-{end.strftime('%Y%m%d')}"
        try:
            raw = _fetch_json(
                ESPN_SCOREBOARD_URL,
                name=f"mlb:{date_range}",
                params={"dates": date_range},
                timeout=4,
                retries=1,
                ttl=ttl,
                count_items=lambda d: len(_unwrap_scoreboard(d).get("events") or []),
                min_items=1,
            )
            data = _unwrap_scoreboard(raw)
        except Exception:
            data = {"events": []}
    return _fixtures_from_espn(data)


def get_fixtures(days_back=2, days_ahead=7):
    """Every MLB game in the window, sorted chronologically."""
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

    primary = "mlb_statsapi"
    fallback = "espn_cdn"
    enabled = True
    order = []
    try:
        import source_settings
        pref = source_settings.feed_preference("mlb")
        enabled = bool(pref.get("enabled", True))
        order = source_settings.provider_order("mlb", kind="fixtures")
    except Exception:
        order = []

    if not enabled:
        return list(cached["data"]) if cached else []

    if not order:
        for name in (primary, fallback, "mlb_statsapi", "espn_cdn", "espn_site"):
            if name and name not in order:
                order.append(name)

    fixtures = []
    for name in order:
        try:
            if name == "mlb_statsapi":
                fixtures = _fetch_statsapi_range(start, end, ttl)
            elif name in ("espn_cdn", "espn_site"):
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
    return list(fixtures)
