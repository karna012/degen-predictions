"""Integration tests — modules wired together (routes + picks + analytics + settle)."""
import pytest

import analytics
import combo_results
import today_picks


pytestmark = pytest.mark.integration


def test_todays_picks_builder_accepts_nfl_kind():
    sport, league = today_picks.get_league("american_football", "nfl")
    assert sport is not None
    assert league["kind"] == "nfl"
    today, picks, n = today_picks.normalize_nfl_slate({"today_et": "2099-01-01", "board": []})
    assert today == "2099-01-01"
    assert picks == []
    assert n == 0


def test_nfl_tips_feed_combo_favorite_cover_rule():
    pred = {
        "home": "KC", "away": "BUF",
        "home_prob": 0.84, "away_prob": 0.16,
        "spread_home": -6.5,
        "spread_pick": "KC -6.5",
        "spread_pick_probability": 81.0,
        "total_points_pick": "Over",
        "total_points_line": 44.5,
        "total_points_pick_probability": 56.0,
        "home_points_pick": "Over", "home_points_line": 24.5,
        "home_points_pick_probability": 55.0,
        "away_points_pick": "Under", "away_points_line": 20.5,
        "away_points_pick_probability": 54.0,
    }
    tips = today_picks._nfl_market_tips(
        pred, home_name="Kansas City Chiefs", away_name="Buffalo Bills",
        include_moneyline=True, include_favorite_spread=True,
    )
    ml = next(t for t in tips if t["market"] == "Moneyline")
    spread = next(t for t in tips if t["market"] == "Spread")
    assert today_picks._leg_is_favorite_cover({
        "market": ml["market"], "pick": ml["pick"], "confidence": ml["probability"],
    }) or ml["probability"] < today_picks.FAVORITE_COVER_MIN
    assert "-" in spread["pick"]


def test_analytics_counts_nfl_unlock_path():
    assert analytics.is_get_prediction_path("/nfl/api/predict?home=KC&away=BUF")
    meta = analytics.get_prediction_event_meta("/nfl/api/predict?home=KC&away=BUF")
    assert meta["league_label"] == "NFL"
    assert meta["kind"] == "event"


def test_combo_settle_nfl_markets_with_box_score():
    score = {
        "home": "Kansas City Chiefs", "away": "Buffalo Bills",
        "home_score": 27, "away_score": 20, "completed": True,
    }
    won, _ = combo_results.settle_leg_against_score(
        "Moneyline", "Kansas City Chiefs", score,
    )
    assert won == combo_results.WON
    st, _ = combo_results.settle_leg_against_score(
        "Spread", "Kansas City Chiefs -3.5", score,
    )
    assert st == combo_results.WON
    st, _ = combo_results.settle_leg_against_score(
        "Total points", "Over 44.5", score,
    )
    assert st == combo_results.WON


def test_schedule_url_for_nfl_points_at_fixtures():
    import app as app_module
    assert app_module._todays_picks_schedule_url("american_football", "nfl") == "/nfl/fixtures"


def test_sitemap_includes_nfl_static_urls(client):
    # /sitemap.xml is a sitemap index; static pages live in /sitemap-static.xml.
    body = client.get("/sitemap-static.xml").data.decode()
    assert "https://degenpredictions.com/sports/american-football" in body
    assert "https://degenpredictions.com/nfl/fixtures" in body
    assert "https://degenpredictions.com/todays-picks/american_football/nfl" in body
