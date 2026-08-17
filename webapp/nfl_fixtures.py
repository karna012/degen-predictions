"""Live/upcoming NFL fixtures via ESPN scoreboard feeds.

CDN is primary (site.api often 403 from cloud IPs). Same fail-soft contract
as mlb_fixtures.py.
"""
from __future__ import annotations

import datetime
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

ESPN_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
ESPN_CDN_SCOREBOARD_URL = "https://cdn.espn.com/core/nfl/scoreboard"
NFLVERSE_GAMES_URL = "https://github.com/nflverse/nflverse-data/releases/download/schedules/games.csv"
CACHE_TTL_SECONDS = 2 * 60
LIVE_CACHE_TTL_SECONDS = 25
NFLVERSE_CACHE_TTL_SECONDS = 30 * 60
MAX_CDN_DAYS = 10
_cache = {}
_nflverse_cache: dict = {}

# nflverse (github.com/nflverse/nflverse-data, MIT license, no key) uses
# standard 2-3 letter team codes; map to full names to match this project's
# ESPN-shaped fixture rows (home_team/away_team as display names).
NFLVERSE_TEAM_NAMES = {
    "BUF": "Buffalo Bills", "MIA": "Miami Dolphins", "NE": "New England Patriots", "NYJ": "New York Jets",
    "BAL": "Baltimore Ravens", "CIN": "Cincinnati Bengals", "CLE": "Cleveland Browns", "PIT": "Pittsburgh Steelers",
    "HOU": "Houston Texans", "IND": "Indianapolis Colts", "JAX": "Jacksonville Jaguars", "TEN": "Tennessee Titans",
    "DEN": "Denver Broncos", "KC": "Kansas City Chiefs", "LV": "Las Vegas Raiders", "LAC": "Los Angeles Chargers",
    "DAL": "Dallas Cowboys", "NYG": "New York Giants", "PHI": "Philadelphia Eagles", "WAS": "Washington Commanders",
    "CHI": "Chicago Bears", "DET": "Detroit Lions", "GB": "Green Bay Packers", "MIN": "Minnesota Vikings",
    "ATL": "Atlanta Falcons", "CAR": "Carolina Panthers", "NO": "New Orleans Saints", "TB": "Tampa Bay Buccaneers",
    "ARI": "Arizona Cardinals", "LA": "Los Angeles Rams", "SF": "San Francisco 49ers", "SEA": "Seattle Seahawks",
}


def _has_live_event(data):
    for event in data.get("events", []):
        try:
            if event["competitions"][0].get("status", {}).get("type", {}).get("state") == "in":
                return True
        except (KeyError, IndexError):
            continue
    return False


def _unwrap_scoreboard(payload):
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


def _fetch_one(url, *, name, params=None, timeout=5, ttl=CACHE_TTL_SECONDS, retries=1):
    import feed_store
    data = feed_store.get_json(
        name,
        url,
        params=params,
        timeout=timeout,
        retries=retries,
        ttl=ttl,
        count_items=lambda d: len(_unwrap_scoreboard(d).get("events") or []),
        min_items=1,
    )
    return _unwrap_scoreboard(data)


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
    while cur <= end and len(out) < 28:
        out.append(cur.strftime("%Y%m%d"))
        cur += datetime.timedelta(days=1)
    return out


CDN_CALENDAR_TTL_SECONDS = 6 * 60 * 60


def _fetch_cdn_calendar():
    """ESPN's CDN scoreboard embeds the season's week calendar in its
    default (no date/week params) response. The ``date=`` query param is
    silently ignored for NFL -- every date fetched this way returns the
    same "current" week -- so per-day fan-out can never see future weeks.
    ``week=``/``year=``/``seasontype=`` do work; this fetches the calendar
    that maps any date to those values."""
    import feed_store
    try:
        raw = feed_store.get_json(
            "nflcdn:calendar",
            ESPN_CDN_SCOREBOARD_URL,
            params={"xhr": "1"},
            timeout=6,
            retries=1,
            ttl=CDN_CALENDAR_TTL_SECONDS,
            count_items=lambda d: len(((d or {}).get("content") or {}).get("calendar") or []),
            min_items=1,
        )
    except Exception:
        return None
    if not isinstance(raw, dict):
        return None
    content = raw.get("content") or {}
    calendar = content.get("calendar")
    season = ((content.get("sbData") or {}).get("season")) or {}
    year = season.get("year")
    if not isinstance(calendar, list) or not year:
        return None
    return {"calendar": calendar, "year": year}


