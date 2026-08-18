"""Computes a chronological team Elo rating for NBA from the downloaded
hoopR schedule data, plus rolling scoring-form EMAs, streaks, and season
record -- same structure as scripts/mlb/build_elo.py, adapted for
basketball's points-for/points-against instead of baseball's box-score
columns (hoopR's schedule file only has final scores, not shooting/
rebounding splits, so those aren't available as features here).

HOME_ADVANTAGE is re-derived empirically from the real downloaded home win
rate (solving the Elo formula for the point spread that reproduces it at
rating parity) rather than kept as a guessed constant -- this codebase has
been burned before by a hardcoded constant that was never checked against
the real data (see mlb_predict.py's TOTAL_RUNS_STD_DEV comment).

Run from repo root: python scripts/nba/build_elo.py
Outputs data/nba/elo_ratings.csv and data/nba/game_history.csv.
"""
import math
import os
import sys
from collections import deque

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nba_config import DATA_DIR, ELO_INITIAL, ELO_K, TEAM_INFO  # noqa: E402

RAW_DIR = os.path.join(DATA_DIR, "raw")
EMA_ALPHA = 0.03  # NBA plays ~82 games/season vs MLB's 162 -- a slightly
                   # faster-adapting EMA than MLB's 0.02 so form still updates
                   # meaningfully within a season.
LEAGUE_AVG_POINTS = 112.0  # modern-era (2016+) NBA points-per-team-per-game, EMA seed


def load_games():
    path = os.path.join(RAW_DIR, "games.csv")
    if not os.path.exists(path):
        return pd.DataFrame(columns=["Date", "VisitorTeam", "HomeTeam", "VisitorScore", "HomeScore"])
    games = pd.read_csv(path)
    games["Date"] = pd.to_datetime(games["Date"])
    return games.sort_values("Date").reset_index(drop=True)


def derive_home_advantage(games: pd.DataFrame) -> float:
    """Solve for the Elo home-court bonus that reproduces the real observed
    home win rate at rating parity: home_win_rate = 1 / (1 + 10^(-HA/400))."""
    if games.empty:
        return 100.0
    home_win_rate = (games["HomeScore"] > games["VisitorScore"]).mean()
    home_win_rate = min(max(home_win_rate, 0.501), 0.999)  # keep the log finite/sane
    return round(400.0 * math.log10(home_win_rate / (1 - home_win_rate)), 1)


def expected_home_score(elo_home, elo_away, home_advantage):
    return 1.0 / (1.0 + 10 ** (-((elo_home + home_advantage) - elo_away) / 400.0))


def _update_ema(store, team, key, value, seed):
    prev = store.setdefault(team, {}).setdefault(key, seed)
    store[team][key] = prev + EMA_ALPHA * (float(value) - prev)


def build_elo():
    games = load_games()
    print(f"{len(games)} games loaded across {games['Date'].dt.year.nunique() if len(games) else 0} calendar years")
    home_advantage = derive_home_advantage(games)
    print(f"Derived home-court Elo advantage from real data: {home_advantage} "
          f"(observed home win rate {(games['HomeScore'] > games['VisitorScore']).mean():.3f})")

    elo = {}
    games_played = {}
    scoring_for = {}
    scoring_against = {}
    last10 = {}
    current_streak = {}
    last_date = {}
    history_rows = []

    for row in games.itertuples(index=False):
        home, away = row.HomeTeam, row.VisitorTeam
        home_score, away_score = row.HomeScore, row.VisitorScore

        elo_home = elo.get(home, ELO_INITIAL)
        elo_away = elo.get(away, ELO_INITIAL)
        exp_home = expected_home_score(elo_home, elo_away, home_advantage)
        home_won = 1.0 if home_score > away_score else 0.0
        elo[home] = elo_home + ELO_K * (home_won - exp_home)
        elo[away] = elo_away + ELO_K * ((1 - home_won) - (1 - exp_home))

        for team, pts_for, pts_against in ((home, home_score, away_score), (away, away_score, home_score)):
            _update_ema(scoring_for, team, "Points", pts_for, LEAGUE_AVG_POINTS)
            _update_ema(scoring_against, team, "Points", pts_against, LEAGUE_AVG_POINTS)
            games_played[team] = games_played.get(team, 0) + 1
            last_date[team] = row.Date

        last10.setdefault(home, deque(maxlen=10)).append(1 if home_won else 0)
        last10.setdefault(away, deque(maxlen=10)).append(0 if home_won else 1)

        for team, won in ((home, home_won == 1.0), (away, home_won == 0.0)):
            streak = current_streak.get(team, 0)
            current_streak[team] = (streak + 1 if streak >= 0 else 1) if won else (streak - 1 if streak <= 0 else -1)

        history_rows.append({
            "Date": row.Date, "VisitorTeam": away, "HomeTeam": home,
            "VisitorScore": away_score, "HomeScore": home_score,
            "VisitorEloAfter": round(elo[away], 1), "HomeEloAfter": round(elo[home], 1),
        })

    history_df = pd.DataFrame(history_rows)
    current_year = games["Date"].dt.year.max() if len(games) else None
    season_record = {}
    if current_year is not None:
        # NBA seasons span two calendar years -- last 12 months covers the
        # current/most-recent season regardless of where in the season it is.
        cutoff = games["Date"].max() - pd.Timedelta(days=365)
        season_games = games[games["Date"] > cutoff]
        for team in set(season_games["HomeTeam"]) | set(season_games["VisitorTeam"]):
            home_g = season_games[season_games["HomeTeam"] == team]
            away_g = season_games[season_games["VisitorTeam"] == team]
            wins = int((home_g["HomeScore"] > home_g["VisitorScore"]).sum()) + \
                int((away_g["VisitorScore"] > away_g["HomeScore"]).sum())
            losses = len(home_g) + len(away_g) - wins
            season_record[team] = (wins, losses)

    teams = sorted(elo.keys())

    def stat(store, team, key):
        return round(store.get(team, {key: LEAGUE_AVG_POINTS})[key], 2)

    ratings = pd.DataFrame({
        "Team": teams,
        "TeamName": [TEAM_INFO.get(t, t) for t in teams],
        "Elo": [round(elo[t], 1) for t in teams],
        "GamesPlayed": [games_played.get(t, 0) for t in teams],
        "AvgPointsFor": [stat(scoring_for, t, "Points") for t in teams],
        "AvgPointsAllowed": [stat(scoring_against, t, "Points") for t in teams],
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
    ha_path = os.path.join(DATA_DIR, "home_advantage.txt")
    ratings.to_csv(ratings_path, index=False)
    history_df.to_csv(history_path, index=False)
    with open(ha_path, "w", encoding="utf-8") as f:
        f.write(str(home_advantage))
    print(f"wrote {len(ratings)} team ratings -> {ratings_path}")
    print(ratings.to_string(index=False))


if __name__ == "__main__":
    build_elo()
