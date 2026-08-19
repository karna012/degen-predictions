"""Live soccer schedule overlay — admin Sources primary/fallback aware.

Prediction CSVs are a snapshot. This module refreshes kickoff times and
full-time scores so finished matches can leave /{league}/ and land on
/{league}/results. Kickoff times are shown in IST.

Providers (Admin → Sources → Soccer):
  - fixturedownload.com JSON (default, free)
  - football-data.org v4 (needs FOOTBALL_DATA_ORG_TOKEN)
  - API-Football / API-Sports (needs API_SPORTS_KEY)
"""
from __future__ import annotations

import logging
import sys
import time
from datetime import datetime, timezone

from today_picks import fixture_is_finished, format_kickoff_ist, split_fixture_board

logger = logging.getLogger(__name__)

FEED_URL = "https://fixturedownload.com/feed/json/{slug}"
FD_ORG_MATCHES = "https://api.football-data.org/v4/competitions/{code}/matches"
API_SPORTS_FIXTURES = "https://v3.football.api-sports.io/fixtures"
OPENFOOTBALL_URL = "https://raw.githubusercontent.com/openfootball/football.json/master/{season}/{slug}.json"
ESPN_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard"
CACHE_TTL_SECONDS = 5 * 60
HEADERS = {"User-Agent": "DegenPredictions/1.0 (https://degenpredictions.com)"}

# ESPN soccer league slugs -- only for leagues with no fixturedownload.com or
# openfootball coverage (currently just Liga MX; fixturedownload has no
# Mexico feed at all, and openfootball's football.json repo doesn't cover
# it either). Sits at the end of the provider fallback chain (see
# _provider_order()), so it's a genuine no-op for every other league.
ESPN_SLUGS = {
    "liga_mx": "mex.1",
    "saudi_pro_league": "ksa.1",
}

# football-data.org competition codes
FD_ORG_CODES = {
    "premier_league": "PL",
    "la_liga": "PD",
    "serie_a": "SA",
    "bundesliga": "BL1",
    "ligue_1": "FL1",
    "primeira_liga": "PPL",
}

# API-Sports league ids (current season)
API_SPORTS_LEAGUE_IDS = {
    "premier_league": 39,
    "la_liga": 140,
    "serie_a": 135,
    "bundesliga": 78,
    "ligue_1": 61,
    "mls": 253,
    "primeira_liga": 94,
    "liga_mx": 262,
}

# openfootball/football.json slugs -- no MLS coverage (public domain, no key,
# github.com/openfootball/football.json). "time" in that feed is each
# league's home-country local kickoff time, not UTC -- see _OPENFOOTBALL_TZ.
OPENFOOTBALL_SLUGS = {
    "premier_league": "en.1",
    "la_liga": "es.1",
    "serie_a": "it.1",
    "bundesliga": "de.1",
    "ligue_1": "fr.1",
    "primeira_liga": "pt.1",
}
_OPENFOOTBALL_TZ = {
    "premier_league": "Europe/London",
    "la_liga": "Europe/Madrid",
    "serie_a": "Europe/Rome",
    "bundesliga": "Europe/Berlin",
    "ligue_1": "Europe/Paris",
    "primeira_liga": "Europe/Lisbon",
}

_cache: dict = {}


def _leagues():
    import os
    scripts = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    from leagues import LEAGUES, correct_fixture_dateutc
    return LEAGUES, correct_fixture_dateutc


def _map_name(name: str, name_map: dict) -> str:
    raw = (name or "").strip()
    return name_map.get(raw, raw)


def _score_value(raw):
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        try:
            return int(float(raw))
        except (TypeError, ValueError):
            return None


def _normalize_row(
    league_key: str,
    *,
    home: str,
    away: str,
    date_utc: str,
    home_score=None,
    away_score=None,
    round_num=None,
    location: str = "",
    completed: bool = False,
) -> dict:
    fx = {
        "league_id": league_key,
        "sport_key": "soccer",
        "round": round_num,
        "date": date_utc,
        "DateUtc": date_utc,
        "kickoff_iso": date_utc,
        "home": home,
        "away": away,
        "home_team": home,
        "away_team": away,
        "home_score": home_score,
        "away_score": away_score,
        "completed": completed,
        "location": location,
    }
    if completed or fixture_is_finished(fx, sport_key="soccer"):
        fx["completed"] = True
    return fx


