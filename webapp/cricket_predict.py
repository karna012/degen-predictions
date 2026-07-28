"""Cricket prediction helpers -- The Hundred (Men's) + Major League Cricket
(Men's), served as two separate leagues (see app.py's /hundred/* and
/mlc/* routes) sharing one trained model backend (scripts/cricket/).

FIXTURES vs RESULTS: Cricsheet is a historical, match-completed data
source only -- there is no live/upcoming fixture feed the way ESPN serves
MLB or spaia.jp serves NPB (see mlb_fixtures.py / npb_fixtures.py).
Investigated and ruled out as real upcoming-fixture sources (see
scripts/cricket/ commit history / task notes for the full trail):
  - ESPN's cricket scoreboard API (site.api.espn.com/apis/site/v2/sports/
    cricket/{id}/scoreboard) IS live and does return real data (confirmed
    working for e.g. the 2023 World Cup, id 8039) -- but unlike MLB's
    stable "mlb" league key, cricket's ESPN endpoint is keyed by a numeric
    ID scoped to ONE tournament EDITION (a new ID every season), and there
    is no working listing/lookup endpoint to discover this season's ID
    programmatically (the sport-level and "leagues" endpoints both 404).
    ESPNcricinfo's own web pages -- where that ID could otherwise be read
    off a series URL -- return HTTP 403 (Akamai edge block) for every
    direct request, so there's no way to resolve a current ID without
    manual/browser access.
  - thehundred.com and majorleaguecricket.com both have fixtures pages,
    but the schedule is rendered client-side by a third-party sports CMS
    (incrowdsports) with no discoverable public JSON endpoint in the
    static HTML -- would require a real headless browser to scrape, which
    isn't available in this pipeline.
  - cricapi.com and similar third-party cricket APIs require a signed-up
    API key even on their free tier -- can't be provisioned unattended.
So: "fixtures" (this module's upcoming_fixtures()) is honestly empty right
now and says so, rather than showing stale historical matches mislabeled
as upcoming. "results" (matches_for_competition() + build_match_view())
are the most recent real COMPLETED matches, each with the model's genuine
pre-match prediction (computed strictly from that match's PRE-MATCH
feature row, built leakage-free by build_training_dataset.py's
chronological walk-forward) compared honestly against the real final
result -- the same methodology as app.py's mlb_track_record()/
npb_track_record(). If a real upcoming-fixture source becomes available
later, upcoming_fixtures() is the one place to wire it in.

data_confidence (computed per-row at feature-build time) is surfaced
directly rather than hidden -- The Hundred/MLC are young competitions and
early rows in particular rest on thin rolling history.
"""
import datetime
import os

import joblib
import pandas as pd
import requests

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data", "cricket")

COMPETITION_LABELS = {
    "hnd_male": "The Hundred (Men's)",
    "mlc_male": "Major League Cricket (Men's)",
}

FEATURES = [
    "EloDiff", "Team1RecentWinRate", "Team2RecentWinRate", "Team1RecentRunRate", "Team2RecentRunRate",
    "VenueAvgFirstInnings", "VenueAvgSecondInnings", "VenueAvgRunsPerOver", "VenueAvgWicketsPerOver",
    "VenueMatchesSeen", "Team1AvgSR", "Team2AvgSR", "Team1AvgEcon", "Team2AvgEcon",
    "Team1DeathUplift", "Team2DeathUplift", "Team1MatchupEdge", "Team2MatchupEdge", "SentimentDiff",
]

_models_cache = None


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


def upcoming_fixtures(competition):
    """Real live/upcoming fixtures for this competition.

    hnd_male (The Hundred): sourced from Wikipedia's season article (see
    hundred_fixtures.py's module docstring for the full trail of sources
    that were tried and ruled out first -- ESPN's per-edition-ID
    scoreboard, thehundred.com's client-side-only schedule, fixturedownload.com
    (covers BBL/IPL/WBBL/World Cups but not The Hundred), key-gated
    third-party cricket APIs). Each fixture gets a lightweight live
    prediction (see predict_live_fixture()) layered on when we have Elo/
    venue data for it -- None if we don't, never a fabricated number.

    mlc_male (MLC): still genuinely [] -- the 2026 MLC season already
    concluded (final played July 18, 2026) and no equivalent actively-
    maintained Wikipedia schedule article covering a next MLC season was
    found. Revisit when MLC's next season is announced."""
    if competition == "hnd_male":
        try:
            import hundred_fixtures
            raw = hundred_fixtures.get_upcoming_and_live()
        except Exception as exc:
            print(f"upcoming_fixtures(hnd_male) failed, degrading to empty: {exc}")
            return []
        out = []
        for f in raw:
            pred = predict_live_fixture(f)
            out.append({**f, "prediction": pred, "slug": hundred_fixtures.slug_for_fixture(f)})
        return out
    return []


