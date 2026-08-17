"""The /results predicted-vs-actual page is internal (accuracy numbers, not
meant for public visitors) -- gated behind HTTP Basic Auth via
ADMIN_USER/ADMIN_PASSWORD env vars, checked with admin_required() in app.py."""
import base64
import datetime
from pathlib import Path

import app as app_module
import today_picks


def _basic_auth_header(user, password):
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def test_results_page_refused_when_admin_creds_not_configured(client, monkeypatch):
    monkeypatch.setattr(app_module, "ADMIN_USER", "")
    monkeypatch.setattr(app_module, "ADMIN_PASSWORD", "")
    resp = client.get("/results")
    assert resp.status_code == 404


def test_results_page_requires_auth_when_configured(client, monkeypatch):
    monkeypatch.setattr(app_module, "ADMIN_USER", "admin")
    monkeypatch.setattr(app_module, "ADMIN_PASSWORD", "secret123")
    resp = client.get("/results")
    assert resp.status_code == 302
    assert "/admin/login" in (resp.headers.get("Location") or "")


def test_results_page_rejects_wrong_credentials(client, monkeypatch):
    monkeypatch.setattr(app_module, "ADMIN_USER", "admin")
    monkeypatch.setattr(app_module, "ADMIN_PASSWORD", "secret123")
    resp = client.get("/results", headers=_basic_auth_header("admin", "wrong"))
    assert resp.status_code == 401


def test_results_page_loads_with_correct_credentials(client, monkeypatch):
    monkeypatch.setattr(app_module, "ADMIN_USER", "admin")
    monkeypatch.setattr(app_module, "ADMIN_PASSWORD", "secret123")
    resp = client.get("/results", headers=_basic_auth_header("admin", "secret123"))
    assert resp.status_code == 200
    body = resp.data.decode()
    assert 'class="admin-shell"' in body
    assert "Admin Console" in body
    assert 'href="/admin?section=tools' in body


def test_backtest_and_players_use_admin_shell(client, monkeypatch):
    monkeypatch.setattr(app_module, "ADMIN_USER", "admin")
    monkeypatch.setattr(app_module, "ADMIN_PASSWORD", "secret123")
    auth = _basic_auth_header("admin", "secret123")
    for path in ("/premier_league/backtest", "/mls/players"):
        resp = client.get(path, headers=auth)
        assert resp.status_code == 200
        body = resp.data.decode()
        assert 'class="admin-shell"' in body
        assert "admin.css" in body


def test_admin_overview_skips_combo_gather(client, monkeypatch):
    monkeypatch.setattr(app_module, "ADMIN_USER", "admin")
    monkeypatch.setattr(app_module, "ADMIN_PASSWORD", "secret123")

    def boom(*_a, **_k):
        raise AssertionError("overview must not rebuild combo board")

    monkeypatch.setattr(app_module, "_gather_combo_picks_today", boom)
    monkeypatch.setattr(app_module, "_gather_admin_full_market_legs", boom)
    auth = _basic_auth_header("admin", "secret123")
    resp = client.get("/admin?section=overview", headers=auth)
    assert resp.status_code == 200
    assert b"Admin Console" in resp.data


def test_admin_picks_and_combos_sections_require_auth(client, monkeypatch):
    monkeypatch.setattr(app_module, "ADMIN_USER", "admin")
    monkeypatch.setattr(app_module, "ADMIN_PASSWORD", "secret123")
    assert client.get("/admin?section=picks").status_code == 302
    assert client.get("/admin?section=combos").status_code == 302
    assert client.get("/admin?section=combo_results").status_code == 302
    auth = _basic_auth_header("admin", "secret123")
    for section in ("picks", "combos", "combo_results", "telegram", "degen_team", "twitter"):
        resp = client.get(f"/admin?section={section}&settle=0", headers=auth)
        assert resp.status_code == 200
        body = resp.data.decode()
        assert 'class="admin-shell"' in body
        assert f'data-admin-section="{section}"' in body
    # Telegram results ledger — settle + RESULT replies for channel posts.
    resp = client.get("/admin?section=degen_team", headers=auth)
    assert resp.status_code == 200
    assert 'data-admin-section="degen_team"' in resp.data.decode()
    assert client.post("/admin/degen-team/settle", data={
        "kind": "single", "id": 1, "status": "won",
    }, headers=auth).status_code != 404


