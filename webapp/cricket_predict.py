"""Cricket prediction helpers -- The Hundred, MLC, IPL, BBL, and CPL.

Historical rows use the leakage-free feature matrix from
scripts/cricket/build_training_dataset.py. Live fixtures use provisional
last-known XIs + Elo/venue/sentiment when full ball-by-ball history isn't
available yet for that fixture.
"""
from __future__ import annotations

import datetime
import json
import os
import re

import joblib
import pandas as pd
import requests

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data", "cricket")
SCRIPTS_CRICKET = os.path.join(BASE_DIR, "scripts", "cricket")

COMPETITION_LABELS = {
    "hnd_male": "The Hundred (Men's)",
    "mlc_male": "Major League Cricket (Men's)",
    "ipl_male": "Indian Premier League",
    "bbl_male": "Big Bash League",
    "cpl_male": "Caribbean Premier League",
}

FEATURES = [
    "EloDiff", "HomeAdvantage", "Comp_ipl", "Comp_bbl", "Comp_hnd", "Comp_mlc", "Comp_cpl",
    "Team1RecentWinRate", "Team2RecentWinRate", "Team1RecentRunRate", "Team2RecentRunRate",
    "VenueAvgFirstInnings", "VenueAvgSecondInnings", "VenueAvgRunsPerOver", "VenueAvgWicketsPerOver",
    "VenueMatchesSeen", "Team1AvgSR", "Team2AvgSR", "Team1AvgEcon", "Team2AvgEcon",
    "Team1DeathUplift", "Team2DeathUplift", "Team1MatchupEdge", "Team2MatchupEdge", "SentimentDiff",
]

_models_cache = None
_json_cache = {}


def _load_model(name):
    path = os.path.join(DATA_DIR, name)
    if not os.path.exists(path):
        return None
    return joblib.load(path)


def load_models():
    global _models_cache
    if _models_cache is None:
        _models_cache = {
            "team1_runs": _load_model("team1_runs_model.joblib"),
            "team2_runs": _load_model("team2_runs_model.joblib"),
            "total_runs": _load_model("total_runs_model.joblib"),
            "winner": _load_model("winner_model.joblib"),
        }
    return _models_cache


def load_dataset():
    path = os.path.join(DATA_DIR, "training_dataset.csv")
    if not os.path.exists(path):
        return pd.DataFrame()
    df = pd.read_csv(path)
    df["Date"] = pd.to_datetime(df["Date"])
    return df.sort_values("Date").reset_index(drop=True)


def load_elo():
    path = os.path.join(DATA_DIR, "elo_ratings.csv")
    if not os.path.exists(path):
        return pd.DataFrame()
    return pd.read_csv(path)


def load_venues():
    path = os.path.join(DATA_DIR, "venue_stats.csv")
    if not os.path.exists(path):
        return pd.DataFrame()
    return pd.read_csv(path)


def _load_json(name):
    if name in _json_cache:
        return _json_cache[name]
    path = os.path.join(DATA_DIR, name)
    if not os.path.exists(path):
        _json_cache[name] = {}
        return _json_cache[name]
    with open(path, encoding="utf-8") as f:
        _json_cache[name] = json.load(f)
    return _json_cache[name]


def load_provisional_xi():
    return _load_json("provisional_xi.json")


def load_player_form():
    return _load_json("player_form_cache.json")


def load_team_form():
    return _load_json("team_form_cache.json")


def _canonical(name: str) -> str:
    try:
        import sys
        if SCRIPTS_CRICKET not in sys.path:
            sys.path.insert(0, SCRIPTS_CRICKET)
        from cricket_config import canonical_team
        return canonical_team(name)
    except Exception:
        return name


def _team_is_home(team: str, venue: str) -> bool:
    try:
        import sys
        if SCRIPTS_CRICKET not in sys.path:
            sys.path.insert(0, SCRIPTS_CRICKET)
        from cricket_config import team_is_home_at_venue
        return team_is_home_at_venue(team, venue)
    except Exception:
        return False


_upcoming_cache = {}
_UPCOMING_TTL = 10 * 60