def _weeks_covering_range(calendar, start, end):
    """(seasontype, week) pairs from the calendar whose window overlaps
    [start, end] (date objects)."""
    range_start = datetime.datetime.combine(start, datetime.time.min, tzinfo=datetime.timezone.utc)
    range_end = datetime.datetime.combine(end, datetime.time.max, tzinfo=datetime.timezone.utc)
    out = []
    for group in calendar or []:
        seasontype = group.get("value")
        for entry in group.get("entries") or []:
            try:
                w_start = datetime.datetime.fromisoformat(str(entry["startDate"]).replace("Z", "+00:00"))
                w_end = datetime.datetime.fromisoformat(str(entry["endDate"]).replace("Z", "+00:00"))
            except (KeyError, ValueError):
                continue
            if w_end < range_start or w_start > range_end:
                continue
            week = entry.get("value")
            if seasontype and week:
                out.append((seasontype, week))
    return out


def _fetch_cdn_week(seasontype, week, year, ttl):
    return _fetch_one(
        ESPN_CDN_SCOREBOARD_URL,
        name=f"nflcdn:st{seasontype}:wk{week}:{year}",
        params={"xhr": "1", "week": str(week), "year": str(year), "seasontype": str(seasontype)},
        ttl=ttl,
        retries=1,
        timeout=6,
    )


def _fetch_cdn_weeks(days, ttl):
    """Calendar-driven fetch: map the day range to real (seasontype, week)
    buckets instead of the broken per-day ``date=`` query."""
    cal = _fetch_cdn_calendar()
    if not cal:
        return []
    try:
        start = datetime.datetime.strptime(days[0], "%Y%m%d").date()
        end = datetime.datetime.strptime(days[-1], "%Y%m%d").date()
    except ValueError:
        return []
    pairs = _weeks_covering_range(cal["calendar"], start, end)
    if not pairs:
        return []
    event_lists = []
    for seasontype, week in dict.fromkeys(pairs):
        try:
            data = _fetch_cdn_week(seasontype, week, cal["year"], ttl)
            event_lists.append(data.get("events") or [])
        except Exception:
            continue
    return _merge_events(*event_lists)


def _fetch_cdn_days(days, ttl):
    if not days:
        return {"events": []}

    weeks_events = _fetch_cdn_weeks(days, ttl)
    if weeks_events:
        return {"events": weeks_events}

    # Fallback: per-day date= fan-out. Currently a no-op for NFL (ESPN
    # ignores date= and always returns the "current" week regardless of
    # value) but kept as a last resort in case the calendar path breaks
    # or ESPN's date= handling changes.
    def _one(day: str):
        try:
            return _fetch_one(
                ESPN_CDN_SCOREBOARD_URL,
                name=f"nflcdn:{day}",
                params={"xhr": "1", "date": day},
                ttl=ttl,
                retries=1,
                timeout=5,
            )
        except Exception:
            return {"events": []}

    event_lists = []
    workers = min(6, max(1, len(days)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_one, day): day for day in days}
        for fut in as_completed(futures):
            event_lists.append(fut.result().get("events") or [])
    return {"events": _merge_events(*event_lists)}


def _fetch_nflverse_games():
    """Whole games.csv, cached in-memory. Not routed through feed_store
    (CSV, not JSON) so it won't show up in /healthz -- acceptable for a
    third-tier fallback behind the two ESPN sources."""
    cached = _nflverse_cache.get("games")
    if cached:
        from live_cache_bust import is_fresh
        if is_fresh(cached["fetched_at"], NFLVERSE_CACHE_TTL_SECONDS):
            return cached["data"]
    import csv
    import io
    import requests
    try:
        resp = requests.get(NFLVERSE_GAMES_URL, timeout=15)
        resp.raise_for_status()
        rows = list(csv.DictReader(io.StringIO(resp.text)))
    except Exception:
        return cached["data"] if cached else []
    _nflverse_cache["games"] = {"data": rows, "fetched_at": time.time()}
    return rows


