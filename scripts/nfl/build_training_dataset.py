"""Builds a leakage-free, per-game training dataset for NFL win/loss
classification -- same structure as scripts/nba/build_training_dataset.py.
Rest days come directly from nflverse's own HomeRest/AwayRest columns
(already correctly computed there) rather than derived from date deltas.
"Short week" (rest < 6 days -- a Thursday game off a Sunday, the closest
NFL analog to NBA's back-to-back) replaces the back-to-back feature.

Run from repo root: python scripts/nfl/build_training_dataset.py
Outputs data/nfl/training_dataset.csv
"""
import os
import sys
from collections import deque

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_elo import derive_home_advantage, expected_home_score, load_games  # noqa: E402
from nfl_config import DATA_DIR, ELO_INITIAL, ELO_K  # noqa: E402

EMA_ALPHA = 0.06
LEAGUE_AVG_POINTS = 22.5
DEFAULT_REST_DAYS = 7.0
SHORT_WEEK_MAX_DAYS = 5.0


def _load_games_with_rest():
    path = os.path.join(DATA_DIR, "raw", "games.csv")
    df = pd.read_csv(path)
    df["Date"] = pd.to_datetime(df["Date"])
    return df.sort_values("Date").reset_index(drop=True)


def build_dataset():
    games = _load_games_with_rest()
    home_advantage = derive_home_advantage(games)

    elo = {}
    scoring_for = {}
    scoring_against = {}
    last10 = {}
    current_streak = {}
    h2h_wins = {}
    rows = []

    def team_stat(store, team, seed=LEAGUE_AVG_POINTS):
        return store.get(team, {"Points": seed})["Points"]

    for row in games.itertuples(index=False):
        home, away = row.HomeTeam, row.VisitorTeam
        home_score, away_score = row.HomeScore, row.VisitorScore

        elo_home = elo.get(home, ELO_INITIAL)
        elo_away = elo.get(away, ELO_INITIAL)

        home_pts_for = team_stat(scoring_for, home)
        away_pts_for = team_stat(scoring_for, away)
        home_pts_against = team_stat(scoring_against, home)
        away_pts_against = team_stat(scoring_against, away)

        home_last10 = last10.get(home, deque(maxlen=10))
        away_last10 = last10.get(away, deque(maxlen=10))
        home_streak_before = current_streak.get(home, 0)
        away_streak_before = current_streak.get(away, 0)

        pair_key = tuple(sorted((home, away)))
        h2h_before = h2h_wins.get(pair_key, {})
        h2h_meetings_before = sum(h2h_before.values())
        home_h2h_rate = (h2h_before.get(home, 0) / h2h_meetings_before) if h2h_meetings_before else 0.5

        home_rest = float(row.HomeRest) if pd.notna(row.HomeRest) else DEFAULT_REST_DAYS
        away_rest = float(row.AwayRest) if pd.notna(row.AwayRest) else DEFAULT_REST_DAYS

        rows.append({
            "Date": row.Date,
            "EloDiff": elo_home - elo_away,
            "HomeLast10WinRate": (sum(home_last10) / len(home_last10)) if home_last10 else 0.5,
            "AwayLast10WinRate": (sum(away_last10) / len(away_last10)) if away_last10 else 0.5,
            "HomeStreak": home_streak_before,
            "AwayStreak": away_streak_before,
            "H2HMeetings": h2h_meetings_before,
            "HomeH2HWinRate": home_h2h_rate,
            "PointsForDiff": home_pts_for - away_pts_for,
            "PointsAllowedDiff": away_pts_against - home_pts_against,
            "HomeDaysRest": home_rest,
            "AwayDaysRest": away_rest,
            "HomeShortWeek": 1.0 if home_rest < SHORT_WEEK_MAX_DAYS else 0.0,
            "AwayShortWeek": 1.0 if away_rest < SHORT_WEEK_MAX_DAYS else 0.0,
            "HomeWin": 1 if home_score > away_score else 0,
        })

        exp_home = expected_home_score(elo_home, elo_away, home_advantage)
        home_won = 1.0 if home_score > away_score else 0.0
        elo[home] = elo_home + ELO_K * (home_won - exp_home)
        elo[away] = elo_away + ELO_K * ((1 - home_won) - (1 - exp_home))

        for team, pts_for, pts_against in ((home, home_score, away_score), (away, away_score, home_score)):
            prev_for = scoring_for.setdefault(team, {"Points": LEAGUE_AVG_POINTS})["Points"]
            scoring_for[team]["Points"] = prev_for + EMA_ALPHA * (float(pts_for) - prev_for)
            prev_against = scoring_against.setdefault(team, {"Points": LEAGUE_AVG_POINTS})["Points"]
            scoring_against[team]["Points"] = prev_against + EMA_ALPHA * (float(pts_against) - prev_against)

        last10.setdefault(home, deque(maxlen=10)).append(1 if home_won else 0)
        last10.setdefault(away, deque(maxlen=10)).append(0 if home_won else 1)
        for team, won in ((home, home_won == 1.0), (away, home_won == 0.0)):
            streak = current_streak.get(team, 0)
            current_streak[team] = (streak + 1 if streak >= 0 else 1) if won else (streak - 1 if streak <= 0 else -1)

        h2h_wins.setdefault(pair_key, {})
        winner = home if home_won else away
        h2h_wins[pair_key][winner] = h2h_wins[pair_key].get(winner, 0) + 1

    df = pd.DataFrame(rows)
    os.makedirs(DATA_DIR, exist_ok=True)
    out_path = os.path.join(DATA_DIR, "training_dataset.csv")
    df.to_csv(out_path, index=False)
    print(f"Wrote {len(df)} training rows -> {out_path}")
    print(f"Home win rate in data: {df['HomeWin'].mean():.3f}")
    return df


if __name__ == "__main__":
    build_dataset()
