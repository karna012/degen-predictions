"""Live/upcoming ATP men's singles fixtures.

Primary: ESPN public scoreboard (works on typical cloud IPs; fail-soft).
Fallback: Wikipedia men's-singles bracket pages for tournaments listed in
TennisMyLife `ongoing_tourneys.csv` (unfinished RD pairs = upcoming).
"""
from __future__ import annotations

import datetime
import logging
import re
import time

import requests

logger = logging.getLogger(__name__)

# site.api.espn.com is often 403 from cloud IPs; the web API serves the same JSON.
ESPN_SCOREBOARD_URLS = (
    "https://site.web.api.espn.com/apis/site/v2/sports/tennis/atp/scoreboard",
    "https://site.api.espn.com/apis/site/v2/sports/tennis/atp/scoreboard",
)
TML_ONGOING_URL = "https://stats.tennismylife.org/data/ongoing_tourneys.csv"
WIKI_API = "https://en.wikipedia.org/w/api.php"

CACHE_TTL_SECONDS = 10 * 60
LIVE_CACHE_TTL_SECONDS = 30

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 DegenPredictions/1.0"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.espn.com/tennis/scoreboard",
}
WIKI_HEADERS = {
    "User-Agent": "DegenPredictions/1.0 (https://degenpredictions.com; contact via site)",
}

# TML short names -> Wikipedia event title stem (without year / – Men's singles).
TOURNEY_WIKI_ALIAS = {
    "Canada Masters": "National Bank Open",
    "Cincinnati Masters": "Cincinnati Open",
    "Madrid Masters": "Madrid Open",
    "Rome Masters": "Italian Open",
    "Monte Carlo Masters": "Monte-Carlo Masters",
    "Indian Wells Masters": "Indian Wells Open",
    "Miami Masters": "Miami Open",
    "Shanghai Masters": "Shanghai Masters",
    "Paris Masters": "Paris Masters",
    "Australian Open": "Australian Open",
    "French Open": "French Open",
    "Wimbledon": "Wimbledon Championships",
    "US Open": "US Open",
}

_cache = {}
FIELD_RE = re.compile(r"^\|[ \t]*([A-Za-z0-9\-]+)[ \t]*=[ \t]*(.*)$", re.M)
WIKILINK_RE = re.compile(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]")
BRACKET_RE = re.compile(
    r"\{\{(?:16TeamBracket-Compact-Tennis3-Byes|8TeamBracket-Tennis3-v2|4TeamBracket-Tennis3|"
    r"16TeamBracket-Tennis3|8TeamBracket-Tennis|2TeamBracket)(.*?)\n\}\}",
    re.S | re.I,
)


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def slug_for_fixture(fixture: dict) -> str:
    return (
        f"{_slugify(fixture['player1'])}-vs-{_slugify(fixture['player2'])}"
        f"-{fixture.get('date') or 'tbd'}-{_slugify(fixture.get('match_id') or '')}"
    )


def _has_live(data) -> bool:
    for event in data.get("events") or []:
        for grouping in event.get("groupings") or []:
            for comp in grouping.get("competitions") or []:
                state = ((comp.get("status") or {}).get("type") or {}).get("state")
                if state == "in":
                    return True
    return False


def _espn_urls_ordered() -> list[str]:
    """Honor admin fixtures_primary (espn_web vs espn_site)."""
    urls = list(ESPN_SCOREBOARD_URLS)
    try:
        import source_settings
        order = source_settings.provider_order("tennis", kind="fixtures")
        if order and order[0] == "espn_site":
            urls = list(reversed(urls))
    except Exception:
        pass
    return urls