def upcoming_fixtures(competition):
    """Real live/upcoming fixtures for this competition (TTL-cached)."""
    try:
        import source_settings
        pref = source_settings.feed_preference("cricket")
        if not pref.get("enabled", True):
            return []
        order = source_settings.provider_order("cricket", kind="fixtures")
    except Exception:
        order = ["fixturedownload", "wikipedia"]

    # History-only primary: don't pretend we have live board data.
    if order and order[0] == "cricsheet":
        return []

    now = datetime.datetime.now().timestamp()
    cached = _upcoming_cache.get(competition)
    if cached and now - cached["fetched_at"] < _UPCOMING_TTL:
        return cached["data"]

    def _from_fd():
        import cricket_fd_fixtures
        base = "ipl" if competition == "ipl_male" else "bbl"
        return cricket_fd_fixtures.get_upcoming(base)

    def _from_hundred():
        import hundred_fixtures
        return hundred_fixtures.get_upcoming_and_live()

    def _from_cpl():
        import cpl_fixtures
        return cpl_fixtures.get_upcoming_and_live()

    raw = []
    used = None
    if competition in ("ipl_male", "bbl_male"):
        for name in order or ["fixturedownload"]:
            if name in ("entity_sport", "api_sports"):
                # Keys exist in vault but live clients are not wired yet.
                continue
            if name == "fixturedownload":
                try:
                    raw = _from_fd()
                except Exception as exc:
                    print(f"upcoming_fixtures({competition}) fd failed: {exc}")
                    raw = []
                if raw:
                    used = name
                    break
            # wikipedia/cricsheet are not live IPL/BBL feeds
    elif competition == "hnd_male":
        for name in order or ["wikipedia"]:
            if name == "wikipedia" or name == "fixturedownload":
                # Hundred is Wikipedia-backed; treat either preference as wiki.
                try:
                    raw = _from_hundred()
                except Exception as exc:
                    print(f"upcoming_fixtures(hnd_male) failed: {exc}")
                    raw = []
                if raw:
                    used = "wikipedia"
                    break
    elif competition == "cpl_male":
        for name in order or ["wikipedia"]:
            if name == "wikipedia" or name == "fixturedownload":
                try:
                    raw = _from_cpl()
                except Exception as exc:
                    print(f"upcoming_fixtures(cpl_male) failed: {exc}")
                    raw = []
                if raw:
                    used = "wikipedia"
                    break
    else:
        raw = []

    out = []
    for f in raw or []:
        if competition in ("ipl_male", "bbl_male", "cpl_male"):
            f = {
                **f,
                "team1_elo_name": _canonical(f.get("team1_elo_name") or f["team1"]),
                "team2_elo_name": _canonical(f.get("team2_elo_name") or f["team2"]),
            }
        pred = predict_live_fixture(f, competition=competition)
        if competition == "hnd_male":
            import hundred_fixtures
            slug = hundred_fixtures.slug_for_fixture(f)
        elif competition in ("ipl_male", "bbl_male"):
            import cricket_fd_fixtures
            slug = cricket_fd_fixtures.slug_for_fixture(f)
        else:
            import cpl_fixtures
            slug = cpl_fixtures.slug_for_fixture(f)
        out.append({
            **f,
            "prediction": pred,
            "slug": slug,
            "insights": fixture_insight_chips(f["team1"], f["team2"], pred),
            "crest_rgb1": team_crest_rgb(f["team1"]),
            "crest_rgb2": team_crest_rgb(f["team2"]),
            "feed_provider": used,
        })

    _upcoming_cache[competition] = {"data": out, "fetched_at": now}
    return out


def season_window(competition):
    df = load_dataset()
    if df.empty:
        return None
    sub = df[df["Competition"] == competition]
    if sub.empty:
        return None
    months = sorted(sub["Date"].dt.month.unique())
    month_name = lambda m: datetime.date(2000, m, 1).strftime("%B")  # noqa: E731
    if len(months) == 1:
        return month_name(months[0])
    return f"{month_name(months[0])}-{month_name(months[-1])}"


def matches_for_competition(competition, limit=30):
    df = load_dataset()
    if df.empty:
        return df
    sub = df[df["Competition"] == competition].copy()
    sub["match_id"] = sub.index
    return sub.sort_values("Date", ascending=False).head(limit)


_CRICKET_NAME_FOLD = {
    "manchester super giants": "manchester originals",
    "manchester originals": "manchester originals",
    "sunrisers leeds": "northern superchargers",
    "northern superchargers": "northern superchargers",
    "mi london": "oval invincibles",
    "oval invincibles": "oval invincibles",
    "jamaica kingsmen": "jamaica tallawahs",
    "jamaica tallawahs": "jamaica tallawahs",
    "barbados tridents": "barbados royals",
    "barbados royals": "barbados royals",
}


def _norm_cricket_team(name: str) -> str:
    raw = re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()
    return _CRICKET_NAME_FOLD.get(raw, raw)


def result_pair_key(date, team1, team2):
    day = str(date or "")[:10]
    return (day, frozenset({_norm_cricket_team(team1), _norm_cricket_team(team2)}))


def _parse_score_runs(raw):
    if raw is None:
        return None
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        pass
    m = re.match(r"^\s*(\d+)", str(raw))
    return int(m.group(1)) if m else None


def _winner_from_result_text(result, team1, team2):
    blob = (result or "").lower()
    if "won" not in blob and "tied" not in blob:
        return None
    for name in (team1, team2):
        if name and name.lower() in blob:
            return name
    return None


