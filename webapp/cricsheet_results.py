"""Cricsheet ball-by-ball match archives (cricsheet.org, free, no key) as a
results-verification fallback for combo_results.py's settlement lookup.

Not a fixtures source -- these per-competition archives are only updated
after matches finish (this project's own local training snapshot lagged by
months; the live cricsheet.org downloads themselves were verified within
days of the most recent match at implementation time). That lag is fine
for settlement, which already tolerates a multi-day pending window before
voiding a leg -- this is consulted only when the live fixture sources
(cricket_fd_fixtures / hundred_fixtures / cpl_fixtures) can't resolve a
score at all.
"""
from __future__ import annotations

import io
import json
import logging
import time
import zipfile

logger = logging.getLogger(__name__)

CRICSHEET_URLS = {
    "ipl": "https://cricsheet.org/downloads/ipl_json.zip",
    "bbl": "https://cricsheet.org/downloads/bbl_json.zip",
    "hundred": "https://cricsheet.org/downloads/hnd_json.zip",
    "cpl": "https://cricsheet.org/downloads/cpl_json.zip",
    "mlc": "https://cricsheet.org/downloads/mlc_json.zip",
}
# Archives are a few MB each and only change when new matches finish --
# no need to refetch more than a couple of times a day.
CACHE_TTL_SECONDS = 12 * 60 * 60
_cache: dict = {}


def _innings_total(innings: dict) -> int | None:
    try:
        return sum(
            d["runs"]["total"]
            for over in innings.get("overs") or []
            for d in over.get("deliveries") or []
        )
    except (KeyError, TypeError):
        return None


def _parse_match(raw: dict) -> dict | None:
    info = raw.get("info") or {}
    teams = info.get("teams") or []
    if len(teams) != 2:
        return None
    dates = info.get("dates") or []
    innings = raw.get("innings") or []
    scores = {inn.get("team"): _innings_total(inn) for inn in innings if inn.get("team")}
    outcome = info.get("outcome") or {}
    winner = outcome.get("winner")
    result_note = outcome.get("result")  # "tie" / "no result" when no winner
    return {
        "team1": teams[0],
        "team2": teams[1],
        "date": dates[-1] if dates else "",
        "completed": bool(winner or result_note),
        "winner_name": winner,
        "result_note": result_note,
        "score1": scores.get(teams[0]),
        "score2": scores.get(teams[1]),
        "event": (info.get("event") or {}).get("name"),
    }


def get_matches(league_id: str) -> list[dict]:
    """All completed matches for one competition, newest first. Empty list
    on any failure -- callers should treat this as best-effort."""
    url = CRICSHEET_URLS.get(league_id)
    if not url:
        return []
    cached = _cache.get(league_id)
    if cached:
        from live_cache_bust import is_fresh
        if is_fresh(cached["fetched_at"], CACHE_TTL_SECONDS):
            return cached["data"]
    import requests
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        matches = []
        for name in zf.namelist():
            if not name.endswith(".json"):
                continue
            try:
                parsed = _parse_match(json.loads(zf.read(name)))
            except Exception:
                continue
            if parsed:
                matches.append(parsed)
    except Exception as exc:
        logger.warning("Cricsheet fetch failed for %s: %s", league_id, exc)
        return cached["data"] if cached else []
    matches.sort(key=lambda m: m.get("date") or "", reverse=True)
    _cache[league_id] = {"data": matches, "fetched_at": time.time()}
    return matches
