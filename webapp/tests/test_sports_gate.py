"""Sports-prediction unlock gate: join Telegram once, unlock every sport's
prediction pages. Today's Picks / Best Combos always stay free.

The gate is a no-op under TESTING (see app.py::sports_unlock_gate) so the
rest of the suite -- written before the gate existed -- doesn't need to
unlock first. These tests flip SPORTS_GATE_FORCE on to exercise it.
"""
import app as app_module
import unlocks
import telegram_gate


def _force_gate(monkeypatch):
    monkeypatch.setitem(app_module.app.config, "SPORTS_GATE_FORCE", True)


def _isolate_unlocks_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "unlocks.db")
    monkeypatch.setattr(unlocks, "DB_PATH", db_path)
    unlocks.init_db()


def _visitor_id_from_client(client):
    with app_module.app.test_request_context():
        cookie = client.get_cookie("dp_vid")
        assert cookie is not None
        return app_module._visitor_cookie_signer().loads(cookie.value)


def test_gated_soccer_page_shows_gate_when_locked(client, monkeypatch, tmp_path):
    _force_gate(monkeypatch)
    _isolate_unlocks_db(tmp_path, monkeypatch)
    resp = client.get("/premier_league/")
    assert resp.status_code == 403
    assert b"Unlock this prediction" in resp.data
    assert client.get_cookie("dp_vid") is not None


def test_gated_predict_api_returns_json_403(client, monkeypatch, tmp_path):
    _force_gate(monkeypatch)
    _isolate_unlocks_db(tmp_path, monkeypatch)
    resp = client.get("/mlb/api/predict")
    assert resp.status_code == 403
    assert resp.get_json()["error"] == "locked"


def test_todays_picks_and_combos_never_gated(client, monkeypatch, tmp_path):
    _force_gate(monkeypatch)
    _isolate_unlocks_db(tmp_path, monkeypatch)
    assert client.get("/todays-picks").status_code == 200
    assert client.get("/todays-picks/combos").status_code == 200


def test_fixtures_results_and_about_pages_never_gated(client, monkeypatch, tmp_path):
    _force_gate(monkeypatch)
    _isolate_unlocks_db(tmp_path, monkeypatch)
    assert client.get("/mlb/fixtures").status_code == 200
    assert client.get("/mlb/results").status_code == 200
    assert client.get("/premier_league/about").status_code == 200


def test_unlocking_the_visitor_removes_the_gate(client, monkeypatch, tmp_path):
    _force_gate(monkeypatch)
    _isolate_unlocks_db(tmp_path, monkeypatch)

    locked = client.get("/premier_league/")
    assert locked.status_code == 403
    visitor_id = _visitor_id_from_client(client)
    assert unlocks.is_unlocked(visitor_id) is False

    unlocks.mark_unlocked(visitor_id, telegram_user_id="999", telegram_username="tester")

    unlocked = client.get("/premier_league/")
    assert unlocked.status_code == 200
    assert client.get("/mlb/api/predict").status_code != 403
    assert client.get("/api/unlock-status").get_json() == {"unlocked": True}


def test_spoofed_crawler_ua_does_not_bypass_gate(client, monkeypatch, tmp_path):
    """Anyone can set a browser's User-Agent to "Googlebot" -- only a real,
    DNS-verified crawler request should skip the gate."""
    _force_gate(monkeypatch)
    _isolate_unlocks_db(tmp_path, monkeypatch)
    resp = client.get(
        "/premier_league/",
        headers={"User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"},
    )
    assert resp.status_code == 403


def test_verified_crawler_skips_the_gate(client, monkeypatch, tmp_path):
    _force_gate(monkeypatch)
    _isolate_unlocks_db(tmp_path, monkeypatch)
    import crawler_verify
    monkeypatch.setattr(crawler_verify, "is_verified_crawler", lambda ua, ip: True)
    resp = client.get("/premier_league/")
    assert resp.status_code == 200


def test_webhook_rejects_wrong_secret_token(client, monkeypatch, tmp_path):
    _isolate_unlocks_db(tmp_path, monkeypatch)
    resp = client.post(
        "/telegram/webhook",
        json={"message": {"text": "/start abc", "from": {"id": 1}}},
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
    )
    assert resp.status_code == 403


def test_webhook_unknown_code_is_a_no_op(client, monkeypatch, tmp_path):
    _isolate_unlocks_db(tmp_path, monkeypatch)
    secret = telegram_gate.webhook_secret()
    resp = client.post(
        "/telegram/webhook",
        json={"message": {"text": "/start does-not-exist", "from": {"id": 1, "username": "x"}}},
        headers={"X-Telegram-Bot-Api-Secret-Token": secret},
    )
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True}


