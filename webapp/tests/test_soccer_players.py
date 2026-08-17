import base64

import app as app_module


def _basic_auth_header(user, password):
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def test_mls_players_requires_admin(client, monkeypatch):
    monkeypatch.setattr(app_module, "ADMIN_USER", "admin")
    monkeypatch.setattr(app_module, "ADMIN_PASSWORD", "secret123")
    resp = client.get("/mls/players")
    assert resp.status_code == 302
    assert "/admin/login" in (resp.headers.get("Location") or "")


def test_mls_players_page_loads_for_admin(client, monkeypatch):
    monkeypatch.setattr(app_module, "ADMIN_USER", "admin")
    monkeypatch.setattr(app_module, "ADMIN_PASSWORD", "secret123")
    resp = client.get("/mls/players", headers=_basic_auth_header("admin", "secret123"))
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Player Analysis" in body
    assert "MLS" in body


def test_mls_players_filters_work_for_admin(client, monkeypatch):
    monkeypatch.setattr(app_module, "ADMIN_USER", "admin")
    monkeypatch.setattr(app_module, "ADMIN_PASSWORD", "secret123")
    resp = client.get(
        "/mls/players?season=2025&sort=goals",
        headers=_basic_auth_header("admin", "secret123"),
    )
    assert resp.status_code == 200
