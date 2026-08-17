"""
Central config for every league the pipeline supports. Adding a league
means adding one entry here -- build_features.py, train_models.py,
predict.py, and backtest.py all read from this instead of hardcoding
Premier-League-only paths/codes.
"""
import datetime as _dt
import os

# leagues.py lives in scripts/; repo root is one level up. Always resolve
# data/model paths against REPO_ROOT so train/predict write the same trees
# the Flask app loads (never scripts/model or scripts/data).
_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(_SCRIPTS_DIR)


def repo_path(*parts: str) -> str:
    return os.path.join(REPO_ROOT, *parts)


def resolve_league_paths(cfg: dict) -> dict:
    """Shallow copy with data_dir/model_dir absolute under the repo root."""
    out = dict(cfg)
    out["data_dir"] = repo_path(cfg["data_dir"])
    out["model_dir"] = repo_path(cfg["model_dir"])
    return out


SEASONS = ["1920", "2021", "2122", "2223", "2324", "2425", "2526"]

UNDERSTAT_SEASON_MAP = {
    "1920": "2019", "2021": "2020", "2122": "2021", "2223": "2022",
    "2324": "2023", "2425": "2024", "2526": "2025",
}


def league_seasons(cfg):
    """Season codes used for match CSVs (European mmz4281 codes by default;
    calendar years for leagues that use football-data.co.uk /new/ files)."""
    return cfg.get("seasons", SEASONS)


# fixturedownload.com's la-liga-2026 feed stores evening CEST kickoffs as
# morning UTC (AM/PM dropped, CEST offset skipped). 19:30 CEST -> 07:30Z
# instead of 17:30Z. That 10-hour gap is 12h AM/PM + 2h CEST. Hours 12+
# (13:00Z/14:00Z weekend placeholders) are left alone. Idempotent: a
# corrected 17:30Z is not shifted again.
_LALIGA_MORNING_UTC_SHIFT = _dt.timedelta(hours=10)

# Official LALIGA EA SPORTS 2026-27 MD1 rearrangements vs the feed.
_LALIGA_KICKOFF_OVERRIDES = {
    ("Celta", "Osasuna"): "2026-08-27 16:30:00Z",
}


def correct_fixture_dateutc(league_key, date_utc, home=None, away=None):
    """Return a DateUtc string with La Liga feed times aligned to real UTC.

    Alavés vs Getafe 2026-08-15 07:30Z becomes 17:30Z = 11:00 PM IST.
    """
    raw = str(date_utc or "").strip()
    if not raw:
        return raw
    if league_key != "la_liga":
        return raw
    if home is not None and away is not None:
        override = _LALIGA_KICKOFF_OVERRIDES.get((str(home), str(away)))
        if override:
            return override
    parsed = None
    for candidate in (raw.replace("Z", ""), raw.replace("Z", "+00:00")):
        try:
            parsed = _dt.datetime.fromisoformat(candidate)
            break
        except ValueError:
            continue
    if parsed is None:
        return raw
    if parsed.tzinfo is not None:
        parsed = parsed.replace(tzinfo=None)
    if parsed.hour < 12:
        parsed = parsed + _LALIGA_MORNING_UTC_SHIFT
    return parsed.strftime("%Y-%m-%d %H:%M:%S") + "Z"


