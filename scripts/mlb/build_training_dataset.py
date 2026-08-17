"""Builds a leakage-free, per-game training dataset for MLB win/loss
classification: one row per game, with every feature computed strictly from
information available *before* that game (Elo, rolling batting/pitching
EMAs, streaks, head-to-head, starter quality, rest days, park), and HomeWin
as the label.

This mirrors build_elo.py's chronological update loop but, instead of only
keeping the final rating snapshot, it records the pre-game feature values
for every single game along the way -- that snapshot-before-update is what
prevents the classic leakage bug of training on a feature that already
includes today's result.

Also writes:
  - data/mlb/pitcher_rolling.csv  (final starter rolling stats for live use)
  - data/mlb/park_home_win_rate.csv  (final park home-win priors)

Run from repo root: python scripts/mlb/build_training_dataset.py
Outputs data/mlb/training_dataset.csv
"""
import math
import os
import sys
from collections import deque

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_elo import expected_home_score, load_games  # noqa: E402
from mlb_config import DATA_DIR, ELO_INITIAL, ELO_K  # noqa: E402

LEAGUE_AVG = {"Runs": 4.5, "Hits": 8.6, "HR": 1.2, "SO": 8.4}
EMA_ALPHA = 0.02
PITCHER_RA_SEED = 4.5
PITCHER_EMA_ALPHA = 0.08
PARK_EMA_ALPHA = 0.02
MAX_REST_DAYS = 14.0
DEFAULT_REST_DAYS = 3.0


def _pitcher_quality(runs_allowed_ema):
    """Higher is better -- inverse of runs allowed, floored so debuts don't explode."""
    return 1.0 / max(float(runs_allowed_ema), 1.0)


