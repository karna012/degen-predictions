"""The /admin traffic dashboard and its underlying request logger."""
import base64
import uuid

import analytics
import app as app_module


def _fake_ip():
    """A fresh, never-before-seen fake "IP" per call -- the on-disk test DB
    (see conftest.py) persists across separate pytest invocations, so a
    hardcoded IP would collide with rows a prior run already inserted.
    Not a real IP shape; the `ip` column is plain TEXT so any unique
    string works fine for grouping/filtering."""
    return f"test-ip-{uuid.uuid4().hex}"


def _basic_auth_header(user, password):
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def test_admin_dashboard_refused_when_creds_not_configured(client, monkeypatch):
    monkeypatch.setattr(app_module, "ADMIN_USER", "")
    monkeypatch.setattr(app_module, "ADMIN_PASSWORD", "")
    resp = client.get("/admin")
    assert resp.status_code == 404


def test_admin_dashboard_requires_auth(client, monkeypatch):
    monkeypatch.setattr(app_module, "ADMIN_USER", "admin")
    monkeypatch.setattr(app_module, "ADMIN_PASSWORD", "secret123")
    resp = client.get("/admin")
    assert resp.status_code == 302
    assert "/admin/login" in (resp.headers.get("Location") or "")


def test_admin_dashboard_loads_with_correct_credentials(client, monkeypatch):
    monkeypatch.setattr(app_module, "ADMIN_USER", "admin")
    monkeypatch.setattr(app_module, "ADMIN_PASSWORD", "secret123")
    resp = client.get("/admin", headers=_basic_auth_header("admin", "secret123"))
    assert resp.status_code == 200
    body = resp.data
    assert b"Admin Dashboard" in body or b"Admin Console" in body
    assert b"admin-sidebar" in body
    assert b"Overview" in body


def test_admin_dashboard_section_param(client, monkeypatch):
    monkeypatch.setattr(app_module, "ADMIN_USER", "admin")
    monkeypatch.setattr(app_module, "ADMIN_PASSWORD", "secret123")
    resp = client.get(
        "/admin?section=feedback",
        headers=_basic_auth_header("admin", "secret123"),
    )
    assert resp.status_code == 200
    assert b'data-admin-section="feedback"' in resp.data
    assert b"admin.css" in resp.data


def test_visiting_a_page_gets_logged(client):
    before = analytics.summary(days=30)["total_visits"]
    client.get("/premier_league/")
    after = analytics.summary(days=30)["total_visits"]
    assert after == before + 1


def test_static_assets_are_not_logged(client):
    before = analytics.summary(days=30)["total_visits"]
    client.get("/static/style.css")
    after = analytics.summary(days=30)["total_visits"]
    assert after == before


def test_should_log_excludes_static_admin_and_favicon():
    assert analytics.should_log("/premier_league/") is True
    assert analytics.should_log("/static/style.css") is False
    assert analytics.should_log("/admin") is False
    assert analytics.should_log("/favicon.ico") is False
    assert analytics.should_log("/healthz") is False


def test_log_visit_swallows_db_errors(monkeypatch, tmp_path):
    """Readonly / broken analytics DB must not raise into request handlers."""
    bad = tmp_path / "readonly"
    bad.mkdir()
    monkeypatch.setattr(analytics, "DB_PATH", str(bad / "analytics.db"))
    # Force connect to a path that cannot be created as a file (directory collision).
    monkeypatch.setattr(analytics, "DB_PATH", str(bad))
    analytics.log_visit("1.2.3.4", "/", "GET", 200, "ua", "")


def test_page_still_200_when_analytics_write_fails(client, monkeypatch):
    def boom(*_a, **_k):
        raise OSError("readonly database")

    monkeypatch.setattr(analytics, "log_visit", boom)
    resp = client.get("/todays-picks")
    assert resp.status_code == 200


def test_top_pages_reflects_logged_visit(client):
    client.get("/premier_league/")
    pages = analytics.top_pages(days=30)
    assert any(p["path"] == "/premier_league/" for p in pages)


# ---- bot vs human detection --------------------------------------------

def test_is_bot_user_agent_detects_known_bots():
    assert analytics.is_bot_user_agent(
        "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)")
    assert analytics.is_bot_user_agent("curl/8.0.1")
    assert analytics.is_bot_user_agent("AhrefsBot/7.0; +http://ahrefs.com/robot/")
    assert analytics.is_bot_user_agent("")
    assert analytics.is_bot_user_agent(None)


