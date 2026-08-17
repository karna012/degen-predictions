"""Tests for the MLB Elo/prediction section."""
import os

import pandas as pd
import pytest

import app as app_module
import combo_results
import mlb_predict


# ---- routes -------------------------------------------------------------

def test_mlb_home_redirects_to_todays_picks(client):
    resp = client.get("/mlb/")
    assert resp.status_code in (301, 302)
    assert resp.headers["Location"].endswith("/todays-picks/baseball/mlb")


def test_mlb_today_redirects_to_todays_picks(client):
    resp = client.get("/mlb/today")
    assert resp.status_code in (301, 302)
    assert resp.headers["Location"].endswith("/todays-picks/baseball/mlb")


def test_mlb_todays_picks_page_loads(client):
    resp = client.get("/todays-picks/baseball/mlb")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Today's Picks" in body
    assert "MLB" in body


def test_mlb_results_page_is_public(client):
    resp = client.get("/mlb/results")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Track Record" in body or "track record" in body.lower()


def test_mlb_fixtures_page_loads(client):
    resp = client.get("/mlb/fixtures")
    assert resp.status_code == 200


def test_mlb_fixtures_include_finished_when_present(client, monkeypatch):
    sample = [{
        "date": "2026-08-11T23:00:00Z",
        "home_team": "New York Yankees", "away_team": "Boston Red Sox",
        "home_abbr": "NYY", "away_abbr": "BOS",
        "home_code": "NYA", "away_code": "BOS",
        "completed": True, "status": "post", "status_detail": "Final",
        "home_score": 5, "away_score": 3,
        "has_rating": False,
    }]
    monkeypatch.setattr("app.get_mlb_fixtures", lambda *a, **k: sample)
    monkeypatch.setattr(combo_results, "refresh_for_finished_events", lambda **k: None)
    fixtures_body = client.get("/mlb/fixtures").data.decode()
    assert "Recent results" not in fixtures_body
    assert "Final: 3-5" not in fixtures_body
    results_body = client.get("/mlb/results?refresh=1").data.decode()
    assert "Latest scores" in results_body
    assert "Final: 3-5" in results_body
    assert "New York Yankees" in results_body


def test_mlb_scoreless_game_is_not_fake_final(client, monkeypatch):
    """ESPN 'completed' with no score, or games beyond the 36h horizon,
    must keep Unlock prediction — never Final: None-None."""
    sample = [{
        "date": "2099-08-20T23:00:00Z",
        "home_team": "New York Yankees", "away_team": "Boston Red Sox",
        "home_abbr": "NYY", "away_abbr": "BOS",
        "completed": True, "status": "post", "status_detail": "Final",
        "home_score": None, "away_score": None,
        "home_pitcher": {"name": "A", "wins": 1, "losses": 1, "era": "3.00"},
        "away_pitcher": {"name": "B", "wins": 1, "losses": 1, "era": "4.00"},
    }]
    monkeypatch.setattr("app.get_mlb_fixtures", lambda *a, **k: sample)
    monkeypatch.setattr(combo_results, "refresh_for_finished_events", lambda **k: None)
    monkeypatch.setattr(
        "app.load_mlb_elo",
        lambda: __import__("pandas").DataFrame(
            {"Team": ["NYA", "BOS"], "TeamName": ["Yankees", "Red Sox"]}
        ),
    )
    body = client.get("/mlb/fixtures").data.decode()
    assert "Final: None-None" not in body
    assert "Unlock prediction" in body
    assert 'data-lock="1"' in body


def test_nav_has_mlb_link(client):
    resp = client.get("/sports/baseball")
    body = resp.data.decode()
    assert 'href="/mlb/fixtures"' in body
    assert "See fixtures" in body


def test_mlb_api_predict_with_real_teams(client):
    df = mlb_predict.load_mlb_elo()
    if len(df) < 2:
        pytest.skip("no MLB elo ratings available")
    home, away = df.iloc[0]["Team"], df.iloc[1]["Team"]
    resp = client.get(f"/mlb/api/predict?home={home}&away={away}")
    data = resp.get_json()
    assert data["home_prob"] + data["away_prob"] == pytest.approx(1.0)
    assert "predicted_total_runs" in data
    assert "favourite" in data


def test_mlb_api_predict_unknown_team_returns_404(client):
    resp = client.get("/mlb/api/predict?home=ZZZ&away=YYY")
    assert resp.status_code == 404


# ---- unit: win probability + home advantage -------------------------------