def live_completed_result_views(competition, limit=40):
    """Completed current-season games from Wikipedia (Hundred/CPL).

    Cricsheet archives lag days behind live results; the public /results
    pages must still show finished matches.
    """
    fixtures = []
    slug_fn = None
    if competition == "hnd_male":
        try:
            import hundred_fixtures
            fixtures = hundred_fixtures.get_hundred_fixtures() or []
            slug_fn = hundred_fixtures.slug_for_fixture
        except Exception:
            fixtures = []
    elif competition == "cpl_male":
        try:
            import cpl_fixtures
            fixtures = cpl_fixtures.get_cpl_fixtures() or []
            slug_fn = cpl_fixtures.slug_for_fixture
        except Exception:
            fixtures = []
    else:
        return []

    out = []
    for f in fixtures:
        if not f.get("completed"):
            continue
        t1, t2 = f.get("team1") or "", f.get("team2") or ""
        s1 = _parse_score_runs(f.get("score1"))
        s2 = _parse_score_runs(f.get("score2"))
        pred = predict_live_fixture(f, competition=competition)
        predicted_winner = None
        if pred and pred.get("team1_win_prob") is not None:
            predicted_winner = t1 if pred["team1_win_prob"] >= 0.5 else t2
        actual_winner = _winner_from_result_text(f.get("result"), t1, t2)
        out.append({
            "match_id": None,
            "slug": slug_fn(f) if slug_fn else None,
            "date": f.get("date"),
            "competition": competition,
            "competition_label": COMPETITION_LABELS.get(competition, competition),
            "venue": f.get("venue"),
            "team1": t1,
            "team2": t2,
            "predicted_team1_runs": pred.get("team1_runs") if pred else None,
            "predicted_team2_runs": pred.get("team2_runs") if pred else None,
            "predicted_total_runs": pred.get("total_runs") if pred else None,
            "team1_win_prob": pred.get("team1_win_prob") if pred else None,
            "predicted_winner": predicted_winner,
            "actual_team1_runs": s1,
            "actual_team2_runs": s2,
            "actual_total_runs": (s1 + s2) if s1 is not None and s2 is not None else None,
            "actual_winner": actual_winner,
            "actual_note": f.get("result"),
            "score_text": " · ".join(
                x for x in (f.get("score1"), f.get("score2")) if x
            ) or None,
            "data_confidence": (pred or {}).get("data_confidence") or "medium",
            "insights": [],
            "crest_rgb1": team_crest_rgb(t1),
            "crest_rgb2": team_crest_rgb(t2),
            "live_scoreboard": True,
        })
    out.sort(key=lambda m: m.get("date") or "", reverse=True)
    return out[:limit]


def merge_competition_result_views(archive_views, live_views, limit=40):
    """Prefer Cricsheet rows when both exist; keep newer Wikipedia finals."""
    seen = set()
    for m in archive_views:
        seen.add(result_pair_key(m.get("date"), m.get("team1"), m.get("team2")))
    extra = [
        m for m in live_views
        if result_pair_key(m.get("date"), m.get("team1"), m.get("team2")) not in seen
    ]
    merged = list(extra) + list(archive_views)
    merged.sort(key=lambda m: m.get("date") or "", reverse=True)
    return merged[:limit]


def get_match_row(match_id):
    df = load_dataset()
    if df.empty or match_id not in df.index:
        return None
    return df.loc[match_id]


def predict_for_row(row):
    models = load_models()
    feat = {}
    for col in FEATURES:
        feat[col] = row[col] if col in row.index and pd.notna(row[col]) else None
    X = pd.DataFrame([feat])

    def _predict(model_entry):
        if model_entry is None:
            return None
        return float(model_entry["model"].predict(X)[0])

    team1_runs = _predict(models["team1_runs"])
    team2_runs = _predict(models["team2_runs"])
    total_runs = _predict(models["total_runs"])
    winner_model = models["winner"]
    if winner_model is not None:
        proba = winner_model["model"].predict_proba(X)[0]
        classes = list(winner_model["model"].classes_)
        team1_win_prob = float(proba[classes.index(1)]) if 1 in classes else float(proba[0])
    else:
        team1_win_prob = None

    return {
        "team1_runs": round(team1_runs, 1) if team1_runs is not None else None,
        "team2_runs": round(team2_runs, 1) if team2_runs is not None else None,
        "total_runs": round(total_runs, 1) if total_runs is not None else None,
        "team1_win_prob": round(team1_win_prob, 3) if team1_win_prob is not None else None,
        "data_confidence": row.get("DataConfidence", "unknown"),
    }


VENUE_COORDS = {
    # Hundred / MLC
    "Kennington Oval, London": (51.4816, -0.1160),
    "Old Trafford, Manchester": (53.4568, -2.2913),
    "Edgbaston, Birmingham": (52.4558, -1.9022),
    "Headingley, Leeds": (53.8168, -1.5824),
    "Lord's, London": (51.5294, -0.1729),
    "The Rose Bowl, Southampton": (50.9271, -1.3183),
    "Sophia Gardens, Cardiff": (51.4816, -3.1873),
    "Trent Bridge, Nottingham": (52.9372, -1.1327),
    "Grand Prairie Stadium": (32.7459, -96.9978),
    "Grand Prairie Stadium, Dallas": (32.7459, -96.9978),
    "Church Street Park, Morrisville": (35.8384, -78.8256),
    "Central Broward Regional Park Stadium Turf Ground, Lauderhill": (26.1478, -80.2478),
    "Oakland Coliseum,Oakland": (37.7516, -122.2005),
    "Knight Riders Cricket Ground, Pomona": (34.0567, -117.7514),
    # IPL
    "Wankhede Stadium": (18.9389, 72.8258),
    "Eden Gardens": (22.5646, 88.3433),
    "M Chinnaswamy Stadium": (12.9788, 77.5996),
    "MA Chidambaram Stadium, Chepauk": (13.0628, 80.2794),
    "Arun Jaitley Stadium": (28.6376, 77.2432),
    "Feroz Shah Kotla": (28.6376, 77.2432),
    "Rajiv Gandhi International Stadium, Uppal": (17.4062, 78.5505),
    "Sawai Mansingh Stadium": (26.8940, 75.8030),
    "Punjab Cricket Association IS Bindra Stadium, Mohali": (30.6909, 76.7372),
    "Narendra Modi Stadium, Ahmedabad": (23.0915, 72.5975),
    "Bharat Ratna Shri Atal Bihari Vajpayee Ekana Cricket Stadium, Lucknow": (26.8100, 81.0210),
    # BBL
    "Melbourne Cricket Ground": (-37.8199, 144.9834),
    "Sydney Cricket Ground": (-33.8915, 151.2247),
    "Adelaide Oval": (-34.9156, 138.5962),
    "Perth Stadium": (-31.9505, 115.8890),
    "Brisbane Cricket Ground, Woolloongabba": (-27.4858, 153.0381),
    "Docklands Stadium": (-37.8165, 144.9475),
    "Bellerive Oval": (-42.8770, 147.3740),
    "Sydney Showground Stadium": (-33.8430, 151.0670),
    "Manuka Oval": (-35.3180, 149.1340),
}

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
WEATHER_CACHE_TTL_SECONDS = 3 * 3600
_weather_cache = {}