def _fetch_fixturedownload(league_key: str, cfg: dict, correct_date) -> list[dict]:
    slug = cfg.get("fixturedownload_slug")
    if not slug:
        return []
    import feed_store
    raw = feed_store.get_json(
        f"soccer:{slug}",
        FEED_URL.format(slug=slug),
        headers=HEADERS,
        timeout=8,
        retries=3,
        ttl=CACHE_TTL_SECONDS,
        count_items=lambda d: len(d) if isinstance(d, list) else 0,
    )
    if not isinstance(raw, list):
        return []
    name_map = cfg.get("fixture_name_map") or {}
    out: list[dict] = []
    for row in raw:
        home = _map_name(row.get("HomeTeam") or "", name_map)
        away = _map_name(row.get("AwayTeam") or "", name_map)
        if not home or not away:
            continue
        date_utc = correct_date(league_key, row.get("DateUtc") or "", home, away)
        home_score = _score_value(row.get("HomeTeamScore"))
        away_score = _score_value(row.get("AwayTeamScore"))
        winner = (row.get("Winner") or "").strip()
        completed = bool(winner) or (home_score is not None and away_score is not None)
        out.append(_normalize_row(
            league_key,
            home=home,
            away=away,
            date_utc=date_utc,
            home_score=home_score,
            away_score=away_score,
            round_num=row.get("RoundNumber"),
            location=(row.get("Location") or "").strip(),
            completed=completed,
        ))
    return out


def _fetch_football_data_org(league_key: str, cfg: dict, correct_date) -> list[dict]:
    code = FD_ORG_CODES.get(league_key)
    if not code:
        return []
    try:
        import source_settings
        token = (
            source_settings.api_key("football_data_org_token")
            or source_settings.secret_value("FOOTBALL_DATA_ORG_TOKEN")
        )
    except Exception:
        token = ""
    if not token:
        return []
    import feed_store
    data = feed_store.get_json(
        f"soccer:fdorg:{code}",
        FD_ORG_MATCHES.format(code=code),
        headers={**HEADERS, "X-Auth-Token": token},
        timeout=10,
        retries=2,
        ttl=CACHE_TTL_SECONDS,
        count_items=lambda d: len((d or {}).get("matches") or []) if isinstance(d, dict) else 0,
    )
    matches = (data or {}).get("matches") if isinstance(data, dict) else None
    if not isinstance(matches, list):
        return []
    name_map = cfg.get("fixture_name_map") or {}
    # Also map football-data.co.uk style names from training name_map (reverse-ish).
    train_map = cfg.get("name_map") or {}
    reverse_train = {v: k for k, v in train_map.items()}
    out: list[dict] = []
    for m in matches:
        home_raw = ((m.get("homeTeam") or {}).get("name") or "").strip()
        away_raw = ((m.get("awayTeam") or {}).get("name") or "").strip()
        home = _map_name(name_map.get(home_raw, reverse_train.get(home_raw, home_raw)), name_map)
        away = _map_name(name_map.get(away_raw, reverse_train.get(away_raw, away_raw)), name_map)
        if not home or not away:
            continue
        utc = (m.get("utcDate") or "").strip()
        if utc.endswith("Z"):
            date_utc = utc.replace("T", " ").replace("Z", "") + "Z"
            # normalize to "YYYY-MM-DD HH:MM:SSZ"
            try:
                dt = datetime.fromisoformat(utc.replace("Z", "+00:00")).astimezone(timezone.utc)
                date_utc = dt.strftime("%Y-%m-%d %H:%M:%S") + "Z"
            except ValueError:
                date_utc = utc
        else:
            date_utc = utc
        date_utc = correct_date(league_key, date_utc, home, away)
        ft = ((m.get("score") or {}).get("fullTime") or {})
        home_score = _score_value(ft.get("home"))
        away_score = _score_value(ft.get("away"))
        status = (m.get("status") or "").upper()
        completed = status in ("FINISHED", "AWARDED") or (
            home_score is not None and away_score is not None
        )
        out.append(_normalize_row(
            league_key,
            home=home,
            away=away,
            date_utc=date_utc,
            home_score=home_score,
            away_score=away_score,
            round_num=m.get("matchday"),
            completed=completed,
        ))
    return out