def test_mlb_win_probability_equal_elo_favors_home():
    """With equal Elo, the home-field bonus should still push the
    probability above 50% for the home team."""
    p = mlb_predict.mlb_win_probability(1500, 1500)
    assert p > 0.5


def test_mlb_win_probability_higher_elo_favored():
    p = mlb_predict.mlb_win_probability(1600, 1500)
    assert p > mlb_predict.mlb_win_probability(1500, 1500)


# ---- unit: head-to-head --------------------------------------------------

def test_get_h2h_record_counts_both_venues():
    history = pd.DataFrame({
        "HomeTeam": ["A", "B", "A"],
        "VisitorTeam": ["B", "A", "B"],
        "HomeScore": [5, 2, 1],
        "VisitorScore": [3, 4, 6],
    })
    # Game 1: A (home) beats B 5-3 -> A win
    # Game 2: B (home) vs A (visitor) 2-4 -> A (visitor) wins
    # Game 3: A (home) vs B (visitor) 1-6 -> B wins
    h2h = mlb_predict.get_h2h_record(history, "A", "B")
    assert h2h == {"home_wins": 2, "away_wins": 1, "meetings": 3}


def test_get_h2h_record_empty_history_returns_zero():
    h2h = mlb_predict.get_h2h_record(pd.DataFrame(), "A", "B")
    assert h2h == {"home_wins": 0, "away_wins": 0, "meetings": 0}


def test_blend_with_h2h_no_meetings_unchanged():
    h2h = {"home_wins": 0, "away_wins": 0, "meetings": 0}
    assert mlb_predict.blend_with_h2h(0.6, h2h) == 0.6


def test_blend_with_h2h_pulls_toward_h2h_rate():
    h2h = {"home_wins": 0, "away_wins": 8, "meetings": 8}
    blended = mlb_predict.blend_with_h2h(0.6, h2h)
    assert blended < 0.6


# ---- unit: pitcher ERA blending ------------------------------------------

def test_blend_with_pitcher_missing_era_unchanged():
    assert mlb_predict.blend_with_pitcher(0.55, "", "") == 0.55
    assert mlb_predict.blend_with_pitcher(0.55, None, None) == 0.55


def test_blend_with_pitcher_better_home_era_favors_home():
    blended = mlb_predict.blend_with_pitcher(0.5, "2.50", "5.00")
    assert blended > 0.5


def test_blend_with_pitcher_has_modest_fixed_weight():
    """A huge ERA gap shouldn't swing the probability by more than the
    configured weight."""
    blended = mlb_predict.blend_with_pitcher(0.5, "1.00", "9.00", weight=0.12)
    assert blended <= 0.5 + 0.12 + 1e-9


# ---- unit: ML feature vector (must match trained model columns) ----------

def test_build_mlb_ml_features_keys_match_expected_schema():
    home_row = pd.Series({
        "Elo": 1520.0, "Last10Wins": 6, "Last10Played": 10, "CurrentStreak": 2,
        "AvgRunsScored": 4.5, "AvgRunsAllowed": 4.0, "AvgHitsFor": 8.5,
        "AvgHRFor": 1.1, "AvgSOInduced": 8.5, "LastGameDate": "2026-08-05",
    })
    away_row = pd.Series({
        "Elo": 1480.0, "Last10Wins": 4, "Last10Played": 10, "CurrentStreak": -1,
        "AvgRunsScored": 4.0, "AvgRunsAllowed": 4.5, "AvgHitsFor": 8.0,
        "AvgHRFor": 1.0, "AvgSOInduced": 8.0, "LastGameDate": "2026-08-04",
    })
    h2h = {"home_wins": 3, "away_wins": 2, "meetings": 5}
    feats = mlb_predict.build_mlb_ml_features(
        home_row, away_row, h2h, home_era="2.50", away_era="4.50",
        home_pitcher={"wins": 8, "losses": 4}, away_pitcher={"wins": 5, "losses": 7},
        as_of_date="2026-08-07",
    )
    assert list(feats.keys()) == mlb_predict.MLB_ML_FEATURE_NAMES
    assert feats["EloDiff"] == pytest.approx(40.0)
    assert feats["PitcherQualityDiff"] > 0
    assert feats["HomeDaysRest"] == pytest.approx(2.0)
    assert feats["AwayDaysRest"] == pytest.approx(3.0)


def test_build_mlb_ml_features_matches_shipped_model_feature_list():
    import joblib
    path = os.path.join(mlb_predict.DATA_DIR, "ml_ensemble_model.joblib")
    if not os.path.exists(path):
        return
    bundle = joblib.load(path)
    assert bundle["features"] == mlb_predict.MLB_ML_FEATURE_NAMES


