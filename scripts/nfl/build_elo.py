"""Computes a chronological team Elo rating for NFL from the downloaded
nflverse game results, plus rolling scoring-form EMAs, streaks, and season
record. Output schema matches nfl_predict.py's existing elo_ratings.csv/
game_history.csv exactly (AvgPointsScored/AvgPointsAllowed naming, same
column set) -- this is a drop-in replacement for the live ESPN-rebuild
system's output, not a new schema, so nothing else in nfl_predict.py needs
to change to use it.

HOME_ADVANTAGE is re-derived empirically from the real downloaded home win
rate, same reasoning as nba_config.py/nhl_config.py's build_elo.py.

Run from repo root: python scripts/nfl/build_elo.py
Outputs data/nfl/elo_ratings.csv and data/nfl/game_history.csv.
"""
import math
import os
import sys
from collections import deque

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nfl_config import DATA_DIR, ELO_INITIAL, ELO_K, TEAM_INFO  # noqa: E402

RAW_DIR = os.path.join(DATA_DIR, "raw")
EMA_ALPHA = 0.06  # NFL plays only ~17 regular-season games/year (vs NBA's 82) --
                   # a much faster-adapting EMA so form actually moves within
                   # a season instead of barely reacting.
LEAGUE_AVG_POINTS = 22.5  # matches nfl_predict.py's existing LEAGUE_AVG_POINTS


def load_games():
    path = os.path.join(RAW_DIR, "games.csv")
    if not os.path.exists(path):
        return pd.DataFrame(columns=["Date", "VisitorTeam", "HomeTeam", "VisitorScore", "HomeScore"])
    games = pd.read_csv(path)
    games["Date"] = pd.to_datetime(games["Date"])
    return games.sort_values("Date").reset_index(drop=True)


def derive_home_advantage(games: pd.DataFrame) -> float:
    if games.empty:
        return 55.0
    home_win_rate = (games["HomeScore"] > games["VisitorScore"]).mean()
    home_win_rate = min(max(home_win_rate, 0.501), 0.999)
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
    print(f"Derived home-field Elo advantage from real data: {home_advantage} "
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
            "Date": str(row.Date.date()), "HomeTeam": home, "VisitorTeam": away,
            "HomeScore": home_score, "VisitorScore": away_score,
            "HomeName": TEAM_INFO.get(home, home), "AwayName": TEAM_INFO.get(away, away),
        })

    history_df = pd.DataFrame(history_rows)
    season_record = {}
    if len(games):
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
        "AvgPointsScored": [stat(scoring_for, t, "Points") for t in teams],
        "AvgPointsAllowed": [stat(scoring_against, t, "Points") for t in teams],
        "CurrentStreak": [current_streak.get(t, 0) for t in teams],
        "SeasonWins": [season_record.get(t, (0, 0))[0] for t in teams],
        "SeasonLosses": [season_record.get(t, (0, 0))[1] for t in teams],
        "Last10Wins": [sum(last10.get(t, [])) for t in teams],
        "Last10Played": [len(last10.get(t, [])) for t in teams],
        "LastGameDate": [str(last_date.get(t).date()) if t in last_date else "" for t in teams],
        "GamesPlayed": [games_played.get(t, 0) for t in teams],
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
