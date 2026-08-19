"""Tests for the NHL Elo/prediction section."""
import pandas as pd
import pytest

import combo_results
import nhl_predict


# ---- routes -------------------------------------------------------------

def test_nhl_home_redirects_to_todays_picks(client):
    resp = client.get("/nhl/")
    assert resp.status_code in (301, 302)
    assert resp.headers["Location"].endswith("/todays-picks/hockey/nhl")


def test_nhl_todays_picks_page_loads(client):
    resp = client.get("/todays-picks/hockey/nhl")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Today's Picks" in body
    assert "NHL" in body


def test_nhl_fixtures_page_loads(client):
    resp = client.get("/nhl/fixtures")
    assert resp.status_code == 200


def test_nhl_results_page_is_public(client):
    resp = client.get("/nhl/results?refresh=1")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Track Record" in body or "track record" in body.lower()


def test_nhl_accuracy_hub_lists_nhl(client):
    resp = client.get("/accuracy")
    assert resp.status_code == 200
    assert b"NHL" in resp.data
    assert b'href="/nhl/results"' in resp.data


def test_nav_has_nhl_link(client):
    resp = client.get("/sports")
    body = resp.data.decode()
    assert 'href="/nhl/fixtures"' in body


def test_nhl_api_predict_with_real_teams(client):
    df = nhl_predict.load_nhl_elo()
    if len(df) < 2:
        pytest.skip("no NHL elo ratings available")
    home, away = df.iloc[0]["Team"], df.iloc[1]["Team"]
    resp = client.get(f"/nhl/api/predict?home={home}&away={away}")
    data = resp.get_json()
    assert data["home_prob"] + data["away_prob"] == pytest.approx(1.0)
    assert "predicted_total_goals" in data
    assert "favourite" in data


def test_nhl_api_predict_unknown_team_returns_404(client):
    resp = client.get("/nhl/api/predict?home=ZZZ&away=YYY")
    assert resp.status_code == 404


def test_nhl_api_predict_includes_insights(client):
    df = nhl_predict.load_nhl_elo()
    if len(df) < 2:
        pytest.skip("no NHL elo ratings available")
    home, away = df.iloc[0]["Team"], df.iloc[1]["Team"]
    resp = client.get(f"/nhl/api/predict?home={home}&away={away}")
    data = resp.get_json()
    assert "insights" in data
    assert isinstance(data["insights"], list)
    assert len(data["insights"]) >= 1


def test_nhl_match_detail_page_loads(client):
    df = nhl_predict.load_nhl_elo()
    if len(df) < 2:
        pytest.skip("no NHL elo ratings available")
    home, away = df.iloc[0]["Team"], df.iloc[1]["Team"]
    resp = client.get(f"/nhl/match/{home}/{away}?home_name=Home+Team&away_name=Away+Team")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Home Team" in body and "Away Team" in body
    assert "Win Probability" in body
    assert "Prediction Insights" in body


def test_nhl_match_detail_unknown_team_is_404(client):
    resp = client.get("/nhl/match/ZZZ/YYY")
    assert resp.status_code == 404


def test_nhl_fixtures_cards_link_to_match_detail(client, monkeypatch):
    sample = [{
        "date": "2099-08-20T23:00:00Z",
        "home_team": "Toronto Maple Leafs", "away_team": "Montreal Canadiens",
        "home_abbr": "TOR", "away_abbr": "MTL",
        "completed": False, "status": "pre",
        "home_score": None, "away_score": None,
    }]
    monkeypatch.setattr("app.get_nhl_fixtures", lambda *a, **k: sample)
    monkeypatch.setattr(combo_results, "refresh_for_finished_events", lambda **k: None)
    monkeypatch.setattr(
        "app.load_nhl_elo",
        lambda: pd.DataFrame({"Team": ["TOR", "MTL"], "TeamName": ["Maple Leafs", "Canadiens"]}),
    )
    body = client.get("/nhl/fixtures").data.decode()
    assert "/nhl/match/" in body


# ---- unit: win probability + home advantage --------------------------------

def test_nhl_win_probability_equal_elo_favors_home():
    p = nhl_predict.nhl_win_probability(1500, 1500, home_advantage=50.0)
    assert p > 0.5


