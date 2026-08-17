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


def test_unlocked_round_midseason_starts_at_first_present_round():
    """MLS-style feed: finished rounds omitted, remaining start at 3 not 1."""
    df = _rounds_df({
        3: ["2099-08-08T20:30:00Z"],
        20: ["2099-08-15T23:30:00Z"],
    })
    assert app_module.compute_unlocked_round(df) == 3


def test_unlocked_round_midseason_snaps_across_round_gap():
    """After round 3 completes, unlock the next present round (20), not 4."""
    df = _rounds_df({
        3: ["2000-08-08T20:30:00Z"],
        20: ["2099-08-15T23:30:00Z"],
        21: ["2099-08-22T23:30:00Z"],
    })
    assert app_module.compute_unlocked_round(df) == 20


# ---- La Liga kickoff correction (fixturedownload morning UTC) --------------

def test_laliga_opener_is_11pm_ist():
    from leagues import correct_fixture_dateutc

    out = correct_fixture_dateutc(
        "la_liga", "2026-08-15 07:30:00Z", "Alaves", "Getafe",
    )
    assert out == "2026-08-15 17:30:00Z"
    # 17:30 UTC = 23:00 IST
    utc = pd.Timestamp(out.replace("Z", "+00:00"))
    ist = utc.tz_convert("Asia/Kolkata")
    assert ist.strftime("%Y-%m-%d %H:%M") == "2026-08-15 23:00"


def test_laliga_kickoff_shift_is_idempotent():
    from leagues import correct_fixture_dateutc

    once = correct_fixture_dateutc("la_liga", "2026-08-15 07:30:00Z")
    twice = correct_fixture_dateutc("la_liga", once)
    assert once == twice == "2026-08-15 17:30:00Z"


def test_laliga_placeholder_afternoon_utc_untouched():
    from leagues import correct_fixture_dateutc

    raw = "2026-09-12 14:00:00Z"
    assert correct_fixture_dateutc("la_liga", raw) == raw


def test_epl_kickoffs_not_shifted():
    from leagues import correct_fixture_dateutc

    raw = "2026-08-21 19:00:00Z"
    assert correct_fixture_dateutc("premier_league", raw) == raw


def test_celta_osasuna_moved_off_opening_weekend():
    from leagues import correct_fixture_dateutc

    out = correct_fixture_dateutc(
        "la_liga", "2026-08-16 09:30:00Z", "Celta", "Osasuna",
    )
    assert out == "2026-08-27 16:30:00Z"


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
    match = _match(
        P_Home_Win=0.80,
        P_Draw=0.12,
        P_Away_Win=0.08,
        P_Favorite_Avoids_Defeat=0.85,
        Favorite_Team="Arsenal",
    )
    tips = app_module.generate_betting_tips(match)
    result_tip = tips[0]
    assert result_tip["market"] == "Safer Result"
    assert result_tip["pick"] == "Arsenal win"
    assert result_tip["confidence"] == "High"


def test_tips_close_outcomes_yield_no_strong_lean():
    match = _match(
        P_Home_Win=0.34,
        P_Draw=0.33,
        P_Away_Win=0.33,
        P_Favorite_Avoids_Defeat=0.69,
        Favorite_Team="Arsenal",
    )
    tips = app_module.generate_betting_tips(match)
    assert tips[0]["pick"] == "No pick"
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


def test_tips_safer_result_is_no_pick_when_favorite_probability_missing():
    match = _match()
    match.pop("P_Favorite_Avoids_Defeat", None)
    tips = app_module.generate_betting_tips(match)
    safer = next(t for t in tips if t["market"] == "Safer Result")
    assert safer["pick"] == "No pick"


def test_tips_safer_result_is_no_pick_when_favorite_probability_nan():
    match = _match(P_Favorite_Avoids_Defeat=float("nan"), Favorite_Team="Arsenal")
    tips = app_module.generate_betting_tips(match)
    safer = next(t for t in tips if t["market"] == "Safer Result")
    assert safer["pick"] == "No pick"


def test_tips_safer_result_present_when_double_chance_qualifies():
    match = _match(P_Favorite_Avoids_Defeat=0.8, Favorite_Team="Arsenal")
    tips = app_module.generate_betting_tips(match)
    safer = next(t for t in tips if t["market"] == "Safer Result")
    assert safer["pick"] == "Arsenal or Draw (1X)"


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


# ---- live_predict skipped corner markets (MLS) -----------------------------

def test_predict_fixture_live_handles_skipped_corner_models(monkeypatch):
    """MLS skips corner markets at train time; live predict must not unpack None."""
    import numpy as np
    import live_predict

    class FakeClf:
        def predict_proba(self, X):
            return np.array([[0.5, 0.2, 0.3]])

    class FakeBin:
        def predict_proba(self, X):
            return np.array([[0.4, 0.6]])

    state = {
        "models": {
            "result": ({"rf": FakeClf()}, None, "rf"),
            "btts": ({"rf": FakeBin()}, None, "rf"),
            "over25": ({"rf": FakeBin()}, None, "rf"),
            "home_more_corners": None,
            "favorite_avoids_defeat": ({"rf": FakeBin()}, None, "rf"),
            "total_corners": None,
        },
        "feature_cols": ["Elo_Diff", "Home_Points_avg5", "Away_Points_avg5"],
        "result_label_map": {"H": 0, "D": 1, "A": 2},
        "latest_form": pd.DataFrame([
            {"Team": "New England Revolution", "Elo": 1500, "Points_avg5": 1.5,
             "GamesPlayed": 10, "SquadQuality": 1.0},
            {"Team": "Houston Dynamo", "Elo": 1480, "Points_avg5": 1.2,
             "GamesPlayed": 10, "SquadQuality": 0.9},
        ]),
        "h2h_history": {},
        "players_by_team": {},
        "data_dir": "data/mls_5yr",
        "model_dir": "model/mls",
    }
    monkeypatch.setitem(live_predict._cache, "mls", state)

    result = live_predict.predict_fixture_live(
        "New England Revolution", "Houston Dynamo", "2026-08-08", league_key="mls",
    )
    assert result["Exp_Total_Corners"] == ""
    assert result["P_Home_More_Corners"] == ""
    assert result["More_Corners_Prediction"] == ""
    assert "Predicted_Result" in result
    assert "P_Home_Win" in result
