"""Contract tests — response shape and content-type agreements."""
import pytest

import app as app_module
from qa_suite.catalog import (
    MLB_PREDICT_CONTRACT,
    NFL_PREDICT_CONTRACT,
    SOCCER_PREDICT_CONTRACT,
    assert_contract,
)


pytestmark = pytest.mark.contract


def test_sitemap_is_xml(client):
    resp = client.get("/sitemap.xml")
    assert resp.status_code == 200
    ctype = (resp.content_type or "").lower()
    assert "xml" in ctype
    body = resp.data.decode()
    # /sitemap.xml is now a sitemap index pointing at sharded leaf sitemaps.
    assert "<sitemapindex" in body
    assert "https://degenpredictions.com/" in body


def test_llms_txt_is_plain_text_with_required_sections(client):
    resp = client.get("/llms.txt")
    assert resp.status_code == 200
    assert "text/plain" in (resp.content_type or "")
    body = resp.data.decode()
    assert body.startswith("# Degen Predictions")
    assert "NFL predictions" in body
    assert "not betting" in body.lower() or "not betting or investment advice" in body.lower()


def test_unlock_contract_is_empty_204(client):
    resp = client.post("/api/unlock/pick")
    assert resp.status_code == 204
    assert resp.content_length in (0, None) or resp.data == b""


def test_soccer_predict_error_contract(client):
    resp = client.get("/premier_league/api/predict/NoSuchTeam/AlsoFake")
    assert resp.status_code == 404
    assert resp.is_json
    payload = resp.get_json()
    assert set(payload.keys()) == {"error"}
    assert isinstance(payload["error"], str)


def test_nfl_predict_success_contract(client, monkeypatch, tmp_path):
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
    payload = client.get("/nfl/api/predict?home=KC&away=BUF").get_json()
    assert_contract(payload, NFL_PREDICT_CONTRACT)
    for key in ("home_prob", "away_prob"):
        assert 0.0 <= float(payload[key]) <= 1.0


def test_soccer_predict_success_contract_when_unlocked(client):
    df = app_module.load_predictions("premier_league")
    if df.empty:
        pytest.skip("no premier_league predictions")
    row = df.iloc[0]
    resp = client.get(f"/premier_league/api/predict/{row['HomeTeam']}/{row['AwayTeam']}")
    if resp.status_code != 200:
        pytest.skip(f"predict status {resp.status_code}")
    assert_contract(resp.get_json(), SOCCER_PREDICT_CONTRACT)


def test_ads_txt_contract(client):
    resp = client.get("/ads.txt")
    assert resp.status_code == 200
    assert "text/plain" in (resp.content_type or "")