def test_admin_telegram_section_uses_sports_hierarchy(client, monkeypatch):
    """Telegram sender mirrors Today's Picks sport → league → event → markets."""
    monkeypatch.setattr(app_module, "ADMIN_USER", "admin")
    monkeypatch.setattr(app_module, "ADMIN_PASSWORD", "secret123")

    soon = (
        datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=3)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    sample_legs = [
        today_picks.annotate_leg(
            today_picks._pick(
                away="Away FC", home="Home FC", pick="Home",
                confidence=0.71, market="1X2",
                matchup="Home FC vs Away FC", status="upcoming",
                kickoff_iso=soon,
            ),
            sport_key="soccer", sport_label="Soccer",
            league_id="mls", league_label="MLS",
        ),
        today_picks.annotate_leg(
            today_picks._pick(
                away="Away FC", home="Home FC", pick="Yes",
                confidence=0.66, market="BTTS",
                matchup="Home FC vs Away FC", status="upcoming",
                kickoff_iso=soon,
            ),
            sport_key="soccer", sport_label="Soccer",
            league_id="mls", league_label="MLS",
        ),
    ]

    monkeypatch.setattr(app_module, "_gather_admin_full_market_legs", lambda: sample_legs)
    monkeypatch.setattr(app_module, "_cached_live_fixtures", lambda: [])
    monkeypatch.setattr(
        app_module, "_live_digest_payload",
        lambda: {"date": "2099-01-01", "picks": [], "combos_2": [], "combos_3": [],
                 "mixed_2": [], "mixed_3": []},
    )

    auth = _basic_auth_header("admin", "secret123")
    resp = client.get("/admin?section=telegram", headers=auth)
    assert resp.status_code == 200
    body = resp.data.decode()
    assert 'data-admin-section="telegram"' in body
    assert 'class="tg-hierarchy"' in body
    assert 'id="tg-sport-pane"' in body
    assert 'id="tg-league-pane"' in body
    assert 'id="tg-event-pane"' in body
    assert 'name="slip_mode"' in body
    assert 'class="tg-pick-cb"' in body
    assert 'class="btn-tg-send"' in body
    assert "Today's digest" in body
    assert 'value="send_digest"' in body
    assert 'name="channel_telegram"' in body
    assert 'name="channel_x"' in body
    assert "Custom market" in body
    assert "+ Custom league" in body
    assert "+ Custom event" in body
    assert 'id="tg-add-league-btn"' in body
    assert 'id="tg-add-event-btn"' in body
    assert "Soccer" in body  # from TODAY_SPORTS
    assert "Home FC vs Away FC" in body
    assert 'name="pick_idx"' in body
    assert "tg-event-summary" not in body  # old accordion gone


def test_admin_telegram_hides_finished_events(client, monkeypatch):
    """Finished / kicked-off fixtures must not appear in Send to Telegram."""
    monkeypatch.setattr(app_module, "ADMIN_USER", "admin")
    monkeypatch.setattr(app_module, "ADMIN_PASSWORD", "secret123")

    past = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=5)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    soon = (
        datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=3)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    sample_legs = [
        today_picks.annotate_leg(
            today_picks._pick(
                away="Done Away", home="Done Home", pick="Home",
                confidence=0.71, market="1X2",
                matchup="Done Home vs Done Away", status="upcoming",
                kickoff_iso=past,
            ),
            sport_key="soccer", sport_label="Soccer",
            league_id="mls", league_label="MLS",
        ),
        today_picks.annotate_leg(
            today_picks._pick(
                away="Live Away", home="Live Home", pick="Away",
                confidence=0.68, market="1X2",
                matchup="Live Home vs Live Away", status="upcoming",
                kickoff_iso=soon,
            ),
            sport_key="soccer", sport_label="Soccer",
            league_id="mls", league_label="MLS",
        ),
    ]
    monkeypatch.setattr(app_module, "_gather_admin_full_market_legs", lambda: sample_legs)
    monkeypatch.setattr(app_module, "_cached_live_fixtures", lambda: [])
    monkeypatch.setattr(
        app_module, "_live_digest_payload",
        lambda: {
            "date": "2099-01-01",
            "picks": [{
                "matchup": "Done Home vs Done Away",
                "pick": "Home",
                "market": "1X2",
                "kickoff_iso": past,
                "status": "final",
                "home_score": 2,
                "away_score": 1,
            }],
            "combos_2": [{
                "title": "Stale 2-leg",
                "legs": [{
                    "matchup": "Done Home vs Done Away",
                    "pick": "Home",
                    "market": "1X2",
                    "kickoff_iso": past,
                    "status": "final",
                    "home_score": 2,
                    "away_score": 1,
                }],
            }],
            "combos_3": [],
            "mixed_2": [],
            "mixed_3": [],
        },
    )

    auth = _basic_auth_header("admin", "secret123")
    resp = client.get("/admin?section=telegram", headers=auth)
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Done Home vs Done Away" not in body
    assert "Live Home vs Live Away" in body
    assert "Stale 2-leg" not in body