def _fetch_api_sports(league_key: str, cfg: dict, correct_date) -> list[dict]:
    league_id = API_SPORTS_LEAGUE_IDS.get(league_key)
    if not league_id:
        return []
    try:
        import source_settings
        key = (
            source_settings.api_key("api_sports_key")
            or source_settings.secret_value("API_SPORTS_KEY")
        )
    except Exception:
        key = ""
    if not key:
        return []
    season = datetime.now(timezone.utc).year
    # European seasons span years; Aug+ uses current year as season start.
    month = datetime.now(timezone.utc).month
    if month < 7 and league_key != "mls":
        season = season - 1
    import feed_store
    data = feed_store.get_json(
        f"soccer:apisports:{league_id}:{season}",
        API_SPORTS_FIXTURES,
        params={"league": league_id, "season": season},
        headers={
            **HEADERS,
            "x-apisports-key": key,
        },
        timeout=12,
        retries=2,
        ttl=CACHE_TTL_SECONDS,
        count_items=lambda d: len((d or {}).get("response") or []) if isinstance(d, dict) else 0,
    )
    rows = (data or {}).get("response") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return []
    name_map = cfg.get("fixture_name_map") or {}
    out: list[dict] = []
    for row in rows:
        teams = row.get("teams") or {}
        home_raw = ((teams.get("home") or {}).get("name") or "").strip()
        away_raw = ((teams.get("away") or {}).get("name") or "").strip()
        home = _map_name(home_raw, name_map)
        away = _map_name(away_raw, name_map)
        if not home or not away:
            continue
        fixture = row.get("fixture") or {}
        utc = (fixture.get("date") or "").strip()
        try:
            dt = datetime.fromisoformat(utc.replace("Z", "+00:00")).astimezone(timezone.utc)
            date_utc = dt.strftime("%Y-%m-%d %H:%M:%S") + "Z"
        except ValueError:
            date_utc = utc
        date_utc = correct_date(league_key, date_utc, home, away)
        goals = row.get("goals") or {}
        home_score = _score_value(goals.get("home"))
        away_score = _score_value(goals.get("away"))
        status = ((fixture.get("status") or {}).get("short") or "").upper()
        completed = status in ("FT", "AET", "PEN") or (
            home_score is not None and away_score is not None and status in ("FT", "AET", "PEN", "AWD", "WO")
        )
        out.append(_normalize_row(
            league_key,
            home=home,
            away=away,
            date_utc=date_utc,
            home_score=home_score,
            away_score=away_score,
            round_num=(row.get("league") or {}).get("round"),
            location=((fixture.get("venue") or {}).get("name") or "").strip(),
            completed=completed,
        ))
    return out


def _openfootball_season_guess() -> str:
    """European season directory name, e.g. "2026-27". Seasons run Aug-May,
    but openfootball only publishes a new season's directory once it has
    real fixtures (observed lagging the Aug-1 boundary) -- callers should
    fall back to the prior season string if this one 404s/is empty."""
    now = datetime.now(timezone.utc)
    start_year = now.year if now.month >= 7 else now.year - 1
    return f"{start_year}-{str(start_year + 1)[-2:]}"


def _openfootball_prior_season(season: str) -> str:
    start_year = int(season[:4]) - 1
    return f"{start_year}-{str(start_year + 1)[-2:]}"


