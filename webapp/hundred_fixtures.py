"""Live/upcoming fixtures for The Hundred (Men's), sourced from Wikipedia's
"<year> The Hundred season" article via the public MediaWiki API (no key,
free, not blocked the way espncricinfo.com's own pages are -- see
cricket_predict.py's module docstring for the full trail of sources that
WERE tried and ruled out: ESPN's cricket scoreboard needs a per-edition
series ID with no working lookup endpoint, thehundred.com's schedule is
client-side-only, fixturedownload.com's cricket section covers BBL/IPL/
WBBL/World Cups but not The Hundred or MLC, cricapi.com-style APIs are
key-gated).

Wikipedia's season article has a very regular, actively edited
"Fixtures > Men" section for the league stage, plus a separate
"Knockout stages > Men" section (Eliminator + Final) once the group
stage ends. Each game is a {{Single-innings cricket match}} template
with date/team1/team2/venue always populated and score/result filled
only once the match finishes -- exactly the completed-vs-upcoming
distinction needed, with no scraping of JavaScript-rendered pages
(this is the wikitext from the public action=parse API).

2026 SEASON REBRAND: three of the eight men's teams were renamed under new
private ownership ahead of the 2026 season -- Oval Invincibles -> MI
London, Northern Superchargers -> Sunrisers Leeds, Manchester Originals ->
Manchester Super Giants (same franchise/home ground each time, confirmed
via the same Wikipedia article's "Teams and venues" table showing identical
home grounds). REBRAND_ALIAS folds the new name back to the Cricsheet
identity our Elo ratings/trained models already know, the same pattern as
mlb_config.py's TEAM_CODE_ALIAS for the Oakland->Athletics rename.

Degrades to an empty fixture list (never raises) if Wikipedia's page
structure changes, the page can't be found, or the request fails -- same
fail-soft contract as mlb_fixtures.py / npb_fixtures.py.
"""
import datetime
import logging
import re
import time

import requests

logger = logging.getLogger(__name__)

WIKI_API = "https://en.wikipedia.org/w/api.php"
CACHE_TTL_SECONDS = 20 * 60  # fixtures/results change at most a few times a day
LIVE_CACHE_TTL_SECONDS = 3 * 60  # finished scores land on Wikipedia within minutes
_cache = {}
# Wikimedia's API blocks requests without a descriptive User-Agent (their
# policy, not a key -- see https://meta.wikimedia.org/wiki/User-Agent_policy).
WIKI_HEADERS = {"User-Agent": "DegenPredictions/1.0 (https://degenpredictions.com; contact via site) requests"}

TEAM_CODE_NAMES = {
    "bp": "Birmingham Phoenix", "ls": "London Spirit", "msg": "Manchester Super Giants",
    "mil": "MI London", "sb": "Southern Brave", "sl": "Sunrisers Leeds",
    "tr": "Trent Rockets", "wf": "Welsh Fire",
}

# 2026-season display name -> Cricsheet/Elo-rated historical identity.
# Unlisted teams (Birmingham Phoenix, London Spirit, Southern Brave, Trent
# Rockets, Welsh Fire) keep their name -- no rebrand for those franchises.
REBRAND_ALIAS = {
    "MI London": "Oval Invincibles",
    "Sunrisers Leeds": "Northern Superchargers",
    "Manchester Super Giants": "Manchester Originals",
}

MATCH_BLOCK_RE = re.compile(r"\{\{Single-innings cricket match(.*?)\n\}\}", re.DOTALL)
FIELD_RE = re.compile(r"^\|[ \t]*(\w+)[ \t]*=[ \t]*(.*)$", re.MULTILINE)
TEAM_CODE_RE = re.compile(r"\{\{cr-ENG\|(\w+)\}\}")
WIKILINK_RE = re.compile(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]")


def _strip_wikilinks(text):
    return WIKILINK_RE.sub(r"\1", text or "").strip()


def _find_child_section_index(sections, parent_line, child_line="Men"):
    """Return the MediaWiki section index for ``parent > child`` (e.g.
    Fixtures > Men, or Knockout stages > Men). Parent must be level 2;
    child level 3. Returns None if either is missing."""
    parent_idx = None
    for s in sections:
        if s["line"] == parent_line and s["level"] == "2":
            parent_idx = s["index"]
            break
    if parent_idx is None:
        return None
    seen_parent = False
    for s in sections:
        if s["index"] == parent_idx:
            seen_parent = True
            continue
        if not seen_parent:
            continue
        if s["level"] == "2":
            break  # left the parent section
        if s["level"] == "3" and s["line"] == child_line:
            return s["index"]
    return None


def _section_wikitext(pageid, section_idx):
    wt_resp = requests.get(WIKI_API, params={
        "action": "parse", "pageid": pageid, "format": "json",
        "prop": "wikitext", "section": section_idx,
    }, timeout=20, headers=WIKI_HEADERS)
    wt_resp.raise_for_status()
    return wt_resp.json().get("parse", {}).get("wikitext", {}).get("*") or ""


