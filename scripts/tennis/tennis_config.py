"""Shared config for men's ATP tennis pipeline (v1).

History: TennisMyLife yearly CSVs (Sackmann-compatible schema; MIT).
  https://stats.tennismylife.org/tennis-match-database
Live fixtures: ESPN public tennis ATP scoreboard (fail-soft).

v1 scope: tour-level main-draw singles only (G / M / 500 / 250).
Skip Challenger, Futures, Davis Cup, doubles, WTA, qualifying rounds.
"""
from __future__ import annotations

import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(BASE_DIR, "data", "tennis")
RAW_DIR = os.path.join(DATA_DIR, "raw")

# Train on recent Open era tour seasons (enough volume, modern game).
YEARS = list(range(2015, 2027))

TML_API = "https://stats.tennismylife.org/api/data-files"
TML_YEAR_URL = "https://stats.tennismylife.org/data/{year}.csv"

ESPN_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/tennis/atp/scoreboard"

ELO_INITIAL = 1500.0
ELO_K = 24.0  # tennis: fewer matches than MLB; each result moves rating more

# Tour levels kept for v1 (Sackmann / TML codes).
ALLOWED_LEVELS = {"G", "M", "500", "250"}

LEVEL_FEATURE = {
    "G": "Level_G",
    "M": "Level_M",
    "500": "Level_500",
    "250": "Level_250",
}

SURFACE_FEATURE = {
    "Hard": "Surface_hard",
    "Clay": "Surface_clay",
    "Grass": "Surface_grass",
    "Carpet": "Surface_hard",  # rare; fold into hard
}

ROLLING_FORM_MATCHES = 20
MIN_H2H_MEETINGS = 2

NUMERIC_FEATURES = [
    "EloDiff",
    "SurfaceEloDiff",
    "RankDiff",
    "Player1RecentWinRate",
    "Player2RecentWinRate",
    "Player1SurfaceWinRate",
    "Player2SurfaceWinRate",
    "Player1DaysRest",
    "Player2DaysRest",
    "BestOf5",
    "Level_G",
    "Level_M",
    "Level_500",
    "Level_250",
    "Surface_hard",
    "Surface_clay",
    "Surface_grass",
    "H2HMeetings",
    "Player1H2HWinRate",
]
