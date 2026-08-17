"""Focused selection, settlement, and evidence tests for Safer Result."""
import pytest

import safer_result


def _selection(**overrides):
    values = {
        "home_team": "Arsenal",
        "away_team": "Chelsea",
        "favorite_team": "Arsenal",
        "p_home_win": 0.80,
        "p_draw": 0.12,
        "p_away_win": 0.08,
        "p_favorite_avoids_defeat": 0.82,
    }
    values.update(overrides)
    return safer_result.select_safer_result(**values)


def test_safer_result_uses_exact_win_only_at_stricter_bar():
    pick = _selection()
    assert pick["market"] == "Safer Result"
    assert pick["selection_type"] == "exact"
    assert pick["pick"] == "Arsenal win"
    assert pick["code"] == "H"


def test_safer_result_falls_back_to_explicit_home_double_chance():
    pick = _selection(p_home_win=0.62, p_draw=0.23, p_away_win=0.15)
    assert pick["selection_type"] == "double_chance"
    assert pick["pick"] == "Arsenal or Draw (1X)"
    assert pick["code"] == "1X"


def test_safer_result_formats_away_double_chance_as_x2():
    pick = _selection(
        favorite_team="Chelsea",
        p_home_win=0.18,
        p_draw=0.22,
        p_away_win=0.60,
    )
    assert pick["selection_type"] == "double_chance"
    assert pick["pick"] == "Chelsea or Draw (X2)"
    assert pick["code"] == "X2"


def test_safer_result_does_not_force_a_pick():
    assert _selection(p_favorite_avoids_defeat=0.69) is None


def test_safer_result_settles_exact_and_double_chance_semantics():
    exact = _selection()
    double_chance = _selection(p_home_win=0.62, p_draw=0.23, p_away_win=0.15)
    assert safer_result.settle_safer_result(exact, "H") is True
    assert safer_result.settle_safer_result(exact, "D") is False
    assert safer_result.settle_safer_result(double_chance, "H") is True
    assert safer_result.settle_safer_result(double_chance, "D") is True
    assert safer_result.settle_safer_result(double_chance, "A") is False
    assert safer_result.settle_safer_result(None, "H") is None
    with pytest.raises(ValueError):
        safer_result.settle_safer_result(double_chance, "P")


def test_safer_result_report_only_enables_supported_leagues():
    report = safer_result.load_oos_report()
    assert report["aggregate"]["accuracy"] >= 0.70
    for league_key, evidence in report["leagues"].items():
        assert evidence["n"] >= safer_result.MIN_EVALUATION_PICKS, league_key
        assert evidence["accuracy"] >= 0.70, league_key
        assert evidence["met_target"] is True
        assert 0 < evidence["coverage"] <= 1