def _fetch_fixtures_section_wikitext(year):
    """Finds this year's "<year> The Hundred season" article and returns
    concatenated wikitext for every men's Single-innings match:

    * Fixtures > Men (league / group stage)
    * Knockout stages > Men (Eliminator + Final — these live OUTSIDE
      Fixtures and were previously dropped, which emptied the board once
      the group stage ended)

    Resolved by title search + section index each call (cached) rather
    than a hardcoded page ID, so this keeps working next year without a
    code change."""
    search_resp = requests.get(WIKI_API, params={
        "action": "query", "list": "search", "srsearch": f"{year} The Hundred season",
        "format": "json",
    }, timeout=20, headers=WIKI_HEADERS)
    search_resp.raise_for_status()
    results = search_resp.json().get("query", {}).get("search", [])
    match = next((r for r in results if r["title"] == f"{year} The Hundred season"), None)
    if not match:
        return None
    pageid = match["pageid"]

    sections_resp = requests.get(WIKI_API, params={
        "action": "parse", "pageid": pageid, "format": "json", "prop": "sections",
    }, timeout=20, headers=WIKI_HEADERS)
    sections_resp.raise_for_status()
    sections = sections_resp.json().get("parse", {}).get("sections", [])

    chunks = []
    for parent in ("Fixtures", "Knockout stages"):
        idx = _find_child_section_index(sections, parent, "Men")
        if idx is None:
            continue
        chunk = _section_wikitext(pageid, idx)
        if chunk:
            chunks.append(chunk)
    if not chunks:
        return None
    return "\n".join(chunks)


def _parse_date(date_str, time_str):
    try:
        dt = datetime.datetime.strptime(date_str.strip(), "%d %B %Y")
        if time_str and time_str.strip():
            hh, mm = time_str.strip().split(":")
            dt = dt.replace(hour=int(hh), minute=int(mm))
        return dt
    except Exception:
        return None


def _parse_fixtures(wikitext):
    fixtures = []
    for block in MATCH_BLOCK_RE.findall(wikitext):
        fields = {m.group(1): m.group(2).strip() for m in FIELD_RE.finditer(block)}
        team1_code = TEAM_CODE_RE.search(fields.get("team1", "") or "")
        team2_code = TEAM_CODE_RE.search(fields.get("team2", "") or "")
        if not team1_code or not team2_code:
            continue
        team1 = TEAM_CODE_NAMES.get(team1_code.group(1))
        team2 = TEAM_CODE_NAMES.get(team2_code.group(1))
        if not team1 or not team2:
            continue

        match_dt = _parse_date(fields.get("date", ""), fields.get("time", ""))
        result = _strip_wikilinks(fields.get("result", ""))
        score1 = fields.get("score1", "").strip()
        score2 = fields.get("score2", "").strip()
        venue = _strip_wikilinks(fields.get("venue", ""))

        fixtures.append({
            "team1": team1, "team2": team2,
            "team1_elo_name": REBRAND_ALIAS.get(team1, team1),
            "team2_elo_name": REBRAND_ALIAS.get(team2, team2),
            "date": match_dt.strftime("%Y-%m-%d") if match_dt else fields.get("date", ""),
            "time": fields.get("time", ""),
            "venue": venue,
            "completed": bool(result) or bool(score1 and score2),
            "result": result or None,
            "score1": score1 or None, "score2": score2 or None,
        })
    return fixtures


def get_hundred_fixtures():
    """Every fixture (played and unplayed) for the current real-world
    Hundred season, or [] if the season's Wikipedia article can't be found/
    parsed (e.g. off-season, before the article for next year exists yet,
    or Wikipedia's template structure changes) -- never raises."""
    year = datetime.datetime.now().year
    cache_key = year
    now = time.time()
    cached = _cache.get(cache_key)
    ttl = CACHE_TTL_SECONDS
    if cached:
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        if any(
            (f.get("date") or "")[:10] == today and not f.get("completed")
            for f in cached["data"]
        ):
            ttl = LIVE_CACHE_TTL_SECONDS
        from live_cache_bust import is_fresh
        if is_fresh(cached["fetched_at"], ttl):
            return cached["data"]

    try:
        wikitext = _fetch_fixtures_section_wikitext(year)
        fixtures = _parse_fixtures(wikitext) if wikitext else []
    except Exception as exc:
        logger.warning("Hundred fixtures fetch/parse failed: %s", exc)
        fixtures = cached["data"] if cached else []

    _cache[cache_key] = {"data": fixtures, "fetched_at": now}
    return fixtures


def _slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def slug_for_fixture(fixture):
    """Stable identifier for a Wikipedia-sourced fixture -- these have no
    Cricsheet numeric match ID (they aren't in the historical archive yet),
    so team names + date stand in instead. Assumes at most one meeting
    between the same two teams on the same day, true for The Hundred's
    round-robin-plus-knockout format."""
    return f"{_slugify(fixture['team1'])}-vs-{_slugify(fixture['team2'])}-{fixture['date']}"


def get_fixture_by_slug(slug):
    for f in get_hundred_fixtures():
        if slug_for_fixture(f) == slug:
            return f
    return None


def get_upcoming_and_live(fixtures=None):
    """Splits get_hundred_fixtures() into just today's/future unplayed
    matches, sorted chronologically -- what the /hundred/fixtures page
    actually shows."""
    fixtures = fixtures if fixtures is not None else get_hundred_fixtures()
    import today_picks
    upcoming = [
        f for f in fixtures
        if today_picks.fixture_is_upcoming_or_live(f, sport_key="cricket")
    ]
    upcoming.sort(key=lambda f: (f["date"], f.get("time") or ""))
    return upcoming
