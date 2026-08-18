"""Shared config for the NBA Elo pipeline.

Data source: sportsdataverse/hoopR-nba-data (github.com, ESPN-sourced,
MIT-family license) -- one bulk CSV covering every game back to the
2001-02 season, already keyed on ESPN's own team abbreviations, so no
translation table is needed between the historical data and ESPN's live
scoreboard (unlike MLB's Retrosheet-code mapping). "season" is the year the
season *ends* (e.g. the 2025-26 season is season=2026), matching hoopR's
own convention.
"""
import os

SEASONS = list(range(2016, 2027))  # last 11 seasons (2015-16 through 2025-26)
SCHEDULE_URL = "https://raw.githubusercontent.com/sportsdataverse/hoopR-nba-data/main/nba/schedules/nba_schedule_master.csv"

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "nba")

ELO_INITIAL = 1500.0
ELO_K = 20.0  # NBA plays 82 games/season (vs MLB's 162) -- a larger per-game K
              # than MLB so ratings still adapt within a season, roughly scaled
              # by games-per-season the same way 538's NBA Elo K (20) compares
              # to their MLB Elo K.
HOME_ADVANTAGE = 100.0  # Elo points added to the home team's rating before
                         # computing win probability -- placeholder seed, close
                         # to the well-documented 538 NBA home-court edge;
                         # build_elo.py re-derives this empirically from the
                         # actual downloaded home win rate and overwrites it.

# Only real, current-era franchises -- the raw hoopR feed also carries
# All-Star Weekend exhibition rows (Team Chuck/Shaq, East/West, Stars/
# Stripes, USA/World, individual draft-captain codes) tagged with the same
# season_type as real games; these are dropped by TEAM_INFO whitelist rather
# than by season_type alone.
TEAM_INFO = {
    "ATL": "Atlanta Hawks",
    "BKN": "Brooklyn Nets",
    "BOS": "Boston Celtics",
    "CHA": "Charlotte Hornets",
    "CHI": "Chicago Bulls",
    "CLE": "Cleveland Cavaliers",
    "DAL": "Dallas Mavericks",
    "DEN": "Denver Nuggets",
    "DET": "Detroit Pistons",
    "GS": "Golden State Warriors",
    "HOU": "Houston Rockets",
    "IND": "Indiana Pacers",
    "LAC": "LA Clippers",
    "LAL": "Los Angeles Lakers",
    "MEM": "Memphis Grizzlies",
    "MIA": "Miami Heat",
    "MIL": "Milwaukee Bucks",
    "MIN": "Minnesota Timberwolves",
    "NO": "New Orleans Pelicans",
    "NY": "New York Knicks",
    "OKC": "Oklahoma City Thunder",
    "ORL": "Orlando Magic",
    "PHI": "Philadelphia 76ers",
    "PHX": "Phoenix Suns",
    "POR": "Portland Trail Blazers",
    "SA": "San Antonio Spurs",
    "SAC": "Sacramento Kings",
    "TOR": "Toronto Raptors",
    "UTAH": "Utah Jazz",
    "WSH": "Washington Wizards",
}

# hoopR abbreviations already match ESPN's live scoreboard abbreviations
# (both are ESPN-sourced), so no alias table is needed here.