def _nflverse_events_for_days(days):
    """Synthesize minimal ESPN-scoreboard-shaped events from nflverse rows
    so they flow through the same parse_event()/_events_to_fixtures() path
    as the ESPN sources. Regular season + playoffs only -- nflverse's games.csv
    has no preseason rows."""
    if not days:
        return []
    day_set = set(days)
    events = []
    for row in _fetch_nflverse_games():
        gameday = (row.get("gameday") or "").replace("-", "")
        if gameday not in day_set:
            continue
        home_abbr = (row.get("home_team") or "").strip()
        away_abbr = (row.get("away_team") or "").strip()
        home_name = NFLVERSE_TEAM_NAMES.get(home_abbr)
        away_name = NFLVERSE_TEAM_NAMES.get(away_abbr)
        if not home_name or not away_name:
            continue
        gametime = (row.get("gametime") or "13:00").strip() or "13:00"
        try:
            kickoff = datetime.datetime.strptime(
                f"{row.get('gameday')} {gametime}", "%Y-%m-%d %H:%M",
            ).replace(tzinfo=datetime.timezone.utc)
        except ValueError:
            continue
        home_score = row.get("home_score") or None
        away_score = row.get("away_score") or None
        completed = bool(home_score) and bool(away_score)
        state = "post" if completed else "pre"
        events.append({
            "id": row.get("game_id") or f"nflverse:{home_abbr}:{away_abbr}:{row.get('gameday')}",
            "date": kickoff.strftime("%Y-%m-%dT%H:%MZ"),
            "status": {"type": {"state": state, "completed": completed}},
            "competitions": [{
                "competitors": [
                    {
                        "homeAway": "home", "score": home_score,
                        "team": {"displayName": home_name, "abbreviation": home_abbr},
                        "records": [],
                    },
                    {
                        "homeAway": "away", "score": away_score,
                        "team": {"displayName": away_name, "abbreviation": away_abbr},
                        "records": [],
                    },
                ],
            }],
        })
    return events


def _fetch_scoreboard(date_range, timeout=8):
    del timeout  # kept for call-site compatibility
    cached = _cache.get(date_range)
    if cached:
        ttl = LIVE_CACHE_TTL_SECONDS if _has_live_event(cached["data"]) else CACHE_TTL_SECONDS
        from live_cache_bust import is_fresh
        if is_fresh(cached["fetched_at"], ttl):
            return cached["data"]

    ttl = LIVE_CACHE_TTL_SECONDS if (cached and _has_live_event(cached["data"])) else CACHE_TTL_SECONDS
    days = _expand_date_range(date_range)

    prefer_site = False
    try:
        import source_settings
        pref = source_settings.feed_preference("nfl")
        if not pref.get("enabled", True):
            return cached["data"] if cached else {"events": []}
        order = source_settings.provider_order("nfl", kind="fixtures")
        prefer_site = bool(order and order[0] == "espn_site")
    except Exception:
        prefer_site = False

    data = {"events": []}

    def _try_site():
        try:
            return _fetch_one(
                ESPN_SCOREBOARD_URL,
                name=f"nfl:{date_range}",
                params={"dates": date_range},
                ttl=ttl,
                retries=1,
                timeout=4,
            )
        except Exception:
            return {"events": []}

    def _try_cdn():
        batches = []
        for i in range(0, len(days), MAX_CDN_DAYS):
            batches.append(_fetch_cdn_days(days[i:i + MAX_CDN_DAYS], ttl).get("events") or [])
        return {"events": _merge_events(*batches)}

    def _try_nflverse():
        try:
            return {"events": _nflverse_events_for_days(days)}
        except Exception:
            return {"events": []}

    # CDN batches cleanly for wide ranges (chunks of MAX_CDN_DAYS), so there is
    # no efficiency reason left to prefer site.api on long windows -- and since
    # site.api routinely 403s from this host's IP (see module docstring), doing
    # so just adds a guaranteed-failed request and a false hard_fail in
    # /healthz on every call. Always respect the configured preference.
    # nflverse sits after CDN (reliable, live) but before site.api (routinely
    # 403s here) -- it's a genuine third vendor, but regular-season/playoffs
    # only, so it's a no-op during preseason.
    attempts = (
        [_try_site, _try_cdn, _try_nflverse] if prefer_site
        else [_try_cdn, _try_nflverse, _try_site]
    )

    for attempt in attempts:
        data = attempt()
        if data.get("events"):
            break

    if not (data.get("events") or []) and cached:
        return cached["data"]

    _cache[date_range] = {"data": data, "fetched_at": time.time()}
    return data