def _fetch_espn(dates: str | None = None) -> dict:
    key = f"espn:{dates or 'current'}"
    now = time.time()
    cached = _cache.get(key)
    if cached:
        ttl = LIVE_CACHE_TTL_SECONDS if _has_live(cached["data"]) else CACHE_TTL_SECONDS
        from live_cache_bust import is_fresh
        if is_fresh(cached["fetched_at"], ttl):
            return cached["data"]
    last_err = None
    params = {"dates": dates} if dates else None
    import feed_store
    ttl = LIVE_CACHE_TTL_SECONDS if (cached and _has_live(cached["data"])) else CACHE_TTL_SECONDS
    for url in _espn_urls_ordered():
        data = feed_store.get_json(
            f"tennis:{key}:{url.split('/')[-2]}",
            url,
            params=params,
            headers=HEADERS,
            timeout=12,
            retries=2,
            ttl=ttl if cached else 0,
            count_items=lambda d: len((d or {}).get("events") or []),
        )
        if isinstance(data, dict) and (data.get("events") or last_err is None):
            _cache[key] = {"data": data, "fetched_at": now}
            return data
    logger.warning("ESPN ATP scoreboard failed: %s", last_err)
    if cached:
        return cached["data"]
    return {"events": []}


def _is_qualifying(round_name: str) -> bool:
    return "qualif" in (round_name or "").lower()


