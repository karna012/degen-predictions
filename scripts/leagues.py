"""
Central config for every league the pipeline supports. Adding a league
means adding one entry here -- build_features.py, train_models.py,
predict.py, and backtest.py all read from this instead of hardcoding
Premier-League-only paths/codes.
"""
SEASONS = ["1920", "2021", "2122", "2223", "2324", "2425", "2526"]

UNDERSTAT_SEASON_MAP = {
    "1920": "2019", "2021": "2020", "2122": "2021", "2223": "2022",
    "2324": "2023", "2425": "2024", "2526": "2025",
}

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
}
