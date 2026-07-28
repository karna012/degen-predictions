"""Computes a chronological team Elo rating for MLB from the downloaded
Retrosheet game logs (plus the current-season ESPN backfill), and a set of
rolling team-level batting/pitching averages, streaks, and season record.

Standard Elo update, processed strictly in date order:

    expected_home = 1 / (1 + 10 ** (-((elo_home + HOME_ADVANTAGE) - elo_away) / 400))
    elo_home_new  = elo_home + K * (result_home - expected_home)

HOME_ADVANTAGE is applied only inside the win-probability calculation (the
standard FiveThirtyEight-style MLB home-field edge), never added to the
team's stored rating itself.

Batting/pitching stats (runs, hits, home runs, strikeouts) are tracked as an
exponential moving average, each split into "for" (this team's batting) and
"against" (what this team's pitching allowed) -- e.g. a team's strikeouts
column is its own batters' strikeouts, while the opposing team's game log
row for that same game is what its *pitching* induced. Missing values
(the current-season ESPN backfill only has scores, not the full box score)
are skipped rather than treated as zero, so they don't drag the average down.

Run from repo root: python scripts/mlb/build_elo.py
Outputs data/mlb/elo_ratings.csv (current rating + stats per team) and
data/mlb/game_history.csv (every game with post-game Elo, used for
head-to-head lookups).
"""
import os
import sys
from collections import deque

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mlb_config import DATA_DIR, ELO_INITIAL, ELO_K, HOME_ADVANTAGE, TEAM_CODE_ALIAS, TEAM_INFO  # noqa: E402

RAW_DIR = os.path.join(DATA_DIR, "raw")
EMA_ALPHA = 0.02
LEAGUE_AVG = {"Runs": 4.5, "Hits": 8.6, "HR": 1.2, "SO": 8.4}  # modern-era MLB per-team-per-game averages, EMA seed


def load_games():
    frames = []
    for name in sorted(os.listdir(RAW_DIR)):
        if not name.endswith(".csv"):
            continue
        frames.append(pd.read_csv(os.path.join(RAW_DIR, name)))
    if not frames:
        return pd.DataFrame(columns=["Date", "VisitorTeam", "HomeTeam", "VisitorScore", "HomeScore"])
    games = pd.concat(frames, ignore_index=True)
    games["Date"] = pd.to_datetime(games["Date"], format="%Y%m%d", errors="coerce")
    games = games.dropna(subset=["Date", "VisitorTeam", "HomeTeam", "VisitorScore", "HomeScore"])
    # Fold renamed/relocated franchises into their current code (e.g. Oakland
    # Athletics -> Athletics) so a rename doesn't fragment one team's rating
    # history into two much smaller, less-informative samples.
    games["VisitorTeam"] = games["VisitorTeam"].replace(TEAM_CODE_ALIAS)
    games["HomeTeam"] = games["HomeTeam"].replace(TEAM_CODE_ALIAS)
    games = games.sort_values("Date").reset_index(drop=True)
    return games


def expected_home_score(elo_home, elo_away):
    return 1.0 / (1.0 + 10 ** (-((elo_home + HOME_ADVANTAGE) - elo_away) / 400.0))


def _update_ema(store, team, key, value):
    if value is None or pd.isna(value):
        return
    prev = store.setdefault(team, dict(LEAGUE_AVG))[key]
    store[team][key] = prev + EMA_ALPHA * (float(value) - prev)


