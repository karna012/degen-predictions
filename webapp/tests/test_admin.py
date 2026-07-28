"""The /results predicted-vs-actual page is internal (accuracy numbers, not
meant for public visitors) -- gated behind HTTP Basic Auth via
ADMIN_USER/ADMIN_PASSWORD env vars, checked with admin_required() in app.py."""
import base64

import app as app_module


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
    assert resp.status_code == 401


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


def test_results_link_not_in_public_nav(client):
    resp = client.get("/")
    body = resp.data.decode()
    assert 'href="/results"' not in body
