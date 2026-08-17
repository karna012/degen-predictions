"""Security tests — defensive checks on this app only (no exploit payloads)."""
import base64

import pytest

import app as app_module
from qa_suite.catalog import ADMIN_PATHS


pytestmark = pytest.mark.security


def _auth(user, password):
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.mark.parametrize("path", ADMIN_PATHS)
def test_admin_surfaces_are_not_public(client, path, monkeypatch):
    monkeypatch.setattr(app_module, "ADMIN_USER", "admin")
    monkeypatch.setattr(app_module, "ADMIN_PASSWORD", "secret123")
    resp = client.get(path)
    assert resp.status_code in (302, 401, 404), f"{path} leaked with {resp.status_code}"
    if resp.status_code == 302:
        assert "/admin/login" in (resp.headers.get("Location") or "")


def test_admin_wrong_password_is_401(client, monkeypatch):
    monkeypatch.setattr(app_module, "ADMIN_USER", "admin")
    monkeypatch.setattr(app_module, "ADMIN_PASSWORD", "secret123")
    resp = client.get("/admin", headers=_auth("admin", "nope"))
    assert resp.status_code == 401


def test_admin_post_without_csrf_is_forbidden(client, monkeypatch):
    monkeypatch.setattr(app_module, "ADMIN_USER", "admin")
    monkeypatch.setattr(app_module, "ADMIN_PASSWORD", "secret123")
    resp = client.post(
        "/admin/telegram-send",
        headers=_auth("admin", "secret123"),
        data={"text": "hi"},
    )
    assert resp.status_code in (403, 400, 404, 405)


def test_telegram_send_requires_admin(client, monkeypatch):
    monkeypatch.setattr(app_module, "ADMIN_USER", "admin")
    monkeypatch.setattr(app_module, "ADMIN_PASSWORD", "secret123")
    assert client.post("/admin/telegram-send").status_code == 401
    assert client.post("/todays-picks/digest/telegram-send").status_code == 401


def test_go_gp_blocks_path_escape_and_offsite(client):
    for path in (
        "/go/gp/../admin",
        "/go/gp/%2e%2e/admin",
        "/go/gp/premier_league/api/predict/a/b",
        "/go/gp/nfl/fixtures",
    ):
        resp = client.get(path)
        assert resp.status_code in (404, 400), path
        loc = resp.headers.get("Location") or ""
        assert not loc.startswith("http://evil") and "evil.example" not in loc


def test_feedback_honeypot_does_not_store_when_filled(client, monkeypatch):
    called = {"n": 0}

    def boom(**_k):
        called["n"] += 1

    monkeypatch.setattr("feedback.submit_feedback", boom)
    resp = client.post("/feedback", data={
        "name": "Bot", "email": "bot@example.com", "message": "spam",
        "website": "http://spam.test",
    }, follow_redirects=False)
    assert resp.status_code in (302, 200)
    assert called["n"] == 0


def test_predict_error_has_no_stack_or_paths(client):
    resp = client.get("/nfl/api/predict?home=ZZZ&away=YYY")
    text = resp.get_data(as_text=True)
    assert "Traceback" not in text
    assert "File \"" not in text
    assert "secret" not in text.lower() or "error" in text.lower()


def test_html_pages_do_not_embed_env_secrets(client):
    body = client.get("/").data.decode()
    for needle in ("ADMIN_PASSWORD", "TELEGRAM_BOT_TOKEN", "AWS_SECRET", "BEGIN RSA"):
        assert needle not in body


def test_robots_disallows_admin_and_unlock_hops(client):
    body = client.get("/robots.txt").data.decode()
    assert "Disallow: /admin" in body
    assert "Disallow: /go/gp" in body
    assert "Disallow: /todays-picks/combos/results" in body


def test_query_script_is_escaped_on_html_pages(client):
    resp = client.get("/faq", query_string={"q": "<script>alert(1)</script>"})
    assert resp.status_code == 200
    assert b"<script>alert(1)</script>" not in resp.data
