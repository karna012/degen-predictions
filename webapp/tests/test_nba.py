"""Tests for the NBA Elo/prediction section."""
import pandas as pd
import pytest

import combo_results
import nba_predict


# ---- routes -------------------------------------------------------------

def test_nba_home_redirects_to_todays_picks(client):
    resp = client.get("/nba/")
    assert resp.status_code in (301, 302)
    assert resp.headers["Location"].endswith("/todays-picks/basketball/nba")


def test_nba_todays_picks_page_loads(client):
    resp = client.get("/todays-picks/basketball/nba")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Today's Picks" in body
    assert "NBA" in body


def test_nba_fixtures_page_loads(client):
    resp = client.get("/nba/fixtures")
    assert resp.status_code == 200


def test_nba_results_page_is_public(client):
    resp = client.get("/nba/results?refresh=1")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Track Record" in body or "track record" in body.lower()


def test_nba_accuracy_hub_lists_nba(client):
    resp = client.get("/accuracy")
    assert resp.status_code == 200
    assert b"NBA" in resp.data
    assert b'href="/nba/results"' in resp.data


def test_nav_has_nba_link(client):
    resp = client.get("/sports")
    body = resp.data.decode()
    assert 'href="/nba/fixtures"' in body


def test_nba_api_predict_with_real_teams(client):
    df = nba_predict.load_nba_elo()
    if len(df) < 2:
        pytest.skip("no NBA elo ratings available")
    home, away = df.iloc[0]["Team"], df.iloc[1]["Team"]
    resp = client.get(f"/nba/api/predict?home={home}&away={away}")
    data = resp.get_json()
    assert data["home_prob"] + data["away_prob"] == pytest.approx(1.0)
    assert "predicted_total_points" in data
    assert "favourite" in data


def test_nba_api_predict_unknown_team_returns_404(client):
    resp = client.get("/nba/api/predict?home=ZZZ&away=YYY")
    assert resp.status_code == 404


def test_nba_api_predict_includes_insights(client):
    df = nba_predict.load_nba_elo()
    if len(df) < 2:
        pytest.skip("no NBA elo ratings available")
    home, away = df.iloc[0]["Team"], df.iloc[1]["Team"]
    resp = client.get(f"/nba/api/predict?home={home}&away={away}")
    data = resp.get_json()
    assert "insights" in data
    assert isinstance(data["insights"], list)
    assert len(data["insights"]) >= 1


def test_nba_match_detail_page_loads(client):
    df = nba_predict.load_nba_elo()
    if len(df) < 2:
        pytest.skip("no NBA elo ratings available")
    home, away = df.iloc[0]["Team"], df.iloc[1]["Team"]
    resp = client.get(f"/nba/match/{home}/{away}?home_name=Home+Team&away_name=Away+Team")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Home Team" in body and "Away Team" in body
    assert "Win Probability" in body
    assert "Prediction Insights" in body


def test_nba_match_detail_unknown_team_is_404(client):
    resp = client.get("/nba/match/ZZZ/YYY")
    assert resp.status_code == 404


def test_nba_fixtures_cards_link_to_match_detail(client, monkeypatch):
    sample = [{
        "date": "2099-08-20T23:00:00Z",
        "home_team": "Boston Celtics", "away_team": "New York Knicks",
        "home_abbr": "BOS", "away_abbr": "NY",
        "completed": False, "status": "pre",
        "home_score": None, "away_score": None,
    }]
    monkeypatch.setattr("app.get_nba_fixtures", lambda *a, **k: sample)
    monkeypatch.setattr(combo_results, "refresh_for_finished_events", lambda **k: None)
    monkeypatch.setattr(
        "app.load_nba_elo",
        lambda: pd.DataFrame({"Team": ["BOS", "NY"], "TeamName": ["Celtics", "Knicks"]}),
    )
    body = client.get("/nba/fixtures").data.decode()
    assert "/nba/match/" in body


# ---- unit: win probability + home advantage --------------------------------

def test_nba_win_probability_equal_elo_favors_home():
    p = nba_predict.nba_win_probability(1500, 1500, home_advantage=100.0)
    assert p > 0.5


def test_nba_win_probability_higher_elo_favored():
    p1 = nba_predict.nba_win_probability(1600, 1500, home_advantage=100.0)
    p2 = nba_predict.nba_win_probability(1500, 1500, home_advantage=100.0)
    assert p1 > p2


