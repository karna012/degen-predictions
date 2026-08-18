"""Shared config for the NHL Elo pipeline.

Data source: the NHL's own official public API (api-web.nhle.com), free,
keyless -- per-team-per-season schedule/results, looped across all 32
teams and deduplicated by game id (no single bulk file exists like
hoopR gives for NBA). Season strings are "YYYYYYYY" (e.g. "20222023" for
the 2022-23 season), matching the NHL API's own convention.
"""
import os

SEASONS = [f"{y}{y+1}" for y in range(2016, 2026)]  # last 10 seasons
SCHEDULE_URL = "https://api-web.nhle.com/v1/club-schedule-season/{team}/{season}"

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "nhl")

ELO_INITIAL = 1500.0
ELO_K = 20.0  # NHL plays 82 games/season, same order as NBA -- same K as nba_config.py.
HOME_ADVANTAGE = 50.0  # placeholder seed; build_elo.py re-derives this
                        # empirically from the real downloaded home win rate,
                        # same reasoning as nba_config.py's HOME_ADVANTAGE.

TEAM_INFO = {
    "ANA": "Anaheim Ducks",
    "BOS": "Boston Bruins",
    "BUF": "Buffalo Sabres",
    "CAR": "Carolina Hurricanes",
    "CBJ": "Columbus Blue Jackets",
    "CGY": "Calgary Flames",
    "CHI": "Chicago Blackhawks",
    "COL": "Colorado Avalanche",
    "DAL": "Dallas Stars",
    "DET": "Detroit Red Wings",
    "EDM": "Edmonton Oilers",
    "FLA": "Florida Panthers",
    "LAK": "Los Angeles Kings",
    "MIN": "Minnesota Wild",
    "MTL": "Montreal Canadiens",
    "NJD": "New Jersey Devils",
    "NSH": "Nashville Predators",
    "NYI": "New York Islanders",
    "NYR": "New York Rangers",
    "OTT": "Ottawa Senators",
    "PHI": "Philadelphia Flyers",
    "PIT": "Pittsburgh Penguins",
    "SEA": "Seattle Kraken",
    "SJS": "San Jose Sharks",
    "STL": "St. Louis Blues",
    "TBL": "Tampa Bay Lightning",
    "TOR": "Toronto Maple Leafs",
    "UTA": "Utah Mammoth",
    "VAN": "Vancouver Canucks",
    "VGK": "Vegas Golden Knights",
    "WPG": "Winnipeg Jets",
    "WSH": "Washington Capitals",
}

# The NHL's own API abbreviations differ from ESPN's live scoreboard for a
# handful of teams -- verified live against both endpoints (same class of
# mismatch MLB already handles via MLB_TO_ESPN_ABBR, e.g. AZ -> ARI).
NHL_TO_ESPN_ABBR = {
    "LAK": "LA",
    "NJD": "NJ",
    "SJS": "SJ",
    "TBL": "TB",
    "UTA": "UTAH",
}
ESPN_TO_NHL_ABBR = {espn: nhl for nhl, espn in NHL_TO_ESPN_ABBR.items()}

# Also fold Arizona Coyotes' old code into the current Utah franchise so a
# 2024 relocation doesn't fragment the rating history across two codes.
TEAM_CODE_ALIAS = {
    "ARI": "UTA",
}
