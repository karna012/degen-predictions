"""Computes a chronological team Elo rating for NPB from the downloaded
spaia.jp schedules, plus rolling run-scoring/allowing averages, streaks, and
current-season record -- same approach as scripts/mlb/build_elo.py, minus
the hits/HR/strikeouts tracking (not available in this data source).

Run from repo root: python scripts/npb/build_elo.py
Outputs data/npb/elo_ratings.csv and data/npb/game_history.csv.
"""
import os
import sys
from collections import deque

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from npb_config import DATA_DIR, ELO_INITIAL, ELO_K, HOME_ADVANTAGE, REGULAR_SEASON_GAME_KINDS, TEAM_FULL_NAMES  # noqa: E402

RAW_DIR = os.path.join(DATA_DIR, "raw")
RUNS_EMA_ALPHA = 0.02
LEAGUE_AVG_RUNS = 4.0  # NPB tends to run slightly lower-scoring than MLB


def load_games():
    frames = []
    for name in sorted(os.listdir(RAW_DIR)):
        if not name.endswith(".csv"):
            continue
        frames.append(pd.read_csv(os.path.join(RAW_DIR, name)))
    if not frames:
        return pd.DataFrame()
    games = pd.concat(frames, ignore_index=True)
    games = games[games["GameKindID"].isin(REGULAR_SEASON_GAME_KINDS)]
    games = games[games["GameState"] == 2]
    games = games.dropna(subset=["DateJPN", "HTeamID", "VTeamID", "HScore", "VScore"])
    games["Date"] = pd.to_datetime(games["DateJPN"], format="%Y%m%d", errors="coerce")
    games = games.dropna(subset=["Date"])
    games = games.sort_values("Date").reset_index(drop=True)
    return games


def expected_home_score(elo_home, elo_away):
    return 1.0 / (1.0 + 10 ** (-((elo_home + HOME_ADVANTAGE) - elo_away) / 400.0))


def build_elo():
    games = load_games()
    print(f"{len(games)} completed regular-season games loaded across "
          f"{games['Date'].dt.year.nunique() if len(games) else 0} seasons")

    elo = {}
    team_names = {}
    games_played = {}
    avg_scored = {}
    avg_allowed = {}
    last10 = {}
    current_streak = {}
    last_date = {}
    history_rows = []

    for row in games.itertuples(index=False):
        home, away = int(row.HTeamID), int(row.VTeamID)
        team_names[home] = row.HomeTeamNameE
        team_names[away] = row.VisitorTeamNameE
        home_score, away_score = row.HScore, row.VScore

        elo_home = elo.get(home, ELO_INITIAL)
        elo_away = elo.get(away, ELO_INITIAL)
        exp_home = expected_home_score(elo_home, elo_away)
        home_won = 1.0 if home_score > away_score else 0.0
        elo[home] = elo_home + ELO_K * (home_won - exp_home)
        elo[away] = elo_away + ELO_K * ((1 - home_won) - (1 - exp_home))

        for team, scored, allowed in ((home, home_score, away_score), (away, away_score, home_score)):
            prev_scored = avg_scored.get(team, LEAGUE_AVG_RUNS)
            prev_allowed = avg_allowed.get(team, LEAGUE_AVG_RUNS)
            avg_scored[team] = prev_scored + RUNS_EMA_ALPHA * (scored - prev_scored)
            avg_allowed[team] = prev_allowed + RUNS_EMA_ALPHA * (allowed - prev_allowed)
            games_played[team] = games_played.get(team, 0) + 1
            last_date[team] = row.Date

        last10.setdefault(home, deque(maxlen=10)).append(1 if home_won else 0)
        last10.setdefault(away, deque(maxlen=10)).append(0 if home_won else 1)
        for team, won in ((home, home_won == 1.0), (away, home_won == 0.0)):
            streak = current_streak.get(team, 0)
            current_streak[team] = (streak + 1 if streak >= 0 else 1) if won else (streak - 1 if streak <= 0 else -1)

        history_rows.append({
            "Date": row.Date, "VisitorTeamID": away, "HomeTeamID": home,
            "VisitorScore": away_score, "HomeScore": home_score,
        })

    history_df = pd.DataFrame(history_rows)
    current_year = games["Date"].dt.year.max() if len(games) else None
    season_record = {}
    if current_year is not None:
        season_games = games[games["Date"].dt.year == current_year]
        for team in set(season_games["HTeamID"]) | set(season_games["VTeamID"]):
            home_g = season_games[season_games["HTeamID"] == team]
            away_g = season_games[season_games["VTeamID"] == team]
            wins = int((home_g["HScore"] > home_g["VScore"]).sum()) + int((away_g["VScore"] > away_g["HScore"]).sum())
            losses = int((home_g["HScore"] < home_g["VScore"]).sum()) + int((away_g["VScore"] < away_g["HScore"]).sum())
            season_record[team] = (wins, losses)

    teams = sorted(elo.keys())
    ratings = pd.DataFrame({
        "TeamID": teams,
        "TeamName": [TEAM_FULL_NAMES.get(t, team_names.get(t, str(t))) for t in teams],
        "Elo": [round(elo[t], 1) for t in teams],
        "GamesPlayed": [games_played.get(t, 0) for t in teams],
        "AvgRunsScored": [round(avg_scored.get(t, LEAGUE_AVG_RUNS), 2) for t in teams],
        "AvgRunsAllowed": [round(avg_allowed.get(t, LEAGUE_AVG_RUNS), 2) for t in teams],
        "Last10Wins": [sum(last10.get(t, [])) for t in teams],
        "Last10Played": [len(last10.get(t, [])) for t in teams],
        "CurrentStreak": [current_streak.get(t, 0) for t in teams],
        "SeasonWins": [season_record.get(t, (0, 0))[0] for t in teams],
        "SeasonLosses": [season_record.get(t, (0, 0))[1] for t in teams],
        "LastGameDate": [last_date.get(t).strftime("%Y-%m-%d") if t in last_date else None for t in teams],
    }).sort_values("Elo", ascending=False).reset_index(drop=True)

    os.makedirs(DATA_DIR, exist_ok=True)
    ratings_path = os.path.join(DATA_DIR, "elo_ratings.csv")
    history_path = os.path.join(DATA_DIR, "game_history.csv")
    ratings.to_csv(ratings_path, index=False)
    history_df.to_csv(history_path, index=False)
    print(f"wrote {len(ratings)} team ratings -> {ratings_path}")
    print(ratings.to_string(index=False))


if __name__ == "__main__":
    build_elo()
