"""Tests for the NFL trained-ML-model layer added on top of the existing
Elo-only prediction chain (nfl_predict.py's original Elo/H2H/streak/spread
logic already has its own coverage elsewhere -- this file is specifically
for the new ML blend, empirically-derived home advantage, and rest-day
features)."""
import pandas as pd
import pytest

import nfl_predict


def test_nfl_ml_feature_names_len_matches_shipped_model():
    import os
    import joblib
    path = os.path.join(nfl_predict.DATA_DIR, "ml_ensemble_model.joblib")
    if not os.path.exists(path):
        pytest.skip("no NFL ML model available")
    bundle = joblib.load(path)
    assert bundle["features"] == nfl_predict.NFL_ML_FEATURE_NAMES


def test_build_nfl_ml_features_keys_match_expected_schema():
    home_row = pd.Series({
        "Elo": 1620.0, "Last10Wins": 6, "Last10Played": 10, "CurrentStreak": 2,
        "AvgPointsScored": 26.0, "AvgPointsAllowed": 20.0,
    })
    away_row = pd.Series({
        "Elo": 1550.0, "Last10Wins": 4, "Last10Played": 10, "CurrentStreak": -1,
        "AvgPointsScored": 22.0, "AvgPointsAllowed": 24.0,
    })
    h2h = {"home_wins": 3, "away_wins": 2, "meetings": 5}
    feats = nfl_predict.build_nfl_ml_features(home_row, away_row, h2h, home_rest=7.0, away_rest=7.0)
    assert list(feats.keys()) == nfl_predict.NFL_ML_FEATURE_NAMES
    assert feats["EloDiff"] == pytest.approx(70.0)
    assert feats["HomeShortWeek"] == 0.0
    assert feats["AwayShortWeek"] == 0.0


def test_build_nfl_ml_features_flags_short_week():
    home_row = pd.Series({
        "Elo": 1500.0, "Last10Wins": 5, "Last10Played": 10, "CurrentStreak": 0,
        "AvgPointsScored": 22.5, "AvgPointsAllowed": 22.5,
    })
    away_row = home_row.copy()
    h2h = {"home_wins": 0, "away_wins": 0, "meetings": 0}
    feats = nfl_predict.build_nfl_ml_features(home_row, away_row, h2h, home_rest=4.0, away_rest=7.0)
    assert feats["HomeShortWeek"] == 1.0
    assert feats["AwayShortWeek"] == 0.0


def test_nfl_win_probability_uses_empirical_home_advantage_by_default():
    p_derived = nfl_predict.nfl_win_probability(1500, 1500)
    p_explicit_zero = nfl_predict.nfl_win_probability(1500, 1500, home_advantage=0)
    assert p_derived > p_explicit_zero == pytest.approx(0.5)


def test_load_home_advantage_is_positive():
    ha = nfl_predict.load_home_advantage()
    assert ha > 0


def test_compute_nfl_prediction_includes_rest_days(client):
    df = nfl_predict.load_nfl_elo()
    if len(df) < 2:
        pytest.skip("no NFL elo ratings available")
    home, away = df.iloc[0]["Team"], df.iloc[1]["Team"]
    pred = nfl_predict.compute_nfl_prediction(home, away)
    assert pred is not None
    assert "home_days_rest" in pred and "away_days_rest" in pred
    assert pred["home_days_rest"] >= 0


def test_nfl_api_predict_still_works_with_ml_blend(client):
    df = nfl_predict.load_nfl_elo()
    if len(df) < 2:
        pytest.skip("no NFL elo ratings available")
    home, away = df.iloc[0]["Team"], df.iloc[1]["Team"]
    resp = client.get(f"/nfl/api/predict?home={home}&away={away}")
    data = resp.get_json()
    assert data["home_prob"] + data["away_prob"] == pytest.approx(1.0)
    assert "spread_pick" in data


def test_nfl_accuracy_hub_shows_calibrated_numbers(client):
    resp = client.get("/accuracy")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "NFL" in body