def _parse_espn_competition(comp: dict, tournament: dict) -> dict | None:
    competitors = comp.get("competitors") or []
    if len(competitors) < 2:
        return None
    try:
        a = next(c for c in competitors if c.get("homeAway") == "away")
        b = next(c for c in competitors if c.get("homeAway") == "home")
    except StopIteration:
        a, b = competitors[0], competitors[1]

    def _name(c):
        ath = c.get("athlete") or {}
        return (ath.get("displayName") or (c.get("team") or {}).get("displayName") or "").strip()

    p1, p2 = _name(a), _name(b)
    if not p1 or not p2:
        return None

    status = (comp.get("status") or {}).get("type") or {}
    state = status.get("state") or ""
    name = status.get("name") or ""
    completed = state == "post" or name in (
        "STATUS_FINAL", "STATUS_RETIRED", "STATUS_WALKOVER", "STATUS_DEFAULT",
    )
    is_live = state == "in"
    round_name = (comp.get("round") or {}).get("displayName") or ""
    if _is_qualifying(round_name):
        return None

    venue = comp.get("venue") or tournament.get("venue") or {}
    venue_name = venue.get("fullName") or venue.get("displayName") or ""
    court = venue.get("court") or ""
    if court:
        venue_name = f"{venue_name} ({court})" if venue_name else court

    date_raw = comp.get("date") or comp.get("startDate") or tournament.get("date") or ""
    day, time_str, kickoff_iso = "", "", ""
    try:
        dt = datetime.datetime.fromisoformat(date_raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        utc = dt.astimezone(datetime.timezone.utc)
        day = utc.strftime("%Y-%m-%d")
        time_str = utc.strftime("%H:%M")
        kickoff_iso = utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        day = (date_raw or "")[:10]

    winner_name = p1 if a.get("winner") else (p2 if b.get("winner") else None)
    score_str = None
    ls1, ls2 = a.get("linescores") or [], b.get("linescores") or []
    first, second = (ls1, ls2)
    if completed and winner_name == p2:
        first, second = ls2, ls1
    if first and second:
        parts = []
        for s1, s2 in zip(first, second):
            v1, v2 = s1.get("value"), s2.get("value")
            if v1 is None or v2 is None:
                continue
            parts.append(f"{int(v1)}-{int(v2)}")
        if parts:
            score_str = " ".join(parts)
    if (winner_name or score_str) and not is_live:
        completed = True

    return {
        "match_id": str(comp.get("id") or ""),
        "player1": p1,
        "player2": p2,
        "tournament": tournament.get("name") or tournament.get("shortName") or "",
        "round": round_name,
        "date": day,
        "time": time_str,
        "kickoff_iso": kickoff_iso,
        "venue": venue_name,
        "completed": completed,
        "is_live": is_live,
        "postponed": "POSTPONED" in name or "SUSPEND" in name,
        "winner": winner_name,
        "score": score_str,
        "status_label": status.get("description") or name or state,
        "surface": None,
        "level": None,
        "source": "espn",
    }


def _espn_events_to_fixtures(data: dict) -> list:
    out = []
    seen = set()
    for event in data.get("events") or []:
        for grouping in event.get("groupings") or []:
            gname = ((grouping.get("grouping") or {}).get("displayName") or "").lower()
            if "women" in gname or "double" in gname:
                continue
            if gname and "men" not in gname:
                continue
            for comp in grouping.get("competitions") or []:
                row = _parse_espn_competition(comp, event)
                if not row:
                    continue
                key = (row["match_id"], row["player1"], row["player2"], row["date"])
                if key in seen:
                    continue
                seen.add(key)
                out.append(row)
    return out


def _strip_wiki_team(raw: str) -> str:
    text = WIKILINK_RE.sub(r"\1", raw or "")
    text = re.sub(r"\{\{[^}]*\}\}", "", text)
    text = re.sub(r"'{2,}", "", text)
    text = re.sub(r"&nbsp;?", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _bracket_set_pairs(fields: dict, rd: int, a: int, b: int) -> list[tuple[int, int]]:
    pairs = []
    for s in (1, 2, 3, 4, 5):
        sa = (fields.get(f"RD{rd}-score{a}-{s}") or "").strip()
        sb = (fields.get(f"RD{rd}-score{b}-{s}") or "").strip()
        if not sa and not sb:
            continue
        try:
            pairs.append((int(sa or 0), int(sb or 0)))
        except ValueError:
            continue
    return pairs


def _parse_bracket_matches(wikitext: str, tournament: str, surface: str, level: str) -> list:
    """Tennis bracket templates -> upcoming and completed singles fixtures."""
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    out = []
    for block in BRACKET_RE.findall(wikitext or ""):
        fields = {m.group(1): m.group(2).strip() for m in FIELD_RE.finditer(block)}
        rounds = sorted({
            int(m.group(1))
            for key in fields
            for m in [re.match(r"RD(\d+)-team\d+", key)]
            if m
        })
        for rd in rounds:
            rd_label = fields.get(f"RD{rd}") or f"R{rd}"
            idxs = sorted({
                int(m.group(1))
                for key in fields
                for m in [re.match(rf"RD{rd}-team(\d+)$", key)]
                if m
            })
            for i in range(0, max(idxs or [0]), 2):
                a, b = i + 1, i + 2
                t1 = _strip_wiki_team(fields.get(f"RD{rd}-team{a}", ""))
                t2 = _strip_wiki_team(fields.get(f"RD{rd}-team{b}", ""))
                if not t1 or not t2:
                    continue
                sets = _bracket_set_pairs(fields, rd, a, b)
                mid = f"wiki-{_slugify(tournament)}-rd{rd}-{a}-{b}"
                row = {
                    "match_id": mid,
                    "player1": t1,
                    "player2": t2,
                    "tournament": tournament,
                    "round": rd_label,
                    "date": today,
                    "time": "",
                    "venue": "",
                    "is_live": False,
                    "postponed": False,
                    "surface": surface,
                    "level": level,
                    "source": "wikipedia",
                }
                if not sets:
                    row.update({
                        "completed": False,
                        "winner": None,
                        "score": None,
                        "status_label": "Scheduled",
                    })
                    out.append(row)
                    continue
                t1_sets = sum(1 for ga, gb in sets if ga > gb)
                t2_sets = sum(1 for ga, gb in sets if gb > ga)
                if t1_sets == t2_sets:
                    # Incomplete / live set — keep pending
                    row.update({
                        "completed": False,
                        "winner": None,
                        "score": " ".join(f"{ga}-{gb}" for ga, gb in sets),
                        "status_label": "In progress",
                        "is_live": True,
                    })
                    out.append(row)
                    continue
                if t1_sets > t2_sets:
                    winner = t1
                    score = " ".join(f"{ga}-{gb}" for ga, gb in sets)
                else:
                    winner = t2
                    score = " ".join(f"{gb}-{ga}" for ga, gb in sets)
                row.update({
                    "completed": True,
                    "winner": winner,
                    "score": score,
                    "status_label": "Final",
                })
                out.append(row)
    return out


def _ongoing_meta() -> list[dict]:
    """Distinct ongoing tour-level events from TML."""
    key = "tml_ongoing"
    now = time.time()
    cached = _cache.get(key)
    from live_cache_bust import is_fresh
    if cached and is_fresh(cached["fetched_at"], CACHE_TTL_SECONDS):
        return cached["data"]
    try:
        resp = requests.get(TML_ONGOING_URL, headers=WIKI_HEADERS, timeout=30)
        resp.raise_for_status()
        import csv
        import io
        rows = list(csv.DictReader(io.StringIO(resp.text)))
    except Exception as exc:
        logger.warning("TML ongoing_tourneys failed: %s", exc)
        rows = cached["data_rows"] if cached else []
        meta = cached["data"] if cached else []
        return meta

    by = {}
    for row in rows:
        level = str(row.get("tourney_level") or "")
        if level not in {"G", "M", "500", "250"}:
            continue
        name = (row.get("tourney_name") or "").strip()
        if not name:
            continue
        by[name] = {
            "tourney_name": name,
            "surface": (row.get("surface") or "Hard").title(),
            "level": level,
        }
    meta = list(by.values())
    _cache[key] = {"data": meta, "data_rows": rows, "fetched_at": now}
    return meta


def get_completed_matches() -> list[dict]:
    """Completed matches from currently-ongoing tournaments, from the same
    ongoing_tourneys.csv already fetched by _ongoing_meta() (Sackmann-format
    columns: winner_name/loser_name/score/tourney_date) -- results-
    verification fallback for combo_results.py's settlement lookup, not a
    fixtures source. Reuses the cached raw rows rather than refetching."""
    _ongoing_meta()  # ensures _cache["tml_ongoing"]["data_rows"] is populated/fresh
    cached = _cache.get("tml_ongoing") or {}
    rows = cached.get("data_rows") or []
    out = []
    for row in rows:
        winner = (row.get("winner_name") or "").strip()
        loser = (row.get("loser_name") or "").strip()
        if not winner or not loser:
            continue
        out.append({
            "winner_name": winner,
            "loser_name": loser,
            "score": (row.get("score") or "").strip(),
            "date": row.get("tourney_date") or "",
            "round": row.get("round") or "",
            "tourney_name": row.get("tourney_name") or "",
        })
    return out


def _wiki_fixtures_for_ongoing() -> list:
    year = datetime.datetime.now().year
    out = []
    for meta in _ongoing_meta():
        stem = TOURNEY_WIKI_ALIAS.get(meta["tourney_name"], meta["tourney_name"])
        title = f"{year} {stem} – Men's singles"
        # Also try ASCII hyphen variants
        titles = [title, title.replace("–", "-"), f"{year} {stem} - Men's singles"]
        wikitext = None
        used = None
        for t in titles:
            try:
                resp = requests.get(
                    WIKI_API,
                    params={"action": "parse", "page": t, "prop": "wikitext", "format": "json"},
                    headers=WIKI_HEADERS,
                    timeout=25,
                )
                resp.raise_for_status()
                data = resp.json()
                if "error" in data:
                    continue
                wikitext = data.get("parse", {}).get("wikitext", {}).get("*")
                used = t
                if wikitext:
                    break
            except Exception as exc:
                logger.warning("wiki parse %s failed: %s", t, exc)
        if not wikitext:
            # search fallback
            try:
                sr = requests.get(
                    WIKI_API,
                    params={
                        "action": "query", "list": "search",
                        "srsearch": f"{year} {stem} Men's singles",
                        "format": "json",
                    },
                    headers=WIKI_HEADERS,
                    timeout=20,
                )
                sr.raise_for_status()
                hits = sr.json().get("query", {}).get("search") or []
                hit = next((h for h in hits if "Men" in h["title"] and "singles" in h["title"].lower()), None)
                if hit:
                    resp = requests.get(
                        WIKI_API,
                        params={"action": "parse", "pageid": hit["pageid"], "prop": "wikitext", "format": "json"},
                        headers=WIKI_HEADERS,
                        timeout=25,
                    )
                    resp.raise_for_status()
                    wikitext = resp.json().get("parse", {}).get("wikitext", {}).get("*")
                    used = hit["title"]
            except Exception as exc:
                logger.warning("wiki search failed: %s", exc)
        if not wikitext:
            continue
        display = used or f"{year} {stem}"
        out.extend(_parse_bracket_matches(
            wikitext,
            tournament=display.replace(" – Men's singles", "").replace(" - Men's singles", ""),
            surface=meta["surface"] if meta["surface"] != "Carpet" else "Hard",
            level=meta["level"],
        ))
    return out


def get_fixtures(days_back: int = 2, days_ahead: int = 7) -> list:
    try:
        import source_settings
        pref = source_settings.feed_preference("tennis")
        if not pref.get("enabled", True):
            return []
        order = source_settings.provider_order("tennis", kind="fixtures")
    except Exception:
        order = ["espn_web", "wikipedia"]

    today = datetime.datetime.now(datetime.timezone.utc).date()
    start = today - datetime.timedelta(days=days_back)
    end = today + datetime.timedelta(days=days_ahead)
    date_range = f"{start.strftime('%Y%m%d')}-{end.strftime('%Y%m%d')}"

    fixtures = []
    want_espn = any(x.startswith("espn") for x in order) or not order
    want_wiki = "wikipedia" in order or not order
    if want_espn:
        for data in (_fetch_espn(None), _fetch_espn(date_range)):
            fixtures.extend(_espn_events_to_fixtures(data))

    # Always merge Wikipedia unless admin disabled the sport entirely —
    # ESPN often 403s and finals drop off the live scoreboard.
    if want_wiki:
        try:
            fixtures.extend(_wiki_fixtures_for_ongoing())
        except Exception as exc:
            logger.warning("Wikipedia ATP fallback failed: %s", exc)

    def _rank(row: dict) -> tuple:
        return (
            int(bool(row.get("completed"))),
            int(bool(row.get("is_live"))),
            int(bool(row.get("score"))),
            int((row.get("source") or "") == "espn"),
            int(bool(row.get("time"))),
        )

    dedup = {}
    for f in fixtures:
        names = "|".join(sorted([
            (f.get("player1") or "").lower(),
            (f.get("player2") or "").lower(),
        ]))
        key = names + "|" + ((f.get("date") or "")[:10])
        prev = dedup.get(key)
        if prev is None or _rank(f) >= _rank(prev):
            dedup[key] = f
    out = list(dedup.values())
    out.sort(key=lambda m: (m.get("date") or "", m.get("time") or ""))
    return out


def _pair_key(row: dict) -> str:
    return "|".join(sorted([
        (row.get("player1") or "").lower(),
        (row.get("player2") or "").lower(),
    ]))


def _recent_completed_pairs(rows=None, *, days: int = 4) -> set[str]:
    cutoff = (
        datetime.datetime.now(datetime.timezone.utc).date()
        - datetime.timedelta(days=days)
    ).isoformat()
    src = list(rows) if rows is not None else get_fixtures()
    out = set()
    for f in src:
        if not f.get("completed"):
            continue
        day = (f.get("date") or "")[:10]
        if day and day < cutoff:
            continue
        key = _pair_key(f)
        if key.strip("|"):
            out.add(key)
    return out


def get_upcoming_and_live(fixtures=None) -> list:
    # Current ESPN scoreboard only — the 7-day range feed stamps leftover
    # draw slots with a placeholder time and keeps finished matches scheduled.
    import today_picks
    if fixtures is None:
        espn_rows = _espn_events_to_fixtures(_fetch_espn(None))
        if not espn_rows:
            today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d")
            espn_rows = _espn_events_to_fixtures(_fetch_espn(today))
        fixtures = espn_rows
        done = _recent_completed_pairs()
    else:
        done = _recent_completed_pairs(fixtures)
    out = []
    for f in fixtures:
        # Wikipedia brackets stamp every draw slot as "today" with no time.
        # Those are not real upcoming fixtures — ESPN (or wiki in-play/finals) is.
        if (f.get("source") == "wikipedia"
                and not f.get("completed")
                and not f.get("is_live")):
            continue
        if f.get("completed"):
            continue
        if _pair_key(f) in done:
            continue
        if not f.get("is_live") and not (f.get("time") or f.get("kickoff_iso")):
            continue
        if today_picks.fixture_is_upcoming_or_live(f, sport_key="tennis"):
            out.append(f)
    out.sort(key=lambda m: (0 if m.get("is_live") else 1, m.get("date") or "", m.get("time") or ""))
    return out


def get_fixture_by_slug(slug: str):
    for f in get_fixtures():
        if slug_for_fixture(f) == slug:
            return f
    return None