def test_nhl_win_probability_higher_elo_favored():
    p1 = nhl_predict.nhl_win_probability(1600, 1500, home_advantage=50.0)
    p2 = nhl_predict.nhl_win_probability(1500, 1500, home_advantage=50.0)
    assert p1 > p2


# ---- unit: head-to-head -----------------------------------------------------

def test_get_h2h_record_counts_both_venues():
    history = pd.DataFrame({
        "HomeTeam": ["A", "B", "A"],
        "VisitorTeam": ["B", "A", "B"],
        "HomeScore": [4, 2, 1],
        "VisitorScore": [3, 5, 3],
    })
    h2h = nhl_predict.get_h2h_record(history, "A", "B")
    assert h2h == {"home_wins": 2, "away_wins": 1, "meetings": 3}


def test_get_h2h_record_empty_history_returns_zero():
    h2h = nhl_predict.get_h2h_record(pd.DataFrame(), "A", "B")
    assert h2h == {"home_wins": 0, "away_wins": 0, "meetings": 0}


def test_blend_with_h2h_pulls_toward_h2h_rate():
    h2h = {"home_wins": 0, "away_wins": 8, "meetings": 8}
    blended = nhl_predict.blend_with_h2h(0.6, h2h)
    assert blended < 0.6


# ---- unit: streak / rest blending -------------------------------------------

def test_blend_with_streak_hot_home_team_favored():
    blended = nhl_predict.blend_with_streak(0.5, 8, -3)
    assert blended > 0.5


def test_blend_with_rest_home_back_to_back_disfavors_home():
    blended = nhl_predict.blend_with_rest(0.5, home_rest=0, away_rest=3)
    assert blended < 0.5


def test_blend_with_rest_has_modest_fixed_weight():
    blended = nhl_predict.blend_with_rest(0.5, home_rest=3, away_rest=0, weight=0.04)
    assert blended <= 0.5 + 0.04 + 1e-9


# ---- unit: ML feature vector (must match trained model columns) ------------

def test_build_nhl_ml_features_keys_match_expected_schema():
    home_row = pd.Series({
        "Elo": 1600.0, "Last10Wins": 6, "Last10Played": 10, "CurrentStreak": 2,
        "AvgGoalsFor": 3.4, "AvgGoalsAllowed": 2.7, "LastGameDate": "2026-08-05",
    })
    away_row = pd.Series({
        "Elo": 1500.0, "Last10Wins": 4, "Last10Played": 10, "CurrentStreak": -1,
        "AvgGoalsFor": 2.9, "AvgGoalsAllowed": 3.1, "LastGameDate": "2026-08-06",
    })
    h2h = {"home_wins": 3, "away_wins": 2, "meetings": 5}
    feats = nhl_predict.build_nhl_ml_features(home_row, away_row, h2h, as_of_date="2026-08-07")
    assert list(feats.keys()) == nhl_predict.NHL_ML_FEATURE_NAMES
    assert feats["EloDiff"] == pytest.approx(100.0)
    assert feats["HomeDaysRest"] == pytest.approx(2.0)
    assert feats["AwayDaysRest"] == pytest.approx(1.0)


def test_build_nhl_ml_features_matches_shipped_model_feature_list():
    import os
    import joblib
    path = os.path.join(nhl_predict.DATA_DIR, "ml_ensemble_model.joblib")
    if not os.path.exists(path):
        return
    bundle = joblib.load(path)
    assert bundle["features"] == nhl_predict.NHL_ML_FEATURE_NAMES


# ---- unit: total goals ------------------------------------------------------

def test_predict_total_goals_is_additive_average():
    home_row = pd.Series({"AvgGoalsFor": 3.4, "AvgGoalsAllowed": 2.7})
    away_row = pd.Series({"AvgGoalsFor": 2.9, "AvgGoalsAllowed": 3.1})
    home_goals, away_goals = nhl_predict.predict_total_goals(home_row, away_row)
    assert home_goals == pytest.approx((3.4 + 3.1) / 2)
    assert away_goals == pytest.approx((2.9 + 2.7) / 2)


def test_total_goals_range_is_plus_minus_std_dev():
    low, high = nhl_predict.total_goals_range(6.0)
    assert low == pytest.approx(6.0 - nhl_predict.TOTAL_GOALS_STD_DEV)
    assert high == pytest.approx(6.0 + nhl_predict.TOTAL_GOALS_STD_DEV)


