"""Regression tests — lock in previously shipped product rules."""
import os

import pytest

import daily_digest
import today_picks


pytestmark = pytest.mark.regression


def test_channel_auto_post_defaults_off(monkeypatch):
    monkeypatch.delenv("CHANNEL_AUTO_POST", raising=False)
    assert daily_digest.channel_auto_post_enabled() is False


def test_no_recent_results_block_on_fixture_boards(client):
    for path in ("/mlb/fixtures", "/nfl/fixtures", "/npb/fixtures"):
        body = client.get(path).data.decode()
        assert "Recent results" not in body
    for path in ("/nfl/fixtures", "/mlb/fixtures"):
        body = client.get(path).data.decode()
        assert ">Get Prediction<" not in body
        assert ">Get Predictions<" not in body


def test_league_hub_cards_say_see_fixtures(client):
    for path in (
        "/sports/soccer",
        "/sports/baseball",
        "/sports/american-football",
        "/todays-picks/soccer",
        "/todays-picks/american_football",
    ):
        body = client.get(path).data.decode()
        assert "See fixtures" in body


def test_combo_favorite_cover_cap_still_one():
    assert today_picks.COMBO_MAX_FAVORITE_COVER_LEGS == 1


def test_under_combo_min_still_stricter_than_public_floor():
    assert today_picks.UNDER_COMBO_MIN >= 0.76


def test_credits_and_bet_now_stay_gone(client):
    body = client.get("/").data.decode().lower()
    assert "credits-bar" not in body
    assert "bet now" not in body
    assert "place your bet" not in body


def test_nfl_home_still_redirects_to_todays_picks(client):
    resp = client.get("/nfl/")
    assert resp.status_code in (301, 302)
    assert resp.headers["Location"].endswith("/todays-picks/american_football/nfl")


def test_public_combo_results_stay_hidden(client):
    resp = client.get("/todays-picks/combos/results")
    assert resp.status_code == 404