def get_venue_weather(venue_name, match_date_str):
    try:
        coords = VENUE_COORDS.get(venue_name)
        if not coords:
            # substring fallback
            for key, c in VENUE_COORDS.items():
                if venue_name and (venue_name.lower() in key.lower() or key.lower() in venue_name.lower()):
                    coords = c
                    break
        if not coords:
            return None
        match_date = datetime.datetime.strptime(match_date_str, "%Y-%m-%d").date()
        today = datetime.datetime.now(datetime.timezone.utc).date()
        if not (today <= match_date <= today + datetime.timedelta(days=16)):
            return None

        cache_key = (venue_name, match_date_str)
        cached = _weather_cache.get(cache_key)
        now = datetime.datetime.now().timestamp()
        if cached and now - cached["fetched_at"] < WEATHER_CACHE_TTL_SECONDS:
            return cached["data"]

        lat, lon = coords
        resp = requests.get(OPEN_METEO_URL, params={
            "latitude": lat, "longitude": lon,
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weathercode",
            "timezone": "auto", "start_date": match_date_str, "end_date": match_date_str,
        }, timeout=8)
        resp.raise_for_status()
        daily = resp.json().get("daily", {})
        if not daily.get("time"):
            return None
        data = {
            "temp_max_c": daily["temperature_2m_max"][0],
            "temp_min_c": daily["temperature_2m_min"][0],
            "precipitation_probability_pct": daily["precipitation_probability_max"][0],
            "date": match_date_str,
        }
        _weather_cache[cache_key] = {"data": data, "fetched_at": now}
        return data
    except Exception:
        return None


def elo_win_probability(elo1, elo2):
    return 1.0 / (1.0 + 10 ** (-(elo1 - elo2) / 400.0))


def _xi_team_features(competition, team):
    """Aggregate provisional XI player form into model-compatible features."""
    xi_blob = (load_provisional_xi().get(competition) or {}).get(team) or {}
    xi = xi_blob.get("xi") or []
    form = (load_player_form().get(competition) or {}).get(team) or {}
    srs, econs, uplifts = [], [], []
    for p in xi:
        stats = form.get(p) or {}
        if stats.get("strike_rate") is not None and (stats.get("bat_innings") or 0) >= 3:
            srs.append(float(stats["strike_rate"]))
        if stats.get("economy") is not None and (stats.get("bowl_innings") or 0) >= 3:
            econs.append(float(stats["economy"]))
        if stats.get("death_uplift") is not None:
            uplifts.append(float(stats["death_uplift"]))
    known = len(xi) > 0 and (len(srs) / max(len(xi), 1)) >= 0.4
    return {
        "xi": xi,
        "label": xi_blob.get("label") or "provisional",
        "avg_sr": sum(srs) / len(srs) if srs else None,
        "avg_econ": sum(econs) / len(econs) if econs else None,
        "death_uplift": sum(uplifts) / len(uplifts) if uplifts else None,
        "known": known,
        "players": [
            {"name": p, **(form.get(p) or {"appearances": 0})}
            for p in xi
        ],
    }


def _live_sentiment_diff(team1, team2):
    try:
        from sentiment import get_team_sentiment, prefetch_team_sentiments
        prefetch_team_sentiments([team1, team2])
        s1 = get_team_sentiment(team1)
        s2 = get_team_sentiment(team2)
        if s1 is None or s2 is None:
            return None, s1, s2
        return s1 - s2, s1, s2
    except Exception:
        return None, None, None