def test_admin_sendable_board_legs_drops_finished(monkeypatch):
    past = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=5)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    soon = (
        datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=3)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    sample_legs = [
        today_picks.annotate_leg(
            today_picks._pick(
                away="A", home="B", pick="B", confidence=0.7, market="1X2",
                matchup="B vs A", status="upcoming", kickoff_iso=past,
            ),
            sport_key="soccer", sport_label="Soccer",
            league_id="mls", league_label="MLS",
        ),
        today_picks.annotate_leg(
            today_picks._pick(
                away="C", home="D", pick="D", confidence=0.7, market="1X2",
                matchup="D vs C", status="upcoming", kickoff_iso=soon,
            ),
            sport_key="soccer", sport_label="Soccer",
            league_id="mls", league_label="MLS",
        ),
    ]
    monkeypatch.setattr(app_module, "_gather_admin_full_market_legs", lambda: sample_legs)
    monkeypatch.setattr(app_module, "_cached_live_fixtures", lambda: [])
    out = app_module._admin_sendable_board_legs()
    assert len(out) == 1
    assert out[0]["matchup"] == "D vs C"


def test_admin_twitter_queue_one_by_one(client, monkeypatch, tmp_path):
    import daily_digest

    monkeypatch.setattr(app_module, "ADMIN_USER", "admin")
    monkeypatch.setattr(app_module, "ADMIN_PASSWORD", "secret123")
    monkeypatch.setattr(daily_digest, "LAST_TELEGRAM_SEND_PATH", tmp_path / "last_tg.json")
    monkeypatch.setattr(daily_digest, "CARDS_DIR", tmp_path / "cards")
    monkeypatch.setattr(daily_digest, "DIGEST_DIR", tmp_path / "digest")
    daily_digest.save_last_telegram_send([{
        "title": "Single",
        "kind": "picks",
        "legs": [{
            "matchup": "Tigers @ Giants", "pick": "Tigers", "market": "Moneyline",
            "sport_key": "baseball", "sport_label": "Baseball",
            "league_id": "npb", "league_label": "NPB",
        }],
    }], day="2026-08-11")
    empty = {"date": "2026-08-11", "picks": [], "combos_2": [], "combos_3": [],
             "mixed_2": [], "mixed_3": []}
    monkeypatch.setattr(app_module, "_live_digest_and_legs", lambda: (empty, None))
    monkeypatch.setattr(app_module, "_live_digest_payload", lambda: empty)
    auth = _basic_auth_header("admin", "secret123")
    resp = client.get("/admin?section=twitter", headers=auth)
    assert resp.status_code == 200
    body = resp.data.decode()
    assert 'data-admin-section="twitter"' in body
    assert "Auto-post this slip" in body or 'tw-ext-post-page' in body
    assert "#DegenPredictions" in body
    assert "#NPB" in body
    assert "/admin/twitter-image/1.png" in body
    assert "Giants vs Tigers" in body
    img = client.get("/admin/twitter-image/1.png?day=2026-08-11", headers=auth)
    assert img.status_code == 200
    assert img.mimetype == "image/png"