def test_webhook_member_unlocks_visitor(client, monkeypatch, tmp_path):
    _isolate_unlocks_db(tmp_path, monkeypatch)
    monkeypatch.setattr(telegram_gate, "is_chat_member", lambda user_id: True)
    dm_calls = []
    monkeypatch.setattr(
        telegram_gate, "send_dm",
        lambda user_id, text: dm_calls.append((user_id, text)) or True,
    )

    visitor_id = unlocks.new_visitor_id()
    code = unlocks.get_or_create_code(visitor_id)
    secret = telegram_gate.webhook_secret()

    resp = client.post(
        "/telegram/webhook",
        json={"message": {"text": f"/start {code}", "from": {"id": 555, "username": "carol"}}},
        headers={"X-Telegram-Bot-Api-Secret-Token": secret},
    )
    assert resp.status_code == 200
    assert unlocks.is_unlocked(visitor_id) is True
    assert dm_calls and dm_calls[0][0] == 555
    assert "verified" in dm_calls[0][1].lower()


def test_webhook_non_member_does_not_unlock_and_prompts_to_join(client, monkeypatch, tmp_path):
    _isolate_unlocks_db(tmp_path, monkeypatch)
    monkeypatch.setattr(telegram_gate, "is_chat_member", lambda user_id: False)
    dm_calls = []
    monkeypatch.setattr(
        telegram_gate, "send_dm",
        lambda user_id, text: dm_calls.append((user_id, text)) or True,
    )

    visitor_id = unlocks.new_visitor_id()
    code = unlocks.get_or_create_code(visitor_id)
    secret = telegram_gate.webhook_secret()

    resp = client.post(
        "/telegram/webhook",
        json={"message": {"text": f"/start {code}", "from": {"id": 777, "username": "dave"}}},
        headers={"X-Telegram-Bot-Api-Secret-Token": secret},
    )
    assert resp.status_code == 200
    assert unlocks.is_unlocked(visitor_id) is False
    assert dm_calls and "join" in dm_calls[0][1].lower()


def test_resolve_code_rejects_expired_code(tmp_path, monkeypatch):
    _isolate_unlocks_db(tmp_path, monkeypatch)
    visitor_id = unlocks.new_visitor_id()
    code = unlocks.get_or_create_code(visitor_id)
    with unlocks.get_db() as conn:
        conn.execute(
            "UPDATE unlocks SET code_expires_at=? WHERE visitor_id=?",
            ("2000-01-01T00:00:00+00:00", visitor_id),
        )
    assert unlocks.resolve_code(code) is None


def test_get_or_create_code_is_stable_until_expiry(tmp_path, monkeypatch):
    _isolate_unlocks_db(tmp_path, monkeypatch)
    visitor_id = unlocks.new_visitor_id()
    code1 = unlocks.get_or_create_code(visitor_id)
    code2 = unlocks.get_or_create_code(visitor_id)
    assert code1 == code2


def test_get_or_create_code_returns_empty_once_unlocked(tmp_path, monkeypatch):
    _isolate_unlocks_db(tmp_path, monkeypatch)
    visitor_id = unlocks.new_visitor_id()
    unlocks.get_or_create_code(visitor_id)
    unlocks.mark_unlocked(visitor_id, telegram_user_id="1")
    assert unlocks.get_or_create_code(visitor_id) == ""


def test_gate_kind_distinguishes_fixture_detail_from_fixtures_list():
    """/hundred/fixture/<slug> (singular, detail) must gate; /hundred/fixtures
    (plural, listing) must not -- naive prefix matching would conflate them."""
    assert app_module._sports_gate_kind("/hundred/fixture/some-match") == "page"
    assert app_module._sports_gate_kind("/hundred/fixtures") is None
    assert app_module._sports_gate_kind("/tennis/fixture/some-match") == "page"
    assert app_module._sports_gate_kind("/tennis/fixtures") is None


def test_gate_kind_covers_all_configured_leagues():
    from leagues import LEAGUES
    for league_key in LEAGUES:
        assert app_module._sports_gate_kind(f"/{league_key}/") == "page"
        assert app_module._sports_gate_kind(f"/{league_key}/match/A/B") == "page"
        assert app_module._sports_gate_kind(f"/{league_key}/api/predict/A/B") == "api"
        assert app_module._sports_gate_kind(f"/{league_key}/about") is None
        assert app_module._sports_gate_kind(f"/{league_key}/results") is None
