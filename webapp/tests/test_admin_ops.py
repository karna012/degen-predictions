"""Admin audit, security policy, ops helpers."""
from pathlib import Path

import admin_audit
import admin_ops
import admin_security_cfg
import source_settings


def test_audit_log_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(admin_audit, "LOG_PATH", Path(tmp_path) / "audit.jsonl")
    admin_audit.log_event("login_password", ok=True, ip="1.2.3.4", detail="ok")
    admin_audit.log_event("login_password", ok=False, ip="1.2.3.4", detail="bad")
    rows = admin_audit.recent(10)
    assert len(rows) == 2
    assert rows[0]["ok"] is False
    assert rows[1]["action"] == "login_password"


def test_ip_allowlist(tmp_path, monkeypatch):
    monkeypatch.setattr(admin_security_cfg, "PATH", Path(tmp_path) / "security.json")
    assert admin_security_cfg.ip_allowed("8.8.8.8") is True
    admin_security_cfg.save({"ip_allowlist": ["203.0.113.10", "10.0.0.0/8"]})
    assert admin_security_cfg.ip_allowed("203.0.113.10") is True
    assert admin_security_cfg.ip_allowed("10.1.2.3") is True
    assert admin_security_cfg.ip_allowed("8.8.8.8") is False
    assert admin_security_cfg.ip_allowed("127.0.0.1") is True


def test_session_epoch_bump(tmp_path, monkeypatch):
    monkeypatch.setattr(admin_security_cfg, "PATH", Path(tmp_path) / "security.json")
    a = admin_security_cfg.load()["session_epoch"]
    b = admin_security_cfg.bump_session_epoch()
    assert b == a + 1


def test_provider_order(tmp_path, monkeypatch):
    monkeypatch.setattr(source_settings, "SETTINGS_PATH", Path(tmp_path) / "settings.json")
    source_settings.clear_cache()
    source_settings.save_settings({
        "feeds": {
            "nfl": {
                "enabled": True,
                "fixtures_primary": "espn_site",
                "fixtures_fallback": "espn_cdn",
                "results_primary": "espn_cdn",
                "results_fallback": "espn_site",
            }
        }
    })
    order = source_settings.provider_order("nfl", kind="fixtures")
    assert order[0] == "espn_site"
    assert "espn_cdn" in order


def test_ops_jobs_offline(tmp_path, monkeypatch):
    monkeypatch.setattr(admin_ops, "ALERT_STATE", Path(tmp_path) / "alert.json")
    # Stub heavy probes
    monkeypatch.setattr(
        "source_health.status_board",
        lambda force=False: {
            "summary": {"working": 2, "down": 0},
            "alert_count": 0,
            "alerts": [],
        },
    )
    result = admin_ops.run_job("probe_sources")
    assert result["ok"] is True
    bad = admin_ops.run_job("nope")
    assert bad["ok"] is False