# ---- unit: head-to-head -----------------------------------------------------

def test_get_h2h_record_counts_both_venues():
    history = pd.DataFrame({
        "HomeTeam": ["A", "B", "A"],
        "VisitorTeam": ["B", "A", "B"],
        "HomeScore": [105, 98, 101],
        "VisitorScore": [100, 110, 115],
    })
    h2h = nba_predict.get_h2h_record(history, "A", "B")
    assert h2h == {"home_wins": 2, "away_wins": 1, "meetings": 3}


def test_get_h2h_record_empty_history_returns_zero():
    h2h = nba_predict.get_h2h_record(pd.DataFrame(), "A", "B")
    assert h2h == {"home_wins": 0, "away_wins": 0, "meetings": 0}


def test_blend_with_h2h_no_meetings_unchanged():
    h2h = {"home_wins": 0, "away_wins": 0, "meetings": 0}
    assert nba_predict.blend_with_h2h(0.6, h2h) == 0.6


def test_blend_with_h2h_pulls_toward_h2h_rate():
    h2h = {"home_wins": 0, "away_wins": 8, "meetings": 8}
    blended = nba_predict.blend_with_h2h(0.6, h2h)
    assert blended < 0.6


# ---- unit: streak / rest blending -------------------------------------------

def test_blend_with_streak_hot_home_team_favored():
    blended = nba_predict.blend_with_streak(0.5, 8, -3)
    assert blended > 0.5


def test_blend_with_streak_has_modest_fixed_weight():
    blended = nba_predict.blend_with_streak(0.5, 10, -10, weight=0.05)
    assert blended <= 0.5 + 0.05 + 1e-9


def test_blend_with_rest_home_back_to_back_disfavors_home():
    blended = nba_predict.blend_with_rest(0.5, home_rest=0, away_rest=3)
    assert blended < 0.5


def test_blend_with_rest_has_modest_fixed_weight():
    blended = nba_predict.blend_with_rest(0.5, home_rest=3, away_rest=0, weight=0.06)
    assert blended <= 0.5 + 0.06 + 1e-9


# ---- unit: ML feature vector (must match trained model columns) ------------

def test_build_nba_ml_features_keys_match_expected_schema():
    home_row = pd.Series({
        "Elo": 1620.0, "Last10Wins": 6, "Last10Played": 10, "CurrentStreak": 2,
        "AvgPointsFor": 114.0, "AvgPointsAllowed": 108.0, "LastGameDate": "2026-08-05",
    })
    away_row = pd.Series({
        "Elo": 1550.0, "Last10Wins": 4, "Last10Played": 10, "CurrentStreak": -1,
        "AvgPointsFor": 110.0, "AvgPointsAllowed": 112.0, "LastGameDate": "2026-08-06",
    })
    h2h = {"home_wins": 3, "away_wins": 2, "meetings": 5}
    feats = nba_predict.build_nba_ml_features(home_row, away_row, h2h, as_of_date="2026-08-07")
    assert list(feats.keys()) == nba_predict.NBA_ML_FEATURE_NAMES
    assert feats["EloDiff"] == pytest.approx(70.0)
    assert feats["HomeDaysRest"] == pytest.approx(2.0)
    assert feats["AwayDaysRest"] == pytest.approx(1.0)
    assert feats["AwayBackToBack"] == 0.0


def test_build_nba_ml_features_matches_shipped_model_feature_list():
    import os
    import joblib
    path = os.path.join(nba_predict.DATA_DIR, "ml_ensemble_model.joblib")
    if not os.path.exists(path):
        return
    bundle = joblib.load(path)
    assert bundle["features"] == nba_predict.NBA_ML_FEATURE_NAMES


# ---- unit: total points ------------------------------------------------------

def test_predict_total_points_is_additive_average():
    home_row = pd.Series({"AvgPointsFor": 115.0, "AvgPointsAllowed": 108.0})
    away_row = pd.Series({"AvgPointsFor": 110.0, "AvgPointsAllowed": 112.0})
    home_pts, away_pts = nba_predict.predict_total_points(home_row, away_row)
    assert home_pts == pytest.approx((115.0 + 112.0) / 2)
    assert away_pts == pytest.approx((110.0 + 108.0) / 2)


