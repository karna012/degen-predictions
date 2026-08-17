"""Live source health probes + one-click switch."""
from pathlib import Path

import source_health
import source_settings


def test_status_board_lists_alternates(tmp_path, monkeypatch):
    monkeypatch.setattr(source_settings, "SETTINGS_PATH", Path(tmp_path) / "settings.json")
    monkeypatch.setattr(source_health, "CACHE_PATH", Path(tmp_path) / "health.json")
    source_settings.clear_cache()

    def _ok():
        return source_health._result("working", detail="stub", http=200, ms=12, items=3)

    def _down():
        return source_health._result("down", detail="stub 403", http=403, ms=9)

    monkeypatch.setattr(
        source_health,
        "PROVIDER_PROBES",
        {k: (_down if "espn_site" in k else _ok) for k in source_health.PROVIDER_PROBES},
    )
    board = source_health.status_board(force=True)
    assert board["sports"]
    assert board["summary"]["working"] >= 1
    mlb = next(s for s in board["sports"] if s["id"] == "mlb")
    ids = {o["id"] for o in mlb["options"]}
    assert "mlb_statsapi" in ids
    assert "espn_cdn" in ids
    assert "espn_site" in ids


def test_switch_provider_updates_primary(tmp_path, monkeypatch):
    monkeypatch.setattr(source_settings, "SETTINGS_PATH", Path(tmp_path) / "settings.json")
    source_settings.clear_cache()
    result = source_health.switch_provider("mlb", "fixtures_primary", "espn_cdn")
    assert result["ok"] is True
    pref = source_settings.feed_preference("mlb")
    assert pref["fixtures_primary"] == "espn_cdn"
    assert pref["fixtures_fallback"] != "espn_cdn"


def test_heal_switches_broken_primary(tmp_path, monkeypatch):
    monkeypatch.setattr(source_settings, "SETTINGS_PATH", Path(tmp_path) / "settings.json")
    monkeypatch.setattr(source_health, "CACHE_PATH", Path(tmp_path) / "health.json")
    source_settings.clear_cache()
    source_settings.save_settings({
        "feeds": {
            "mlb": {
                "enabled": True,
                "fixtures_primary": "espn_site",
                "fixtures_fallback": "mlb_statsapi",
                "results_primary": "mlb_statsapi",
                "results_fallback": "espn_cdn",
            }
        }
    })

    def _stub(pid_status):
        def _fn():
            return source_health._result(
                pid_status,
                detail="stub",
                http=200 if pid_status == "working" else 403,
            )
        return _fn

    probes = {
        pid: _stub("down" if "espn_site" in pid else "working")
        for pid in source_health.PROVIDER_PROBES
    }
    monkeypatch.setattr(source_health, "PROVIDER_PROBES", probes)

    board = source_health.status_board(force=True)
    assert board["alert_count"] >= 1
    result = source_health.heal_broken_sources(force_probe=False)
    assert result["count"] >= 1
    pref = source_settings.feed_preference("mlb")
    assert pref["fixtures_primary"] != "espn_site"


def test_admin_view_includes_status_badges(tmp_path, monkeypatch):
    monkeypatch.setattr(source_settings, "SETTINGS_PATH", Path(tmp_path) / "settings.json")
    monkeypatch.setattr(source_health, "CACHE_PATH", Path(tmp_path) / "health.json")
    source_settings.clear_cache()

    monkeypatch.setattr(
        source_health,
        "status_board",
        lambda force=False: {
            "sports": [{
                "id": "mlb",
                "label": "MLB",
                "health": "working",
                "fixtures_primary": "mlb_statsapi",
                "fixtures_fallback": "espn_cdn",
                "results_primary": "mlb_statsapi",
                "results_fallback": "espn_cdn",
                "options": [{
                    "id": "mlb_statsapi",
                    "label": "MLB Stats API",
                    "status": "working",
                    "detail": "ok",
                    "ms": 10,
                    "http": 200,
                    "roles": ["fixtures", "results"],
                }],
            }],
            "summary": {"working": 1, "down": 0, "degraded": 0, "needs_key": 0, "history": 0},
            "alerts": [],
            "alert_count": 0,
            "checked_at": "2026-08-16T00:00:00Z",
            "cache_ttl": 90,
        },
    )
    view = source_settings.admin_view_model()
    assert view["status_board"]["summary"]["working"] == 1
    mlb = next(f for f in view["feeds"] if f["id"] == "mlb")
    assert mlb["fixture_options"][0]["status"] == "working"
    assert mlb["fixture_options"][0]["id"] == "mlb_statsapi"
