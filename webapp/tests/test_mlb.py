"""Tests for the MLB Elo/prediction section."""
import pandas as pd
import pytest

import app as app_module
import mlb_predict


# ---- routes -------------------------------------------------------------

def test_mlb_home_redirects_to_fixtures(client):
    resp = client.get("/mlb/")
    assert resp.status_code in (301, 302)
    assert resp.headers["Location"].endswith("/mlb/fixtures")


def test_mlb_fixtures_page_loads(client):
    resp = client.get("/mlb/fixtures")
    assert resp.status_code == 200


def test_nav_has_mlb_link(client):
    resp = client.get("/sports/baseball")
    body = resp.data.decode()
    assert 'href="/mlb/fixtures"' in body


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


def test_mlb_fixtures_cards_link_to_match_detail(client):
    resp = client.get("/mlb/fixtures")
    body = resp.data.decode()
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
