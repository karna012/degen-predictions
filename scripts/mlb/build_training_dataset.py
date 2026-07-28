"""Builds a leakage-free, per-game training dataset for MLB win/loss
classification: one row per game, with every feature computed strictly from
information available *before* that game (Elo, rolling batting/pitching
EMAs, streaks, head-to-head), and HomeWin as the label.

This mirrors build_elo.py's chronological update loop but, instead of only
keeping the final rating snapshot, it records the pre-game feature values
for every single game along the way -- that snapshot-before-update is what
prevents the classic leakage bug of training on a feature that already
includes today's result.

Run from repo root: python scripts/mlb/build_training_dataset.py
Outputs data/mlb/training_dataset.csv
"""
import os
import sys
from collections import deque

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_elo import expected_home_score, load_games  # noqa: E402
from mlb_config import DATA_DIR, ELO_INITIAL, ELO_K  # noqa: E402

LEAGUE_AVG = {"Runs": 4.5, "Hits": 8.6, "HR": 1.2, "SO": 8.4}
EMA_ALPHA = 0.02


def build_dataset():
    games = load_games()
    has_box_cols = all(c in games.columns for c in
                        ("VisitorHits", "VisitorHR", "VisitorSO", "HomeHits", "HomeHR", "HomeSO"))

    elo = {}
    batting_for = {}
    batting_against = {}
    last10 = {}
    current_streak = {}
    h2h_wins = {}  # (team_a, team_b) sorted tuple -> {team: wins}
    rows = []

    def team_stats(team, key):
        return batting_for.get(team, dict(LEAGUE_AVG))[key], batting_against.get(team, dict(LEAGUE_AVG))[key]

    for row in games.itertuples(index=False):
        home, away = row.HomeTeam, row.VisitorTeam
        home_score, away_score = row.HomeScore, row.VisitorScore

        elo_home = elo.get(home, ELO_INITIAL)
        elo_away = elo.get(away, ELO_INITIAL)

        home_runs_for, home_runs_against = team_stats(home, "Runs")
        away_runs_for, away_runs_against = team_stats(away, "Runs")
        home_hits_for, home_hits_against = team_stats(home, "Hits")
        away_hits_for, away_hits_against = team_stats(away, "Hits")
        home_hr_for, home_hr_against = team_stats(home, "HR")
        away_hr_for, away_hr_against = team_stats(away, "HR")
        home_so_for, home_so_against = team_stats(home, "SO")
        away_so_for, away_so_against = team_stats(away, "SO")

        home_last10 = last10.get(home, deque(maxlen=10))
        away_last10 = last10.get(away, deque(maxlen=10))
        home_streak_before = current_streak.get(home, 0)
        away_streak_before = current_streak.get(away, 0)

        pair_key = tuple(sorted((home, away)))
        h2h_before = h2h_wins.get(pair_key, {})
        h2h_meetings_before = sum(h2h_before.values())
        home_h2h_rate = (h2h_before.get(home, 0) / h2h_meetings_before) if h2h_meetings_before else 0.5

        rows.append({
            "Date": row.Date,
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

    df = pd.DataFrame(rows)
    out_path = os.path.join(DATA_DIR, "training_dataset.csv")
    df.to_csv(out_path, index=False)
    print(f"Wrote {len(df)} training rows -> {out_path}")
    print(f"Home win rate in data: {df['HomeWin'].mean():.3f}")
    return df


if __name__ == "__main__":
    build_dataset()
