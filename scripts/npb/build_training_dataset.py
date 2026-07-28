"""Builds a leakage-free, per-game training dataset for NPB win/loss
classification -- same approach as scripts/mlb/build_training_dataset.py,
minus the hits/HR/strikeouts features (not available from spaia.jp).

Run from repo root: python scripts/npb/build_training_dataset.py
Outputs data/npb/training_dataset.csv
"""
import os
import sys
from collections import deque

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_elo import expected_home_score, load_games  # noqa: E402
from npb_config import DATA_DIR, ELO_INITIAL, ELO_K  # noqa: E402

LEAGUE_AVG_RUNS = 4.0
RUNS_EMA_ALPHA = 0.02


def build_dataset():
    games = load_games()

    elo = {}
    avg_scored = {}
    avg_allowed = {}
    last10 = {}
    current_streak = {}
    h2h_wins = {}
    rows = []

    for row in games.itertuples(index=False):
        home, away = int(row.HTeamID), int(row.VTeamID)
        home_score, away_score = row.HScore, row.VScore

        elo_home = elo.get(home, ELO_INITIAL)
        elo_away = elo.get(away, ELO_INITIAL)

        home_runs_for = avg_scored.get(home, LEAGUE_AVG_RUNS)
        home_runs_against = avg_allowed.get(home, LEAGUE_AVG_RUNS)
        away_runs_for = avg_scored.get(away, LEAGUE_AVG_RUNS)
        away_runs_against = avg_allowed.get(away, LEAGUE_AVG_RUNS)

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
            "HomeWin": 1 if home_score > away_score else 0,
        })

        exp_home = expected_home_score(elo_home, elo_away)
        home_won = 1.0 if home_score > away_score else 0.0
        elo[home] = elo_home + ELO_K * (home_won - exp_home)
        elo[away] = elo_away + ELO_K * ((1 - home_won) - (1 - exp_home))

        for team, scored, allowed in ((home, home_score, away_score), (away, away_score, home_score)):
            prev_scored = avg_scored.get(team, LEAGUE_AVG_RUNS)
            prev_allowed = avg_allowed.get(team, LEAGUE_AVG_RUNS)
            avg_scored[team] = prev_scored + RUNS_EMA_ALPHA * (scored - prev_scored)
            avg_allowed[team] = prev_allowed + RUNS_EMA_ALPHA * (allowed - prev_allowed)

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
