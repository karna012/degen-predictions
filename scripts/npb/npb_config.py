"""Shared config for the NPB (Nippon Professional Baseball) Elo pipeline.

Data source: spaia.jp's public schedule API (spaia.jp/baseball/npb/api/schedules?Year=YYYY)
-- the same "unofficial but widely used, read-only, no-auth" category as
ESPN's endpoints already used elsewhere in this codebase. Documented (by a
third party, not spaia themselves) at
github.com/armstjc/Nippon-Baseball-Data-Repository/blob/main/spaia_api_endpoints.md.

Unlike MLB (which needed Retrosheet for history + ESPN for live fixtures),
this single endpoint covers both: it returns the full season's schedule --
past results (GameState=2, with final scores) and future/scheduled games
(GameState=1, no score yet) -- for any year, including the current
in-progress season. Team IDs are spaia's own numeric IDs, used directly as
the team identifier (no cross-source name-mapping problem like tennis/MLB
had).

Reduced scope vs MLB: this endpoint does not include team box-score stats
(hits, home runs, strikeouts) or per-pitcher season ERA -- only final score
and the starting pitcher's *name*. So NPB predictions cover win probability,
head-to-head, streak, and total-runs, but not the hits/HR/strikeouts markets
or a live pitcher-ERA blend that MLB has.
"""
import os

YEARS = list(range(2018, 2027))  # 2026 is the current in-progress season -- this
                                  # endpoint includes it (with future games too)
SCHEDULE_URL = "https://spaia.jp/baseball/npb/api/schedules"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"}

# GameKindID: 1 = Central League regular season, 2 = Pacific League regular
# season -- other codes are exhibition/All-Star/Climax Series/Japan Series,
# excluded to keep the Elo model trained on real regular-season results only.
REGULAR_SEASON_GAME_KINDS = (1, 2)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "npb")

ELO_INITIAL = 1500.0
ELO_K = 4.0
HOME_ADVANTAGE = 24.0  # same constant used for MLB -- no NPB-specific study readily available,
                       # and a modest fixed home-field edge is a reasonable default either way.

# spaia.jp's HomeTeamNameE/VisitorTeamNameE fields are short nickname-only
# ("Tigers", "Hawks") -- these are the full official English franchise names,
# keyed by spaia's own numeric team ID (seen directly in the schedule data).
TEAM_FULL_NAMES = {
    1: "Yomiuri Giants",
    2: "Tokyo Yakult Swallows",
    3: "Yokohama DeNA BayStars",
    4: "Chunichi Dragons",
    5: "Hanshin Tigers",
    6: "Hiroshima Toyo Carp",
    7: "Saitama Seibu Lions",
    8: "Hokkaido Nippon-Ham Fighters",
    9: "Chiba Lotte Marines",
    11: "Orix Buffaloes",
    12: "Fukuoka SoftBank Hawks",
    376: "Tohoku Rakuten Golden Eagles",
}