def _team_record(competitor):
    for rec in competitor.get("records") or []:
        if (rec.get("type") or rec.get("name") or "").lower() in ("total", "overall", ""):
            summary = rec.get("summary")
            if summary:
                return summary
    for rec in competitor.get("records") or []:
        if rec.get("summary"):
            return rec.get("summary")
    return ""


def parse_event(event):
    """Turn one ESPN scoreboard event into the fixture dict used on the site."""
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
    home_team = home["team"] or {}
    away_team = away["team"] or {}
    date = event.get("date") or ""
    return {
        "id": event.get("id") or "",
        "date": date,
        "start_time_utc": date,
        "home_team": home_team.get("displayName"),
        "home_abbr": home_team.get("abbreviation"),
        "away_team": away_team.get("displayName"),
        "away_abbr": away_team.get("abbreviation"),
        "home_record": _team_record(home),
        "away_record": _team_record(away),
        "status": status.get("state"),
        "status_detail": status.get("shortDetail") or status.get("detail"),
        "completed": completed,
        "home_score": home_score if completed or state == "in" else None,
        "away_score": away_score if completed or state == "in" else None,
    }


def _events_to_fixtures(data):
    fixtures = []
    for event in data.get("events", []):
        try:
            fixtures.append(parse_event(event))
        except (KeyError, IndexError, StopIteration, TypeError):
            continue
    fixtures.sort(key=lambda m: m.get("date") or "")
    return fixtures


def get_fixtures(days_back=5, days_ahead=14):
    """NFL games ESPN reports in the given window, sorted chronologically."""
    today = datetime.datetime.now(datetime.timezone.utc).date()
    start = today - datetime.timedelta(days=days_back)
    end = today + datetime.timedelta(days=days_ahead)
    date_range = f"{start.strftime('%Y%m%d')}-{end.strftime('%Y%m%d')}"
    return _events_to_fixtures(_fetch_scoreboard(date_range))


def get_history_fixtures(days_back=400, chunk_days=120):
    """Completed-game window for Elo, fetched in ESPN-friendly chunks.

    A single 400-day ``dates=`` range often returns an empty payload; smaller
    windows match the live scoreboard endpoint more reliably.
    """
    today = datetime.datetime.now(datetime.timezone.utc).date()
    start = today - datetime.timedelta(days=days_back)
    fixtures = []
    cur = start
    while cur <= today:
        end = min(cur + datetime.timedelta(days=chunk_days - 1), today)
        date_range = f"{cur.strftime('%Y%m%d')}-{end.strftime('%Y%m%d')}"
        fixtures.extend(_events_to_fixtures(_fetch_scoreboard(date_range)))
        cur = end + datetime.timedelta(days=1)
    # Dedupe by id/date after chunk joins
    seen = set()
    out = []
    for m in fixtures:
        key = m.get("id") or f"{m.get('date')}|{m.get('home_abbr')}|{m.get('away_abbr')}"
        if key in seen:
            continue
        seen.add(key)
        out.append(m)
    out.sort(key=lambda m: m.get("date") or "")
    return out
