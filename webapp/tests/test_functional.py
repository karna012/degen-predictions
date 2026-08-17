"""Functional tests: drive every route through Flask's test client the way
a browser would, without needing a running server."""
import app as app_module


# ---- Landing / leagues page -------------------------------------------------

def test_homepage_daily_drop_not_slate(client):
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "SPORTS" in body.upper()
    assert "CRYPTO" in body.upper()
    assert "STOCKS" in body.upper()
    assert "home-drop" in body
    assert "Today’s slate" not in body
    assert "home-slate-legs" not in body
    assert "dp_daily_streak" in body
    assert "Unlock today’s calls" in body or "/todays-picks" in body


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


def test_soccer_results_page_loads(client):
    resp = client.get("/la_liga/results")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Results" in body
    assert 'href="/la_liga/"' in body


def test_soccer_fixtures_alias_redirects(client):
    resp = client.get("/la_liga/fixtures")
    assert resp.status_code in (301, 302)
    assert resp.headers["Location"].endswith("/la_liga/")


def test_mlb_and_nfl_results_are_not_captured_by_soccer_results(client):
    mlb = client.get("/mlb/results")
    assert mlb.status_code == 200
    assert "MLB" in mlb.data.decode()
    nfl = client.get("/nfl/results")
    assert nfl.status_code == 200


def test_soccer_fixtures_hide_past_mls_opener(client):
    body = client.get("/mls/").data.decode()
    assert 'data-home="New England Revolution" data-away="Houston Dynamo"' not in body


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
    assert "Safer_Result_Pick" in data
    assert "P_Safer_Result" in data


def test_api_predict_unknown_fixture_returns_404_json(client):
    resp = client.get("/premier_league/api/predict/NoSuchTeam/AlsoFake")
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "Fixture not found"


def test_api_predict_mls_handles_skipped_corner_models(client):
    """MLS trains without corner markets; live predict must not 500 on unpack."""
    df = app_module.load_predictions("mls")
    if df.empty:
        return
    row = df.iloc[0]
    resp = client.get(f"/mls/api/predict/{row['HomeTeam']}/{row['AwayTeam']}")
    assert resp.status_code == 200, resp.get_json()
    data = resp.get_json()
    assert "Predicted_Result" in data
    assert "P_Home_Win" in data
    assert "Safer_Result_Pick" in data
    # Skipped corner markets serialize as empty strings (same as batch CSV).
    assert data["Exp_Total_Corners"] == ""
    assert data["P_Home_More_Corners"] == ""
    assert data["More_Corners_Prediction"] == ""


def test_api_predict_hides_raw_python_exceptions(client, monkeypatch):
    df = app_module.load_predictions("premier_league")
    if df.empty:
        return
    row = df.iloc[0]

    def boom(*_args, **_kwargs):
        raise TypeError("cannot unpack non-iterable NoneType object")

    monkeypatch.setattr(app_module, "predict_fixture_live", boom)
    resp = client.get(
        f"/premier_league/api/predict/{row['HomeTeam']}/{row['AwayTeam']}"
    )
    assert resp.status_code == 500
    err = resp.get_json()["error"]
    assert "cannot unpack" not in err
    assert "Prediction unavailable" in err


# ---- Backtest / about pages -------------------------------------------------

def test_backtest_page_requires_admin_for_every_league(client, league_key, monkeypatch):
    monkeypatch.setattr(app_module, "ADMIN_USER", "admin")
    monkeypatch.setattr(app_module, "ADMIN_PASSWORD", "secret123")
    resp = client.get(f"/{league_key}/backtest")
    assert resp.status_code == 302
    assert "/admin/login" in (resp.headers.get("Location") or "")


def test_backtest_page_loads_for_admin(client, league_key, monkeypatch):
    import base64
    monkeypatch.setattr(app_module, "ADMIN_USER", "admin")
    monkeypatch.setattr(app_module, "ADMIN_PASSWORD", "secret123")
    token = base64.b64encode(b"admin:secret123").decode()
    resp = client.get(
        f"/{league_key}/backtest",
        headers={"Authorization": f"Basic {token}"},
    )
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Trust Out-of-Sample" in body
    assert "OOS default" in body or 'value="out"' in body


def test_about_page_loads_for_every_league(client, league_key):
    resp = client.get(f"/{league_key}/about")
    assert resp.status_code == 200


def test_about_page_shows_contact_details(client):
    resp = client.get("/premier_league/about")
    body = resp.data.decode()
    assert "About the Author" not in body
    assert "Deployment & Hosting" not in body
    assert "How we score (full slate vs 70% tier)" not in body
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


def test_footer_has_telegram_and_instagram(client):
    body = client.get("/").data.decode()
    assert "Join Telegram" in body
    assert "Follow Instagram" in body
    assert "Follow @degenprediction" in body
    assert "footer-social-link--telegram" in body
    assert "footer-social-link--instagram" in body
    assert "footer-social-link--x" in body
    assert "instagram.com/degen_predictions467" in body
    assert "x.com/degenprediction" in body
    assert "t.me/" in body
    assert 'href="/go/telegram"' in body
    assert 'id="tg-join-overlay"' in body
    assert "tg-join-x" in body
    assert "openTelegramJoinPopup" in body
    assert "dp-tg-join-seen" not in body
    assert "mailto:info@degenpredictions.com" in body
    assert "footer-social-link--email" in body
    assert 'href="/contact">Contact Us</a>' not in body
    inner = client.get("/sports").data.decode()
    assert 'id="tg-join-overlay"' not in inner
    assert "openTelegramJoinPopup" not in inner


def test_go_telegram_redirects_to_channel(client):
    resp = client.get("/go/telegram", follow_redirects=False)
    assert resp.status_code == 302
    assert "t.me/" in (resp.headers.get("Location") or "")


def test_contact_page_redirects_to_about(client):
    resp = client.get("/contact", follow_redirects=False)
    assert resp.status_code == 301
    assert resp.headers["Location"].rstrip("/").endswith("/about")


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