def _fetch_openfootball(league_key: str, cfg: dict, correct_date) -> list[dict]:
    slug = OPENFOOTBALL_SLUGS.get(league_key)
    if not slug:
        return []
    try:
        from zoneinfo import ZoneInfo
        local_tz = ZoneInfo(_OPENFOOTBALL_TZ.get(league_key, "UTC"))
    except Exception:
        local_tz = timezone.utc

    import feed_store

    def _fetch_season(season: str):
        return feed_store.get_json(
            f"soccer:openfootball:{slug}:{season}",
            OPENFOOTBALL_URL.format(season=season, slug=slug),
            headers=HEADERS,
            timeout=8,
            retries=2,
            ttl=CACHE_TTL_SECONDS,
            count_items=lambda d: len((d or {}).get("matches") or []) if isinstance(d, dict) else 0,
        )

    season = _openfootball_season_guess()
    data = _fetch_season(season)
    matches = (data or {}).get("matches") if isinstance(data, dict) else None
    if not isinstance(matches, list) or not matches:
        season = _openfootball_prior_season(season)
        data = _fetch_season(season)
        matches = (data or {}).get("matches") if isinstance(data, dict) else None
    if not isinstance(matches, list):
        return []

    name_map = cfg.get("fixture_name_map") or {}
    now = datetime.now(timezone.utc)
    out: list[dict] = []
    for m in matches:
        home = _map_name((m.get("team1") or "").strip(), name_map)
        away = _map_name((m.get("team2") or "").strip(), name_map)
        if not home or not away:
            continue
        day = (m.get("date") or "").strip()
        clock = (m.get("time") or "12:00").strip()
        try:
            local_dt = datetime.strptime(f"{day} {clock}", "%Y-%m-%d %H:%M").replace(tzinfo=local_tz)
            dt_utc = local_dt.astimezone(timezone.utc)
            date_utc = dt_utc.strftime("%Y-%m-%d %H:%M:%S") + "Z"
        except ValueError:
            continue
        # Skip fixtures too far outside "now" -- guards against a stale
        # fallback-season fetch (or an unbackfilled score, see below) being
        # read as this week's slate.
        if abs((dt_utc - now).days) > 45:
            continue
        score = m.get("score")
        home_score = away_score = None
        completed = False
        if isinstance(score, dict):
            ft = score.get("ft")
            if isinstance(ft, list) and len(ft) == 2:
                home_score, away_score = _score_value(ft[0]), _score_value(ft[1])
                completed = home_score is not None and away_score is not None
        # A plain [h,a] list (not a dict with "ft") means "not yet played" in
        # this feed's convention -- but ~7% of finished matches in past
        # seasons were never backfilled past that placeholder, so leave
        # completed=False here and let _normalize_row's own kickoff-age
        # check (fixture_is_finished) catch those rather than trusting the
        # placeholder shape either way.
        date_utc = correct_date(league_key, date_utc, home, away)
        out.append(_normalize_row(
            league_key,
            home=home,
            away=away,
            date_utc=date_utc,
            home_score=home_score,
            away_score=away_score,
            round_num=m.get("round"),
            completed=completed,
        ))
    return out


def _fetch_espn(league_key: str, cfg: dict, correct_date) -> list[dict]:
    slug = ESPN_SLUGS.get(league_key)
    if not slug:
        return []
    import feed_store
    from datetime import timedelta

    # One wide-window call (45 days back/ahead) so recent results and
    # upcoming fixtures both come back together, same span openfootball
    # guards against above.
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    date_range = f"{(start - timedelta(days=45)):%Y%m%d}-{(start + timedelta(days=45)):%Y%m%d}"
    data = feed_store.get_json(
        f"soccer:espn:{slug}",
        ESPN_SCOREBOARD_URL.format(slug=slug),
        params={"dates": date_range},
        # ESPN's soccer scoreboard 403s the site's normal identifying UA
        # specifically when a "dates" range is present (confirmed: works
        # fine with no dates param, or with a generic UA even with dates --
        # verified from this project's own EC2 IP, so it's UA-content
        # specific, not IP reputation). Override just for this call rather
        # than touching feed_store's shared default, which other sports'
        # ESPN calls rely on unchanged.
        headers={"User-Agent": "python-requests/2.31.0"},
        timeout=8,
        retries=2,
        ttl=CACHE_TTL_SECONDS,
        count_items=lambda d: len((d or {}).get("events") or []) if isinstance(d, dict) else 0,
    )
    events = (data or {}).get("events") if isinstance(data, dict) else None
    if not isinstance(events, list):
        return []

    name_map = cfg.get("fixture_name_map") or {}
    out: list[dict] = []
    for e in events:
        comp = (e.get("competitions") or [{}])[0]
        competitors = comp.get("competitors") or []
        home_c = next((c for c in competitors if c.get("homeAway") == "home"), None)
        away_c = next((c for c in competitors if c.get("homeAway") == "away"), None)
        if not home_c or not away_c:
            continue
        home = _map_name((home_c.get("team") or {}).get("displayName") or "", name_map)
        away = _map_name((away_c.get("team") or {}).get("displayName") or "", name_map)
        if not home or not away:
            continue
        # ESPN dates are ISO8601 UTC, e.g. "2026-08-18T01:00Z" (no seconds).
        try:
            dt_utc = datetime.strptime((e.get("date") or "").strip(), "%Y-%m-%dT%H:%MZ")
        except ValueError:
            continue
        date_utc = dt_utc.strftime("%Y-%m-%d %H:%M:%S") + "Z"
        completed = bool(((comp.get("status") or {}).get("type") or {}).get("completed"))
        home_score = _score_value(home_c.get("score")) if completed else None
        away_score = _score_value(away_c.get("score")) if completed else None
        date_utc = correct_date(league_key, date_utc, home, away)
        out.append(_normalize_row(
            league_key,
            home=home,
            away=away,
            date_utc=date_utc,
            home_score=home_score,
            away_score=away_score,
            round_num=None,
            completed=completed,
        ))
    return out


