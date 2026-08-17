"""Admin TOTP 2FA enrollment + login gate."""
from pathlib import Path

import pyotp

import admin_2fa
import app as app_module


def _csrf(client):
    client.get("/admin/login")
    with client.session_transaction() as sess:
        return sess.get("_csrf_token")


def test_2fa_setup_confirm_and_login(client, monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "ADMIN_USER", "admin")
    monkeypatch.setattr(app_module, "ADMIN_PASSWORD", "secret123")
    monkeypatch.setattr(admin_2fa, "STATE_PATH", Path(tmp_path) / "2fa.json")

    assert admin_2fa.is_enabled() is False
    setup = admin_2fa.start_setup(account_name="admin")
    code = pyotp.TOTP(setup["secret"]).now()
    result = admin_2fa.confirm_setup(code)
    assert result["ok"] is True
    assert len(result["backup_codes"]) == admin_2fa.BACKUP_COUNT
    assert admin_2fa.is_enabled() is True

    csrf = _csrf(client)
    resp = client.post(
        "/admin/login",
        data={"username": "admin", "password": "secret123", "csrf_token": csrf, "next": "/admin"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "/admin/2fa" in (resp.headers.get("Location") or "")
    with client.session_transaction() as sess:
        assert sess.get("admin_2fa_pending") is True
        assert not sess.get("admin_authenticated")

    assert client.get("/admin").status_code == 302

    with client.session_transaction() as sess:
        csrf2 = sess.get("_csrf_token")
    otp = pyotp.TOTP(setup["secret"]).now()
    resp2 = client.post(
        "/admin/2fa",
        data={"code": otp, "csrf_token": csrf2, "next": "/admin"},
        follow_redirects=False,
    )
    assert resp2.status_code == 302
    assert (resp2.headers.get("Location") or "").endswith("/admin")
    dash = client.get("/admin?section=security")
    assert dash.status_code == 200
    assert b"2FA is active" in dash.data or b"protected" in dash.data


def test_2fa_backup_code_login(client, monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "ADMIN_USER", "admin")
    monkeypatch.setattr(app_module, "ADMIN_PASSWORD", "secret123")
    monkeypatch.setattr(admin_2fa, "STATE_PATH", Path(tmp_path) / "2fa.json")
    setup = admin_2fa.start_setup()
    admin_2fa.confirm_setup(pyotp.TOTP(setup["secret"]).now())
    # Fresh backups via regenerate with TOTP
    regen = admin_2fa.regenerate_backups(pyotp.TOTP(setup["secret"]).now())
    assert regen["ok"]
    backup = regen["backup_codes"][0]

    csrf = _csrf(client)
    client.post(
        "/admin/login",
        data={"username": "admin", "password": "secret123", "csrf_token": csrf, "next": "/admin"},
    )
    with client.session_transaction() as sess:
        csrf2 = sess.get("_csrf_token")
    resp = client.post(
        "/admin/2fa",
        data={"code": backup, "csrf_token": csrf2, "next": "/admin"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert client.get("/admin").status_code == 200
    # Same backup cannot be reused
    assert admin_2fa.verify_backup(backup) is False


def test_login_without_2fa_unchanged(client, monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "ADMIN_USER", "admin")
    monkeypatch.setattr(app_module, "ADMIN_PASSWORD", "secret123")
    monkeypatch.setattr(admin_2fa, "STATE_PATH", Path(tmp_path) / "2fa-off.json")
    csrf = _csrf(client)
    resp = client.post(
        "/admin/login",
        data={"username": "admin", "password": "secret123", "csrf_token": csrf, "next": "/admin"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert (resp.headers.get("Location") or "").endswith("/admin")
    assert client.get("/admin").status_code == 200