def predict_live_fixture(fixture, competition=None):
    """Live prediction: prefer full ML feature vector when provisional XI
    form exists; otherwise Elo + venue. Always labels the basis honestly."""
    elo_df = load_elo()
    if elo_df.empty:
        return None
    team1_name = _canonical(fixture.get("team1_elo_name", fixture["team1"]))
    team2_name = _canonical(fixture.get("team2_elo_name", fixture["team2"]))
    row1 = elo_df[elo_df["Team"] == team1_name]
    row2 = elo_df[elo_df["Team"] == team2_name]
    if row1.empty or row2.empty:
        return None

    elo1, elo2 = float(row1.iloc[0]["Elo"]), float(row2.iloc[0]["Elo"])
    venue = fixture.get("venue") or ""
    ha = 0.0
    if competition and competition in ("ipl_male", "bbl_male"):
        try:
            import sys
            if SCRIPTS_CRICKET not in sys.path:
                sys.path.insert(0, SCRIPTS_CRICKET)
            from cricket_config import ELO_HOME_ADVANTAGE
            if _team_is_home(team1_name, venue):
                ha = ELO_HOME_ADVANTAGE
            elif _team_is_home(team2_name, venue):
                ha = -ELO_HOME_ADVANTAGE
        except Exception:
            ha = 0.0

    venues = load_venues()
    venue_row = venues[venues["Venue"] == venue] if not venues.empty and venue else pd.DataFrame()
    if venue_row.empty and venue and not venues.empty:
        # fuzzy contains
        mask = venues["Venue"].str.contains(venue.split(",")[0], case=False, na=False)
        venue_row = venues[mask]

    venue_known = not venue_row.empty
    if venue_known:
        v = venue_row.iloc[0]
        v_first = float(v["AvgFirstInningsTotal"]) if pd.notna(v["AvgFirstInningsTotal"]) else None
        v_second = float(v["AvgSecondInningsTotal"]) if pd.notna(v["AvgSecondInningsTotal"]) else None
        v_rpo = float(v["AvgRunsPerOver"]) if pd.notna(v.get("AvgRunsPerOver")) else None
        v_wpo = float(v["AvgWicketsPerOver"]) if pd.notna(v.get("AvgWicketsPerOver")) else None
        v_seen = int(v["MatchesPlayed"])
    else:
        v_first = v_second = v_rpo = v_wpo = None
        v_seen = 0

    xi1 = _xi_team_features(competition, team1_name) if competition else {"known": False}
    xi2 = _xi_team_features(competition, team2_name) if competition else {"known": False}
    sent_diff, sent1, sent2 = _live_sentiment_diff(team1_name, team2_name)

    use_ml = bool(competition and xi1.get("known") and xi2.get("known") and load_models().get("winner"))
    basis = "Elo + venue average (live estimate)"
    team1_win_prob = elo_win_probability(elo1 + (ha if ha > 0 else 0), elo2 + (-ha if ha < 0 else 0))
    team1_runs = v_first
    team2_runs = v_second
    if team1_runs is None and pd.notna(row1.iloc[0].get("RecentRunRate")):
        team1_runs = float(row1.iloc[0]["RecentRunRate"]) * 20
    if team2_runs is None and pd.notna(row2.iloc[0].get("RecentRunRate")):
        team2_runs = float(row2.iloc[0]["RecentRunRate"]) * 20

    if use_ml:
        feat = {
            "EloDiff": (elo1 + (ha if ha > 0 else 0)) - (elo2 + (-ha if ha < 0 else 0)),
            "HomeAdvantage": 1.0 if ha != 0 else 0.0,
            "Comp_ipl": 1.0 if competition == "ipl_male" else 0.0,
            "Comp_bbl": 1.0 if competition == "bbl_male" else 0.0,
            "Comp_hnd": 1.0 if competition == "hnd_male" else 0.0,
            "Comp_mlc": 1.0 if competition == "mlc_male" else 0.0,
            "Comp_cpl": 1.0 if competition == "cpl_male" else 0.0,
            "Team1RecentWinRate": float(row1.iloc[0]["RecentWinRate"]) if pd.notna(row1.iloc[0].get("RecentWinRate")) else 0.5,
            "Team2RecentWinRate": float(row2.iloc[0]["RecentWinRate"]) if pd.notna(row2.iloc[0].get("RecentWinRate")) else 0.5,
            "Team1RecentRunRate": float(row1.iloc[0]["RecentRunRate"]) if pd.notna(row1.iloc[0].get("RecentRunRate")) else None,
            "Team2RecentRunRate": float(row2.iloc[0]["RecentRunRate"]) if pd.notna(row2.iloc[0].get("RecentRunRate")) else None,
            "VenueAvgFirstInnings": v_first,
            "VenueAvgSecondInnings": v_second,
            "VenueAvgRunsPerOver": v_rpo,
            "VenueAvgWicketsPerOver": v_wpo,
            "VenueMatchesSeen": v_seen,
            "Team1AvgSR": xi1.get("avg_sr"),
            "Team2AvgSR": xi2.get("avg_sr"),
            "Team1AvgEcon": xi1.get("avg_econ"),
            "Team2AvgEcon": xi2.get("avg_econ"),
            "Team1DeathUplift": xi1.get("death_uplift"),
            "Team2DeathUplift": xi2.get("death_uplift"),
            "Team1MatchupEdge": None,
            "Team2MatchupEdge": None,
            "SentimentDiff": sent_diff,
        }
        X = pd.DataFrame([feat])
        models = load_models()
        try:
            if models["team1_runs"]:
                team1_runs = float(models["team1_runs"]["model"].predict(X)[0])
            if models["team2_runs"]:
                team2_runs = float(models["team2_runs"]["model"].predict(X)[0])
            if models["winner"]:
                proba = models["winner"]["model"].predict_proba(X)[0]
                classes = list(models["winner"]["model"].classes_)
                team1_win_prob = float(proba[classes.index(1)]) if 1 in classes else float(proba[0])
            basis = "Full ML (provisional XI + Elo + venue + sentiment)"
        except Exception:
            basis = "Elo + venue average (ML fallback after feature error)"

    # Soft sentiment nudge on Elo path only (ML already includes SentimentDiff)
    if not use_ml and sent_diff is not None:
        team1_win_prob = max(0.05, min(0.95, team1_win_prob + 0.03 * sent_diff))

    total = (team1_runs + team2_runs) if team1_runs is not None and team2_runs is not None else None
    return {
        "team1_win_prob": round(team1_win_prob, 3),
        "team2_win_prob": round(1 - team1_win_prob, 3),
        "predicted_team1_runs": round(team1_runs, 1) if team1_runs is not None else None,
        "predicted_team2_runs": round(team2_runs, 1) if team2_runs is not None else None,
        "predicted_total_runs": round(total, 1) if total is not None else None,
        "elo_team1": round(elo1, 1), "elo_team2": round(elo2, 1),
        "venue_known": venue_known,
        "home_advantage_applied": ha != 0,
        "basis": basis,
        "sentiment_team1": sent1,
        "sentiment_team2": sent2,
        "sentiment_diff": round(sent_diff, 3) if sent_diff is not None else None,
        # MLC is genuinely too young to trust yet -- confirmed against Cricsheet
        # directly, only 109 matches have ever been played, and the trained
        # model's real holdout accuracy (48.3% full-slate) never clears even a
        # relaxed confidence bar. use_ml/venue_known being True here means the
        # model *ran*, not that its picks are reliable -- always show "low"
        # for this one competition regardless, rather than a "medium" badge
        # implying more confidence than the calibration data actually supports.
        "data_confidence": (
            "low" if competition == "mlc_male" else ("medium" if (use_ml or venue_known) else "low")
        ),
    }


