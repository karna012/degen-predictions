"""Soccer / cricket / NPB honor Admin → Sources prefs."""
from pathlib import Path

import cricket_predict
import npb_fixtures
import soccer_fixtures
import source_settings


def test_soccer_catalog_fallback_is_valid():
    meta = source_settings.FEED_CATALOG["soccer"]
    fix_ids = {o[0] for o in meta["fixture_options"]}
    assert meta["defaults"]["fixtures_fallback"] in fix_ids


def test_soccer_provider_order_tries_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(source_settings, "SETTINGS_PATH", Path(tmp_path) / "settings.json")
    source_settings.clear_cache()
    source_settings.save_settings({
        "feeds": {
            "soccer": {
                "enabled": True,
                "fixtures_primary": "football_data_org",
                "fixtures_fallback": "fixturedownload",
                "results_primary": "fixturedownload",
                "results_fallback": "football_data_csv",
            }
        }
    })
    soccer_fixtures.clear_cache()
    calls = []

    def _fd(*_a, **_k):
        calls.append("fixturedownload")
        return [{
            "league_id": "premier_league",
            "sport_key": "soccer",
            "home": "Arsenal",
            "away": "Chelsea",
            "home_team": "Arsenal",
            "away_team": "Chelsea",
            "date": "2099-01-01 15:00:00Z",
            "DateUtc": "2099-01-01 15:00:00Z",
            "kickoff_iso": "2099-01-01 15:00:00Z",
            "home_score": None,
            "away_score": None,
            "completed": False,
            "round": 1,
            "location": "",
        }]

    def _org(*_a, **_k):
        calls.append("football_data_org")
        return []

    monkeypatch.setattr(soccer_fixtures, "_fetch_football_data_org", _org)
    monkeypatch.setattr(soccer_fixtures, "_fetch_fixturedownload", _fd)
    monkeypatch.setattr(soccer_fixtures, "_fetch_api_sports", lambda *_a, **_k: [])
    out = soccer_fixtures.get_feed("premier_league")
    assert calls[0] == "football_data_org"
    assert "fixturedownload" in calls
    assert out and out[0]["home"] == "Arsenal"


def test_soccer_disabled_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(source_settings, "SETTINGS_PATH", Path(tmp_path) / "settings.json")
    source_settings.clear_cache()
    source_settings.set_feed_enabled("soccer", False)
    soccer_fixtures.clear_cache()
    assert soccer_fixtures.get_feed("premier_league") == []


def test_cricket_cricsheet_primary_skips_live(tmp_path, monkeypatch):
    monkeypatch.setattr(source_settings, "SETTINGS_PATH", Path(tmp_path) / "settings.json")
    source_settings.clear_cache()
    source_settings.save_settings({
        "feeds": {
            "cricket": {
                "enabled": True,
                "fixtures_primary": "cricsheet",
                "fixtures_fallback": "fixturedownload",
                "results_primary": "cricsheet",
                "results_fallback": "fixturedownload",
            }
        }
    })
    cricket_predict._upcoming_cache.clear()
    assert cricket_predict.upcoming_fixtures("ipl_male") == []


def test_npb_disabled_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(source_settings, "SETTINGS_PATH", Path(tmp_path) / "settings.json")
    source_settings.clear_cache()
    source_settings.set_feed_enabled("npb", False)
    assert npb_fixtures.get_fixtures(resolve_pitchers=False) == []