def test_build_mlb_ml_features_neutral_pitcher_when_era_missing():
    home_row = pd.Series({
        "Elo": 1500.0, "Last10Wins": 5, "Last10Played": 10, "CurrentStreak": 0,
        "AvgRunsScored": 4.5, "AvgRunsAllowed": 4.5, "AvgHitsFor": 8.5,
        "AvgHRFor": 1.1, "AvgSOInduced": 8.5, "LastGameDate": None,
    })
    away_row = home_row.copy()
    h2h = {"home_wins": 0, "away_wins": 0, "meetings": 0}
    feats = mlb_predict.build_mlb_ml_features(home_row, away_row, h2h)
    assert feats["PitcherQualityDiff"] == 0.0
    assert feats["PitcherWinRateDiff"] == 0.0
    assert feats["PitcherStartsDiff"] == 0.0


# ---- unit: total runs -----------------------------------------------------

def test_predict_total_runs_is_additive_average():
    home_row = pd.Series({"AvgRunsScored": 5.0, "AvgRunsAllowed": 4.0})
    away_row = pd.Series({"AvgRunsScored": 4.0, "AvgRunsAllowed": 5.0})
    home_runs, away_runs = mlb_predict.predict_total_runs(home_row, away_row)
    assert home_runs == pytest.approx((5.0 + 5.0) / 2)
    assert away_runs == pytest.approx((4.0 + 4.0) / 2)


# ---- unit: hits / home runs / strikeouts / streak -------------------------

def test_predict_total_hits_is_additive_average():
    home_row = pd.Series({"AvgHitsFor": 9.0, "AvgHitsAllowed": 8.0})
    away_row = pd.Series({"AvgHitsFor": 8.0, "AvgHitsAllowed": 9.0})
    home_hits, away_hits = mlb_predict.predict_total_hits(home_row, away_row)
    assert home_hits == pytest.approx((9.0 + 9.0) / 2)
    assert away_hits == pytest.approx((8.0 + 8.0) / 2)


def test_predict_total_home_runs_is_additive_average():
    home_row = pd.Series({"AvgHRFor": 1.5, "AvgHRAllowed": 1.0})
    away_row = pd.Series({"AvgHRFor": 1.0, "AvgHRAllowed": 1.5})
    home_hr, away_hr = mlb_predict.predict_total_home_runs(home_row, away_row)
    assert home_hr == pytest.approx((1.5 + 1.5) / 2)
    assert away_hr == pytest.approx((1.0 + 1.0) / 2)


def test_predict_total_strikeouts_uses_atbat_and_induced():
    home_row = pd.Series({"AvgSOAtBat": 8.0, "AvgSOInduced": 9.0})
    away_row = pd.Series({"AvgSOAtBat": 7.0, "AvgSOInduced": 10.0})
    home_so, away_so = mlb_predict.predict_total_strikeouts(home_row, away_row)
    assert home_so == pytest.approx((8.0 + 10.0) / 2)
    assert away_so == pytest.approx((7.0 + 9.0) / 2)


def test_blend_with_streak_no_difference_pulls_slightly_to_neutral():
    """Equal streaks (both hot or both cold) carry no directional signal, so
    the blend should pull slightly toward 0.5, not leave the probability
    untouched."""
    blended = mlb_predict.blend_with_streak(0.6, 3, 3, weight=0.05)
    assert blended == pytest.approx(0.6 * 0.95 + 0.5 * 0.05)


def test_blend_with_streak_hot_home_team_favored():
    blended = mlb_predict.blend_with_streak(0.5, 8, -3)
    assert blended > 0.5


def test_blend_with_streak_has_modest_fixed_weight():
    blended = mlb_predict.blend_with_streak(0.5, 10, -10, weight=0.05)
    assert blended <= 0.5 + 0.05 + 1e-9


def test_mlb_api_predict_includes_new_market_fields(client):
    df = mlb_predict.load_mlb_elo()
    if len(df) < 2:
        pytest.skip("no MLB elo ratings available")
    home, away = df.iloc[0]["Team"], df.iloc[1]["Team"]
    resp = client.get(f"/mlb/api/predict?home={home}&away={away}")
    data = resp.get_json()
    for field in (
        "home_streak", "away_streak", "home_season_record", "away_season_record",
        "predicted_total_hits", "predicted_total_home_runs", "predicted_total_strikeouts",
    ):
        assert field in data


# ---- data sanity ----------------------------------------------------------