def get_team_analysis(competition, team1, team2):
    elo = load_elo()
    form = load_team_form().get(competition) or {}
    t1, t2 = _canonical(team1), _canonical(team2)

    def pack(name):
        row = elo[elo["Team"] == name] if not elo.empty else pd.DataFrame()
        tf = form.get(name) or {}
        return {
            "name": name,
            "elo": float(row.iloc[0]["Elo"]) if not row.empty else None,
            "recent_win_rate": float(row.iloc[0]["RecentWinRate"]) if not row.empty and pd.notna(row.iloc[0].get("RecentWinRate")) else None,
            "recent_run_rate": float(row.iloc[0]["RecentRunRate"]) if not row.empty and pd.notna(row.iloc[0].get("RecentRunRate")) else None,
            "season_win_rate": tf.get("win_rate"),
            "avg_runs_for": tf.get("avg_runs_for"),
            "avg_runs_against": tf.get("avg_runs_against"),
            "matches": tf.get("matches"),
        }

    # H2H from match history
    hist_path = os.path.join(DATA_DIR, "match_history.csv")
    h2h = {"meetings": 0, "team1_wins": 0, "team2_wins": 0}
    if os.path.exists(hist_path):
        hist = pd.read_csv(hist_path)
        sub = hist[
            ((hist["Team1"] == t1) & (hist["Team2"] == t2)) |
            ((hist["Team1"] == t2) & (hist["Team2"] == t1))
        ]
        if competition:
            sub = sub[sub["Competition"] == competition]
        h2h["meetings"] = int(len(sub))
        h2h["team1_wins"] = int((sub["Winner"] == t1).sum())
        h2h["team2_wins"] = int((sub["Winner"] == t2).sum())

    return {"team1": pack(t1), "team2": pack(t2), "h2h": h2h}


def get_player_analysis(competition, team1, team2, top_n=5):
    def top_players(team):
        form = (load_player_form().get(competition) or {}).get(_canonical(team)) or {}
        xi_info = _xi_team_features(competition, _canonical(team))
        batters = sorted(
            [p for p in xi_info["players"] if p.get("strike_rate") is not None],
            key=lambda r: r.get("strike_rate") or 0, reverse=True,
        )[:top_n]
        bowlers = sorted(
            [p for p in xi_info["players"] if p.get("economy") is not None],
            key=lambda r: r.get("economy") or 99,
        )[:top_n]
        return {
            "xi": xi_info["xi"],
            "label": xi_info["label"],
            "top_batters": batters,
            "top_bowlers": bowlers,
            "all_form_players": len(form),
        }

    return {"team1": top_players(team1), "team2": top_players(team2)}


def get_home_ground_report(venue, team1, team2):
    pitch = get_venue_pitch_report(venue)
    venues = load_venues()
    home_team = None
    if _team_is_home(team1, venue):
        home_team = team1
    elif _team_is_home(team2, venue):
        home_team = team2
    extra = {}
    if not venues.empty and venue:
        match = venues[venues["Venue"] == venue]
        if match.empty:
            match = venues[venues["Venue"].str.contains(venue.split(",")[0], case=False, na=False)]
        if not match.empty:
            v = match.iloc[0]
            extra = {
                "avg_home_team_runs": v.get("AvgHomeTeamRuns"),
                "avg_away_team_runs": v.get("AvgAwayTeamRuns"),
                "home_matches": int(v["HomeMatches"]) if pd.notna(v.get("HomeMatches")) else 0,
                "away_matches": int(v["AwayMatches"]) if pd.notna(v.get("AwayMatches")) else 0,
            }
    return {
        "pitch": pitch,
        "home_team": home_team,
        "home_advantage_league": home_team is not None,
        **extra,
    }


