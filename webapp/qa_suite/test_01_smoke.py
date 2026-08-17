"""Smoke tests — critical user-facing surfaces must load without 5xx."""
import pytest

from qa_suite.catalog import PUBLIC_SMOKE_PATHS, RETIRED_PATHS


pytestmark = pytest.mark.smoke


@pytest.mark.parametrize("path", PUBLIC_SMOKE_PATHS)
def test_public_surface_is_reachable(client, path):
    resp = client.get(path)
    assert resp.status_code < 500, f"{path} returned {resp.status_code}"
    assert resp.status_code in (200, 301, 302, 404)


def test_homepage_is_200(client):
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Degen Predictions" in body
    assert "Sports" in body


def test_sports_hub_lists_all_five_sports(client):
    body = client.get("/sports").data.decode()
    for needle in ("SOCCER", "BASEBALL", "CRICKET", "TENNIS", "AMERICAN FOOTBALL"):
        assert needle in body


def test_nfl_fixtures_or_empty_state(client):
    resp = client.get("/nfl/fixtures")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "NFL" in body
    assert (
        "Unlock prediction" in body
        or "No fixtures found" in body
        or "No rating yet" in body
    )


def test_todays_picks_and_combos_load(client):
    assert client.get("/todays-picks").status_code == 200
    combos = client.get("/todays-picks/combos")
    assert combos.status_code == 200
    assert b"Unlock combo" in combos.data or b"Best Combos" in combos.data


def test_sitemap_and_robots_are_plain_ok(client):
    sm = client.get("/sitemap.xml")
    assert sm.status_code == 200
    # /sitemap.xml is a sitemap index pointing at sharded leaf sitemaps.
    assert b"<urlset" in sm.data or b"<url>" in sm.data or b"<sitemapindex" in sm.data
    robots = client.get("/robots.txt")
    assert robots.status_code == 200
    assert b"Sitemap:" in robots.data


@pytest.mark.parametrize("path", RETIRED_PATHS)
def test_retired_surfaces_are_not_live_apps(client, path):
    resp = client.get(path)
    assert resp.status_code in (301, 302, 404)