def test_total_points_range_is_plus_minus_std_dev():
    low, high = nba_predict.total_points_range(220.0)
    assert low == pytest.approx(220.0 - nba_predict.TOTAL_POINTS_STD_DEV)
    assert high == pytest.approx(220.0 + nba_predict.TOTAL_POINTS_STD_DEV)


def test_nba_api_predict_includes_points_range(client):
    df = nba_predict.load_nba_elo()
    if len(df) < 2:
        pytest.skip("no NBA elo ratings available")
    home, away = df.iloc[0]["Team"], df.iloc[1]["Team"]
    resp = client.get(f"/nba/api/predict?home={home}&away={away}")
    data = resp.get_json()
    assert data["predicted_total_points_low"] == pytest.approx(
        data["predicted_total_points"] - nba_predict.TOTAL_POINTS_STD_DEV)
    assert data["predicted_total_points_high"] == pytest.approx(
        data["predicted_total_points"] + nba_predict.TOTAL_POINTS_STD_DEV)


# ---- unit: AI insights -------------------------------------------------------

def test_generate_insights_mentions_elo_edge_when_large():
    home_row = pd.Series({"Elo": 1650.0})
    away_row = pd.Series({"Elo": 1500.0})
    h2h = {"home_wins": 0, "away_wins": 0, "meetings": 0}
    insights = nba_predict.generate_insights(
        "BOS", "WSH", home_row, away_row, h2h, 0, 0, "BOS", 220.0,
        home_rest=2, away_rest=2,
    )
    assert any("Elo" in i for i in insights)


def test_generate_insights_mentions_back_to_back():
    home_row = pd.Series({"Elo": 1500.0})
    away_row = pd.Series({"Elo": 1500.0})
    h2h = {"home_wins": 0, "away_wins": 0, "meetings": 0}
    insights = nba_predict.generate_insights(
        "BOS", "WSH", home_row, away_row, h2h, 0, 0, "BOS", 220.0,
        home_rest=2, away_rest=0,
        home_prob=0.6, away_prob=0.4,
    )
    assert any("back-to-back" in i for i in insights)


def test_generate_insights_falls_back_when_nothing_stands_out():
    home_row = pd.Series({"Elo": 1500.0})
    away_row = pd.Series({"Elo": 1501.0})
    h2h = {"home_wins": 0, "away_wins": 0, "meetings": 0}
    insights = nba_predict.generate_insights(
        "BOS", "WSH", home_row, away_row, h2h, 0, 0, "WSH", 220.0,
        home_rest=2, away_rest=2,
    )
    assert len(insights) == 1
    assert "close" in insights[0]


# ---- data sanity --------------------------------------------------------------

def test_nba_elo_ratings_have_all_30_teams():
    df = nba_predict.load_nba_elo()
    if df.empty:
        pytest.skip("no NBA elo ratings available")
    assert len(df) == 30


def test_nba_elo_ratings_have_expected_columns():
    df = nba_predict.load_nba_elo()
    if df.empty:
        pytest.skip("no NBA elo ratings available")
    for col in (
        "AvgPointsFor", "AvgPointsAllowed", "CurrentStreak",
        "SeasonWins", "SeasonLosses", "Last10Wins", "Last10Played",
    ):
        assert col in df.columns


def test_home_advantage_file_is_positive():
    ha = nba_predict.load_home_advantage()
    assert ha > 0


# ---- fixtures parsing ---------------------------------------------------------

def test_nba_fixtures_from_espn_parses_real_shape():
    import nba_fixtures
    payload = {
        "events": [{
            "id": "1",
            "date": "2026-10-03T23:00Z",
            "status": {"type": {"state": "pre", "completed": False, "shortDetail": "10/3"}},
            "competitions": [{
                "competitors": [
                    {"homeAway": "home", "team": {"displayName": "Toronto Raptors", "abbreviation": "TOR"}, "score": None},
                    {"homeAway": "away", "team": {"displayName": "Miami Heat", "abbreviation": "MIA"}, "score": None},
                ],
            }],
        }],
    }
    rows = nba_fixtures._fixtures_from_espn(payload)
    assert len(rows) == 1
    assert rows[0]["home_abbr"] == "TOR"
    assert rows[0]["away_abbr"] == "MIA"
    assert rows[0]["completed"] is False
