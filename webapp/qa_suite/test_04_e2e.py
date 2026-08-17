"""End-to-end journeys through Flask's test client (browser-like, no live browser)."""
import pytest

import app as app_module


pytestmark = pytest.mark.e2e


def test_journey_home_to_nfl_unlock_api(client, monkeypatch, tmp_path):
    home = client.get("/")
    assert home.status_code == 200
    assert b"/sports" in home.data

    sports = client.get("/sports")
    assert sports.status_code == 200
    assert b"/sports/american-football" in sports.data

    hub = client.get("/sports/american-football")
    assert hub.status_code == 200
    assert b"/nfl/fixtures" in hub.data
    assert b"See fixtures" in hub.data

    fixtures = client.get("/nfl/fixtures")
    assert fixtures.status_code == 200

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

    unlock = client.get("/nfl/api/predict?home=KC&away=BUF")
    assert unlock.status_code == 200
    data = unlock.get_json()
    assert data["favourite"] in ("KC", "BUF")

    match = client.get(
        "/nfl/match/KC/BUF?home_name=Kansas+City+Chiefs&away_name=Buffalo+Bills"
    )
    assert match.status_code == 200
    assert b"Win Probability" in match.data
    assert b"SportsEvent" in match.data


def test_journey_todays_picks_to_league_board(client):
    hub = client.get("/todays-picks")
    assert hub.status_code == 200
    assert b"/todays-picks/american_football" in hub.data
    leagues = client.get("/todays-picks/american_football")
    assert leagues.status_code == 200
    assert b"/nfl/fixtures" in leagues.data
    slate = client.get("/todays-picks/american_football/nfl")
    assert slate.status_code == 200
    assert b"Today's Picks" in slate.data


def test_journey_soccer_fixture_to_predict(client):
    leagues = client.get("/sports/soccer")
    assert leagues.status_code == 200
    assert b"/premier_league/" in leagues.data
    board = client.get("/premier_league/")
    assert board.status_code == 200
    df = app_module.load_predictions("premier_league")
    if df.empty:
        pytest.skip("no premier_league fixtures")
    row = df.iloc[0]
    detail = client.get(f"/premier_league/match/{row['HomeTeam']}/{row['AwayTeam']}")
    assert detail.status_code == 200
    api = client.get(f"/premier_league/api/predict/{row['HomeTeam']}/{row['AwayTeam']}")
    assert api.status_code in (200, 403, 500)
    if api.status_code == 500:
        err = (api.get_json() or {}).get("error") or ""
        assert "Prediction unavailable" in err
        assert "Traceback" not in err


def test_journey_combos_board_to_unlock_ping(client):
    page = client.get("/todays-picks/combos")
    assert page.status_code == 200
    ping = client.post("/api/unlock/combo")
    assert ping.status_code == 204


def test_journey_accuracy_and_faq(client):
    acc = client.get("/accuracy")
    assert acc.status_code == 200
    faq = client.get("/faq")
    assert faq.status_code == 200
    assert b"NFL" in faq.data