def _provider_order() -> list[str]:
    try:
        import source_settings
        order = source_settings.provider_order("soccer", kind="fixtures")
        if order:
            return order
    except Exception:
        pass
    return ["fixturedownload", "openfootball", "football_data_org", "api_sports", "espn"]


def get_feed(league_key: str) -> list[dict]:
    """All fixtures for a soccer league, with corrected UTC kickoffs + scores."""
    try:
        import source_settings
        if not source_settings.feed_preference("soccer").get("enabled", True):
            return []
    except Exception:
        pass
    now = time.time()
    cached = _cache.get(league_key)
    from live_cache_bust import is_fresh
    if cached and is_fresh(cached["fetched_at"], CACHE_TTL_SECONDS):
        return cached["data"]

    leagues, correct_date = _leagues()
    cfg = leagues.get(league_key) or {}
    out: list[dict] = []
    for name in _provider_order():
        try:
            if name == "fixturedownload":
                out = _fetch_fixturedownload(league_key, cfg, correct_date)
            elif name == "openfootball":
                out = _fetch_openfootball(league_key, cfg, correct_date)
            elif name == "football_data_org":
                out = _fetch_football_data_org(league_key, cfg, correct_date)
            elif name == "api_sports":
                out = _fetch_api_sports(league_key, cfg, correct_date)
            elif name == "espn":
                out = _fetch_espn(league_key, cfg, correct_date)
            elif name == "football_data_csv":
                # Results/history CSV — not a live fixture feed.
                continue
            else:
                continue
        except Exception:
            logger.exception("soccer provider %s failed for %s", name, league_key)
            out = []
        if out:
            break

    if not out:
        if cached and cached.get("data"):
            return cached["data"]
        return out
    _cache[league_key] = {"fetched_at": now, "data": out}
    return out


def clear_cache() -> None:
    _cache.clear()


def _feed_index(league_key: str) -> dict:
    by_round = {}
    by_pair = {}
    for fx in get_feed(league_key):
        home = str(fx.get("home") or "").strip().lower()
        away = str(fx.get("away") or "").strip().lower()
        rnd = str(fx.get("round") or "")
        by_round[(home, away, rnd)] = fx
        by_pair.setdefault((home, away), []).append(fx)
    return {"by_round": by_round, "by_pair": by_pair}


def overlay_prediction_row(league_key: str, row: dict, index=None) -> dict:
    """Copy a prediction row and stamp live kickoff / score from the feed."""
    out = dict(row)
    if index is None:
        index = _feed_index(league_key)
    home = str(out.get("HomeTeam") or "").strip().lower()
    away = str(out.get("AwayTeam") or "").strip().lower()
    rnd = str(out.get("RoundNumber") or "")
    fx = index["by_round"].get((home, away, rnd))
    if fx is None:
        hits = index["by_pair"].get((home, away)) or []
        fx = hits[0] if len(hits) == 1 else None
    if fx is None:
        if fixture_is_finished(
            {"kickoff_iso": str(out.get("DateUtc") or ""), "sport_key": "soccer"},
            sport_key="soccer",
        ):
            out["completed"] = True
        return out
    out["DateUtc"] = fx.get("DateUtc") or out.get("DateUtc")
    out["home_score"] = fx.get("home_score")
    out["away_score"] = fx.get("away_score")
    out["completed"] = bool(fx.get("completed"))
    try:
        out["MatchDate"] = format_kickoff_ist(out.get("DateUtc") or "")
    except Exception:
        pass
    return out


def overlay_predictions(league_key: str, rows: list[dict]) -> list[dict]:
    index = _feed_index(league_key)
    return [overlay_prediction_row(league_key, row, index=index) for row in rows]


def split_soccer_rows(league_key: str, rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Upcoming/live vs finished, after live-feed overlay."""
    overlaid = overlay_predictions(league_key, rows)
    upcoming, _postponed, finished = split_fixture_board(overlaid, sport_key="soccer")
    upcoming.sort(key=lambda r: str(r.get("DateUtc") or ""))
    finished.sort(key=lambda r: str(r.get("DateUtc") or ""), reverse=True)
    return upcoming, finished