def get_sentiment_analysis(team1, team2):
    diff, s1, s2 = _live_sentiment_diff(_canonical(team1), _canonical(team2))
    return {
        "team1": team1, "team2": team2,
        "score1": round(s1, 3) if s1 is not None else None,
        "score2": round(s2, 3) if s2 is not None else None,
        "diff": round(diff, 3) if diff is not None else None,
        "note": "Google News headlines via VADER; None means cold cache / no headlines yet.",
    }


def get_match_events_summary(match_id):
    """For completed Cricsheet rows: phase run rates from training features
    aren't stored per-over on the CSV; pull raw JSON if path resolvable."""
    row = get_match_row(match_id)
    if row is None:
        return None
    # Reconstruct a light statistical panel from the stored feature row.
    return {
        "venue_avg_first": float(row["VenueAvgFirstInnings"]) if pd.notna(row.get("VenueAvgFirstInnings")) else None,
        "venue_avg_second": float(row["VenueAvgSecondInnings"]) if pd.notna(row.get("VenueAvgSecondInnings")) else None,
        "venue_rpo": float(row["VenueAvgRunsPerOver"]) if pd.notna(row.get("VenueAvgRunsPerOver")) else None,
        "team1_sr": float(row["Team1AvgSR"]) if pd.notna(row.get("Team1AvgSR")) else None,
        "team2_sr": float(row["Team2AvgSR"]) if pd.notna(row.get("Team2AvgSR")) else None,
        "team1_econ": float(row["Team1AvgEcon"]) if pd.notna(row.get("Team1AvgEcon")) else None,
        "team2_econ": float(row["Team2AvgEcon"]) if pd.notna(row.get("Team2AvgEcon")) else None,
        "team1_death_uplift": float(row["Team1DeathUplift"]) if pd.notna(row.get("Team1DeathUplift")) else None,
        "team2_death_uplift": float(row["Team2DeathUplift"]) if pd.notna(row.get("Team2DeathUplift")) else None,
        "actual_team1_runs": int(row["Team1Runs"]),
        "actual_team2_runs": int(row["Team2Runs"]),
        "actual_total": int(row["TotalRuns"]),
    }


def team_crest_rgb(name):
    """Stable soft RGB for monogram crests (no official logos)."""
    s = str(name or "team")
    h = 0
    for ch in s:
        h = (h * 33 + ord(ch)) & 0xFFFFFF
    # Keep mid-saturation teal/blue/amber family to match site, avoid neon purple
    r = 40 + (h & 0x7F)
    g = 90 + ((h >> 8) & 0x5F)
    b = 120 + ((h >> 16) & 0x5F)
    return f"{r}, {g}, {b}"


def fixture_insight_chips(team1, team2, prediction, *, team_analysis=None, home_ground=None):
    """Short chips for fixture cards / terminal strip."""
    chips = []
    if not prediction or prediction.get("team1_win_prob") is None:
        return chips
    p1 = float(prediction["team1_win_prob"])
    p2 = float(prediction.get("team2_win_prob") or (1 - p1))
    fav = team1 if p1 >= p2 else team2
    fav_p = max(p1, p2)
    if fav_p <= 0.57:
        chips.append({"text": "Coin-flip lean", "accent": True})
        dog = team2 if fav == team1 else team1
        chips.append({"text": f"Upset live: {dog}", "accent": False})
    else:
        chips.append({"text": f"Edge: {fav} · {fav_p * 100:.0f}%", "accent": True})

    total = prediction.get("predicted_total_runs")
    if total is not None:
        chips.append({"text": f"Total ~{total:.0f}", "accent": False})

    if prediction.get("home_advantage_applied"):
        chips.append({"text": "Home adv", "accent": False})

    if team_analysis and team_analysis.get("h2h", {}).get("meetings", 0) >= 4:
        h2h = team_analysis["h2h"]
        chips.append({
            "text": f"H2H {h2h['team1_wins']}-{h2h['team2_wins']} ({h2h['meetings']})",
            "accent": False,
        })

    if home_ground and home_ground.get("home_team"):
        chips.append({"text": f"Home: {home_ground['home_team']}", "accent": False})

    basis = prediction.get("basis") or ""
    if "Full ML" in basis:
        chips.append({"text": "ML + XI", "accent": False})
    elif basis:
        chips.append({"text": "Elo path", "accent": False})

    return chips[:5]