def test_admin_x_queue_json(client, monkeypatch, tmp_path):
    import daily_digest

    monkeypatch.setattr(app_module, "ADMIN_USER", "admin")
    monkeypatch.setattr(app_module, "ADMIN_PASSWORD", "secret123")
    monkeypatch.setattr(daily_digest, "LAST_TELEGRAM_SEND_PATH", tmp_path / "last_tg.json")
    monkeypatch.setattr(daily_digest, "CARDS_DIR", tmp_path / "cards")
    monkeypatch.setattr(daily_digest, "DIGEST_DIR", tmp_path / "digest")
    daily_digest.save_last_telegram_send([{
        "title": "Single",
        "kind": "picks",
        "legs": [{
            "matchup": "Tigers @ Giants", "pick": "Tigers", "market": "Moneyline",
            "sport_key": "baseball", "sport_label": "Baseball",
            "league_id": "npb", "league_label": "NPB",
        }],
    }], day="2026-08-11")
    empty = {"date": "2026-08-11", "picks": [], "combos_2": [], "combos_3": [],
             "mixed_2": [], "mixed_3": []}
    monkeypatch.setattr(app_module, "_live_digest_and_legs", lambda: (empty, None))
    monkeypatch.setattr(app_module, "_live_digest_payload", lambda: empty)
    auth = _basic_auth_header("admin", "secret123")
    resp = client.get("/admin/x-queue.json?prefer=telegram", headers=auth)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["count"] == 1
    assert data["posts"][0]["text"]
    assert "/admin/twitter-image/1.png" in data["posts"][0]["image_url"]


def test_filter_pick_legs_and_combos():
    legs = [
        today_picks.annotate_leg(
            today_picks._pick(
                away="a", home="b", pick="Under 2.5", confidence=0.80,
                market="Total Goals", matchup="a vs b", status="upcoming",
            ),
            sport_key="soccer", sport_label="Soccer",
            league_id="premier_league", league_label="Premier League",
        ),
        today_picks.annotate_leg(
            today_picks._pick(
                away="c", home="d", pick="Over 8.5", confidence=0.70,
                market="Total runs", matchup="c @ d", status="upcoming",
            ),
            sport_key="baseball", sport_label="Baseball",
            league_id="mlb", league_label="MLB",
        ),
    ]
    only_under = today_picks.filter_pick_legs(legs, side="under")
    assert len(only_under) == 1
    assert only_under[0]["league_id"] == "premier_league"
    mlb = today_picks.filter_pick_legs(legs, sport="baseball", min_conf=0.69)
    assert len(mlb) == 1

    combos = today_picks.build_best_combos(legs, sizes=(2,), top_k=1)
    has_over = today_picks.filter_combos(combos[2], side="over")
    assert len(has_over) == 1
    no_soccer = today_picks.filter_combos(combos[2], sport="cricket")
    assert no_soccer == []


def test_results_link_not_in_public_nav(client):
    resp = client.get("/")
    body = resp.data.decode()
    assert 'href="/results"' not in body


def test_player_analysis_and_backtest_hidden_on_fixtures(client):
    resp = client.get("/premier_league/")
    body = resp.data.decode()
    assert "Player analysis" not in body
    assert 'href="/premier_league/players"' not in body
    assert 'href="/premier_league/backtest"' not in body
    assert "Past seasons" not in body


def test_backtest_requires_auth_when_configured(client, monkeypatch):
    monkeypatch.setattr(app_module, "ADMIN_USER", "admin")
    monkeypatch.setattr(app_module, "ADMIN_PASSWORD", "secret123")
    resp = client.get("/premier_league/backtest")
    assert resp.status_code == 302
    assert "/admin/login" in (resp.headers.get("Location") or "")


def test_players_requires_auth_when_configured(client, monkeypatch):
    monkeypatch.setattr(app_module, "ADMIN_USER", "admin")
    monkeypatch.setattr(app_module, "ADMIN_PASSWORD", "secret123")
    resp = client.get("/mls/players")
    assert resp.status_code == 302
    assert "/admin/login" in (resp.headers.get("Location") or "")


