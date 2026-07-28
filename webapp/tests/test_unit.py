"""Unit tests for pure functions in app.py -- no Flask request context,
no file I/O, just the logic that decides what a user sees."""
import pandas as pd
import pytest

import app as app_module


# ---- compute_unlocked_round ------------------------------------------------

def _rounds_df(round_dates):
    """Builds a minimal DataFrame with RoundNumber/DateUtc columns.
    round_dates: {round_number: [iso_date_str, ...]}"""
    rows = []
    for r, dates in round_dates.items():
        for d in dates:
            rows.append({"RoundNumber": r, "DateUtc": d})
    return pd.DataFrame(rows)


def test_unlocked_round_empty_df_returns_1():
    assert app_module.compute_unlocked_round(pd.DataFrame()) == 1


def test_unlocked_round_all_future_stays_at_1():
    df = _rounds_df({1: ["2099-01-01T00:00:00Z"], 2: ["2099-01-08T00:00:00Z"]})
    assert app_module.compute_unlocked_round(df) == 1


def test_unlocked_round_advances_once_past_round_completes():
    df = _rounds_df({
        1: ["2000-01-01T00:00:00Z"],   # long in the past -> complete
        2: ["2099-01-08T00:00:00Z"],   # future -> not complete
    })
    assert app_module.compute_unlocked_round(df) == 2


def test_unlocked_round_does_not_skip_ahead_over_an_incomplete_round():
    """Regression: round 3 must not unlock just because round 1 is done if
    round 2 hasn't kicked off yet -- unlocking must be sequential."""
    df = _rounds_df({
        1: ["2000-01-01T00:00:00Z"],   # complete
        2: ["2099-01-08T00:00:00Z"],   # not complete
        3: ["2000-01-01T00:00:00Z"],   # "complete" by date, but must not unlock
    })
    assert app_module.compute_unlocked_round(df) == 2


def test_unlocked_round_requires_every_fixture_in_round_to_finish():
    """A round with one finished match and one still-future match should NOT
    count as complete -- it's gated on the round's LAST fixture."""
    df = _rounds_df({
        1: ["2000-01-01T00:00:00Z", "2099-01-01T00:00:00Z"],
    })
    assert app_module.compute_unlocked_round(df) == 1


# ---- generate_betting_tips -------------------------------------------------

def _match(**overrides):
    base = {
        "HomeTeam": "Arsenal", "AwayTeam": "Chelsea",
        "P_Home_Win": 0.5, "P_Draw": 0.3, "P_Away_Win": 0.2,
        "P_BTTS_Yes": 0.5, "P_Over_2.5_Goals": 0.5, "P_Home_More_Corners": 0.5,
    }
    base.update(overrides)
    return base


def test_tips_clear_favorite_produces_match_result_tip():
    match = _match(P_Home_Win=0.6, P_Draw=0.25, P_Away_Win=0.15)
    tips = app_module.generate_betting_tips(match)
    result_tip = tips[0]
    assert result_tip["market"] == "Match Result"
    assert result_tip["pick"] == "Arsenal"
    assert result_tip["confidence"] == "High"


def test_tips_close_outcomes_yield_no_strong_lean():
    match = _match(P_Home_Win=0.34, P_Draw=0.33, P_Away_Win=0.33)
    tips = app_module.generate_betting_tips(match)
    assert tips[0]["pick"] == "No strong lean"
    assert tips[0]["confidence"] == "Low"


def test_tips_skips_btts_when_near_even():
    """Only markets with real separation (outside 0.45-0.55) should raise a tip."""
    match = _match(P_BTTS_Yes=0.50)
    tips = app_module.generate_betting_tips(match)
    markets = [t["market"] for t in tips]
    assert "Both Teams to Score" not in markets


def test_tips_includes_btts_when_confident():
    match = _match(P_BTTS_Yes=0.7)
    tips = app_module.generate_betting_tips(match)
    btts = next(t for t in tips if t["market"] == "Both Teams to Score")
    assert btts["pick"] == "Yes"
    assert btts["confidence"] == "High"


def test_tips_favorite_avoids_defeat_omitted_when_missing():
    match = _match()
    match.pop("P_Favorite_Avoids_Defeat", None)
    tips = app_module.generate_betting_tips(match)
    assert all(t["market"] != "Favorite Avoids Defeat" for t in tips)


def test_tips_favorite_avoids_defeat_omitted_when_nan():
    match = _match(P_Favorite_Avoids_Defeat=float("nan"), Favorite_Team="Arsenal")
    tips = app_module.generate_betting_tips(match)
    assert all(t["market"] != "Favorite Avoids Defeat" for t in tips)


def test_tips_favorite_avoids_defeat_present_when_valid():
    match = _match(P_Favorite_Avoids_Defeat=0.8, Favorite_Team="Arsenal")
    tips = app_module.generate_betting_tips(match)
    fav = next(t for t in tips if t["market"] == "Favorite Avoids Defeat")
    assert fav["pick"] == "Arsenal win or draw"


# ---- summarize_backtest ----------------------------------------------------

def test_summarize_backtest_empty_returns_empty_dict():
    assert app_module.summarize_backtest(pd.DataFrame()) == {}


def test_summarize_backtest_computes_per_sample_accuracy():
    df = pd.DataFrame({
        "Sample_Type": ["In-Sample", "In-Sample", "Out-of-Sample"],
        "Result_Correct": [True, False, True],
        "BTTS_Correct": [True, True, False],
        "Goals_Correct": [True, True, True],
        "Corners_Winner_Correct": [False, False, True],
    })
    summary = app_module.summarize_backtest(df)
    assert summary["In-Sample"]["n"] == 2
    assert summary["In-Sample"]["result_acc"] == pytest.approx(0.5)
    assert summary["Out-of-Sample"]["n"] == 1
    assert summary["Out-of-Sample"]["result_acc"] == pytest.approx(1.0)
    # Column not present in this dataframe -> reported as None, not a KeyError
    assert summary["In-Sample"]["favorite_acc"] is None


# ---- get_league_or_404 -----------------------------------------------------

def test_get_league_or_404_returns_config_for_known_league():
    cfg = app_module.get_league_or_404("premier_league")
    assert cfg["label"] == "Premier League"


def test_get_league_or_404_aborts_for_unknown_league(flask_app):
    with flask_app.test_request_context():
        with pytest.raises(Exception):  # werkzeug.exceptions.NotFound
            app_module.get_league_or_404("not_a_real_league")