def _safe_pitcher_id(value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    text = str(value).strip()
    return text or None


def build_dataset():
    games = load_games()
    has_box_cols = all(c in games.columns for c in
                        ("VisitorHits", "VisitorHR", "VisitorSO", "HomeHits", "HomeHR", "HomeSO"))
    has_pitcher_cols = all(c in games.columns for c in
                           ("HomeStartingPitcherID", "VisitorStartingPitcherID"))
    has_park = "ParkID" in games.columns
    has_day_night = "DayNight" in games.columns

    elo = {}
    batting_for = {}
    batting_against = {}
    last10 = {}
    current_streak = {}
    h2h_wins = {}
    last_game_date = {}
    pitcher_stats = {}  # id -> {starts, wins, runs_allowed_ema}
    park_home_win = {}  # park -> EMA of home wins
    rows = []

    def team_stats(team, key):
        return batting_for.get(team, dict(LEAGUE_AVG))[key], batting_against.get(team, dict(LEAGUE_AVG))[key]

    def pitcher_snapshot(pid):
        if not pid or pid not in pitcher_stats:
            return 0, 0.5, PITCHER_RA_SEED
        s = pitcher_stats[pid]
        wr = (s["wins"] / s["starts"]) if s["starts"] else 0.5
        return s["starts"], wr, s["runs_allowed_ema"]

    for row in games.itertuples(index=False):
        home, away = row.HomeTeam, row.VisitorTeam
        home_score, away_score = row.HomeScore, row.VisitorScore
        game_date = row.Date

        elo_home = elo.get(home, ELO_INITIAL)
        elo_away = elo.get(away, ELO_INITIAL)

        home_runs_for, home_runs_against = team_stats(home, "Runs")
        away_runs_for, away_runs_against = team_stats(away, "Runs")
        home_hits_for, _ = team_stats(home, "Hits")
        away_hits_for, _ = team_stats(away, "Hits")
        home_hr_for, _ = team_stats(home, "HR")
        away_hr_for, _ = team_stats(away, "HR")
        _, home_so_against = team_stats(home, "SO")
        _, away_so_against = team_stats(away, "SO")

        home_last10 = last10.get(home, deque(maxlen=10))
        away_last10 = last10.get(away, deque(maxlen=10))
        home_streak_before = current_streak.get(home, 0)
        away_streak_before = current_streak.get(away, 0)

        pair_key = tuple(sorted((home, away)))
        h2h_before = h2h_wins.get(pair_key, {})
        h2h_meetings_before = sum(h2h_before.values())
        home_h2h_rate = (h2h_before.get(home, 0) / h2h_meetings_before) if h2h_meetings_before else 0.5

        home_pid = _safe_pitcher_id(getattr(row, "HomeStartingPitcherID", None)) if has_pitcher_cols else None
        away_pid = _safe_pitcher_id(getattr(row, "VisitorStartingPitcherID", None)) if has_pitcher_cols else None
        home_starts, home_pwr, home_pra = pitcher_snapshot(home_pid)
        away_starts, away_pwr, away_pra = pitcher_snapshot(away_pid)
        pitcher_quality_diff = _pitcher_quality(home_pra) - _pitcher_quality(away_pra)
        pitcher_win_rate_diff = home_pwr - away_pwr
        pitcher_starts_diff = math.log1p(home_starts) - math.log1p(away_starts)

        def days_rest(team):
            prev = last_game_date.get(team)
            if prev is None:
                return DEFAULT_REST_DAYS
            delta = (pd.Timestamp(game_date) - pd.Timestamp(prev)).days
            return float(min(max(delta, 0), MAX_REST_DAYS))

        home_rest = days_rest(home)
        away_rest = days_rest(away)

        is_day = 0.0
        if has_day_night:
            dn = getattr(row, "DayNight", "")
            is_day = 1.0 if str(dn).upper() == "D" else 0.0

        park_id = _safe_pitcher_id(getattr(row, "ParkID", None)) if has_park else None
        park_rate = park_home_win.get(park_id, 0.54) if park_id else 0.54

        rows.append({
            "Date": game_date,
            "EloDiff": elo_home - elo_away,
            "HomeLast10WinRate": (sum(home_last10) / len(home_last10)) if home_last10 else 0.5,
            "AwayLast10WinRate": (sum(away_last10) / len(away_last10)) if away_last10 else 0.5,
            "HomeStreak": home_streak_before,
            "AwayStreak": away_streak_before,
            "H2HMeetings": h2h_meetings_before,
            "HomeH2HWinRate": home_h2h_rate,
            "RunsForDiff": home_runs_for - away_runs_for,
            "RunsAllowedDiff": away_runs_against - home_runs_against,
            "HitsForDiff": home_hits_for - away_hits_for,
            "HRForDiff": home_hr_for - away_hr_for,
            "SOInducedDiff": home_so_against - away_so_against,
            "PitcherQualityDiff": pitcher_quality_diff,
            "PitcherWinRateDiff": pitcher_win_rate_diff,
            "PitcherStartsDiff": pitcher_starts_diff,
            "HomeDaysRest": home_rest,
            "AwayDaysRest": away_rest,
            "IsDayGame": is_day,
            "ParkHomeWinRate": park_rate,
            "HomeWin": 1 if home_score > away_score else 0,
        })

        exp_home = expected_home_score(elo_home, elo_away)
        home_won = 1.0 if home_score > away_score else 0.0
        elo[home] = elo_home + ELO_K * (home_won - exp_home)
        elo[away] = elo_away + ELO_K * ((1 - home_won) - (1 - exp_home))

        home_hits = getattr(row, "HomeHits", None) if has_box_cols else None
        away_hits = getattr(row, "VisitorHits", None) if has_box_cols else None
        home_hr = getattr(row, "HomeHR", None) if has_box_cols else None
        away_hr = getattr(row, "VisitorHR", None) if has_box_cols else None
        home_so = getattr(row, "HomeSO", None) if has_box_cols else None
        away_so = getattr(row, "VisitorSO", None) if has_box_cols else None

        def _update(store, team, key, value):
            if value is None or pd.isna(value):
                return
            prev = store.setdefault(team, dict(LEAGUE_AVG))[key]
            store[team][key] = prev + EMA_ALPHA * (float(value) - prev)

        for team, r_for, r_against, h_for, h_against, hr_for, hr_against, so_for, so_against in (
            (home, home_score, away_score, home_hits, away_hits, home_hr, away_hr, home_so, away_so),
            (away, away_score, home_score, away_hits, home_hits, away_hr, home_hr, away_so, home_so),
        ):
            _update(batting_for, team, "Runs", r_for)
            _update(batting_against, team, "Runs", r_against)
            _update(batting_for, team, "Hits", h_for)
            _update(batting_against, team, "Hits", h_against)
            _update(batting_for, team, "HR", hr_for)
            _update(batting_against, team, "HR", hr_against)
            _update(batting_for, team, "SO", so_for)
            _update(batting_against, team, "SO", so_against)

        last10.setdefault(home, deque(maxlen=10)).append(1 if home_won else 0)
        last10.setdefault(away, deque(maxlen=10)).append(0 if home_won else 1)
        for team, won in ((home, home_won == 1.0), (away, home_won == 0.0)):
            streak = current_streak.get(team, 0)
            current_streak[team] = (streak + 1 if streak >= 0 else 1) if won else (streak - 1 if streak <= 0 else -1)

        h2h_wins.setdefault(pair_key, {})
        winner = home if home_won else away
        h2h_wins[pair_key][winner] = h2h_wins[pair_key].get(winner, 0) + 1

        last_game_date[home] = game_date
        last_game_date[away] = game_date

        if park_id:
            prev_park = park_home_win.get(park_id, 0.54)
            park_home_win[park_id] = prev_park + PARK_EMA_ALPHA * (home_won - prev_park)

        for pid, team_won, runs_allowed in (
            (home_pid, home_won == 1.0, away_score),
            (away_pid, home_won == 0.0, home_score),
        ):
            if not pid:
                continue
            s = pitcher_stats.setdefault(pid, {
                "starts": 0, "wins": 0, "runs_allowed_ema": PITCHER_RA_SEED,
            })
            prev_ra = s["runs_allowed_ema"]
            s["runs_allowed_ema"] = prev_ra + PITCHER_EMA_ALPHA * (float(runs_allowed) - prev_ra)
            s["starts"] += 1
            if team_won:
                s["wins"] += 1

    df = pd.DataFrame(rows)
    out_path = os.path.join(DATA_DIR, "training_dataset.csv")
    df.to_csv(out_path, index=False)
    print(f"Wrote {len(df)} training rows -> {out_path}")
    print(f"Home win rate in data: {df['HomeWin'].mean():.3f}")

    pitcher_rows = []
    for pid, s in sorted(pitcher_stats.items()):
        pitcher_rows.append({
            "PitcherID": pid,
            "Starts": s["starts"],
            "Wins": s["wins"],
            "WinRate": (s["wins"] / s["starts"]) if s["starts"] else 0.5,
            "RunsAllowedEMA": round(s["runs_allowed_ema"], 3),
            "Quality": round(_pitcher_quality(s["runs_allowed_ema"]), 4),
        })
    pitcher_path = os.path.join(DATA_DIR, "pitcher_rolling.csv")
    pd.DataFrame(pitcher_rows).to_csv(pitcher_path, index=False)
    print(f"Wrote {len(pitcher_rows)} pitcher rolling rows -> {pitcher_path}")

    park_rows = [{"ParkID": p, "HomeWinRate": round(r, 4)} for p, r in sorted(park_home_win.items())]
    park_path = os.path.join(DATA_DIR, "park_home_win_rate.csv")
    pd.DataFrame(park_rows).to_csv(park_path, index=False)
    print(f"Wrote {len(park_rows)} park priors -> {park_path}")
    return df


if __name__ == "__main__":
    build_dataset()
