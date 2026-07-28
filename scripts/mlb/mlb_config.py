"""Shared config for the MLB Elo pipeline.

Data source: Retrosheet game logs (retrosheet.org/gamelogs) -- free, public
domain, one zip per season containing every regular/post-season game's box
score line (date, teams, final score, plus a lot of columns we don't need).
Retrosheet publishes each season's log after that season ends, so the most
recent in-progress season won't be included yet; ESPN's live scoreboard
(mlb_fixtures.py) is used separately for actual upcoming/live fixtures.
"""
import os

YEARS = list(range(2019, 2026))  # last 7 seasons
GAMELOG_URL = "https://www.retrosheet.org/gamelogs/gl{year}.zip"

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "mlb")

ELO_INITIAL = 1500.0
ELO_K = 4.0  # MLB plays ~162 games/season -- a much smaller per-game K than
             # tennis/football since any single game should move a team's
             # rating only slightly.
HOME_ADVANTAGE = 24.0  # Elo points added to the home team's rating before
                        # computing win probability -- the standard FiveThirtyEight-
                        # style MLB home-field edge, not stored on the team itself.

# Retrosheet's team codes -> (full display name, ESPN abbreviation). Only
# current (2014+) franchises are needed since YEARS starts at 2014 -- no
# relocations (Expos->Nationals, Montreal etc.) fall inside this window.
TEAM_INFO = {
    "ARI": ("Arizona Diamondbacks", "ARI"),
    "ATL": ("Atlanta Braves", "ATL"),
    "BAL": ("Baltimore Orioles", "BAL"),
    "BOS": ("Boston Red Sox", "BOS"),
    "CHA": ("Chicago White Sox", "CHW"),
    "CHN": ("Chicago Cubs", "CHC"),
    "CIN": ("Cincinnati Reds", "CIN"),
    "CLE": ("Cleveland Guardians", "CLE"),
    "COL": ("Colorado Rockies", "COL"),
    "DET": ("Detroit Tigers", "DET"),
    "HOU": ("Houston Astros", "HOU"),
    "KCA": ("Kansas City Royals", "KC"),
    "ANA": ("Los Angeles Angels", "LAA"),
    "LAN": ("Los Angeles Dodgers", "LAD"),
    "MIA": ("Miami Marlins", "MIA"),
    "MIL": ("Milwaukee Brewers", "MIL"),
    "MIN": ("Minnesota Twins", "MIN"),
    "NYA": ("New York Yankees", "NYY"),
    "NYN": ("New York Mets", "NYM"),
    "ATH": ("Athletics", "ATH"),  # renamed from Oakland A's starting 2025 -- OAK
                                  # is normalized into this same code (see
                                  # TEAM_CODE_ALIAS) so the franchise's Elo/form
                                  # history doesn't fragment across the rename.
    "PHI": ("Philadelphia Phillies", "PHI"),
    "PIT": ("Pittsburgh Pirates", "PIT"),
    "SDN": ("San Diego Padres", "SD"),
    "SEA": ("Seattle Mariners", "SEA"),
    "SFN": ("San Francisco Giants", "SF"),
    "SLN": ("St. Louis Cardinals", "STL"),
    "TBA": ("Tampa Bay Rays", "TB"),
    "TEX": ("Texas Rangers", "TEX"),
    "TOR": ("Toronto Blue Jays", "TOR"),
    "WAS": ("Washington Nationals", "WSH"),
}

# Franchise renames within the YEARS window -- old code is folded into the
# current one before Elo/form are computed, so the rename doesn't fragment
# one team's history into two separate (and much smaller) ratings.
TEAM_CODE_ALIAS = {
    "OAK": "ATH",  # Oakland Athletics -> Athletics (2025 relocation/rename)
}

# Reverse lookup: ESPN abbreviation -> Retrosheet code, used to match live
# ESPN fixtures back to the Elo ratings table.
ESPN_TO_RETROSHEET = {espn: code for code, (_, espn) in TEAM_INFO.items()}
