"""Shared config for the cricket pipeline (The Hundred, MLC, IPL, BBL, CPL).

Data source: Cricsheet.org (cricsheet.org/downloads/) -- free, public-domain
ball-by-ball JSON for every match of each competition, one zip per
competition. Cricsheet's JSON schema is documented at
cricsheet.org/format/json/: each match file has a top-level "info" block
(teams, venue, dates, players per team = playing XI, toss, outcome) and an
"innings" list, each innings a "team" plus an "overs" list of
    {"over": int, "deliveries": [...]}, each delivery a dict with "batter",
    "bowler", "non_striker", "runs": {"batter", "extras", "total"}, and an
    optional "wickets" list.

The Hundred (2021+) and MLC (2023+) are young / small-N. IPL, BBL, and CPL
add larger historical samples and stable franchise home grounds, so Elo
uses a modest home-advantage term for those three.
"""
import os

COMPETITIONS = {
    "hnd_male": {
        "label": "The Hundred (Men's)",
        "zip_url": "https://cricsheet.org/downloads/hnd_male_json.zip",
        "home_advantage": False,
        "slug": "hundred",
    },
    "mlc_male": {
        "label": "Major League Cricket (Men's)",
        "zip_url": "https://cricsheet.org/downloads/mlc_male_json.zip",
        "home_advantage": False,
        "slug": "mlc",
    },
    "ipl_male": {
        "label": "Indian Premier League",
        "zip_url": "https://cricsheet.org/downloads/ipl_male_json.zip",
        "home_advantage": True,
        "slug": "ipl",
    },
    "bbl_male": {
        "label": "Big Bash League",
        "zip_url": "https://cricsheet.org/downloads/bbl_male_json.zip",
        "home_advantage": True,
        "slug": "bbl",
    },
    "cpl_male": {
        "label": "Caribbean Premier League",
        "zip_url": "https://cricsheet.org/downloads/cpl_male_json.zip",
        "home_advantage": True,
        "slug": "cpl",
    },
}

# Elo home-field boost (in rating points) applied only when
# COMPETITIONS[comp]["home_advantage"] is True. Team1 is treated as the
# home side when info.teams[0] matches the venue's usual franchise or when
# the match marks a clear home team; otherwise HA is skipped.
ELO_HOME_ADVANTAGE = 25.0

PEOPLE_CSV_URL = "https://cricsheet.org/register/people.csv"

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(BASE_DIR, "data", "cricket")
RAW_DIR = os.path.join(DATA_DIR, "raw")

ELO_INITIAL = 1500.0
ELO_K = 20.0  # cricket teams play far fewer matches per season than MLB (~15-20
              # vs 162), so each result needs to move the rating meaningfully.

# Historical franchise names -> canonical current identity (Elo continuity).
# Applied during Elo / feature walks so Delhi Daredevils and Delhi Capitals
# share one rating curve; live fixture aliasing also uses this map.
FRANCHISE_ALIAS = {
    # IPL
    "Delhi Daredevils": "Delhi Capitals",
    "Kings XI Punjab": "Punjab Kings",
    "Royal Challengers Bangalore": "Royal Challengers Bengaluru",
    "Rising Pune Supergiant": "Rising Pune Supergiants",
    "Pune Warriors": "Pune Warriors",
    "Deccan Chargers": "Deccan Chargers",
    "Kochi Tuskers Kerala": "Kochi Tuskers Kerala",
    "Gujarat Lions": "Gujarat Lions",
    # BBL (stable franchise names historically)
    # CPL (franchise rebrands / 2026 display names)
    "Barbados Tridents": "Barbados Royals",
    "St Lucia Zouks": "Saint Lucia Kings",
    "St Lucia Stars": "Saint Lucia Kings",
    "St Lucia Kings": "Saint Lucia Kings",
    "Antigua Hawksbills": "Antigua and Barbuda Falcons",
    "Antigua & Barbuda Falcons": "Antigua and Barbuda Falcons",
    "Trinidad & Tobago Red Steel": "Trinbago Knight Riders",
    "Trinidad and Tobago Red Steel": "Trinbago Knight Riders",
    "Jamaica Kingsmen": "Jamaica Tallawahs",
    "St Kitts & Nevis Patriots": "St Kitts and Nevis Patriots",
    "St. Kitts and Nevis Patriots": "St Kitts and Nevis Patriots",
}

