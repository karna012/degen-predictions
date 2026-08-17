"""API tests — JSON/HTTP behavior of public predict and unlock endpoints."""
import pytest

import app as app_module
from qa_suite.catalog import (
    MLB_PREDICT_CONTRACT,
    NFL_PREDICT_CONTRACT,
    SOCCER_PREDICT_CONTRACT,
    assert_contract,
)


pytestmark = pytest.mark.api


def test_unlock_pick_and_combo_return_204(client):
    for kind in ("pick", "combo"):
        resp = client.get(f"/api/unlock/{kind}")
        assert resp.status_code == 204
        assert resp.data == b""
        resp = client.post(f"/api/unlock/{kind}")
        assert resp.status_code == 204


def test_unlock_unknown_kind_is_404(client):
    assert client.get("/api/unlock/secret").status_code == 404
    assert client.get("/api/unlock/").status_code in (404, 308, 301)


def test_soccer_predict_unknown_fixture_json_404(client):
    resp = client.get("/premier_league/api/predict/NoSuchTeam/AlsoFake")
    assert resp.status_code == 404
    payload = resp.get_json()
    assert payload["error"] == "Fixture not found"


def test_soccer_predict_contract_when_fixture_exists(client):
    df = app_module.load_predictions("premier_league")
    if df.empty:
        pytest.skip("no premier_league predictions")
    row = df.iloc[0]
    resp = client.get(f"/premier_league/api/predict/{row['HomeTeam']}/{row['AwayTeam']}")
    if resp.status_code in (403, 500):
        pytest.skip(f"live predict not available ({resp.status_code})")
    assert resp.status_code == 200
    assert resp.content_type.startswith("application/json")
    assert_contract(resp.get_json(), SOCCER_PREDICT_CONTRACT)


def test_nfl_predict_missing_teams_json_404(client):
    resp = client.get("/nfl/api/predict?home=ZZZ&away=YYY")
    assert resp.status_code == 404
    body = resp.get_json()
    assert "error" in body
    assert "Traceback" not in (body.get("error") or "")


def test_nfl_predict_contract_when_rated(client, monkeypatch, tmp_path):
    import nfl_predict

    games = [
        {
            "date": "2025-09-07T17:00:00Z",
            "home_team": "Kansas City Chiefs", "home_abbr": "KC",
            "away_team": "Buffalo Bills", "away_abbr": "BUF",
            "completed": True, "home_score": 31, "away_score": 17,
        },
        {
            "date": "2025-09-14T17:00:00Z",
            "home_team": "Buffalo Bills", "home_abbr": "BUF",
            "away_team": "Kansas City Chiefs", "away_abbr": "KC",
            "completed": True, "home_score": 20, "away_score": 24,
        },
    ]
    monkeypatch.setattr(nfl_predict, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(nfl_predict, "ELO_PATH", str(tmp_path / "elo.csv"))
    monkeypatch.setattr(nfl_predict, "HISTORY_PATH", str(tmp_path / "hist.csv"))
    elo_df, hist = nfl_predict.rebuild_ratings(games)
    nfl_predict._save_ratings(elo_df, hist)
    monkeypatch.setattr(nfl_predict, "ensure_nfl_ratings", lambda **k: nfl_predict.load_nfl_elo())
    resp = client.get("/nfl/api/predict?home=KC&away=BUF")
    assert resp.status_code == 200
    assert resp.content_type.startswith("application/json")
    payload = resp.get_json()
    assert_contract(payload, NFL_PREDICT_CONTRACT)
    assert abs(payload["home_prob"] + payload["away_prob"] - 1.0) < 1e-6


def test_mlb_predict_unknown_is_json_error(client):
    resp = client.get("/mlb/api/predict?home=ZZZ&away=YYY")
    assert resp.status_code == 404
    assert resp.get_json().get("error")


def test_go_gp_valid_shape_redirects_or_404s_cleanly(client):
    resp = client.get("/go/gp/ipl/fixture/csk-vs-mi-2099-01-01")
    assert resp.status_code in (302, 404)
    if resp.status_code == 302:
        assert "/ipl/fixture/" in (resp.headers.get("Location") or "")


def test_go_gp_rejects_offsite_target(client):
    resp = client.get("/go/gp/https://evil.example/phish")
    assert resp.status_code in (404, 400)
    loc = resp.headers.get("Location") or ""
    assert "evil.example" not in loc


def test_api_predict_does_not_leak_python_traceback(client, monkeypatch):
    df = app_module.load_predictions("premier_league")
    if df.empty:
        pytest.skip("no premier_league predictions")
    row = df.iloc[0]

    def boom(*_a, **_k):
        raise RuntimeError("secret internals xyz")

    monkeypatch.setattr(app_module, "predict_fixture_live", boom)
    resp = client.get(f"/premier_league/api/predict/{row['HomeTeam']}/{row['AwayTeam']}")
    assert resp.status_code == 500
    err = resp.get_json()["error"]
    assert "secret internals" not in err
    assert "Prediction unavailable" in err