def build_fixture_detail(fixture, competition=None):
    pred = predict_live_fixture(fixture, competition=competition)
    pitch_report = get_venue_pitch_report(fixture.get("venue"))
    weather = get_venue_weather(fixture["venue"], fixture["date"]) if fixture.get("date") else None
    predicted_winner = None
    if pred and pred["team1_win_prob"] is not None:
        predicted_winner = fixture["team1"] if pred["team1_win_prob"] >= 0.5 else fixture["team2"]

    team_analysis = get_team_analysis(competition, fixture["team1"], fixture["team2"]) if competition else None
    player_analysis = get_player_analysis(competition, fixture["team1"], fixture["team2"]) if competition else None
    home_ground = get_home_ground_report(fixture.get("venue"), fixture["team1"], fixture["team2"])
    sentiment = get_sentiment_analysis(fixture["team1"], fixture["team2"])
    insights = fixture_insight_chips(
        fixture["team1"], fixture["team2"], pred,
        team_analysis=team_analysis, home_ground=home_ground,
    )

    return {
        "team1": fixture["team1"], "team2": fixture["team2"],
        "date": fixture.get("date"), "time": fixture.get("time"), "venue": fixture.get("venue"),
        "completed": fixture.get("completed", False),
        "result": fixture.get("result"),
        "score1": fixture.get("score1"),
        "score2": fixture.get("score2"),
        "competition": competition,
        "competition_label": COMPETITION_LABELS.get(competition, competition),
        "prediction": pred,
        "predicted_winner": predicted_winner,
        "pitch_report": pitch_report,
        "weather": weather,
        "team_analysis": team_analysis,
        "player_analysis": player_analysis,
        "home_ground": home_ground,
        "sentiment": sentiment,
        "insights": insights,
        "crest_rgb1": team_crest_rgb(fixture["team1"]),
        "crest_rgb2": team_crest_rgb(fixture["team2"]),
    }


def get_venue_pitch_report(venue_name):
    venues = load_venues()
    if venues.empty or not venue_name:
        return None
    match = venues[venues["Venue"] == venue_name]
    if match.empty:
        match = venues[venues["Venue"].str.contains(str(venue_name).split(",")[0], case=False, na=False)]
    if match.empty:
        return None
    v = match.iloc[0]
    return {
        "venue": v["Venue"],
        "matches_played": int(v["MatchesPlayed"]),
        "avg_first_innings": v["AvgFirstInningsTotal"],
        "avg_second_innings": v["AvgSecondInningsTotal"],
        "pitch_type": v["PitchType"],
        "avg_rpo": v.get("AvgRunsPerOver"),
        "avg_wpo": v.get("AvgWicketsPerOver"),
        "thin_history": int(v["MatchesPlayed"]) < 5,
    }


def build_match_view(row, *, rich=True):
    pred = predict_for_row(row)
    actual_winner = None
    if pd.notna(row.get("Winner")):
        actual_winner = row["Team1"] if row["Winner"] == 1 else row["Team2"]
    predicted_winner = None
    if pred["team1_win_prob"] is not None:
        predicted_winner = row["Team1"] if pred["team1_win_prob"] >= 0.5 else row["Team2"]

    competition = row["Competition"]
    view = {
        "match_id": int(row.name) if hasattr(row, "name") else None,
        "date": row["Date"].strftime("%Y-%m-%d") if pd.notna(row["Date"]) else None,
        "competition": competition,
        "competition_label": COMPETITION_LABELS.get(competition, competition),
        "venue": row["Venue"],
        "team1": row["Team1"], "team2": row["Team2"],
        "predicted_team1_runs": pred["team1_runs"], "predicted_team2_runs": pred["team2_runs"],
        "predicted_total_runs": pred["total_runs"],
        "team1_win_prob": pred["team1_win_prob"],
        "team2_win_prob": round(1 - pred["team1_win_prob"], 3) if pred["team1_win_prob"] is not None else None,
        "predicted_winner": predicted_winner,
        "actual_team1_runs": int(row["Team1Runs"]), "actual_team2_runs": int(row["Team2Runs"]),
        "actual_total_runs": int(row["TotalRuns"]),
        "actual_winner": actual_winner,
        "data_confidence": pred["data_confidence"],
        "elo_diff": round(float(row["EloDiff"]), 1) if pd.notna(row["EloDiff"]) else None,
        "team_analysis": None,
        "player_analysis": None,
        "home_ground": None,
        "stat_analysis": None,
        "insights": [],
        "crest_rgb1": team_crest_rgb(row["Team1"]),
        "crest_rgb2": team_crest_rgb(row["Team2"]),
    }
    if rich:
        view["team_analysis"] = get_team_analysis(competition, row["Team1"], row["Team2"])
        view["player_analysis"] = get_player_analysis(competition, row["Team1"], row["Team2"])
        view["home_ground"] = get_home_ground_report(row["Venue"], row["Team1"], row["Team2"])
        view["stat_analysis"] = get_match_events_summary(int(row.name) if hasattr(row, "name") else None)
    # Lightweight insight chips (no extra IO beyond what we already have)
    view["insights"] = fixture_insight_chips(
        row["Team1"], row["Team2"],
        {
            "team1_win_prob": pred["team1_win_prob"],
            "team2_win_prob": view["team2_win_prob"],
            "predicted_total_runs": pred["total_runs"],
            "basis": "Full ML" if pred["team1_win_prob"] is not None else "",
            "home_advantage_applied": False,
        },
        team_analysis=view.get("team_analysis"),
        home_ground=view.get("home_ground"),
    )
    return view