LEAGUES = {
    "premier_league": {
        "label": "Premier League",
        "country": "England",
        "fd_code": "E0",
        "data_dir": "data/pl_5yr",
        "model_dir": "model/pl",
        "output_name": "PremierLeague",
        "understat_league": "EPL",
        "fixturedownload_slug": "epl-2026",
        "name_map": {
            "Manchester United": "Man United", "Manchester City": "Man City",
            "Tottenham": "Tottenham", "Newcastle United": "Newcastle",
            "Wolverhampton Wanderers": "Wolves", "Nottingham Forest": "Nott'm Forest",
            "Leeds": "Leeds", "Leicester": "Leicester", "West Bromwich Albion": "West Brom",
            "Brighton": "Brighton", "West Ham": "West Ham", "Sheffield United": "Sheffield United",
        },
        "fixture_name_map": {"Spurs": "Tottenham", "Man Utd": "Man United"},
    },
    "la_liga": {
        "label": "La Liga",
        "country": "Spain",
        "fd_code": "SP1",
        "data_dir": "data/laliga_5yr",
        "model_dir": "model/laliga",
        "output_name": "LaLiga",
        "understat_league": "La_liga",
        "fixturedownload_slug": "la-liga-2026",
        "name_map": {
            "Atletico Madrid": "Ath Madrid", "Athletic Club": "Ath Bilbao",
            "Real Betis": "Betis", "Celta Vigo": "Celta", "Deportivo Alaves": "Alaves",
            "Rayo Vallecano": "Vallecano", "Real Sociedad": "Sociedad",
            "Espanyol": "Espanol", "Cadiz": "Cadiz",
        },
        "fixture_name_map": {
            "Athletic Club": "Ath Bilbao", "Atlético de Madrid": "Ath Madrid",
            "CA Osasuna": "Osasuna", "Deportivo Alavés": "Alaves",
            "Elche CF": "Elche", "FC Barcelona": "Barcelona", "Getafe CF": "Getafe",
            "Levante UD": "Levante", "RCD Espanyol de Barcelona": "Espanol",
            "Rayo Vallecano": "Vallecano", "Real Betis": "Betis",
            "Real Sociedad": "Sociedad", "Sevilla FC": "Sevilla",
            "Valencia CF": "Valencia", "Villarreal CF": "Villarreal",
        },
    },
    "serie_a": {
        "label": "Serie A",
        "country": "Italy",
        "fd_code": "I1",
        "data_dir": "data/seriea_5yr",
        "model_dir": "model/seriea",
        "output_name": "SerieA",
        "understat_league": "Serie_A",
        "fixturedownload_slug": "serie-a-2026",
        "name_map": {
            "AC Milan": "Milan", "Inter": "Inter", "Hellas Verona": "Verona",
            "AS Roma": "Roma",
        },
        "fixture_name_map": {},
    },
    "bundesliga": {
        "label": "Bundesliga",
        "country": "Germany",
        "fd_code": "D1",
        "data_dir": "data/bundesliga_5yr",
        "model_dir": "model/bundesliga",
        "output_name": "Bundesliga",
        "understat_league": "Bundesliga",
        "fixturedownload_slug": "bundesliga-2026",
        "name_map": {
            "Bayern Munich": "Bayern Munich", "Borussia Dortmund": "Dortmund",
            "Bayer Leverkusen": "Leverkusen", "Eintracht Frankfurt": "Ein Frankfurt",
            "Borussia M.Gladbach": "M'gladbach", "FC Koln": "FC Koln",
            "Werder Bremen": "Werder Bremen", "RasenBallsport Leipzig": "RB Leipzig",
        },
        "fixture_name_map": {
            "1. FC Köln": "FC Koln", "1. FC Union Berlin": "Union Berlin",
            "1. FSV Mainz 05": "Mainz", "Bayer 04 Leverkusen": "Leverkusen",
            "Borussia Mönchengladbach": "M'gladbach", "Eintracht Frankfurt": "Ein Frankfurt",
            "FC Augsburg": "Augsburg", "FC Bayern München": "Bayern Munich",
            "FC Schalke 04": "Schalke 04", "Hamburger SV": "Hamburg",
            "SC Paderborn 07": "Paderborn", "SV Werder Bremen": "Werder Bremen",
            "Sport-Club Freiburg": "Freiburg", "TSG Hoffenheim": "Hoffenheim",
            "VfB Stuttgart": "Stuttgart",
        },
    },
    "ligue_1": {
        "label": "Ligue 1",
        "country": "France",
        "fd_code": "F1",
        "data_dir": "data/ligue1_5yr",
        "model_dir": "model/ligue1",
        "output_name": "Ligue1",
        "understat_league": "Ligue_1",
        "fixturedownload_slug": "ligue-1-2026",
        "name_map": {
            "Paris Saint Germain": "Paris SG", "Saint-Etienne": "St Etienne",
            "Stade Rennais": "Rennes",
        },
        "fixture_name_map": {
            "AJ Auxerre": "Auxerre", "AS Monaco": "Monaco", "Angers SCO": "Angers",
            "Estac Troyes": "Troyes", "FC Lorient": "Lorient",
            "Havre Athletic Club": "Le Havre", "LOSC Lille": "Lille",
            "OGC Nice": "Nice", "Olympique Lyonnais": "Lyon",
            "Olympique de Marseille": "Marseille", "Paris Saint-Germain": "Paris SG",
            "RC Lens": "Lens", "RC Strasbourg Alsace": "Strasbourg",
            "Stade Brestois 29": "Brest", "Stade Rennais FC": "Rennes",
            "Toulouse FC": "Toulouse",
        },
    },
    # No Understat coverage for Portugal (understat_league stays None, same
    # gap as MLS-without-ASA) -- squad_quality_by_season() degrades to {}
    # gracefully, so this league trains on goals/Elo/H2H/market-odds signal
    # only, same as every other league before its squad-quality features
    # existed.
    "primeira_liga": {
        "label": "Primeira Liga",
        "country": "Portugal",
        "fd_code": "P1",
        "data_dir": "data/primeiraliga_5yr",
        "model_dir": "model/primeiraliga",
        "output_name": "PrimeiraLiga",
        "understat_league": None,
        "fixturedownload_slug": "primeira-liga-2026",
        "name_map": {},
        "fixture_name_map": {
            "CD Nacional": "Nacional", "Casa Pia AC": "Casa Pia",
            "Estoril Praia": "Estoril", "Estrela Amadora": "Estrela",
            "FC Alverca": "Alverca", "FC Arouca": "Arouca",
            "FC Famalicão": "Famalicao", "FC Porto": "Porto",
            "Gil Vicente FC": "Gil Vicente", "Marítimo M.": "Maritimo",
            "Moreirense FC": "Moreirense", "Rio Ave FC": "Rio Ave",
            "SC Braga": "Sp Braga", "SL Benfica": "Benfica",
            "Sporting CP": "Sp Lisbon", "Vitória SC": "Guimaraes",
            "Académico": "Academico Viseu",
        },
    },
    # MLS uses football-data.co.uk /new/USA.csv (calendar seasons, results+odds
    # only -- no shot/corner/card match stats). Player xG / g+ comes from the
    # American Soccer Analysis public API (Understat has no MLS coverage).
    "mls": {
        "label": "MLS",
        "country": "USA",
        "fd_code": "USA",
        "data_format": "fd_new",
        "fd_new_url": "https://www.football-data.co.uk/new/USA.csv",
        "fd_new_league": "MLS",
        "data_dir": "data/mls_5yr",
        "model_dir": "model/mls",
        "output_name": "MLS",
        "understat_league": None,
        "asa_league": "mls",
        "seasons": ["2019", "2020", "2021", "2022", "2023", "2024", "2025", "2026"],
        "display_seasons": ["2022", "2023", "2024", "2025", "2026"],
        "fixturedownload_slug": "mls-2026",
        "name_map": {
            # ASA team_name -> football-data HomeTeam
            "Atlanta United FC": "Atlanta Utd",
            "Austin FC": "Austin FC",
            "CF Montréal": "CF Montreal",
            "CF Montreal": "CF Montreal",
            "Charlotte FC": "Charlotte",
            "Chicago Fire FC": "Chicago Fire",
            "Colorado Rapids": "Colorado Rapids",
            "Columbus Crew": "Columbus Crew",
            "D.C. United": "DC United",
            "FC Cincinnati": "FC Cincinnati",
            "FC Dallas": "FC Dallas",
            "Houston Dynamo FC": "Houston Dynamo",
            "Inter Miami CF": "Inter Miami",
            "LA Galaxy": "Los Angeles Galaxy",
            "Los Angeles FC": "Los Angeles FC",
            "Minnesota United FC": "Minnesota United",
            "Nashville SC": "Nashville SC",
            "New England Revolution": "New England Revolution",
            "New York City FC": "New York City",
            "New York Red Bulls": "New York Red Bulls",
            "Orlando City SC": "Orlando City",
            "Philadelphia Union": "Philadelphia Union",
            "Portland Timbers FC": "Portland Timbers",
            "Real Salt Lake": "Real Salt Lake",
            "San Diego FC": "San Diego FC",
            "San Jose Earthquakes": "San Jose Earthquakes",
            "Seattle Sounders FC": "Seattle Sounders",
            "Sporting Kansas City": "Sporting Kansas City",
            "St. Louis City SC": "St. Louis City",
            "Toronto FC": "Toronto FC",
            "Vancouver Whitecaps FC": "Vancouver Whitecaps",
        },
        "fixture_name_map": {
            "Atlanta United": "Atlanta Utd",
            "CF Montréal": "CF Montreal",
            "CF Montreal": "CF Montreal",
            "Charlotte FC": "Charlotte",
            "Chicago Fire FC": "Chicago Fire",
            "D.C. United": "DC United",
            "Houston Dynamo FC": "Houston Dynamo",
            "Inter Miami CF": "Inter Miami",
            "LA Galaxy": "Los Angeles Galaxy",
            "Los Angeles Football Club": "Los Angeles FC",
            "Minnesota United FC": "Minnesota United",
            "New York City Football Club": "New York City",
            "Red Bull New York": "New York Red Bulls",
            "Seattle Sounders FC": "Seattle Sounders",
            "St. Louis CITY SC": "St. Louis City",
            "Vancouver Whitecaps FC": "Vancouver Whitecaps",
            "Orlando City": "Orlando City",
        },
    },
}
