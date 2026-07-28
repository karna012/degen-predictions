"""Shared Cricsheet JSON loading/parsing for the cricket pipeline.

Cricsheet schema notes (confirmed from actual downloaded files, see
cricsheet.org/format/json/):
  - top-level: meta, info, innings
  - info: teams (list of 2), players (dict team -> XI list), venue, dates,
    season, outcome (winner + by-runs/by-wickets, or "result": "tie"/"no
    result"), toss, overs (innings length, usually 20)
  - innings: list of 1-2 dicts, each {"team": name, "overs": [...]}; each
    over is {"over": <0-based index>, "deliveries": [...]}; each delivery
    has batter/bowler/non_striker, "runs": {"batter","extras","total"}, and
    an optional "wickets" list.

Over-phase split (powerplay/middle/death) uses the 0-based "over" index
directly rather than balls_per_over, since The Hundred's "overs" are
5-ball sets while MLC's are standard 6-ball overs -- using the set index
(0-19 either way, both are 20-set/20-over innings) keeps the phase
boundaries meaningful across both competitions without needing to convert
ball counts.
"""
import glob
import json
import os

import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cricket_config import COMPETITIONS, DEATH_OVERS_START, POWERPLAY_OVERS, RAW_DIR  # noqa: E402


def phase_of_over(over_idx, total_overs):
    """0-based over index -> 'powerplay' / 'middle' / 'death'."""
    if over_idx < POWERPLAY_OVERS:
        return "powerplay"
    death_start_idx = max(total_overs - (20 - (DEATH_OVERS_START - 1)), POWERPLAY_OVERS)
    # Simpler, robust rule: last 4 overs of a 20-over innings are "death";
    # scales proportionally for a shortened (rain-affected) innings.
    death_start_idx = max(POWERPLAY_OVERS, total_overs - 4)
    if over_idx >= death_start_idx:
        return "death"
    return "middle"


def iter_matches():
    """Yields (competition_key, match_dict) for every downloaded match,
    sorted chronologically across both competitions combined -- required
    for a leakage-free walk-forward feature build."""
    matches = []
    for comp_key in COMPETITIONS:
        comp_dir = os.path.join(RAW_DIR, comp_key)
        for path in sorted(glob.glob(os.path.join(comp_dir, "*.json"))):
            try:
                with open(path, encoding="utf-8") as f:
                    match = json.load(f)
            except Exception as exc:
                print(f"skipping unreadable match file {path}: {exc}")
                continue
            dates = match.get("info", {}).get("dates") or []
            if not dates:
                continue
            match["_competition"] = comp_key
            match["_date"] = dates[0]
            match["_path"] = path
            matches.append(match)
    matches.sort(key=lambda m: (m["_date"], m["_path"]))
    return matches


def innings_summary(innings):
    """Ball-by-ball -> per-innings totals, per-over totals/wickets, and
    per-player batting/bowling lines (runs, balls faced/bowled, dismissed).
    Returns a dict; never raises on malformed deliveries -- a delivery
    missing an expected key is skipped and counted, printed by the caller's
    missing-data report rather than silently ignored.
    """
    team = innings.get("team")
    total_runs = 0
    total_wickets = 0
    overs_data = []  # list of (over_idx, runs_this_over, wickets_this_over)
    batting = {}  # player -> {"runs": int, "balls": int, "phase_runs": {p:0}, "phase_balls": {p:0}}
    bowling = {}  # player -> {"runs_conceded": int, "balls": int, "wickets": int}
    pair_events = []  # list of (batter, bowler, runs_off_bat, is_dismissal)
    malformed = 0
    total_overs_in_innings = len(innings.get("overs", []))

    for over in innings.get("overs", []):
        over_idx = over.get("over", 0)
        over_runs = 0
        over_wkts = 0
        phase = phase_of_over(over_idx, total_overs_in_innings)
        for delivery in over.get("deliveries", []):
            try:
                batter = delivery["batter"]
                bowler = delivery["bowler"]
                runs = delivery["runs"]
                bat_runs = int(runs.get("batter", 0))
                total_this_ball = int(runs.get("total", 0))
                extras = delivery.get("extras", {})
                is_legal = "wides" not in extras and "noballs" not in extras
            except Exception:
                malformed += 1
                continue

            total_runs += total_this_ball
            over_runs += total_this_ball

            b = batting.setdefault(batter, {"runs": 0, "balls": 0,
                                             "phase_runs": {"powerplay": 0, "middle": 0, "death": 0},
                                             "phase_balls": {"powerplay": 0, "middle": 0, "death": 0}})
            b["runs"] += bat_runs
            if is_legal:
                b["balls"] += 1
                b["phase_runs"][phase] += bat_runs
                b["phase_balls"][phase] += 1

            bw = bowling.setdefault(bowler, {"runs_conceded": 0, "balls": 0, "wickets": 0})
            bw["runs_conceded"] += total_this_ball - int(extras.get("byes", 0)) - int(extras.get("legbyes", 0))
            if is_legal:
                bw["balls"] += 1

            dismissed = False
            for w in delivery.get("wickets", []) or []:
                total_wickets += 1
                over_wkts += 1
                dismissed = True
                out_player = w.get("player_out")
                if out_player and out_player in bowling.get(bowler, {}) is False:
                    pass
                if w.get("kind") not in ("run out",):  # run-outs aren't the bowler's dismissal
                    bw["wickets"] += 1

            pair_events.append((batter, bowler, bat_runs, dismissed))

        overs_data.append((over_idx, over_runs, over_wkts))

    return {
        "team": team, "total_runs": total_runs, "total_wickets": total_wickets,
        "overs_data": overs_data, "batting": batting, "bowling": bowling,
        "pair_events": pair_events, "malformed_deliveries": malformed,
    }


def match_result(match):
    """Winner (team name), or None for tie/no-result -- these are dropped
    from the classification target (same reasoning as MLB dropping
    forfeits: no real win/loss signal to learn from) but kept for the runs
    regressions, which don't need a winner."""
    outcome = match.get("info", {}).get("outcome", {})
    return outcome.get("winner")