def season_window(competition):
    """Data-driven (not guessed) description of when this competition
    typically plays, from the actual month-of-year spread seen across all
    historical matches -- used for the fixtures page's honest empty state
    ("next season starts around ...") instead of a hardcoded date."""
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
    """Most recent `limit` matches for one competition, newest first --
    used for both the fixtures listing and to resolve a match_id (its row
    index in the FULL combined dataset, stable across competitions)."""
    df = load_dataset()
    if df.empty:
        return df
    sub = df[df["Competition"] == competition].copy()
    sub["match_id"] = sub.index
    return sub.sort_values("Date", ascending=False).head(limit)


def get_match_row(match_id):
    df = load_dataset()
    if df.empty or match_id not in df.index:
        return None
    row = df.loc[match_id]
    return row


def predict_for_row(row):
    """Runs all four trained models against one match's stored pre-match
    feature row, and returns predictions + the row's data_confidence label
    -- never presenting a prediction without also surfacing how much
    history it actually rests on."""
    models = load_models()
    X = pd.DataFrame([row[FEATURES].to_dict()])

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


# Lat/lon for every ground currently seen in the Hundred/MLC Cricsheet
# data (data/cricket/venue_stats.csv) -- hardcoded because Cricsheet's
# "venue" field is a free-text ground name/city string, not coordinates,
# and there is no bulk geocoding source already wired into this codebase.
# New grounds added by a future season simply won't have a weather section
# (fails soft, see get_venue_weather below) until added here.
VENUE_COORDS = {
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
}

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
WEATHER_CACHE_TTL_SECONDS = 3 * 3600
_weather_cache = {}


def get_venue_weather(venue_name, match_date_str):
    """Open-Meteo (api.open-meteo.com) free, no-API-key forecast for a
    venue on a given date -- fails soft (returns None) on ANY problem: no
    known coordinates for this ground, the date is too far in the past/
    future for Open-Meteo's forecast window, the request fails, or the
    response is malformed. This must never raise and never block a match
    page from rendering; a missing weather section is the correct/honest
    behaviour when a genuine forecast isn't available (e.g. for every
    match currently in this dataset, which is historical -- Cricsheet has
    no upcoming-fixture feed, so this code path is intentionally dormant
    today and activates automatically once a real future fixture is
    representable in the pipeline)."""
    try:
        coords = VENUE_COORDS.get(venue_name)
        if not coords:
            return None
        match_date = datetime.datetime.strptime(match_date_str, "%Y-%m-%d").date()
        today = datetime.datetime.now(datetime.timezone.utc).date()
        # Open-Meteo's free forecast endpoint only covers ~16 days ahead and
        # doesn't do historical dates -- outside that window there is
        # genuinely no forecast to show, so this is a real "no data" case,
        # not a bug to paper over.
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
        # Any failure here (network, bad response shape, unexpected date
        # format) degrades to "no weather section" rather than a 500 --
        # same fail-soft contract as sentiment.get_team_sentiment.
        return None


def elo_win_probability(elo1, elo2):
    """Same formula as scripts/cricket/build_elo.py's expected_score() --
    no home-advantage term (see that module's docstring for why)."""
    return 1.0 / (1.0 + 10 ** (-(elo1 - elo2) / 400.0))


