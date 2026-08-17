"""Pure selection and settlement logic for soccer's Safer Result market.

The market has two possible semantics:
* exact favourite win (H/A), only at the stricter exact-result bar;
* favourite or draw (1X/X2), at the double-chance bar.

Both branches use only probabilities and favourite identity known before
kickoff.  If neither branch qualifies, the honest output is no pick.
"""
from __future__ import annotations

import json
import math
import os
from functools import lru_cache


MARKET_NAME = "Safer Result"
DOUBLE_CHANCE_THRESHOLD = 0.70
EXACT_WIN_THRESHOLD = 0.75
MIN_RAW_EXACT_WIN = 0.75
MIN_EVALUATION_PICKS = 40
REPORT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "safer_result_oos.json")


def _probability(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or value < 0.0 or value > 1.0:
        return None
    return value


def calibrate_probability(raw_probability, calibration=None):
    """Apply a stored Platt map to a raw model score.

    The map is fitted on the earlier chronological calibration slice.  An
    absent map is treated as identity, which is useful for isolated unit
    tests but is not enough to mark a league as supported in production.
    """
    raw = _probability(raw_probability)
    if raw is None:
        return None
    if not calibration:
        return raw
    try:
        intercept = float(calibration["intercept"])
        slope = float(calibration["slope"])
    except (KeyError, TypeError, ValueError):
        return None
    clipped = min(max(raw, 1e-6), 1.0 - 1e-6)
    log_odds = math.log(clipped / (1.0 - clipped))
    z = intercept + slope * log_odds
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    ez = math.exp(z)
    return ez / (1.0 + ez)


def select_safer_result(
    *,
    home_team,
    away_team,
    favorite_team,
    p_home_win,
    p_draw,
    p_away_win,
    p_favorite_avoids_defeat,
    calibration=None,
    double_chance_threshold=DOUBLE_CHANCE_THRESHOLD,
    exact_win_threshold=EXACT_WIN_THRESHOLD,
    min_raw_exact_win=MIN_RAW_EXACT_WIN,
):
    """Return one threshold-qualified selection, or ``None``.

    Exact-win and double-chance scores are calibrated separately and are
    never compared as if they represented the same event.  The exact branch
    also requires the favourite to lead the raw 1X2 distribution.
    """
    home = str(home_team)
    away = str(away_team)
    favorite = str(favorite_team)
    if favorite not in (home, away):
        return None

    p_home = _probability(p_home_win)
    p_draw_value = _probability(p_draw)
    p_away = _probability(p_away_win)
    p_dc_raw = _probability(p_favorite_avoids_defeat)
    if None in (p_home, p_draw_value, p_away, p_dc_raw):
        return None

    calibration = calibration or {}
    p_dc = calibrate_probability(
        p_dc_raw, calibration.get("favorite_avoids_defeat")
    )
    if p_dc is None or p_dc < float(double_chance_threshold):
        return None

    home_is_favorite = favorite == home
    p_favorite_win_raw = p_home if home_is_favorite else p_away
    p_other_win = p_away if home_is_favorite else p_home
    p_favorite_win = calibrate_probability(
        p_favorite_win_raw, calibration.get("favorite_win")
    )

    if (
        p_favorite_win is not None
        and p_favorite_win >= float(exact_win_threshold)
        and p_favorite_win_raw >= float(min_raw_exact_win)
        and p_favorite_win_raw >= p_draw_value
        and p_favorite_win_raw >= p_other_win
    ):
        result_code = "H" if home_is_favorite else "A"
        return {
            "market": MARKET_NAME,
            "selection_type": "exact",
            "pick": f"{favorite} win",
            "code": result_code,
            "result_code": result_code,
            "favorite_team": favorite,
            "probability": p_favorite_win,
            "raw_probability": p_favorite_win_raw,
            "threshold": float(exact_win_threshold),
        }

    double_chance_code = "1X" if home_is_favorite else "X2"
    return {
        "market": MARKET_NAME,
        "selection_type": "double_chance",
        "pick": f"{favorite} or Draw ({double_chance_code})",
        "code": double_chance_code,
        "favorite_team": favorite,
        "probability": p_dc,
        "raw_probability": p_dc_raw,
        "threshold": float(double_chance_threshold),
    }


def settle_safer_result(selection, actual_result):
    """Settle a Safer Result selection against an H/D/A final result."""
    if selection is None:
        return None
    actual = str(actual_result).upper()
    if actual not in {"H", "D", "A"}:
        raise ValueError("actual_result must be H, D, or A")
    selection_type = selection.get("selection_type")
    if selection_type == "exact":
        return actual == selection.get("result_code")
    if selection_type == "double_chance":
        code = selection.get("code")
        if code == "1X":
            return actual in {"H", "D"}
        if code == "X2":
            return actual in {"D", "A"}
    raise ValueError("invalid Safer Result selection")


@lru_cache(maxsize=1)
def load_oos_report():
    try:
        with open(REPORT_PATH, encoding="utf-8") as handle:
            report = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    return report if isinstance(report, dict) else {}


def league_evidence(league_key):
    return (load_oos_report().get("leagues") or {}).get(league_key) or {}


def league_is_supported(league_key):
    evidence = league_evidence(league_key)
    return bool(
        evidence.get("met_target")
        and int(evidence.get("n") or 0) >= MIN_EVALUATION_PICKS
    )