def test_is_bot_user_agent_allows_real_browsers():
    chrome = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    assert analytics.is_bot_user_agent(chrome) is False


def _insert_visit(ip, ts, user_agent, path="/a"):
    with analytics.get_db() as conn:
        conn.execute(
            "INSERT INTO visits (ts, ip, path, method, status, user_agent, referrer) "
            "VALUES (?, ?, ?, 'GET', 200, ?, '')",
            (ts, ip, path, user_agent),
        )


# ---- recurring visitors -------------------------------------------------

def test_recurring_visitors_requires_multiple_distinct_days():
    ip = _fake_ip()
    _insert_visit(ip, "2020-01-01T10:00:00+00:00", "Mozilla/5.0 test-browser", "/a")
    assert not any(v["ip"] == ip for v in analytics.recurring_visitors(days=3650))

    _insert_visit(ip, "2020-01-02T10:00:00+00:00", "Mozilla/5.0 test-browser", "/b")
    recurring = analytics.recurring_visitors(days=3650)
    match = next(v for v in recurring if v["ip"] == ip)
    assert match["distinct_days"] == 2
    assert match["is_bot"] is False


def test_recurring_visitors_flags_bot_ip_and_respects_filter():
    ip = _fake_ip()
    _insert_visit(ip, "2020-02-01T10:00:00+00:00", "AhrefsBot/7.0", "/a")
    _insert_visit(ip, "2020-02-02T10:00:00+00:00", "AhrefsBot/7.0", "/b")

    assert any(v["ip"] == ip for v in analytics.recurring_visitors(days=3650, filter="all"))
    assert not any(v["ip"] == ip for v in analytics.recurring_visitors(days=3650, filter="human"))
    assert any(v["ip"] == ip for v in analytics.recurring_visitors(days=3650, filter="bot"))


# ---- filtering top_visitors / recent_visits -----------------------------

def test_top_visitors_and_recent_visits_filter_by_bot_or_human(client):
    ip = _fake_ip()
    client.get("/premier_league/", headers={"User-Agent": "curl/8.0.1", "X-Real-IP": ip})

    top_bots = analytics.top_visitors(days=30, filter="bot")
    assert any(v["ip"] == ip for v in top_bots)
    assert not any(v["ip"] == ip for v in analytics.top_visitors(days=30, filter="human"))

    recent_bots = analytics.recent_visits(limit=200, filter="bot")
    assert any(r["ip"] == ip and r["is_bot"] for r in recent_bots)
    assert not any(r["ip"] == ip for r in analytics.recent_visits(limit=200, filter="human"))


def test_admin_dashboard_accepts_filter_param(client, monkeypatch):
    monkeypatch.setattr(app_module, "ADMIN_USER", "admin")
    monkeypatch.setattr(app_module, "ADMIN_PASSWORD", "secret123")
    resp = client.get("/admin?filter=bot", headers=_basic_auth_header("admin", "secret123"))
    assert resp.status_code == 200
    assert b"Recurring Visitors" in resp.data


CHROME = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def test_is_get_prediction_path_matches_api_and_button_hop():
    assert analytics.is_get_prediction_path("/premier_league/api/predict/Arsenal/Chelsea")
    assert analytics.is_get_prediction_api_path("/mlb/api/predict")
    assert analytics.is_get_prediction_path("/mlb/api/predict?home=NYY&away=BOS")
    assert analytics.is_get_prediction_path("/nfl/api/predict?home=KC&away=BUF")
    assert analytics.is_get_prediction_path("/go/gp/ipl/fixture/csk-vs-mi-2026-04-01")
    assert not analytics.is_get_prediction_path("/premier_league/")
    assert not analytics.is_get_prediction_path("/go/telegram")


def test_get_prediction_event_meta_labels_fixtures():
    soccer = analytics.get_prediction_event_meta("/premier_league/api/predict/Arsenal/Chelsea")
    assert soccer["kind"] == "event"
    assert soccer["league_label"] == "Premier League"
    assert soccer["label"] == "Arsenal vs Chelsea"
    mlb = analytics.get_prediction_event_meta("/mlb/api/predict?home=NYY&away=BOS")
    assert mlb["league_label"] == "MLB"
    assert "NYY" in mlb["label"] and "BOS" in mlb["label"]
    cricket = analytics.get_prediction_event_meta("/go/gp/ipl/fixture/csk-vs-mi-2026-04-01")
    assert cricket["league_label"] == "IPL"
    assert "csk" in cricket["label"].lower()