def build_elo():
    games = load_games()
    print(f"{len(games)} games loaded across {games['Date'].dt.year.nunique() if len(games) else 0} seasons")

    elo = {}
    games_played = {}
    batting_for = {}   # team -> {"Runs":.., "Hits":.., "HR":.., "SO":..} (this team's own batting)
    batting_against = {}  # team -> same stats, but what this team's pitching allowed
    last10 = {}
    current_streak = {}  # team -> (positive int = win streak, negative int = loss streak)
    last_date = {}
    history_rows = []
    has_box_cols = all(c in games.columns for c in ("VisitorHits", "VisitorHR", "VisitorSO", "HomeHits", "HomeHR", "HomeSO"))

    for row in games.itertuples(index=False):
        home, away = row.HomeTeam, row.VisitorTeam
        home_score, away_score = row.HomeScore, row.VisitorScore

        elo_home = elo.get(home, ELO_INITIAL)
        elo_away = elo.get(away, ELO_INITIAL)
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

        for team, runs_for, runs_against, hits_for, hits_against, hr_for, hr_against, so_for, so_against in (
            (home, home_score, away_score, home_hits, away_hits, home_hr, away_hr, home_so, away_so),
            (away, away_score, home_score, away_hits, home_hits, away_hr, home_hr, away_so, home_so),
        ):
            _update_ema(batting_for, team, "Runs", runs_for)
            _update_ema(batting_against, team, "Runs", runs_against)
            _update_ema(batting_for, team, "Hits", hits_for)
            _update_ema(batting_against, team, "Hits", hits_against)
            _update_ema(batting_for, team, "HR", hr_for)
            _update_ema(batting_against, team, "HR", hr_against)
            _update_ema(batting_for, team, "SO", so_for)
            _update_ema(batting_against, team, "SO", so_against)
            games_played[team] = games_played.get(team, 0) + 1
            last_date[team] = row.Date

        last10.setdefault(home, deque(maxlen=10)).append(1 if home_won else 0)
        last10.setdefault(away, deque(maxlen=10)).append(0 if home_won else 1)

        for team, won in ((home, home_won == 1.0), (away, home_won == 0.0)):
            streak = current_streak.get(team, 0)
            if won:
                current_streak[team] = streak + 1 if streak >= 0 else 1
            else:
                current_streak[team] = streak - 1 if streak <= 0 else -1

        history_rows.append({
            "Date": row.Date, "VisitorTeam": away, "HomeTeam": home,
            "VisitorScore": away_score, "HomeScore": home_score,
            "VisitorEloAfter": round(elo[away], 1), "HomeEloAfter": round(elo[home], 1),
        })

    history_df = pd.DataFrame(history_rows)
    current_year = games["Date"].dt.year.max() if len(games) else None
    season_record = {}
    if current_year is not None:
        season_games = games[games["Date"].dt.year == current_year]
        for team in set(season_games["HomeTeam"]) | set(season_games["VisitorTeam"]):
            home_g = season_games[season_games["HomeTeam"] == team]
            away_g = season_games[season_games["VisitorTeam"] == team]
            wins = int((home_g["HomeScore"] > home_g["VisitorScore"]).sum()) + \
                int((away_g["VisitorScore"] > away_g["HomeScore"]).sum())
            losses = len(home_g) + len(away_g) - wins
            season_record[team] = (wins, losses)

    teams = sorted(elo.keys())

    def stat(store, team, key):
        return round(store.get(team, dict(LEAGUE_AVG))[key], 2)

    ratings = pd.DataFrame({
        "Team": teams,
        "TeamName": [TEAM_INFO.get(t, (t, t))[0] for t in teams],
        "Elo": [round(elo[t], 1) for t in teams],
        "GamesPlayed": [games_played.get(t, 0) for t in teams],
        "AvgRunsScored": [stat(batting_for, t, "Runs") for t in teams],
        "AvgRunsAllowed": [stat(batting_against, t, "Runs") for t in teams],
        "AvgHitsFor": [stat(batting_for, t, "Hits") for t in teams],
        "AvgHitsAllowed": [stat(batting_against, t, "Hits") for t in teams],
        "AvgHRFor": [stat(batting_for, t, "HR") for t in teams],
        "AvgHRAllowed": [stat(batting_against, t, "HR") for t in teams],
        "AvgSOAtBat": [stat(batting_for, t, "SO") for t in teams],
        "AvgSOInduced": [stat(batting_against, t, "SO") for t in teams],
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
