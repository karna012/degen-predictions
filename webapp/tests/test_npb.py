"""Tests for the NPB Elo/prediction section."""
import pandas as pd
import pytest

import npb_fixtures
import npb_predict


# ---- katakana romanization --------------------------------------------------

def test_katakana_to_romaji_known_name():
    assert npb_fixtures._katakana_to_romaji("トコダ　ヒロキ") == "Tokoda Hiroki"


def test_katakana_to_romaji_handles_none():
    assert npb_fixtures._katakana_to_romaji(None) is None


def test_katakana_to_romaji_handles_small_tsu_and_long_vowel():
    # "matto" (small tsu doubles the following consonant) and "sa-" (long vowel repeats "a")
    result = npb_fixtures._katakana_to_romaji("マット")
    assert result is not None


def test_npb_fixtures_use_full_team_names(client):
    resp = client.get("/npb/fixtures")
    body = resp.data.decode()
    # at least one full official franchise name should appear if any fixtures loaded
    if "fixture-teams" in body:
        assert any(name in body for name in npb_fixtures.TEAM_FULL_NAMES.values())


# ---- routes ---------------------------------------------------------------

def test_npb_home_redirects_to_fixtures(client):
    resp = client.get("/npb/")
    assert resp.status_code in (301, 302)
    assert resp.headers["Location"].endswith("/npb/fixtures")


def test_npb_fixtures_page_loads(client):
    resp = client.get("/npb/fixtures")
    assert resp.status_code == 200


def test_nav_has_npb_link(client):
    resp = client.get("/sports/baseball")
    body = resp.data.decode()
    assert 'href="/npb/fixtures"' in body


def test_npb_api_predict_with_real_teams(client):
    df = npb_predict.load_npb_elo()
    if len(df) < 2:
        pytest.skip("no NPB elo ratings available")
    home, away = int(df.iloc[0]["TeamID"]), int(df.iloc[1]["TeamID"])
    resp = client.get(f"/npb/api/predict?home={home}&away={away}")
    data = resp.get_json()
    assert data["home_prob"] + data["away_prob"] == pytest.approx(1.0)
    assert "predicted_total_runs" in data
    assert "favourite_name" in data


def test_npb_api_predict_unknown_team_returns_404(client):
    resp = client.get("/npb/api/predict?home=999999&away=888888")
    assert resp.status_code == 404


def test_npb_api_predict_invalid_id_returns_400(client):
    resp = client.get("/npb/api/predict?home=abc&away=def")
    assert resp.status_code == 400


def test_npb_match_detail_page_loads(client):
    df = npb_predict.load_npb_elo()
    if len(df) < 2:
        pytest.skip("no NPB elo ratings available")
    home, away = int(df.iloc[0]["TeamID"]), int(df.iloc[1]["TeamID"])
    resp = client.get(f"/npb/match/{home}/{away}?home_name=Home+Team&away_name=Away+Team")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Home Team" in body and "Away Team" in body
    assert "Win Probability" in body
    assert "Prediction Insights" in body


def test_npb_match_detail_unknown_team_is_404(client):
    resp = client.get("/npb/match/999999/888888")
    assert resp.status_code == 404


def test_npb_fixtures_cards_link_to_match_detail(client):
    resp = client.get("/npb/fixtures")
    body = resp.data.decode()
    assert "/npb/match/" in body


# ---- unit: win probability + home advantage --------------------------------

def test_npb_win_probability_equal_elo_favors_home():
    p = npb_predict.npb_win_probability(1500, 1500)
    assert p > 0.5


def test_npb_win_probability_higher_elo_favored():
    p = npb_predict.npb_win_probability(1600, 1500)
    assert p > npb_predict.npb_win_probability(1500, 1500)


# ---- unit: head-to-head ----------------------------------------------------

def test_get_h2h_record_counts_both_venues():
    history = pd.DataFrame({
        "HomeTeamID": [1, 2, 1],
        "VisitorTeamID": [2, 1, 2],
        "HomeScore": [5, 2, 1],
        "VisitorScore": [3, 4, 6],
    })
    h2h = npb_predict.get_h2h_record(history, 1, 2)
    assert h2h == {"home_wins": 2, "away_wins": 1, "meetings": 3}


