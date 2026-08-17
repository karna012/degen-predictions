"""
Cosmetic-only display metadata for the league picker/landing page. Kept
separate from scripts/leagues.py (which drives the actual data/training
pipeline) since this is pure UI: a monogram + flag + accent color badge per
league, standing in for official league logos. Real logos would mean
hotlinking or redistributing trademarked images -- fragile (Wikipedia
rate-limits hotlinks) and legally murkier than a simple custom badge once
this is anything more than a private demo.
"""
LEAGUE_DISPLAY = {
    "premier_league": {
        "monogram": "PL", "flag": "\U0001F3F4\U000E0067\U000E0062\U000E0065\U000E006E\U000E0067\U000E007F",
        "color": "#3d195b", "country": "England",
        "keywords": "Premier League predictions, EPL predictions, English football predictions, "
                    "soccer predictions, Premier League tips, EPL tips today",
    },
    "la_liga": {
        "monogram": "LL", "flag": "\U0001F1EA\U0001F1F8", "color": "#ee8707", "country": "Spain",
        "keywords": "La Liga predictions, LaLiga predictions, Spanish football predictions, "
                    "soccer predictions, La Liga tips, Real Madrid Barcelona predictions",
    },
    "serie_a": {
        "monogram": "SA", "flag": "\U0001F1EE\U0001F1F9", "color": "#008fd7", "country": "Italy",
        "keywords": "Serie A predictions, Italian football predictions, soccer predictions, "
                    "Serie A tips, Serie A match predictions",
    },
    "bundesliga": {
        "monogram": "BL", "flag": "\U0001F1E9\U0001F1EA", "color": "#d20515", "country": "Germany",
        "keywords": "Bundesliga predictions, German football predictions, soccer predictions, "
                    "Bundesliga tips, Bundesliga match predictions",
    },
    "ligue_1": {
        "monogram": "L1", "flag": "\U0001F1EB\U0001F1F7", "color": "#091c3e", "country": "France",
        "keywords": "Ligue 1 predictions, French football predictions, soccer predictions, "
                    "Ligue 1 tips, PSG match predictions",
    },
    "mls": {
        "monogram": "MLS", "flag": "\U0001F1FA\U0001F1F8", "color": "#c39e6d", "country": "USA",
        "keywords": "MLS predictions, Major League Soccer predictions, MLS tips, "
                    "Inter Miami predictions, soccer predictions USA",
    },
    "primeira_liga": {
        "monogram": "PPL", "flag": "\U0001F1F5\U0001F1F9", "color": "#006600", "country": "Portugal",
        "keywords": "Primeira Liga predictions, Portuguese football predictions, soccer predictions, "
                    "Primeira Liga tips, Benfica Porto Sporting predictions",
    },
}