def test_get_prediction_stats_counts_humans_not_bots():
    human_ip = _fake_ip()
    bot_ip = _fake_ip()
    chrome = CHROME
    analytics.log_visit(human_ip, "/premier_league/api/predict/Arsenal/Chelsea", "GET", 200, chrome, "")
    analytics.log_visit(human_ip, "/premier_league/api/predict/Arsenal/Chelsea", "GET", 200, chrome, "")
    analytics.log_visit(bot_ip, "/premier_league/api/predict/Arsenal/Chelsea", "GET", 200, "curl/8.0.1", "")
    analytics.log_visit(human_ip, "/go/gp/hundred/fixture/oval-vs-spirit-2026-08-08", "GET", 200, chrome, "")

    stats = analytics.get_prediction_stats(days=30)
    assert stats["clicks"] >= 3
    assert stats["unique_events"] >= 2
    labels = {e["label"] for e in stats["events"]}
    assert any("Arsenal" in x and "Chelsea" in x for x in labels)
    assert stats["unique_humans"] >= 1
    # Bot-only IP must not be the only reason a row exists; human clicks still counted.
    arsenal = next(e for e in stats["events"] if "Arsenal" in e["label"])
    assert arsenal["clicks"] >= 2
    assert arsenal["unique_humans"] >= 1


def test_go_gp_redirects_fixture_and_rejects_open_redirect(client):
    resp = client.get("/go/gp/ipl/fixture/csk-vs-mi-2026-04-01", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/ipl/fixture/csk-vs-mi-2026-04-01")
    bad = client.get("/go/gp/https://evil.example/phish", follow_redirects=False)
    assert bad.status_code == 404


def test_admin_dashboard_shows_human_get_prediction_counts(client, monkeypatch):
    monkeypatch.setattr(app_module, "ADMIN_USER", "admin")
    monkeypatch.setattr(app_module, "ADMIN_PASSWORD", "secret123")
    ip = _fake_ip()
    analytics.log_visit(
        ip, "/premier_league/api/predict/Arsenal/Chelsea", "GET", 200, CHROME, "",
    )
    headers = _basic_auth_header("admin", "secret123")
    overview = client.get("/admin", headers=headers)
    assert overview.status_code == 200
    body = overview.data
    assert b"Unlock prediction" in body
    assert b"Arsenal vs Chelsea" in body
    gp = client.get("/admin?section=gp", headers=headers)
    assert gp.status_code == 200
    assert b"Unlock prediction by event" in gp.data
    assert b"Arsenal vs Chelsea" in gp.data


def test_api_unlock_combo_and_pick_return_204(client):
    combo = client.get("/api/unlock/combo?event=2-leg-mixed-test", headers={"User-Agent": CHROME})
    pick = client.post("/api/unlock/pick?event=EPL+%C2%B7+Arsenal+vs+Chelsea", headers={"User-Agent": CHROME})
    assert combo.status_code == 204
    assert pick.status_code == 204
    assert client.get("/api/unlock/nope").status_code == 404


def test_unlock_clicks_count_as_human_combo_and_pick_events(client):
    chrome = {"User-Agent": CHROME, "X-Real-IP": _fake_ip()}
    bot = {"User-Agent": "curl/8.0.1", "X-Real-IP": _fake_ip()}
    client.get("/api/unlock/combo?event=2-leg+Cross-sport+Arsenal+vs+Chelsea", headers=chrome)
    client.get("/api/unlock/pick?event=Premier+League+%C2%B7+Arsenal+vs+Chelsea+%C2%B7+Safer+Result", headers=chrome)
    client.get("/api/unlock/combo?event=2-leg+Cross-sport+Arsenal+vs+Chelsea", headers=bot)
    stats = analytics.get_prediction_stats(days=30)
    assert stats["combo_clicks"] >= 1
    assert stats["pick_clicks"] >= 1
    labels = {e["league_label"]: e for e in stats["events"]}
    assert "Best Combos" in labels
    assert "Today's Picks" in labels