def test_admin_custom_betslip_send_skips_board_gather(client, monkeypatch, tmp_path):
    """Pasted/OCR slips must not rebuild every league before sendPhoto."""
    import daily_digest

    monkeypatch.setattr(app_module, "ADMIN_USER", "admin")
    monkeypatch.setattr(app_module, "ADMIN_PASSWORD", "secret123")
    monkeypatch.setattr(daily_digest, "CARDS_DIR", tmp_path / "cards")
    monkeypatch.setattr(daily_digest, "DIGEST_DIR", tmp_path / "digest")
    monkeypatch.setattr(daily_digest, "LAST_TELEGRAM_SEND_PATH", tmp_path / "last_tg.json")

    def boom(*_a, **_k):
        raise AssertionError("full-market gather must not run for custom-only send")

    monkeypatch.setattr(app_module, "_gather_admin_full_market_legs", boom)
    monkeypatch.setattr(
        app_module, "_live_digest_payload",
        lambda: {"date": "2099-01-01", "picks": [], "combos_2": [], "combos_3": [],
                 "mixed_2": [], "mixed_3": []},
    )

    posted = {}

    def fake_post(digest, slip_images=None, **kwargs):
        posted["slips"] = list(slip_images or [])
        posted["rebuild"] = kwargs.get("rebuild_images")
        return True, "posted Telegram slip images (1 photos: 1 picks + 0 combos, +1 header)"

    monkeypatch.setattr(daily_digest, "post_telegram", fake_post)

    auth = _basic_auth_header("admin", "secret123")
    with client.session_transaction() as sess:
        sess["_csrf_token"] = "csrf-test"
    resp = client.post("/admin/telegram-send", headers=auth, data={
        "csrf_token": "csrf-test",
        "action": "send_custom",
        "slip_mode": "single",
        "custom_matchup": "Team A vs Team B",
        "custom_market": "BTTS",
        "custom_pick": "Yes",
        "custom_sport": "Soccer",
        "custom_league": "MLS",
    })
    assert resp.status_code == 302
    loc = resp.headers.get("Location") or ""
    assert "ok_1" in loc
    assert posted.get("slips")
    slip = posted["slips"][0]
    assert slip.get("png_bytes") or (slip.get("path") and Path(slip["path"]).is_file())
    assert posted.get("rebuild") is False


def test_admin_digest_queue_send_picks_without_pick_idx(client, monkeypatch, tmp_path):
    import daily_digest

    monkeypatch.setattr(app_module, "ADMIN_USER", "admin")
    monkeypatch.setattr(app_module, "ADMIN_PASSWORD", "secret123")
    monkeypatch.setattr(daily_digest, "CARDS_DIR", tmp_path / "cards")
    monkeypatch.setattr(daily_digest, "DIGEST_DIR", tmp_path / "digest")
    monkeypatch.setattr(daily_digest, "LAST_TELEGRAM_SEND_PATH", tmp_path / "last_tg.json")
    monkeypatch.setattr(app_module, "_gather_admin_full_market_legs", lambda: [])
    monkeypatch.setattr(
        app_module, "_live_digest_payload",
        lambda: {
            "date": "2099-01-01",
            "picks": [{
                "matchup": "Dodgers @ Brewers",
                "pick": "Dodgers",
                "market": "Moneyline",
                "sport_label": "Baseball",
                "league_label": "MLB",
                "status": "upcoming",
            }],
            "combos_2": [],
            "combos_3": [],
            "mixed_2": [],
            "mixed_3": [],
        },
    )
    posted = {}

    def fake_post(digest, slip_images=None, **kwargs):
        posted["slips"] = list(slip_images or [])
        return True, "posted"

    monkeypatch.setattr(daily_digest, "post_telegram", fake_post)
    auth = _basic_auth_header("admin", "secret123")
    with client.session_transaction() as sess:
        sess["_csrf_token"] = "csrf-test"
    resp = client.post("/admin/telegram-send", headers=auth, data={
        "csrf_token": "csrf-test",
        "action": "send_picks",
    })
    assert resp.status_code == 302
    assert "ok_" in (resp.headers.get("Location") or "")
    assert posted.get("slips")


def test_admin_digest_send_x_only_skips_telegram(client, monkeypatch, tmp_path):
    import daily_digest

    monkeypatch.setattr(app_module, "ADMIN_USER", "admin")
    monkeypatch.setattr(app_module, "ADMIN_PASSWORD", "secret123")
    monkeypatch.setattr(daily_digest, "CARDS_DIR", tmp_path / "cards")
    monkeypatch.setattr(daily_digest, "DIGEST_DIR", tmp_path / "digest")
    monkeypatch.setattr(daily_digest, "LAST_TELEGRAM_SEND_PATH", tmp_path / "last_tg.json")
    monkeypatch.setattr(app_module, "_gather_admin_full_market_legs", lambda: [])
    monkeypatch.setattr(
        app_module, "_live_digest_payload",
        lambda: {
            "date": "2099-01-01",
            "picks": [{
                "matchup": "Dodgers @ Brewers",
                "pick": "Dodgers",
                "market": "Moneyline",
                "sport_label": "Baseball",
                "league_label": "MLB",
                "status": "upcoming",
            }],
            "combos_2": [],
            "combos_3": [],
            "mixed_2": [],
            "mixed_3": [],
        },
    )
    called = {"tg": 0}
    monkeypatch.setattr(
        daily_digest, "post_telegram",
        lambda *_a, **_k: called.__setitem__("tg", 1) or (True, "posted"),
    )
    auth = _basic_auth_header("admin", "secret123")
    with client.session_transaction() as sess:
        sess["_csrf_token"] = "csrf-test"
    resp = client.post("/admin/telegram-send", headers=auth, data={
        "csrf_token": "csrf-test",
        "action": "send_picks",
        "channel_x": "1",
    })
    assert resp.status_code == 302
    loc = resp.headers.get("Location") or ""
    assert "section=twitter" in loc
    assert called["tg"] == 0
    assert (tmp_path / "last_tg.json").is_file()


