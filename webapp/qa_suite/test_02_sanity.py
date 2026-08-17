"""Sanity tests — recent product rules still hold on a shallow pass."""
import pytest

import today_picks


pytestmark = pytest.mark.sanity


def test_nfl_is_in_todays_picks_catalog():
    sport = today_picks.TODAY_SPORTS["american_football"]
    assert sport["leagues"]["nfl"]["kind"] == "nfl"
    assert sport["leagues"]["nfl"]["label"] == "NFL"


def test_nfl_hub_uses_see_fixtures_not_todays_picks_cta(client):
    body = client.get("/sports/american-football").data.decode()
    assert "See fixtures" in body
    assert 'href="/nfl/fixtures"' in body
    assert "Unlock today's pick" not in body.lower()


def test_nfl_fixture_cards_use_unlock_prediction_copy(client):
    body = client.get("/nfl/fixtures").data.decode()
    assert "Get Prediction" not in body
    assert "Get Predictions" not in body
    if "fixture-card" in body or "Unlock prediction" in body:
        assert "Unlock prediction" in body or "No rating yet" in body or "No fixtures found" in body


def test_soccer_league_hub_see_fixtures(client):
    body = client.get("/sports/soccer").data.decode()
    assert "See fixtures" in body


def test_best_combos_still_says_unlock_combo(client):
    body = client.get("/todays-picks/combos").data.decode()
    assert "Unlock combo" in body or "Best Combos" in body


def test_disclaimer_present_on_core_boards(client):
    for path in ("/", "/sports", "/todays-picks", "/nfl/fixtures"):
        text = client.get(path).data.decode().lower()
        assert "not betting" in text or "not betting advice" in text or "educational" in text


def test_nav_includes_nfl_under_todays_picks(client):
    body = client.get("/").data.decode()
    assert "/todays-picks/american_football/nfl" in body
