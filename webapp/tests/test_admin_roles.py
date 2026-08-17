"""Roles + password hashing."""
from pathlib import Path

import admin_roles
import admin_security_cfg


def test_role_perms():
    assert admin_roles.has_perm("viewer", "read")
    assert not admin_roles.has_perm("viewer", "secrets")
    assert admin_roles.has_perm("poster", "post")
    assert not admin_roles.has_perm("poster", "security")
    assert admin_roles.has_perm("full", "secrets")


def test_operator_upsert_and_login_hash(tmp_path, monkeypatch):
    monkeypatch.setattr(admin_security_cfg, "PATH", Path(tmp_path) / "security.json")
    cfg = admin_security_cfg.load()
    result = admin_roles.upsert_operator(cfg, username="poster1", password="hunter2xx", role="poster")
    assert result["ok"] is True
    cfg2 = admin_security_cfg.load()
    op = admin_roles.find_operator(cfg2, "poster1")
    assert op is not None
    assert op["role"] == "poster"
    assert admin_roles.verify_password(op["password_hash"], "hunter2xx")
    assert not admin_roles.verify_password(op["password_hash"], "wrong")


def test_sessions_register(tmp_path, monkeypatch):
    monkeypatch.setattr(admin_security_cfg, "PATH", Path(tmp_path) / "security.json")
    admin_security_cfg.register_session(
        session_id="abc", actor="admin", role="full", ip="1.1.1.1", ua="test"
    )
    rows = admin_security_cfg.list_sessions()
    assert rows and rows[0]["id"] == "abc"
    assert admin_security_cfg.revoke_session("abc")
    assert admin_security_cfg.list_sessions() == []
