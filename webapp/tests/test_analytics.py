"""The /admin traffic dashboard and its underlying request logger."""
import base64

import analytics
import app as app_module


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
    assert resp.status_code == 401


def test_admin_dashboard_loads_with_correct_credentials(client, monkeypatch):
    monkeypatch.setattr(app_module, "ADMIN_USER", "admin")
    monkeypatch.setattr(app_module, "ADMIN_PASSWORD", "secret123")
    resp = client.get("/admin", headers=_basic_auth_header("admin", "secret123"))
    assert resp.status_code == 200
    assert b"Admin Dashboard" in resp.data


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


def test_top_pages_reflects_logged_visit(client):
    client.get("/premier_league/")
    pages = analytics.top_pages(days=30)
    assert any(p["path"] == "/premier_league/" for p in pages)
