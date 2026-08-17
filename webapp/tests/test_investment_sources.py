"""Stocks/crypto admin feeds + board gating helpers."""
from pathlib import Path

import investment_sources
import source_health
import source_settings


def test_feed_catalog_includes_stocks_crypto():
    assert "stocks" in source_settings.FEED_CATALOG
    assert "crypto" in source_settings.FEED_CATALOG
    assert source_settings.FEED_CATALOG["stocks"]["kind"] == "markets"
    assert source_settings.FEED_CATALOG["crypto"]["defaults"]["fixtures_primary"] == "ranked_csv"
    assert "fmp_api_key" in source_settings.API_FIELDS
    assert "coingecko_api_key" in source_settings.API_FIELDS


def test_set_feed_enabled_toggles_markets(tmp_path, monkeypatch):
    monkeypatch.setattr(source_settings, "SETTINGS_PATH", Path(tmp_path) / "settings.json")
    source_settings.clear_cache()
    assert investment_sources.markets_enabled("crypto") is True
    out = source_settings.set_feed_enabled("crypto", False)
    assert out["ok"] is True
    assert investment_sources.markets_enabled("crypto") is False
    source_settings.set_feed_enabled("crypto", True)
    assert investment_sources.markets_enabled("crypto") is True


def test_ranked_csv_probe(tmp_path, monkeypatch):
    monkeypatch.setattr(source_health, "REPO", tmp_path)
    csv_dir = tmp_path / "data" / "investment_scoring"
    csv_dir.mkdir(parents=True)
    (csv_dir / "ranked_assets.csv").write_text(
        "ticker,asset_class,horizon_months\nAAPL,us_stocks,6\nBTC,crypto,6\n",
        encoding="utf-8",
    )
    result = source_health.probe_ranked_csv()
    assert result["status"] in ("working", "degraded")
    assert "stocks=" in (result.get("detail") or "")


def test_status_board_lists_markets_first(tmp_path, monkeypatch):
    monkeypatch.setattr(source_settings, "SETTINGS_PATH", Path(tmp_path) / "settings.json")
    monkeypatch.setattr(source_health, "CACHE_PATH", Path(tmp_path) / "health.json")
    source_settings.clear_cache()

    def _ok():
        return source_health._result("working", detail="stub", http=200, ms=5, items=1)

    monkeypatch.setattr(
        source_health,
        "PROVIDER_PROBES",
        {k: _ok for k in source_health.PROVIDER_PROBES},
    )
    board = source_health.status_board(force=True)
    ids = [s["id"] for s in board["sports"]]
    assert "stocks" in ids and "crypto" in ids
    assert board["sports"][0]["kind"] == "markets"


def test_crypto_disabled_returns_503(client, tmp_path, monkeypatch):
    monkeypatch.setattr(source_settings, "SETTINGS_PATH", Path(tmp_path) / "settings.json")
    source_settings.clear_cache()
    source_settings.set_feed_enabled("crypto", False)
    resp = client.get("/crypto")
    assert resp.status_code == 503
    assert b"disabled" in resp.data.lower()


def test_allow_live_coingecko_respects_prefs(tmp_path, monkeypatch):
    monkeypatch.setattr(source_settings, "SETTINGS_PATH", Path(tmp_path) / "settings.json")
    source_settings.clear_cache()
    source_settings.save_settings({
        "feeds": {
            "crypto": {
                "enabled": True,
                "fixtures_primary": "ranked_csv",
                "fixtures_fallback": "binance",
                "results_primary": "binance",
                "results_fallback": "binance",
            }
        }
    })
    assert investment_sources.allow_live_coingecko() is False
    source_settings.save_settings({
        "feeds": {
            "crypto": {
                "enabled": True,
                "fixtures_primary": "ranked_csv",
                "fixtures_fallback": "coingecko",
                "results_primary": "coingecko",
                "results_fallback": "binance",
            }
        }
    })
    assert investment_sources.allow_live_coingecko() is True


def test_replace_ranked_csv(tmp_path, monkeypatch):
    monkeypatch.setattr(investment_sources, "CSV_PATH", tmp_path / "ranked_assets.csv")
    monkeypatch.setattr(investment_sources, "CSV_BACKUP_DIR", tmp_path / "backups")
    body = (
        b"ticker,asset_class,horizon_months\n"
        b"AAPL,us_stocks,6\nMSFT,us_stocks,6\nGOOG,us_stocks,6\n"
        b"BTC,crypto,6\nETH,crypto,6\n"
    )
    out = investment_sources.replace_ranked_csv(body)
    assert out["ok"] is True
    assert investment_sources.CSV_PATH.is_file()
    snap = investment_sources.csv_snapshot()
    assert snap["rows"] == 5