def test_mlb_elo_ratings_have_all_30_teams():
    df = mlb_predict.load_mlb_elo()
    if df.empty:
        pytest.skip("no MLB elo ratings available")
    assert len(df) == 30


# ---- unit: AI insights ----------------------------------------------------

def test_generate_insights_mentions_elo_edge_when_large():
    home_row = pd.Series({"Elo": 1600.0})
    away_row = pd.Series({"Elo": 1500.0})
    h2h = {"home_wins": 0, "away_wins": 0, "meetings": 0}
    insights = mlb_predict.generate_insights(
        "LAN", "COL", home_row, away_row, h2h, "", "", 0, 0, "LAN", 8.5,
    )
    assert any("Elo" in i for i in insights)


def test_generate_insights_mentions_h2h_when_lopsided_and_enough_meetings():
    home_row = pd.Series({"Elo": 1500.0})
    away_row = pd.Series({"Elo": 1500.0})
    h2h = {"home_wins": 8, "away_wins": 2, "meetings": 10}
    insights = mlb_predict.generate_insights(
        "LAN", "COL", home_row, away_row, h2h, "", "", 0, 0, "LAN", 8.5,
    )
    assert any("head-to-head series" in i for i in insights)


def test_generate_insights_mentions_streak_when_hot():
    home_row = pd.Series({"Elo": 1500.0})
    away_row = pd.Series({"Elo": 1500.0})
    h2h = {"home_wins": 0, "away_wins": 0, "meetings": 0}
    insights = mlb_predict.generate_insights(
        "LAN", "COL", home_row, away_row, h2h, "", "", 6, 0, "LAN", 8.5,
    )
    assert any("form" in i for i in insights)


def test_generate_insights_mentions_pitcher_edge():
    home_row = pd.Series({"Elo": 1500.0})
    away_row = pd.Series({"Elo": 1500.0})
    h2h = {"home_wins": 0, "away_wins": 0, "meetings": 0}
    insights = mlb_predict.generate_insights(
        "LAN", "COL", home_row, away_row, h2h, "2.00", "5.00", 0, 0, "LAN", 8.5,
    )
    assert any("mound" in i for i in insights)


def test_generate_insights_mentions_high_scoring_lean():
    home_row = pd.Series({"Elo": 1500.0})
    away_row = pd.Series({"Elo": 1500.0})
    h2h = {"home_wins": 0, "away_wins": 0, "meetings": 0}
    insights = mlb_predict.generate_insights(
        "LAN", "COL", home_row, away_row, h2h, "", "", 0, 0, "LAN", 10.5,
    )
    assert any("high-scoring" in i for i in insights)


def test_generate_insights_falls_back_when_nothing_stands_out():
    home_row = pd.Series({"Elo": 1500.0})
    away_row = pd.Series({"Elo": 1501.0})
    h2h = {"home_wins": 0, "away_wins": 0, "meetings": 0}
    insights = mlb_predict.generate_insights(
        "LAN", "COL", home_row, away_row, h2h, "", "", 0, 0, "COL", 8.0,
    )
    assert len(insights) == 1
    assert "close" in insights[0]


def test_generate_insights_flags_coinflip_underdog():
    home_row = pd.Series({"Elo": 1510.0})
    away_row = pd.Series({"Elo": 1500.0})
    h2h = {"home_wins": 0, "away_wins": 0, "meetings": 0}
    insights = mlb_predict.generate_insights(
        "LAN", "COL", home_row, away_row, h2h, "", "", 0, 0, "LAN", 8.5,
        home_prob=0.53, away_prob=0.47,
    )
    assert any("coin-flip" in i.lower() for i in insights)
    assert any("COL" in i for i in insights)


def test_mlb_api_predict_includes_insights(client):
    df = mlb_predict.load_mlb_elo()
    if len(df) < 2:
        pytest.skip("no MLB elo ratings available")
    home, away = df.iloc[0]["Team"], df.iloc[1]["Team"]
    resp = client.get(f"/mlb/api/predict?home={home}&away={away}")
    data = resp.get_json()
    assert "insights" in data
    assert isinstance(data["insights"], list)
    assert len(data["insights"]) >= 1


# ---- match detail page -----------------------------------------------

def test_mlb_match_detail_page_loads(client):
    df = mlb_predict.load_mlb_elo()
    if len(df) < 2:
        pytest.skip("no MLB elo ratings available")
    home, away = df.iloc[0]["Team"], df.iloc[1]["Team"]
    resp = client.get(f"/mlb/match/{home}/{away}?home_name=Home+Team&away_name=Away+Team")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Home Team" in body and "Away Team" in body
    assert "Win Probability" in body
    assert "Prediction Insights" in body


