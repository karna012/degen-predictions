"""Functional tests: drive every route through Flask's test client the way
a browser would, without needing a running server."""
import app as app_module


# ---- Landing / leagues page -------------------------------------------------

def test_root_redirect_renders_category_landing_page(client):
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "SPORTS" in body.upper()
    assert "CRYPTO" in body.upper()
    assert "STOCKS" in body.upper()


def test_soccer_leagues_page_lists_all_configured_leagues(client):
    resp = client.get("/sports/soccer")
    assert resp.status_code == 200
    body = resp.data.decode()
    for cfg in app_module.LEAGUES.values():
        assert cfg["label"].upper() in body.upper()


# ---- Per-league fixture page -------------------------------------------------

def test_index_page_loads_for_every_league(client, league_key):
    resp = client.get(f"/{league_key}/")
    assert resp.status_code == 200


def test_index_unknown_league_is_404(client):
    resp = client.get("/not_a_real_league/")
    assert resp.status_code == 404


def test_index_team_filter_narrows_results(client, league_key):
    df = app_module.load_predictions(league_key)
    if df.empty:
        return
    team = df.iloc[0]["HomeTeam"]
    resp = client.get(f"/{league_key}/", query_string={"team": team})
    assert resp.status_code == 200
    body = resp.data.decode()
    assert team in body


def test_index_round_query_param_selects_that_round(client, league_key):
    df = app_module.load_predictions(league_key)
    if df.empty:
        return
    unlocked = app_module.compute_unlocked_round(df)
    resp = client.get(f"/{league_key}/", query_string={"round": unlocked})
    assert resp.status_code == 200
    assert f"Round {unlocked}" in resp.data.decode()


def test_index_locked_round_falls_back_to_unlocked_round(client, league_key):
    """Requesting a round number past what's unlocked shouldn't 500 or leak
    locked fixtures -- app.py silently falls back to the unlocked round."""
    df = app_module.load_predictions(league_key)
    if df.empty:
        return
    unlocked = app_module.compute_unlocked_round(df)
    far_future_round = unlocked + 999
    resp = client.get(f"/{league_key}/", query_string={"round": far_future_round})
    assert resp.status_code == 200


# ---- Match detail page -------------------------------------------------

def test_match_detail_loads_for_a_real_fixture(client, league_key):
    df = app_module.load_predictions(league_key)
    if df.empty:
        return
    row = df.iloc[0]
    resp = client.get(f"/{league_key}/match/{row['HomeTeam']}/{row['AwayTeam']}")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert row["HomeTeam"] in body and row["AwayTeam"] in body


def test_match_detail_unknown_fixture_is_404(client):
    resp = client.get("/premier_league/match/NoSuchTeam/AlsoFake")
    assert resp.status_code == 404


# ---- Live predict API -------------------------------------------------

def test_api_predict_returns_json_for_real_fixture(client):
    df = app_module.load_predictions("premier_league")
    if df.empty:
        return
    row = df.iloc[0]
    resp = client.get(f"/premier_league/api/predict/{row['HomeTeam']}/{row['AwayTeam']}")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "Predicted_Result" in data
    assert "P_Home_Win" in data


def test_api_predict_unknown_fixture_returns_404_json(client):
    resp = client.get("/premier_league/api/predict/NoSuchTeam/AlsoFake")
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "Fixture not found"


# ---- Backtest / about pages -------------------------------------------------

def test_backtest_page_loads_for_every_league(client, league_key):
    resp = client.get(f"/{league_key}/backtest")
    assert resp.status_code == 200


def test_about_page_loads_for_every_league(client, league_key):
    resp = client.get(f"/{league_key}/about")
    assert resp.status_code == 200


def test_about_page_shows_author_details(client):
    resp = client.get("/premier_league/about")
    body = resp.data.decode()
    assert "About the Author" in body
    assert "Karan R" in body
    assert "info@degenpredictions.com" in body


# ---- Legal pages -------------------------------------------------

def test_terms_page_loads(client):
    resp = client.get("/terms")
    assert resp.status_code == 200


def test_privacy_page_loads(client):
    resp = client.get("/privacy")
    assert resp.status_code == 200


def test_footer_has_working_about_link(client, league_key):
    resp = client.get(f"/{league_key}/")
    body = resp.data.decode()
    assert f'href="/{league_key}/about">About</a>' in body
    about_resp = client.get(f"/{league_key}/about")
    assert about_resp.status_code == 200


# ---- Removed auth/credits/simulator surface should be fully gone -----------

def test_login_route_exists(client):
    resp = client.get("/login")
    assert resp.status_code == 200


def test_signup_route_exists(client):
    resp = client.get("/signup")
    assert resp.status_code == 200


def test_profile_route_no_longer_exists(client):
    resp = client.get("/profile", follow_redirects=True)
    assert resp.status_code == 404


def test_simulator_route_no_longer_exists(client):
    resp = client.get("/premier_league/simulator")
    assert resp.status_code == 404


def test_admin_deposits_route_no_longer_exists(client):
    resp = client.get("/admin/deposits")
    assert resp.status_code == 404


def test_credits_api_route_no_longer_exists(client):
    resp = client.get("/api/credits")
    assert resp.status_code == 404