def test_nhl_api_predict_includes_goals_range(client):
    df = nhl_predict.load_nhl_elo()
    if len(df) < 2:
        pytest.skip("no NHL elo ratings available")
    home, away = df.iloc[0]["Team"], df.iloc[1]["Team"]
    resp = client.get(f"/nhl/api/predict?home={home}&away={away}")
    data = resp.get_json()
    assert data["predicted_total_goals_low"] == pytest.approx(
        data["predicted_total_goals"] - nhl_predict.TOTAL_GOALS_STD_DEV)
    assert data["predicted_total_goals_high"] == pytest.approx(
        data["predicted_total_goals"] + nhl_predict.TOTAL_GOALS_STD_DEV)


# ---- unit: AI insights -------------------------------------------------------

def test_generate_insights_mentions_elo_edge_when_large():
    home_row = pd.Series({"Elo": 1650.0})
    away_row = pd.Series({"Elo": 1500.0})
    h2h = {"home_wins": 0, "away_wins": 0, "meetings": 0}
    insights = nhl_predict.generate_insights(
        "BOS", "WSH", home_row, away_row, h2h, 0, 0, "BOS", 6.0,
        home_rest=2, away_rest=2,
    )
    assert any("Elo" in i for i in insights)


def test_generate_insights_falls_back_when_nothing_stands_out():
    home_row = pd.Series({"Elo": 1500.0})
    away_row = pd.Series({"Elo": 1501.0})
    h2h = {"home_wins": 0, "away_wins": 0, "meetings": 0}
    insights = nhl_predict.generate_insights(
        "BOS", "WSH", home_row, away_row, h2h, 0, 0, "WSH", 6.0,
        home_rest=2, away_rest=2,
    )
    assert len(insights) == 1
    assert "close" in insights[0]


# ---- data sanity --------------------------------------------------------------

def test_nhl_elo_ratings_have_all_32_teams():
    df = nhl_predict.load_nhl_elo()
    if df.empty:
        pytest.skip("no NHL elo ratings available")
    assert len(df) == 32


def test_nhl_elo_ratings_have_expected_columns():
    df = nhl_predict.load_nhl_elo()
    if df.empty:
        pytest.skip("no NHL elo ratings available")
    for col in (
        "AvgGoalsFor", "AvgGoalsAllowed", "CurrentStreak",
        "SeasonWins", "SeasonLosses", "Last10Wins", "Last10Played",
    ):
        assert col in df.columns


def test_home_advantage_file_is_positive():
    ha = nhl_predict.load_home_advantage()
    assert ha > 0


# ---- fixtures parsing ---------------------------------------------------------

def test_nhl_fixtures_from_espn_translates_abbreviations():
    import nhl_fixtures
    payload = {
        "events": [{
            "id": "1",
            "date": "2026-10-03T23:00Z",
            "status": {"type": {"state": "pre", "completed": False, "shortDetail": "10/3"}},
            "competitions": [{
                "competitors": [
                    {"homeAway": "home", "team": {"displayName": "Los Angeles Kings", "abbreviation": "LA"}, "score": None},
                    {"homeAway": "away", "team": {"displayName": "San Jose Sharks", "abbreviation": "SJ"}, "score": None},
                ],
            }],
        }],
    }
    rows = nhl_fixtures._fixtures_from_espn(payload)
    assert len(rows) == 1
    assert rows[0]["home_abbr"] == "LAK"
    assert rows[0]["away_abbr"] == "SJS"


def test_nhl_fixtures_from_week_parses_real_shape():
    import nhl_fixtures
    payload = {
        "gameWeek": [{
            "date": "2026-10-03",
            "games": [{
                "gameType": 2,
                "gameState": "FUT",
                "startTimeUTC": "2026-10-03T23:00:00Z",
                "homeTeam": {"abbrev": "TOR", "placeName": {"default": "Toronto"}, "commonName": {"default": "Maple Leafs"}, "score": None},
                "awayTeam": {"abbrev": "MTL", "placeName": {"default": "Montreal"}, "commonName": {"default": "Canadiens"}, "score": None},
            }],
        }],
    }
    rows = nhl_fixtures._fixtures_from_nhl_week(payload)
    assert len(rows) == 1
    assert rows[0]["home_abbr"] == "TOR"
    assert rows[0]["away_abbr"] == "MTL"
    assert rows[0]["completed"] is False