def test_mlb_match_detail_unknown_team_is_404(client):
    resp = client.get("/mlb/match/ZZZ/YYY")
    assert resp.status_code == 404


def test_mlb_fixtures_cards_link_to_match_detail(client, monkeypatch):
    sample = [{
        "date": "2099-08-20T23:00:00Z",
        "home_team": "New York Yankees", "away_team": "Boston Red Sox",
        "home_abbr": "NYY", "away_abbr": "BOS",
        "completed": False, "status": "pre",
        "home_score": None, "away_score": None,
        "home_pitcher": {"name": "A", "wins": 1, "losses": 1, "era": "3.00"},
        "away_pitcher": {"name": "B", "wins": 1, "losses": 1, "era": "4.00"},
    }]
    monkeypatch.setattr("app.get_mlb_fixtures", lambda *a, **k: sample)
    monkeypatch.setattr(combo_results, "refresh_for_finished_events", lambda **k: None)
    monkeypatch.setattr(
        "app.load_mlb_elo",
        lambda: __import__("pandas").DataFrame(
            {"Team": ["NYA", "BOS"], "TeamName": ["Yankees", "Red Sox"]}
        ),
    )
    body = client.get("/mlb/fixtures").data.decode()
    assert "/mlb/match/" in body


# ---- total runs range (+/- empirical std dev, see TOTAL_RUNS_STD_DEV) -----

def test_total_runs_range_is_plus_minus_std_dev():
    low, high = mlb_predict.total_runs_range(9.0)
    assert low == pytest.approx(9.0 - mlb_predict.TOTAL_RUNS_STD_DEV)
    assert high == pytest.approx(9.0 + mlb_predict.TOTAL_RUNS_STD_DEV)


def test_total_runs_range_does_not_go_negative():
    low, high = mlb_predict.total_runs_range(0.5)
    assert low == 0.0
    assert high == pytest.approx(0.5 + mlb_predict.TOTAL_RUNS_STD_DEV)


def test_mlb_api_predict_includes_runs_range(client):
    df = mlb_predict.load_mlb_elo()
    if len(df) < 2:
        pytest.skip("no MLB elo ratings available")
    home, away = df.iloc[0]["Team"], df.iloc[1]["Team"]
    resp = client.get(f"/mlb/api/predict?home={home}&away={away}")
    data = resp.get_json()
    assert data["predicted_total_runs_low"] == pytest.approx(
        data["predicted_total_runs"] - mlb_predict.TOTAL_RUNS_STD_DEV)
    assert data["predicted_total_runs_high"] == pytest.approx(
        data["predicted_total_runs"] + mlb_predict.TOTAL_RUNS_STD_DEV)


def test_mlb_elo_ratings_have_expanded_columns():
    df = mlb_predict.load_mlb_elo()
    if df.empty:
        pytest.skip("no MLB elo ratings available")
    for col in (
        "AvgHitsFor", "AvgHitsAllowed", "AvgHRFor", "AvgHRAllowed",
        "AvgSOAtBat", "AvgSOInduced", "CurrentStreak", "SeasonWins", "SeasonLosses",
    ):
        assert col in df.columns


def test_mlb_statsapi_abbr_and_final_parse():
    import mlb_fixtures
    assert mlb_fixtures._normalize_abbr("AZ") == "ARI"
    assert mlb_fixtures._normalize_abbr("CWS") == "CHW"
    assert mlb_fixtures._normalize_abbr("NYY") == "NYY"
    payload = {
        "dates": [{
            "games": [{
                "gameDate": "2026-08-15T23:10:00Z",
                "status": {
                    "abstractGameState": "Final",
                    "detailedState": "Final",
                },
                "teams": {
                    "away": {
                        "score": 4,
                        "team": {"name": "Chicago White Sox", "abbreviation": "CWS"},
                        "probablePitcher": {"id": 1, "fullName": "A Starter"},
                    },
                    "home": {
                        "score": 3,
                        "team": {"name": "Detroit Tigers", "abbreviation": "DET"},
                        "probablePitcher": {},
                    },
                },
            }]
        }]
    }
    rows = mlb_fixtures._fixtures_from_statsapi(payload)
    assert len(rows) == 1
    assert rows[0]["away_abbr"] == "CHW"
    assert rows[0]["completed"] is True
    assert rows[0]["away_score"] == 4
    assert rows[0]["home_score"] == 3
    assert rows[0]["away_pitcher"]["name"] == "A Starter"