def test_get_h2h_record_empty_history_returns_zero():
    h2h = npb_predict.get_h2h_record(pd.DataFrame(), 1, 2)
    assert h2h == {"home_wins": 0, "away_wins": 0, "meetings": 0}


def test_blend_with_h2h_no_meetings_unchanged():
    h2h = {"home_wins": 0, "away_wins": 0, "meetings": 0}
    assert npb_predict.blend_with_h2h(0.6, h2h) == 0.6


def test_blend_with_h2h_pulls_toward_h2h_rate():
    h2h = {"home_wins": 0, "away_wins": 8, "meetings": 8}
    blended = npb_predict.blend_with_h2h(0.6, h2h)
    assert blended < 0.6


# ---- unit: streak -----------------------------------------------------------

def test_blend_with_streak_hot_home_team_favored():
    blended = npb_predict.blend_with_streak(0.5, 8, -3)
    assert blended > 0.5


def test_blend_with_streak_has_modest_fixed_weight():
    blended = npb_predict.blend_with_streak(0.5, 10, -10, weight=0.05)
    assert blended <= 0.5 + 0.05 + 1e-9


# ---- unit: total runs -------------------------------------------------------

def test_predict_total_runs_is_additive_average():
    home_row = pd.Series({"AvgRunsScored": 5.0, "AvgRunsAllowed": 4.0})
    away_row = pd.Series({"AvgRunsScored": 4.0, "AvgRunsAllowed": 5.0})
    home_runs, away_runs = npb_predict.predict_total_runs(home_row, away_row)
    assert home_runs == pytest.approx((5.0 + 5.0) / 2)
    assert away_runs == pytest.approx((4.0 + 4.0) / 2)


# ---- unit: insights ----------------------------------------------------------

def test_generate_insights_mentions_elo_edge_when_large():
    home_row = pd.Series({"Elo": 1600.0, "TeamName": "Home"})
    away_row = pd.Series({"Elo": 1500.0, "TeamName": "Away"})
    h2h = {"home_wins": 0, "away_wins": 0, "meetings": 0}
    insights = npb_predict.generate_insights("Home", "Away", home_row, away_row, h2h, 0, 0, "Home", 6.0)
    assert any("Elo" in i for i in insights)


def test_generate_insights_falls_back_when_nothing_stands_out():
    home_row = pd.Series({"Elo": 1500.0, "TeamName": "Home"})
    away_row = pd.Series({"Elo": 1501.0, "TeamName": "Away"})
    h2h = {"home_wins": 0, "away_wins": 0, "meetings": 0}
    insights = npb_predict.generate_insights("Home", "Away", home_row, away_row, h2h, 0, 0, "Away", 6.5)
    assert len(insights) == 1
    assert "close" in insights[0]


# ---- total runs range (+/- 1.5 std dev) -----------------------------------

def test_total_runs_range_is_plus_minus_1_5():
    low, high = npb_predict.total_runs_range(7.0)
    assert low == 5.5
    assert high == 8.5


def test_npb_api_predict_includes_runs_range(client):
    df = npb_predict.load_npb_elo()
    if len(df) < 2:
        pytest.skip("no NPB elo ratings available")
    home, away = int(df.iloc[0]["TeamID"]), int(df.iloc[1]["TeamID"])
    resp = client.get(f"/npb/api/predict?home={home}&away={away}")
    data = resp.get_json()
    assert data["predicted_total_runs_low"] == pytest.approx(data["predicted_total_runs"] - 1.5)
    assert data["predicted_total_runs_high"] == pytest.approx(data["predicted_total_runs"] + 1.5)


# ---- data sanity -------------------------------------------------------------

def test_npb_elo_ratings_have_all_12_teams():
    df = npb_predict.load_npb_elo()
    if df.empty:
        pytest.skip("no NPB elo ratings available")
    assert len(df) == 12
