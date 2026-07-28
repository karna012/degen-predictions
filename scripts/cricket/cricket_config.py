"""Shared config for the cricket (Men's The Hundred + Men's Major League
Cricket) pipeline.

Data source: Cricsheet.org (cricsheet.org/downloads/) -- free, public-domain
ball-by-ball JSON for every match of each competition, one zip per
competition. Cricsheet's JSON schema is documented at
cricsheet.org/format/json/: each match file has a top-level "info" block
(teams, venue, dates, players per team = playing XI, toss, outcome) and an
"innings" list, each innings a "team" plus an "overs" list of
{"over": int, "deliveries": [...]}, each delivery a dict with "batter",
"bowler", "non_striker", "runs": {"batter", "extras", "total"}, and an
optional "wickets" list.

Both competitions are genuinely young (The Hundred started 2021, MLC 2023),
so sample sizes are small by design -- ~167 Hundred matches, ~105 MLC
matches at time of writing. This is surfaced honestly via data_confidence
throughout the pipeline rather than hidden.
"""
import os

COMPETITIONS = {
    "hnd_male": {
        "label": "The Hundred (Men's)",
        "zip_url": "https://cricsheet.org/downloads/hnd_male_json.zip",
    },
    "mlc_male": {
        "label": "Major League Cricket (Men's)",
        "zip_url": "https://cricsheet.org/downloads/mlc_male_json.zip",
    },
}

PEOPLE_CSV_URL = "https://cricsheet.org/register/people.csv"

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(BASE_DIR, "data", "cricket")
RAW_DIR = os.path.join(DATA_DIR, "raw")

ELO_INITIAL = 1500.0
ELO_K = 20.0  # cricket teams play far fewer matches per season than MLB (~15-20
              # vs 162), so each result needs to move the rating meaningfully.

# Powerplay = overs 1-6, death/slog = overs 17-20 (both formats are 20-over
# innings: The Hundred is nominally "100 balls" but Cricsheet represents it
# as ~16.4 six-ball-equivalent overs; MLC is standard T20). Middle overs =
# everything between. This over-phase split is the same shape for both
# competitions since both are short-format, high-tempo cricket.
POWERPLAY_OVERS = 6
DEATH_OVERS_START = 17

ROLLING_FORM_MATCHES = 8   # team form window
PLAYER_FORM_INNINGS = 10   # player form window (batting/bowling)

# Minimum sample sizes below which a signal is considered "thin" and backed
# off toward a broader average (never left as raw 0/NaN).
MIN_H2H_BALLS = 12
MIN_VENUE_MATCHES = 5
MIN_PLAYER_INNINGS = 3
