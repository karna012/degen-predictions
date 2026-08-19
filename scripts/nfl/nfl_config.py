"""Shared config for the NFL Elo pipeline.

Data source: nflverse-data (github.com/nflverse/nflverse-data, MIT
license, free, no key) -- one bulk CSV covering every game back to 1999,
including full playoffs and (unusually) pre-computed rest-day columns.
No preseason rows exist in this feed, which is fine -- the live-fixtures
side (nfl_fixtures.py / the existing ensure_nfl_ratings() ESPN rebuild)
already covers preseason separately.

Team codes here differ from ESPN's live scoreboard for a few
relocated/renamed franchises -- folded via TEAM_CODE_ALIAS so this
pipeline's output uses the exact same codes the live system (and
nfl_predict.py's existing elo_ratings.csv) already does.
"""
import os

SEASONS = list(range(2016, 2027))  # last 11 seasons (matches NBA/NHL's window)
SCHEDULE_URL = "https://github.com/nflverse/nflverse-data/releases/download/schedules/games.csv"

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "nfl")

ELO_INITIAL = 1500.0
ELO_K = 20.0  # matches the K already used by nfl_predict.py's live-rebuild Elo,
              # so a full pipeline run doesn't suddenly shift ratings for a
              # reason unrelated to actual results.
HOME_ADVANTAGE = 55.0  # placeholder seed matching nfl_predict.py's existing
                        # HOME_ADVANTAGE constant; build_elo.py re-derives
                        # this empirically from the real downloaded home win
                        # rate, same reasoning as nba_config.py/nhl_config.py.

TEAM_INFO = {
    "ARI": "Arizona Cardinals", "ATL": "Atlanta Falcons", "BAL": "Baltimore Ravens",
    "BUF": "Buffalo Bills", "CAR": "Carolina Panthers", "CHI": "Chicago Bears",
    "CIN": "Cincinnati Bengals", "CLE": "Cleveland Browns", "DAL": "Dallas Cowboys",
    "DEN": "Denver Broncos", "DET": "Detroit Lions", "GB": "Green Bay Packers",
    "HOU": "Houston Texans", "IND": "Indianapolis Colts", "JAX": "Jacksonville Jaguars",
    "KC": "Kansas City Chiefs", "LAC": "Los Angeles Chargers", "LAR": "Los Angeles Rams",
    "LV": "Las Vegas Raiders", "MIA": "Miami Dolphins", "MIN": "Minnesota Vikings",
    "NE": "New England Patriots", "NO": "New Orleans Saints", "NYG": "New York Giants",
    "NYJ": "New York Jets", "PHI": "Philadelphia Eagles", "PIT": "Pittsburgh Steelers",
    "SEA": "Seattle Seahawks", "SF": "San Francisco 49ers", "TB": "Tampa Bay Buccaneers",
    "TEN": "Tennessee Titans", "WSH": "Washington Commanders",
}

# nflverse's own codes -> the current-franchise / ESPN-matching codes above.
TEAM_CODE_ALIAS = {
    "OAK": "LV",   # Oakland -> Las Vegas Raiders (2020 relocation)
    "SD": "LAC",   # San Diego -> LA Chargers (2017 relocation)
    "LA": "LAR",   # nflverse's Rams code vs ESPN's LAR
    "WAS": "WSH",  # nflverse's Washington code vs ESPN's WSH
}