def predict_live_fixture(fixture):
    """Lightweight prediction for a REAL upcoming/live fixture (sourced
    from hundred_fixtures.py, not from the static backtested training
    dataset used by predict_for_row()). This deliberately does NOT run the
    full trained sklearn models -- those need the complete 19-feature
    vector (rolling player form, batter-vs-bowler matchup edge, sentiment,
    etc.) that build_training_dataset.py computes only for matches already
    in Cricsheet's historical archive. Fabricating placeholder values for
    those features just to force the trained model to run would produce a
    falsely-precise-looking number built on invented inputs -- worse than
    being upfront about a simpler estimate. So this uses only what's
    honestly available live: each team's current Elo rating (aliased
    through REBRAND_ALIAS for the 3 rebranded teams) and the venue's
    historical average innings totals -- the same signals the full model
    also uses, just without the rolling-form/player-level layers.
    Returns None entirely if we don't even have Elo ratings for both teams
    (e.g. a franchise with zero Cricsheet history under any name)."""
    elo_df = load_elo()
    if elo_df.empty:
        return None
    team1_name = fixture.get("team1_elo_name", fixture["team1"])
    team2_name = fixture.get("team2_elo_name", fixture["team2"])
    row1 = elo_df[elo_df["Team"] == team1_name]
    row2 = elo_df[elo_df["Team"] == team2_name]
    if row1.empty or row2.empty:
        return None

    elo1, elo2 = float(row1.iloc[0]["Elo"]), float(row2.iloc[0]["Elo"])
    team1_win_prob = elo_win_probability(elo1, elo2)

    venues = load_venues()
    venue_row = venues[venues["Venue"] == fixture["venue"]] if not venues.empty else pd.DataFrame()
    if not venue_row.empty:
        team1_runs = venue_row.iloc[0]["AvgFirstInningsTotal"]
        team2_runs = venue_row.iloc[0]["AvgSecondInningsTotal"]
        venue_known = True
    else:
        # No venue history at all -- fall back to each team's own recent
        # run rate (still real data, just less specific than this ground),
        # rather than a fabricated fixed number.
        team1_runs = float(row1.iloc[0]["RecentRunRate"]) * 20 if pd.notna(row1.iloc[0].get("RecentRunRate")) else None
        team2_runs = float(row2.iloc[0]["RecentRunRate"]) * 20 if pd.notna(row2.iloc[0].get("RecentRunRate")) else None
        venue_known = False

    return {
        "team1_win_prob": round(team1_win_prob, 3),
        "team2_win_prob": round(1 - team1_win_prob, 3),
        "predicted_team1_runs": round(team1_runs, 1) if team1_runs is not None else None,
        "predicted_team2_runs": round(team2_runs, 1) if team2_runs is not None else None,
        "predicted_total_runs": round(team1_runs + team2_runs, 1) if team1_runs is not None and team2_runs is not None else None,
        "elo_team1": round(elo1, 1), "elo_team2": round(elo2, 1),
        "venue_known": venue_known,
        "basis": "Elo + venue average (live estimate -- simpler than the full trained model, see cricket_predict.predict_live_fixture docstring)",
    }


def build_fixture_detail(fixture):
    """Full detail view for a real upcoming/live Hundred fixture (from
    hundred_fixtures.py) -- the live-fixture equivalent of build_match_view()
    for historical Cricsheet rows. Bundles the lightweight live prediction
    (predict_live_fixture), the venue's data-driven pitch report, and a
    weather forecast (get_venue_weather -- this is exactly the case that
    function was built for: a real fixture with a real future/today date,
    unlike every match in the historical dataset)."""
    pred = predict_live_fixture(fixture)
    pitch_report = get_venue_pitch_report(fixture["venue"])
    weather = get_venue_weather(fixture["venue"], fixture["date"]) if fixture.get("date") else None
    predicted_winner = None
    if pred and pred["team1_win_prob"] is not None:
        predicted_winner = fixture["team1"] if pred["team1_win_prob"] >= 0.5 else fixture["team2"]

    return {
        "team1": fixture["team1"], "team2": fixture["team2"],
        "date": fixture.get("date"), "time": fixture.get("time"), "venue": fixture.get("venue"),
        "completed": fixture.get("completed", False),
        "prediction": pred,
        "predicted_winner": predicted_winner,
        "pitch_report": pitch_report,
        "weather": weather,
    }


def get_venue_pitch_report(venue_name):
    """Human-readable, data-backed pitch report for a venue -- returns None
    if the venue isn't in venue_stats.csv at all (shouldn't happen for a
    match already in the dataset, but defensive)."""
    venues = load_venues()
    if venues.empty:
        return None
    match = venues[venues["Venue"] == venue_name]
    if match.empty:
        return None
    v = match.iloc[0]
    return {
        "venue": venue_name,
        "matches_played": int(v["MatchesPlayed"]),
        "avg_first_innings": v["AvgFirstInningsTotal"],
        "avg_second_innings": v["AvgSecondInningsTotal"],
        "pitch_type": v["PitchType"],
        "thin_history": int(v["MatchesPlayed"]) < 5,
    }


def build_match_view(row):
    pred = predict_for_row(row)
    actual_winner = None
    if pd.notna(row.get("Winner")):
        actual_winner = row["Team1"] if row["Winner"] == 1 else row["Team2"]
    predicted_winner = None
    if pred["team1_win_prob"] is not None:
        predicted_winner = row["Team1"] if pred["team1_win_prob"] >= 0.5 else row["Team2"]

    return {
        "match_id": int(row.name) if hasattr(row, "name") else None,
        "date": row["Date"].strftime("%Y-%m-%d") if pd.notna(row["Date"]) else None,
        "competition": row["Competition"],
        "competition_label": COMPETITION_LABELS.get(row["Competition"], row["Competition"]),
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
    }