# Primary home grounds for current IPL / BBL / CPL franchises (HA lookup).
# Venue strings in Cricsheet vary; matching uses substring contains.
FRANCHISE_HOME_GROUNDS = {
    "Chennai Super Kings": ["MA Chidambaram", "Chepauk"],
    "Mumbai Indians": ["Wankhede"],
    "Kolkata Knight Riders": ["Eden Gardens"],
    "Royal Challengers Bengaluru": ["Chinnaswamy", "M Chinnaswamy"],
    "Royal Challengers Bangalore": ["Chinnaswamy", "M Chinnaswamy"],
    "Delhi Capitals": ["Arun Jaitley", "Feroz Shah Kotla"],
    "Delhi Daredevils": ["Arun Jaitley", "Feroz Shah Kotla"],
    "Punjab Kings": ["IS Bindra", "Mohali", "PCA"],
    "Kings XI Punjab": ["IS Bindra", "Mohali", "PCA"],
    "Rajasthan Royals": ["Sawai Mansingh", "Jaipur"],
    "Sunrisers Hyderabad": ["Rajiv Gandhi", "Uppal"],
    "Gujarat Titans": ["Narendra Modi", "Ahmedabad"],
    "Lucknow Super Giants": ["Ekana", "Lucknow", "Bharat Ratna"],
    "Adelaide Strikers": ["Adelaide Oval"],
    "Brisbane Heat": ["Gabba", "Woolloongabba", "Brisbane Cricket Ground"],
    "Hobart Hurricanes": ["Bellerive", "Ninja Stadium"],
    "Melbourne Renegades": ["Docklands", "Marvel"],
    "Melbourne Stars": ["Melbourne Cricket Ground", "MCG"],
    "Perth Scorchers": ["Perth Stadium", "Optus", "WACA", "Western Australia"],
    "Sydney Sixers": ["Sydney Cricket Ground", "SCG"],
    "Sydney Thunder": ["Showground", "ENGIE", "Stadium Australia"],
    # CPL
    "Barbados Royals": ["Kensington Oval", "Bridgetown"],
    "Barbados Tridents": ["Kensington Oval", "Bridgetown"],
    "Guyana Amazon Warriors": ["Providence", "Guyana"],
    "Jamaica Tallawahs": ["Sabina Park", "Kingston"],
    "Jamaica Kingsmen": ["Sabina Park", "Kingston"],
    "St Kitts and Nevis Patriots": ["Warner Park", "Basseterre"],
    "St Kitts & Nevis Patriots": ["Warner Park", "Basseterre"],
    "Saint Lucia Kings": ["Daren Sammy", "Gros Islet", "Beausejour"],
    "St Lucia Zouks": ["Daren Sammy", "Gros Islet", "Beausejour"],
    "St Lucia Stars": ["Daren Sammy", "Gros Islet", "Beausejour"],
    "Trinbago Knight Riders": ["Brian Lara", "Queens Park Oval", "Port of Spain", "Tarouba"],
    "Antigua and Barbuda Falcons": ["Sir Vivian Richards", "North Sound", "Antigua"],
    "Antigua & Barbuda Falcons": ["Sir Vivian Richards", "North Sound", "Antigua"],
    "Antigua Hawksbills": ["Sir Vivian Richards", "North Sound", "Antigua"],
}


def canonical_team(name: str) -> str:
    return FRANCHISE_ALIAS.get(name, name)


def team_is_home_at_venue(team: str, venue: str) -> bool:
    if not venue:
        return False
    v = venue.lower()
    for cand in (team, canonical_team(team)):
        for needle in FRANCHISE_HOME_GROUNDS.get(cand, []):
            if needle.lower() in v:
                return True
    return False

# Powerplay = overs 1-6, death/slog = overs 17-20 (both formats are 20-over
# innings: The Hundred is nominally "100 balls" but Cricsheet represents it
# as ~16.4 six-ball-equivalent overs; MLC/IPL/BBL/CPL are standard T20). Middle
# overs = everything between.
POWERPLAY_OVERS = 6
DEATH_OVERS_START = 17

ROLLING_FORM_MATCHES = 8   # team form window
PLAYER_FORM_INNINGS = 10   # player form window (batting/bowling)

# Minimum sample sizes below which a signal is considered "thin" and backed
# off toward a broader average (never left as raw 0/NaN).
MIN_H2H_BALLS = 12
MIN_VENUE_MATCHES = 5
MIN_PLAYER_INNINGS = 3
