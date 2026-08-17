"""Unit + light functional tests for soccer_match_analysis (explain-only)."""
import pandas as pd

import soccer_match_analysis as sma


def test_role_bucket_understat_and_asa():
    assert sma._role_bucket("GK") == "GK"
    assert sma._role_bucket("CB") == "DEF"
    assert sma._role_bucket("FB") == "DEF"
    assert sma._role_bucket("CM") == "MID"
    assert sma._role_bucket("W") == "WING"
    assert sma._role_bucket("ST") == "ATT"
    assert sma._role_bucket("D M S") == "MID"
    assert sma._role_bucket("F S") == "ATT"
    assert sma._role_bucket("D S") == "DEF"


def test_shot_quality_off_target_half_weight():
    # 4 on target + 6 off target => 4 + 0.5*6 = 7
    assert sma.shot_quality(4, 10) == 7.0
    assert sma.OFF_TARGET_WEIGHT == 0.5


def test_transfer_churn_detects_arrivals_and_departures():
    prev = [
        {"id": "1", "player_name": "Alice", "contrib": 5, "minutes": 900, "xG": 3},
        {"id": "2", "player_name": "Bob", "contrib": 8, "minutes": 1200, "xG": 5},
    ]
    curr = [
        {"id": "2", "player_name": "Bob", "contrib": 8, "minutes": 1200, "xG": 5},
        {"id": "3", "player_name": "Cara", "contrib": 4, "minutes": 600, "xG": 2},
    ]
    churn = sma._transfer_churn(curr, prev)
    assert churn["n_left"] == 1
    assert churn["n_arrived"] == 1
    assert churn["left"][0]["name"] == "Alice"
    assert churn["arrived"][0]["name"] == "Cara"


def test_underdog_helper_returns_none_for_small_gap(tmp_path, monkeypatch):
    # No train_rows / tiny gap => None
    assert sma._underdog_from_train_rows("premier_league", elo_diff=10) is None


def test_get_stadium_weather_unknown_team_fails_soft():
    assert sma.get_stadium_weather("NotARealFC", "2026-08-10") is None


def test_get_stadium_weather_far_future_fails_soft():
    assert sma.get_stadium_weather("Arsenal", "2099-01-01") is None


def test_build_match_analysis_mls_smoke():
    report = sma.build_match_analysis(
        "mls", "MLS", "New England Revolution", "Houston Dynamo", "2026-08-08",
        match={"Predicted_Result": "New England Revolution"},
    )
    assert isinstance(report, dict)
    assert "sections" in report
    assert "verdict" in report
    # With live MLS player data this should usually be available
    if report.get("available"):
        assert report["disclaimer"]
        assert "Scout" in (report["verdict"] or "") or "scout" in (report["verdict"] or "").lower() or report["verdict"]


def test_match_page_includes_analysis_when_possible(client):
    # Pick first MLS fixture if present
    import app as app_module
    df = app_module.load_predictions("mls")
    if df.empty:
        return
    row = df.iloc[0]
    resp = client.get(f"/mls/match/{row['HomeTeam']}/{row['AwayTeam']}")
    assert resp.status_code == 200
    body = resp.data.decode()
    # Analysis block or at least match page chrome
    assert row["HomeTeam"] in body
    assert "Match Analysis" in body or "Match Context" in body or "Prediction Insights" in body