def test_admin_results_post_respects_channel_flags(client, monkeypatch):
    monkeypatch.setattr(app_module, "ADMIN_USER", "admin")
    monkeypatch.setattr(app_module, "ADMIN_PASSWORD", "secret123")
    monkeypatch.setattr(app_module.combo_results, "settle_pending", lambda: {})
    seen = {}

    def fake_post(**kwargs):
        seen.update(kwargs)
        return {"checked": 0, "posted": 0, "failed": 0,
                "twitter_posted": 0, "twitter_queued": 2, "twitter_failed": 0}

    monkeypatch.setattr(
        app_module.daily_digest, "post_due_telegram_results", fake_post,
    )
    auth = _basic_auth_header("admin", "secret123")
    with client.session_transaction() as sess:
        sess["_csrf_token"] = "csrf-test"
    resp = client.post("/admin/telegram-results-post", headers=auth, data={
        "csrf_token": "csrf-test",
        "channel_x": "1",
    })
    assert resp.status_code == 302
    assert seen.get("do_telegram") is False
    assert seen.get("do_twitter") is True


def test_admin_settle_does_not_auto_post_results(client, monkeypatch):
    import combo_results

    monkeypatch.setattr(app_module, "ADMIN_USER", "admin")
    monkeypatch.setattr(app_module, "ADMIN_PASSWORD", "secret123")
    monkeypatch.setattr(
        combo_results, "manual_settle_pick",
        lambda *_a, **_k: {"ok": True, "status": "won"},
    )
    called = {"tg": 0}
    monkeypatch.setattr(
        app_module.daily_digest, "post_due_telegram_results",
        lambda: called.__setitem__("tg", called["tg"] + 1) or {},
    )
    auth = _basic_auth_header("admin", "secret123")
    with client.session_transaction() as sess:
        sess["_csrf_token"] = "csrf-test"
    resp = client.post("/admin/degen-team/settle", headers=auth, data={
        "csrf_token": "csrf-test",
        "kind": "single",
        "id": "7",
        "status": "won",
    })
    assert resp.status_code == 302
    loc = resp.headers.get("Location") or ""
    assert "ok_won" in loc
    assert called["tg"] == 0


def test_admin_login_page_renders_brand(client, monkeypatch):
    monkeypatch.setattr(app_module, "ADMIN_USER", "admin")
    monkeypatch.setattr(app_module, "ADMIN_PASSWORD", "secret123")
    resp = client.get("/admin/login")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Admin Console" in body
    assert "DEGEN" in body
    assert "logo.png" in body or "logo-nav" in body
    assert 'name="username"' in body
    assert 'name="password"' in body
    assert 'name="csrf_token"' in body


