"""Load season player stats for soccer leagues (Understat or ASA/MLS)."""
from __future__ import annotations

import json
import os
from functools import lru_cache

from leagues import LEAGUES, UNDERSTAT_SEASON_MAP, league_seasons

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def available_player_seasons(league_key: str) -> list[str]:
    cfg = LEAGUES[league_key]
    players_dir = os.path.join(BASE_DIR, cfg["data_dir"], "players")
    if not os.path.isdir(players_dir):
        return []
    seasons = []
    if cfg.get("asa_league"):
        candidates = league_seasons(cfg)
    elif cfg.get("understat_league"):
        candidates = [u for _, u in UNDERSTAT_SEASON_MAP.items()]
    else:
        return []
    for s in candidates:
        path = os.path.join(players_dir, f"players_{s}.json")
        if os.path.exists(path):
            seasons.append(str(s))
    return seasons


def latest_player_season(league_key: str) -> str | None:
    seasons = available_player_seasons(league_key)
    return seasons[-1] if seasons else None


@lru_cache(maxsize=32)
def load_players(league_key: str, season: str) -> tuple:
    """Return immutable tuple of player dicts for caching."""
    cfg = LEAGUES[league_key]
    path = os.path.join(BASE_DIR, cfg["data_dir"], "players", f"players_{season}.json")
    if not os.path.exists(path):
        return tuple()
    with open(path, encoding="utf-8") as f:
        players = json.load(f)
    # Normalize numeric fields and a display name
    out = []
    for p in players:
        row = dict(p)
        row["player_name"] = (
            p.get("player_name") or p.get("player") or p.get("id") or "?"
        )
        # Understat uses player_name sometimes as nested; common key is just there
        for key in ("goals", "assists", "xG", "xA", "shots", "key_passes", "time",
                    "goals_added", "xG_chain", "goals_minus_xG", "shots_on_target",
                    "pass_completion_pct", "passes_over_expected"):
            if key in row and row[key] is not None:
                try:
                    row[key] = float(row[key])
                except (TypeError, ValueError):
                    row[key] = 0.0
        mins = float(row.get("time") or 0)
        row["minutes"] = mins
        row["per90_goals"] = (row.get("goals") or 0) * 90 / mins if mins >= 90 else 0.0
        row["per90_xG"] = (row.get("xG") or 0) * 90 / mins if mins >= 90 else 0.0
        row["per90_xA"] = (row.get("xA") or 0) * 90 / mins if mins >= 90 else 0.0
        row["finishing"] = (row.get("goals") or 0) - (row.get("xG") or 0)
        out.append(row)
    return tuple(out)


def players_table(
    league_key: str,
    season: str | None = None,
    team: str = "",
    position: str = "",
    q: str = "",
    sort: str = "goals_added",
    min_minutes: int = 200,
) -> dict:
    seasons = available_player_seasons(league_key)
    if not seasons:
        return {
            "players": [], "seasons": [], "season": None, "teams": [],
            "positions": [], "sort": sort, "min_minutes": min_minutes,
            "source": None, "leaders": {},
        }
    season = season if season in seasons else seasons[-1]
    players = [dict(p) for p in load_players(league_key, season)]
    source = "asa" if any(p.get("source") == "asa" for p in players) else "understat"

    teams = sorted({p.get("team_title") or "" for p in players if p.get("team_title")})
    positions = sorted({p.get("position") or "" for p in players if p.get("position")})

    filtered = []
    for p in players:
        if (p.get("minutes") or 0) < min_minutes:
            continue
        if team and (p.get("team_title") or "").lower() != team.lower():
            continue
        if position and (p.get("position") or "").upper() != position.upper():
            continue
        if q and q.lower() not in (p.get("player_name") or "").lower():
            continue
        filtered.append(p)

    sort_key = sort if sort in {
        "goals", "assists", "xG", "xA", "xG_chain", "goals_added", "shots",
        "key_passes", "minutes", "per90_goals", "per90_xG", "finishing",
        "passes_over_expected", "player_name",
    } else ("goals_added" if source == "asa" else "xG")

    reverse = sort_key != "player_name"
    filtered.sort(key=lambda r: (r.get(sort_key) is None, r.get(sort_key) or 0), reverse=reverse)

    # Leaderboards (min 400') for the analysis header
    pool = [p for p in players if (p.get("minutes") or 0) >= 400]
    def top(metric, n=5):
        return sorted(pool, key=lambda r: r.get(metric) or 0, reverse=True)[:n]

    leaders = {
        "goals": top("goals"),
        "xG": top("xG"),
        "assists": top("assists"),
        "xA": top("xA"),
    }
    if source == "asa":
        leaders["goals_added"] = top("goals_added")
        leaders["finishing"] = top("finishing")

    return {
        "players": filtered,
        "seasons": seasons,
        "season": season,
        "teams": teams,
        "positions": positions,
        "sort": sort_key,
        "min_minutes": min_minutes,
        "source": source,
        "leaders": leaders,
        "team": team,
        "position": position,
        "q": q,
    }
