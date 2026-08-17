"""IndexNow helpers used by daily digest + post-event."""
import indexnow


def test_default_url_list_includes_core_pages():
    urls = indexnow.default_url_list()
    assert "https://degenpredictions.com/" in urls
    assert "https://degenpredictions.com/todays-picks/combos" in urls
    assert "https://degenpredictions.com/premier_league/" in urls


def test_urls_from_digest_adds_league_and_pick_paths():
    digest = {
        "league_urls": [{"url": "/todays-picks/soccer/premier_league"}],
        "picks": [{"url": "https://degenpredictions.com/mlb/"}],
    }
    urls = indexnow.urls_from_digest(digest)
    assert "https://degenpredictions.com/todays-picks/soccer/premier_league" in urls
    assert "https://degenpredictions.com/mlb/" in urls


def test_notify_after_settle_skips_when_nothing_graded():
    out = indexnow.notify_after_settle({
        "legs_settled": 0, "slips_settled": 0, "picks_settled": 0,
    })
    assert out.get("skipped") is True


def test_submit_respects_disabled_flag(monkeypatch):
    monkeypatch.setenv("INDEXNOW_ENABLED", "0")
    out = indexnow.submit_urls(["https://degenpredictions.com/"])
    assert out.get("skipped") is True