def test_admin_login_session_grants_access(client, monkeypatch):
    monkeypatch.setattr(app_module, "ADMIN_USER", "admin")
    monkeypatch.setattr(app_module, "ADMIN_PASSWORD", "secret123")
    login_page = client.get("/admin/login")
    assert login_page.status_code == 200
    with client.session_transaction() as sess:
        csrf = sess.get("_csrf_token")
    assert csrf
    resp = client.post(
        "/admin/login",
        data={"username": "admin", "password": "secret123", "csrf_token": csrf, "next": "/admin"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert (resp.headers.get("Location") or "").endswith("/admin")
    dash = client.get("/admin?section=overview")
    assert dash.status_code == 200
    assert b"Admin Console" in dash.data


def test_admin_login_session_is_not_permanent(client, monkeypatch):
    monkeypatch.setattr(app_module, "ADMIN_USER", "admin")
    monkeypatch.setattr(app_module, "ADMIN_PASSWORD", "secret123")
    client.get("/admin/login")
    with client.session_transaction() as sess:
        csrf = sess.get("_csrf_token")
    client.post(
        "/admin/login",
        data={"username": "admin", "password": "secret123", "csrf_token": csrf, "next": "/admin"},
    )
    with client.session_transaction() as sess:
        assert sess.get("admin_authenticated") is True
        assert sess.permanent is False


def test_admin_login_rejects_bad_password(client, monkeypatch):
    monkeypatch.setattr(app_module, "ADMIN_USER", "admin")
    monkeypatch.setattr(app_module, "ADMIN_PASSWORD", "secret123")
    client.get("/admin/login")
    with client.session_transaction() as sess:
        csrf = sess.get("_csrf_token")
    resp = client.post(
        "/admin/login",
        data={"username": "admin", "password": "wrong", "csrf_token": csrf},
    )
    assert resp.status_code == 200
    assert b"Invalid username or password" in resp.data
    assert client.get("/admin").status_code == 302


def test_lifecycle_section_all_sports(client, monkeypatch):
    monkeypatch.setattr(app_module, "ADMIN_USER", "admin")
    monkeypatch.setattr(app_module, "ADMIN_PASSWORD", "secret123")
    auth = _basic_auth_header("admin", "secret123")

    def fake_board(**_kwargs):
        return {
            "rows": [{
                "row_kind": "board",
                "kind": "",
                "id": None,
                "sport_key": "baseball",
                "league_id": "mlb",
                "league_label": "MLB",
                "matchup": "Yankees vs Red Sox",
                "kickoff_iso": "2026-08-16T23:00:00+00:00",
                "kickoff_ist": "Sun 17 Aug 2026, 04:30 IST",
                "phase": "upcoming",
                "tip": "Over 8.5 (total)",
                "confidence": 0.71,
                "channel_serial": None,
                "channel_sent": False,
                "settle_status": "",
                "hours_overdue": None,
                "waiting_tg": False,
                "waiting_x": False,
                "result_blocked": False,
                "tg_result_posted": False,
                "x_result_posted": False,
                "actual_note": "",
                "url": "/mlb/fixtures",
                "result_key": "",
                "slate_date": "",
            }],
            "kpis": {
                "total": 1, "upcoming": 1, "live": 0, "final": 0,
                "overdue": 0, "waiting_tg": 0, "waiting_x": 0,
                "channel_sent": 0, "board_only": 1, "pending": 0,
            },
            "sports": ["baseball", "soccer", "cricket", "tennis", "american_football"],
            "leagues": [("mlb", "MLB")],
            "waiting_due_count": 0,
        }

    monkeypatch.setattr(app_module.combo_results, "build_lifecycle_board", fake_board)
    monkeypatch.setattr(
        app_module, "_gather_combo_picks_today",
        lambda *a, **k: ([], {2: [], 3: [], "mixed_2": [], "mixed_3": []}),
    )
    resp = client.get("/admin?section=lifecycle", headers=auth)
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Lifecycle — all sports" in body
    assert "Yankees vs Red Sox" in body
    assert "baseball" in body
    assert 'name="lc_sport"' in body


def test_lifecycle_block_skips_results_due(tmp_path, monkeypatch):
    import combo_results

    monkeypatch.setattr(combo_results, "DB_PATH", str(tmp_path / "lc.db"))
    combo_results.init_db()
    now = "2026-08-16T12:00:00+00:00"
    with combo_results.get_db() as conn:
        conn.execute(
            """
            INSERT INTO published_picks (
                slate_date, signature, sport_key, league_id, matchup, market, pick,
                status, snapped_at, source, telegram_message_ids, settled_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "2026-08-16", "sig-lc-1", "soccer", "mls", "A vs B", "1X2", "Home",
                combo_results.WON, now, combo_results.SOURCE_DEGEN_TEAM,
                "[101]", now,
            ),
        )
        pid = conn.execute("SELECT id FROM published_picks").fetchone()[0]
    due = combo_results.list_telegram_results_due(limit=10, channel="telegram")
    assert any(r.get("id") == pid for r in due)
    assert combo_results.set_result_blocked("single", pid, blocked=True)["ok"]
    due2 = combo_results.list_telegram_results_due(limit=10, channel="telegram")
    assert not any(r.get("id") == pid for r in due2)
