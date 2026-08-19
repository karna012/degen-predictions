"""Today's Picks catalog + per-league slate builders.

Nav flow: /todays-picks -> sport -> league -> that league's slate.
Each builder returns a common shape so one template can render every sport.

Today's Picks / Best Combos take high-probability markets across soccer,
baseball, and cricket — moneyline / Safer Result / DNB / handicap / BTTS /
goals / corners / totals / team runs / hits / Ks. Best Combos round-robins
the leg pool by sport/league and market family, then publishes both a
market-mixed slate and a dedicated mixed sports/leagues board (cross-sport
or cross-league slips).

Calibrated ~70% OOS bars are preferred when available; otherwise a wise
≥70% floor applies on soft markets and ≥68% on hard markets. Soccer's
``Safer Result`` is only enabled where its fixed rule cleared 70% on a
later chronological evaluation slice. Best Combos targets ~70%+ slip hit
rate: prefer all-leg ≥70% cards, never publish below 68%, and reject
upset leans / high-correlation soft joints. Same fixtures are not
reshuffled across Best/Mixed slips. Favorite-covers (ML / match
winner / DNB / win-a-set) and Unders are shrunk and capped on every sport;
one favorite-cover may glue at most one published combo. Unders are not
combo glue below the raised bar.
"""
from __future__ import annotations

import datetime
import json
import math
import os
import re
from functools import lru_cache
from urllib.parse import quote

import pandas as pd

import safer_result

# Soft floor when a market has no calibrated 70% bar yet.
HIGH_CONF_MIN = 0.55
# Never tip below this on Today's Picks — selective high-prob floor.
HIGH_PROB_MIN = 0.68
# Stricter floor for uncalibrated / soft derived markets (totals, DNB, etc.).
WISE_PROB_MIN = 0.70
MAX_PICKS_PER_LEAGUE = 6
# Wider pool for cross-sport combos (still high-probability only).
MAX_PICKS_FOR_COMBOS = 18
# Always aim for 3 two-leg + 3 three-leg slips on the Best Combos page.
MAX_COMBOS_PER_SIZE = 3
# Soft daily floor — fill at least this many per size when any pool exists.
MIN_COMBOS_PER_SIZE = 2
# Dedicated mixed sports / leagues board (cross-sport preferred, else cross-league).
MAX_MIXED_COMBOS_PER_SIZE = 3
COMBO_POOL_CAP = 48
# Number of promising partial slips retained while building 3+ leg cards.
# Pairs are cheap at the pool cap; pruning before the third leg avoids C(n, 3).
COMBO_BEAM_WIDTH = 256
# Outstanding combo target — push published slip hit rate toward ≥70%.
COMBO_TARGET_HIT_RATE = 0.70
# Prefer every leg at/above the ~70% wise floor (Today's Picks soft markets).
COMBO_PREFER_MIN = WISE_PROB_MIN
# Primary usable floor — selective; never tip near coin-flip into combos.
COMBO_FALLBACK_MIN = 0.68
# Thin-slate soft fill — still high-prob (was 0.55); quality over quantity.
COMBO_SOFT_FILL_MIN = 0.68
# Last-resort widening for thin slates where nothing clears 0.68: still well
# clear of coin-flip (BASEBALL_COINFLIP_FAV_MAX=0.57, HIGH_CONF_MIN=0.55),
# just short of the usual combo bar. Only used when the higher floors above
# cannot fill even MIN_COMBOS_PER_SIZE slips.
COMBO_NEAR_MISS_MIN = 0.60
# Hard reject: weakest leg below this never publishes. Callers building a
# candidate list at a lower tier (e.g. COMBO_NEAR_MISS_MIN) pass that floor
# through explicitly instead of relying on this default.
COMBO_MIN_LEG = 0.68
# Prefer cards whose average leg confidence clears the hit-rate target.
COMBO_MIN_AVG_OUTSTANDING = COMBO_TARGET_HIT_RATE
# One fixture may glue at most this many published combo cards (Best + Mixed).
# 1 = no reshuffling the same event across today's slips.
COMBO_MAX_SLIPS_PER_FIXTURE = 1
# Favorite-cover (ML / match winner / DNB / win-a-set / Safer Result) — one per published slate.
COMBO_MAX_FAVORITE_COVER_LEGS = 1
FAVORITE_COVER_MIN = 0.78
# Shrink overconfident favorite-covers and Unders on every sport.
FAVORITE_COVER_SHRINK = 0.82
FAVORITE_COVER_CAP = 0.82
UNDER_SHRINK = 0.78
UNDER_CAP = 0.74
UNDER_COMBO_MIN = 0.76
# Tennis aliases (same numbers — keep names used by tennis tips / tests).
TENNIS_SET_WINNER_SHRINK = FAVORITE_COVER_SHRINK
TENNIS_SET_WINNER_CAP = FAVORITE_COVER_CAP
TENNIS_UNDER_SETS_SHRINK = UNDER_SHRINK
TENNIS_UNDER_SETS_CAP = UNDER_CAP
TENNIS_UNDER_SETS_MIN = UNDER_COMBO_MIN
SOCCER_UNDER_GOALS_MIN = UNDER_COMBO_MIN
# Baseball / cricket winner markets are near coin-flip on full slate.
# Skip favorite moneyline / match winner on Today's Picks / Combos; surface
# dogs only when the game is close.
BASEBALL_COINFLIP_FAV_MAX = 0.57
BASEBALL_UPSET_LEAN_MIN = 0.40
BASEBALL_MAX_UPSET_LEANS = 3
# Underdog run-line / AH helpers.
BASEBALL_RUNLINE = 1.5
BASEBALL_MARGIN_STD = 3.0
SOCCER_DOG_AH = 0.5
# Clear-favourite gap before emitting a dog handicap tip.
HANDICAP_FAV_EDGE = 0.05
BASEBALL_PROP_MARKETS = frozenset({
    "Total runs", "Team runs", "Total hits", "Total strikeouts", "Handicap",
})
# Back-compat aliases used in earlier MLB patch.
MLB_COINFLIP_FAV_MAX = BASEBALL_COINFLIP_FAV_MAX
MLB_UPSET_LEAN_MIN = BASEBALL_UPSET_LEAN_MIN
MLB_MAX_UPSET_LEANS = BASEBALL_MAX_UPSET_LEANS
MLB_PROP_MARKETS = BASEBALL_PROP_MARKETS
# Shared names for cricket + baseball coin-flip winner treatment.
COINFLIP_FAV_MAX = BASEBALL_COINFLIP_FAV_MAX
UPSET_LEAN_MIN = BASEBALL_UPSET_LEAN_MIN
MAX_UPSET_LEANS = BASEBALL_MAX_UPSET_LEANS
PROP_MARKETS = BASEBALL_PROP_MARKETS

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LIVE_CAL_PATH = os.path.join(_BASE_DIR, "data", "feedback", "live_calibration.json")
_live_cal_cache: tuple[float | None, float | None] = (None, None)  # mtime, value


def _live_suggested_min_confidence() -> float | None:
    """Optional raised floor from EC2 settle feedback (never lowers bars)."""
    global _live_cal_cache
    try:
        mtime = os.path.getmtime(_LIVE_CAL_PATH)
    except OSError:
        _live_cal_cache = (None, None)
        return None
    cached_mtime, cached_val = _live_cal_cache
    if cached_mtime == mtime:
        return cached_val
    try:
        with open(_LIVE_CAL_PATH, encoding="utf-8") as f:
            raw = json.load(f)
        sug = raw.get("suggested_min_confidence")
        val = float(sug) if sug is not None else None
        if val is None or val < HIGH_PROB_MIN or val > 0.90:
            val = None
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        val = None
    _live_cal_cache = (mtime, val)
    return val


def effective_high_prob_min() -> float:
    live = _live_suggested_min_confidence()
    return max(HIGH_PROB_MIN, live) if live is not None else HIGH_PROB_MIN


# Today's Picks label -> stored evidence/threshold key.
_MARKET_THRESHOLD_KEYS = {
    "Safer Result": "safer_result",
    "Draw No Bet": "result",  # reuse 1X2 calibration when present
    "Both Teams to Score": "btts",
    "Total Goals": "over25",
    "Corners": "home_more_corners",
    "Total Corners": "home_more_corners",
    "Team to Score": "btts",
}

_SPORT_THRESHOLD_PATHS = {
    "mlb": ("data", "mlb", "confidence_thresholds.json"),
    "npb": ("data", "npb", "confidence_thresholds.json"),
    "nfl": ("data", "nfl", "confidence_thresholds.json"),
    "nba": ("data", "nba", "confidence_thresholds.json"),
    "nhl": ("data", "nhl", "confidence_thresholds.json"),
    "hundred": ("data", "cricket", "confidence_thresholds.json"),
    "mlc": ("data", "cricket", "confidence_thresholds.json"),
    "ipl": ("data", "cricket", "confidence_thresholds.json"),
    "bbl": ("data", "cricket", "confidence_thresholds.json"),
    "cpl": ("data", "cricket", "confidence_thresholds.json"),
    "atp": ("data", "tennis", "confidence_thresholds.json"),
}

_SPORT_MARKET_KEYS = {
    "Moneyline": "moneyline",
    "Match winner": "match_winner",
    "Total runs": "total_runs",
    "Team runs": "team_runs",
    "Total hits": "total_hits",
    "Total strikeouts": "total_strikeouts",
    "Total sets": "total_sets",
    "Total games": "total_games",
    "Player to win a set": "player_win_set",
    "Handicap": "handicap",
    "Total points": "total_points",
    "Team points": "team_points",
    "Spread": "spread",
    "Total goals": "total_goals",
    "Team goals": "team_goals",
}


@lru_cache(maxsize=16)
def load_soccer_thresholds(league_key: str) -> dict:
    """Per-market min probability for the 70% OOS tier.

    Markets that failed to hit the target on holdout are marked unavailable
    (threshold=None) so Today's Picks does not advertise them as 70% picks.

    Safer Result comes from a separate calibration/evaluation split and is
    unavailable unless that league has at least 40 later-slice picks at
    measured accuracy >=70%.
    """
    try:
        from leagues import LEAGUES
        cfg = LEAGUES.get(league_key) or {}
        path = os.path.join(_BASE_DIR, cfg.get("model_dir", ""), "confidence_thresholds.json")
    except Exception:
        path = ""
    out = {
        "target_accuracy": 0.70,
        "calibrated": False,
        "markets": {k: HIGH_CONF_MIN for k in _MARKET_THRESHOLD_KEYS.values()},
        "available": {k: True for k in _MARKET_THRESHOLD_KEYS.values()},
        "safer_result_win_threshold": safer_result.EXACT_WIN_THRESHOLD,
        "safer_result_calibration": None,
        "safer_result_supported": False,
    }
    out["markets"]["safer_result"] = None
    out["available"]["safer_result"] = False
    evidence = safer_result.league_evidence(league_key)
    if safer_result.league_is_supported(league_key):
        out["markets"]["safer_result"] = safer_result.DOUBLE_CHANCE_THRESHOLD
        out["available"]["safer_result"] = True
        out["safer_result_calibration"] = evidence.get("calibration")
        out["safer_result_supported"] = True
        out["calibrated"] = True
    if not path or not os.path.isfile(path):
        return out
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError):
        return out
    out["target_accuracy"] = float(raw.get("target_accuracy") or 0.70)
    out["calibrated"] = True
    for mkt, info in (raw.get("markets") or {}).items():
        if mkt in {"result_merged", "safer_result"}:
            continue
        if info.get("skipped"):
            out["available"][mkt] = False
            out["markets"][mkt] = None
            continue
        best = (info or {}).get("best") or {}
        thr = best.get("threshold")
        if thr is None or best.get("met_target") is False:
            out["available"][mkt] = False
            out["markets"][mkt] = None
            continue
        out["available"][mkt] = True
        out["markets"][mkt] = max(HIGH_CONF_MIN, float(thr))

    return out


def safer_result_win_threshold(league_key) -> float | None:
    """Calibrated exact-win bar for a supported Safer Result league."""
    if not league_key:
        return safer_result.EXACT_WIN_THRESHOLD
    thr = load_soccer_thresholds(league_key)
    if not thr.get("available", {}).get("safer_result"):
        return None
    return float(
        thr.get("safer_result_win_threshold")
        or safer_result.EXACT_WIN_THRESHOLD
    )


def market_min_conf(league_key: str | None, market_label: str) -> float | None:
    """Return min conf for a market, or None if it is not in the 70% tier."""
    if not league_key:
        return HIGH_CONF_MIN
    thr = load_soccer_thresholds(league_key)
    key = _MARKET_THRESHOLD_KEYS.get(market_label)
    if not key:
        return HIGH_CONF_MIN
    if thr.get("calibrated") and not thr.get("available", {}).get(key, True):
        return None
    val = thr.get("markets", {}).get(key, HIGH_CONF_MIN)
    return HIGH_CONF_MIN if val is None else float(val)


@lru_cache(maxsize=16)
def load_sport_thresholds(league_id: str) -> dict:
    """70% OOS tier for baseball/cricket moneyline-style markets."""
    rel = _SPORT_THRESHOLD_PATHS.get(league_id)
    market_keys = list(_SPORT_MARKET_KEYS.values())
    out = {
        "target_accuracy": 0.70,
        "calibrated": False,
        "markets": {k: HIGH_CONF_MIN for k in market_keys},
        "available": {k: True for k in market_keys},
        "full_slate_acc": None,
    }
    if not rel:
        return out
    path = os.path.join(_BASE_DIR, *rel)
    if not os.path.isfile(path):
        return out
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError):
        return out
    out["target_accuracy"] = float(raw.get("target_accuracy") or 0.70)
    out["calibrated"] = True
    out["full_slate_acc"] = raw.get("full_slate_acc")

    # Cricket file is shared; prefer per-competition slice when present.
    comp_map = {
        "hundred": "hnd_male", "mlc": "mlc_male", "ipl": "ipl_male",
        "bbl": "bbl_male", "cpl": "cpl_male",
    }
    markets = dict(raw.get("markets") or {})
    by_comp = raw.get("by_competition") or {}
    comp_key = comp_map.get(league_id)
    if comp_key and comp_key in by_comp and not by_comp[comp_key].get("skipped"):
        markets = {"match_winner": {"best": by_comp[comp_key].get("best")}}
        out["full_slate_acc"] = by_comp[comp_key].get("full_slate_acc", out["full_slate_acc"])

    for mkt, info in markets.items():
        best = (info or {}).get("best") or {}
        thr = best.get("threshold")
        if thr is None or best.get("met_target") is False or (info or {}).get("skipped"):
            out["available"][mkt] = False
            out["markets"][mkt] = None
            continue
        out["available"][mkt] = True
        out["markets"][mkt] = max(HIGH_CONF_MIN, float(thr))
    return out


def sport_market_min_conf(league_id: str | None, market_label: str) -> float | None:
    """Min conf for baseball/cricket market, or None if not in the 70% tier."""
    if not league_id:
        return HIGH_CONF_MIN
    thr = load_sport_thresholds(league_id)
    key = _SPORT_MARKET_KEYS.get(market_label)
    if not key:
        return HIGH_CONF_MIN
    if thr.get("calibrated") and not thr.get("available", {}).get(key, True):
        return None
    val = thr.get("markets", {}).get(key, HIGH_CONF_MIN)
    return HIGH_CONF_MIN if val is None else float(val)


def tip_min_conf(league_key: str | None, market_label: str) -> float:
    """Floor for a soccer tip: calibrated 70% bar when available, else wise floor."""
    floor = effective_high_prob_min()
    cal = market_min_conf(league_key, market_label)
    if cal is not None:
        return max(float(cal), floor)
    # Soft / derived markets need a stricter bar when uncalibrated.
    soft = {
        "Draw No Bet", "Total Corners", "Team to Score",
        "Both Teams to Score", "Total Goals", "Corners", "Handicap",
    }
    bar = max(WISE_PROB_MIN if market_label in soft else floor, floor)
    return bar


def tip_sport_min_conf(league_id: str | None, market_label: str) -> float:
    """Floor for baseball/cricket tip: calibrated bar or wise high-prob floor."""
    if market_label == "Upset lean":
        return BASEBALL_UPSET_LEAN_MIN
    floor = effective_high_prob_min()
    cal = sport_market_min_conf(league_id, market_label)
    if cal is not None:
        return max(float(cal), floor)
    soft = {
        "Total runs", "Team runs", "Total hits", "Total strikeouts", "Handicap",
        "Total sets", "Total games", "Player to win a set",
    }
    bar = max(WISE_PROB_MIN if market_label in soft else floor, floor)
    return bar


def _combo_gather_bar(floor, wise_bar, *, min_conf):
    """Bar for Today's Picks vs combo gather (incl. soft-fill expansion)."""
    if min_conf is None:
        return float(wise_bar)
    if float(min_conf) + 1e-9 >= COMBO_FALLBACK_MIN:
        return max(float(floor), float(wise_bar))
    return float(floor)


def _normal_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _prob_gt(mean: float, threshold: float, std_dev: float) -> float:
    """P(X > threshold) for X ~ Normal(mean, std_dev²)."""
    if std_dev <= 0:
        return 1.0 if mean > threshold else 0.0
    return 1.0 - _normal_cdf((threshold - mean) / std_dev)


def _soccer_dog_handicap_tip(home, away, p_home, p_draw, p_away, fav_team):
    """Underdog Asian Handicap +0.5 (covers on dog win or draw)."""
    home, away = str(home), str(away)
    fav = str(fav_team) if fav_team is not None else (home if p_home >= p_away else away)
    if fav == home:
        dog, p_dog, p_fav = away, p_away, p_home
    else:
        dog, p_dog, p_fav = home, p_home, p_away
    if p_fav < p_dog + HANDICAP_FAV_EDGE:
        return None
    # +0.5 AH ≡ dog avoids defeat.
    p_cover = min(0.97, max(0.0, p_dog + float(p_draw or 0)))
    if p_cover < 0.55:
        return None
    return {
        "market": "Handicap",
        "pick": f"{dog} +{SOCCER_DOG_AH:g}",
        "probability": float(p_cover),
    }


def _baseball_dog_handicap_tip(pred, home_name, away_name):
    """Underdog run line +1.5 from win probs and/or predicted runs."""
    if not pred:
        return None
    home_p = float(pred.get("home_prob") or 0)
    away_p = float(pred.get("away_prob") or 0)
    if home_p >= away_p:
        fav_name, dog_name = home_name, away_name
        fav_p, dog_p = home_p, away_p
        fav_runs = pred.get("home_predicted_runs")
        dog_runs = pred.get("away_predicted_runs")
    else:
        fav_name, dog_name = away_name, home_name
        fav_p, dog_p = away_p, home_p
        fav_runs = pred.get("away_predicted_runs")
        dog_runs = pred.get("home_predicted_runs")
    if not dog_name or fav_p < dog_p + HANDICAP_FAV_EDGE:
        return None

    if fav_runs is not None and dog_runs is not None:
        # Cover if (dog_runs - fav_runs) + 1.5 > 0.
        margin = float(dog_runs) - float(fav_runs)
        p_cover = _prob_gt(margin, -BASEBALL_RUNLINE, BASEBALL_MARGIN_STD)
    else:
        # ~dog win + share of one-run fav wins (typical MLB).
        p_cover = min(0.95, dog_p + 0.38 * fav_p)

    p_cover = float(min(0.97, max(0.0, p_cover)))
    if p_cover < 0.55:
        return None
    return {
        "market": "Handicap",
        "pick": f"{dog_name} +{BASEBALL_RUNLINE:g}",
        "probability": p_cover,
    }


def _nfl_dog_spread_tip(pred, home_name, away_name):
    """Underdog plus-spread from the model's home line."""
    if not pred:
        return None
    home_p = float(pred.get("home_prob") or 0)
    away_p = float(pred.get("away_prob") or 0)
    try:
        spread_home = float(pred.get("spread_home"))
    except (TypeError, ValueError):
        return None
    if home_p >= away_p:
        fav_p, dog_p, dog_name = home_p, away_p, away_name
        dog_line = abs(spread_home)
    else:
        fav_p, dog_p, dog_name = away_p, home_p, home_name
        dog_line = abs(spread_home)
    if not dog_name or fav_p < dog_p + HANDICAP_FAV_EDGE:
        return None
    if dog_line < 0.5:
        dog_line = 3.5
    fav_spread_p = _pct_to_prob(pred.get("spread_pick_probability"))
    if fav_spread_p is not None:
        p_cover = float(min(0.97, max(0.0, 1.0 - fav_spread_p)))
    else:
        p_cover = min(0.95, dog_p + 0.22 * fav_p)
    if p_cover < 0.55:
        return None
    return {
        "market": "Spread",
        "pick": f"{dog_name} +{dog_line:g}",
        "probability": p_cover,
    }


def _shrink_favorite_cover_prob(p) -> float:
    """Pedigree 90% is not a lock on any sport — shrink then cap."""
    try:
        v = float(p)
    except (TypeError, ValueError):
        return 0.0
    return min(FAVORITE_COVER_CAP, max(0.0, v * FAVORITE_COVER_SHRINK))


def _shrink_under_prob(p) -> float:
    """Under totals correlate and wipe slates — shrink then cap."""
    try:
        v = float(p)
    except (TypeError, ValueError):
        return 0.0
    return min(UNDER_CAP, max(0.0, v * UNDER_SHRINK))


def _normal_ou(point: float, std_dev: float):
    """Sportsbook-style X.5 line + Over/Under side from a normal approx."""
    if point is None or std_dev <= 0:
        return None
    point = float(point)
    line = math.floor(point) + 0.5
    z = (line - point) / std_dev
    prob_over = 1 - 0.5 * (1 + math.erf(z / math.sqrt(2)))
    if prob_over >= 0.5:
        return {"line": line, "pick": "Over", "probability": float(prob_over)}
    return {
        "line": line,
        "pick": "Under",
        "probability": _shrink_under_prob(1.0 - prob_over),
    }


def _is_under_pick(pick) -> bool:
    text = str(pick or "").strip().lower()
    return text.startswith("under") or " under " in text


def _pct_to_prob(value):
    """MLB/NPB store some confidences as 0–100; normalize to 0–1."""
    if value is None:
        return None
    v = float(value)
    return v / 100.0 if v > 1.0 else v


def _tier_status_label(calibrated: bool, *, wise: bool = False) -> str:
    if wise:
        return "High probability"
    return "70%+ confidence tier" if calibrated else "High confidence"

# sport_key -> league_id -> meta
# Slate "today" for every league uses Asia/Kolkata (IST) so admin/Telegram and
# public boards share one calendar day — no UTC vs local midnight confusion.
TODAY_SPORTS = {
    "soccer": {
        "label": "Soccer",
        "color": "#2e7d32",
        "leagues": {
            "premier_league": {
                "label": "Premier League", "tz": "Asia/Kolkata", "kind": "soccer", "color": "#3d195b",
            },
            "la_liga": {
                "label": "La Liga", "tz": "Asia/Kolkata", "kind": "soccer", "color": "#ee8707",
            },
            "serie_a": {
                "label": "Serie A", "tz": "Asia/Kolkata", "kind": "soccer", "color": "#008fd7",
            },
            "bundesliga": {
                "label": "Bundesliga", "tz": "Asia/Kolkata", "kind": "soccer", "color": "#d20515",
            },
            "ligue_1": {
                "label": "Ligue 1", "tz": "Asia/Kolkata", "kind": "soccer", "color": "#091c3e",
            },
            "mls": {
                "label": "MLS", "tz": "Asia/Kolkata", "kind": "soccer", "color": "#c39e6d",
            },
            "primeira_liga": {
                "label": "Primeira Liga", "tz": "Asia/Kolkata", "kind": "soccer", "color": "#006600",
            },
            "liga_mx": {
                "label": "Liga MX", "tz": "Asia/Kolkata", "kind": "soccer", "color": "#0b6e4f",
            },
            "saudi_pro_league": {
                "label": "Saudi Pro League", "tz": "Asia/Kolkata", "kind": "soccer", "color": "#1b6b3d",
            },
        },
    },
    "baseball": {
        "label": "Baseball",
        "color": "#041e42",
        "leagues": {
            "mlb": {
                "label": "MLB", "tz": "Asia/Kolkata", "kind": "mlb", "color": "#041e42",
                "empty_hint": (
                    "MLB moneyline is skipped here — full-slate winners are near a coin-flip (~55%). "
                    "Tips prefer totals / team runs / hits / Ks; close games can also show an Upset lean "
                    "(underdog) labeled outside the 70% tier."
                ),
            },
            "npb": {
                "label": "NPB", "tz": "Asia/Kolkata", "kind": "npb", "color": "#bc002d",
                "empty_hint": (
                    "NPB moneyline is skipped here — full-slate winners are near a coin-flip (~58%) "
                    "and do not clear a honest 70% holdout tier. Tips prefer totals / team runs; "
                    "close games can also show an Upset lean (underdog) outside the 70% tier."
                ),
            },
        },
    },
    "basketball": {
        "label": "Basketball",
        "color": "#c8102e",
        "leagues": {
            "nba": {
                "label": "NBA", "tz": "Asia/Kolkata", "kind": "nba", "color": "#c8102e",
                "empty_hint": (
                    "Moneyline is skipped here for close games — full-slate winners clear a real "
                    "holdout edge (~67%), stronger than most sports on this site. "
                    "Tips prefer totals / team points; close games can also show an Upset lean "
                    "(underdog) outside the 70% tier."
                ),
            },
        },
    },
    "hockey": {
        "label": "Ice Hockey",
        "color": "#041e42",
        "leagues": {
            "nhl": {
                "label": "NHL", "tz": "Asia/Kolkata", "kind": "nhl", "color": "#041e42",
                "empty_hint": (
                    "NHL moneyline is skipped here — full-slate winners are near a coin-flip (~56%), "
                    "one of the least predictable sports on this site (real, measured on holdout — "
                    "not a guess). Tips prefer totals / team goals; close games can also show an "
                    "Upset lean (underdog) outside the 70% tier."
                ),
            },
        },
    },
    "cricket": {
        "label": "Cricket",
        "color": "#7b2ff7",
        "leagues": {
            "hundred": {
                "label": "The Hundred", "tz": "Asia/Kolkata", "kind": "cricket", "color": "#7b2ff7",
                "empty_hint": (
                    "Match winner is skipped here — full-slate cricket winners are near a coin-flip "
                    "(~57% overall; Hundred sample is thin). Tips prefer totals / team runs; "
                    "close games can also show an Upset lean (underdog) outside the 70% tier."
                ),
            },
            "mlc": {
                "label": "MLC", "tz": "Asia/Kolkata", "kind": "cricket", "color": "#d4a017",
                "empty_hint": (
                    "MLC match winner is skipped — full-slate holdout is ~50% (coin-flip) and "
                    "does not clear a honest 70% tier. Tips prefer totals / team runs; "
                    "close games can also show an Upset lean (underdog) outside the 70% tier."
                ),
            },
            "ipl": {
                "label": "IPL", "tz": "Asia/Kolkata", "kind": "cricket", "color": "#0033a0",
                "empty_hint": (
                    "IPL is off-season until ~March — no live fixtures right now. "
                    "Results & analysis stay on /ipl/results. Match winner is skipped on picks; "
                    "totals / team runs preferred when a season is live."
                ),
            },
            "bbl": {
                "label": "BBL", "tz": "Asia/Kolkata", "kind": "cricket", "color": "#00a3e0",
                "empty_hint": (
                    "BBL season typically runs Dec–Jan. Match winner is skipped (coin-flip full slate); "
                    "tips prefer totals / team runs, with Upset leans on close games only."
                ),
            },
            "cpl": {
                "label": "CPL", "tz": "Asia/Kolkata", "kind": "cricket", "color": "#00a651",
                "empty_hint": (
                    "CPL season typically runs Aug–Sep. Match winner is skipped (coin-flip full slate); "
                    "tips prefer totals / team runs, with Upset leans on close games only."
                ),
            },
        },
    },
    "tennis": {
        "label": "Tennis",
        "color": "#c4a35a",
        "leagues": {
            "atp": {
                "label": "ATP (Men)", "tz": "Asia/Kolkata", "kind": "tennis", "color": "#c4a35a",
                "empty_hint": (
                    "No ATP main-draw singles on today's board yet — check /tennis/fixtures. "
                    "When live, tips are match-winner leans (surface Elo + ML) for Today's Picks and Best Combos."
                ),
            },
        },
    },
    "american_football": {
        "label": "American Football",
        "color": "#013369",
        "leagues": {
            "nfl": {
                "label": "NFL", "tz": "Asia/Kolkata", "kind": "nfl", "color": "#013369",
                "empty_hint": (
                    "NFL moneyline is skipped here — full-slate winners are near a coin-flip. "
                    "Tips prefer totals / team points / dog spreads; close games can also show "
                    "an Upset lean (underdog) labeled outside the 70% tier."
                ),
            },
        },
    },
}


def get_sport(sport_key):
    return TODAY_SPORTS.get(sport_key)


def get_league(sport_key, league_id):
    sport = get_sport(sport_key)
    if not sport:
        return None, None
    league = sport["leagues"].get(league_id)
    return sport, league


def _zone(tz_name):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(tz_name)
    except Exception:
        fallbacks = {
            "America/New_York": datetime.timezone(datetime.timedelta(hours=-4)),
            "Asia/Tokyo": datetime.timezone(datetime.timedelta(hours=9)),
            "Asia/Kolkata": datetime.timezone(datetime.timedelta(hours=5, minutes=30)),
            "Europe/London": datetime.timezone(datetime.timedelta(hours=1)),
            "Europe/Madrid": datetime.timezone(datetime.timedelta(hours=2)),
            "Europe/Rome": datetime.timezone(datetime.timedelta(hours=2)),
            "Europe/Berlin": datetime.timezone(datetime.timedelta(hours=2)),
            "Europe/Paris": datetime.timezone(datetime.timedelta(hours=2)),
        }
        return fallbacks.get(tz_name, datetime.timezone.utc)


# Site-wide display / slate day — operators and readers think in IST.
IST_TZ = "Asia/Kolkata"


def format_kickoff_ist(iso_str, *, fmt="%a %d %b %Y, %H:%M IST") -> str:
    """Format a UTC kickoff for display in IST only (never label as UTC)."""
    dt = parse_kickoff_utc(iso_str)
    if dt is None:
        raw = str(iso_str or "").strip()
        return raw[:16] if raw else ""
    return dt.astimezone(_zone(IST_TZ)).strftime(fmt)


def today_in_tz(tz_name):
    return datetime.datetime.now(_zone(tz_name)).date().isoformat()


def local_date(iso_str, tz_name):
    if not iso_str:
        return ""
    try:
        raw = str(iso_str).replace("Z", "+00:00")
        dt = datetime.datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone(_zone(tz_name)).date().isoformat()
    except (ValueError, TypeError):
        return str(iso_str)[:10]


_SPORT_DURATION = {
    "soccer": datetime.timedelta(hours=2, minutes=15),
    "baseball": datetime.timedelta(hours=3, minutes=30),
    "cricket": datetime.timedelta(hours=4),
    "tennis": datetime.timedelta(hours=3),
    "american_football": datetime.timedelta(hours=3, minutes=30),
    "basketball": datetime.timedelta(hours=2, minutes=30),
    "hockey": datetime.timedelta(hours=2, minutes=30),
}

# Fixture pages / Today's Picks: do not dump the rest of the season.
PUBLIC_BOARD_HORIZON = datetime.timedelta(hours=36)


def parse_kickoff_utc(iso_str, *, time_str=""):
    """Parse a kickoff to UTC. Date-only values become midnight UTC."""
    raw = (iso_str or "").strip()
    if not raw:
        return None
    extra = (time_str or "").strip()
    if extra and "T" not in raw and " " not in raw[10:]:
        raw = f"{raw[:10]}T{extra}"
    raw = raw.replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(raw)
    except ValueError:
        try:
            dt = datetime.datetime.strptime(raw[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            try:
                dt = datetime.datetime.strptime(raw[:10], "%Y-%m-%d")
            except ValueError:
                return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(datetime.timezone.utc)


def fixture_is_finished(obj, *, sport_key="", now=None) -> bool:
    """True when a fixture should leave upcoming boards / digest / X queue."""
    if not isinstance(obj, dict):
        return False
    status = (obj.get("status") or "").strip().lower()
    if status in ("final", "completed", "ft", "full time") and _has_final_score(obj):
        return True
    if obj.get("completed") is True and _has_final_score(obj):
        return True
    sport = (sport_key or obj.get("sport_key") or "").strip().lower()
    kickoff = (
        obj.get("kickoff_iso")
        or obj.get("DateUtc")
        or obj.get("start_time_utc")
        or obj.get("date")
        or ""
    )
    dt = parse_kickoff_utc(kickoff, time_str=obj.get("time") or "")
    if dt is None:
        return False
    now = now or datetime.datetime.now(datetime.timezone.utc)
    raw = str(kickoff).strip()
    date_only = len(raw) <= 10 and "T" not in raw and " " not in raw
    if date_only and not (obj.get("time") or "").strip():
        return now >= dt + datetime.timedelta(days=1)
    duration = _SPORT_DURATION.get(sport, datetime.timedelta(hours=2, minutes=30))
    return now >= dt + duration


def _has_final_score(obj) -> bool:
    home = obj.get("home_score", obj.get("HomeTeamScore", obj.get("score1")))
    away = obj.get("away_score", obj.get("AwayTeamScore", obj.get("score2")))
    return home not in (None, "") and away not in (None, "")


def split_fixture_board(fixtures, *, sport_key="", now=None, today_str=None):
    """Split a live feed into upcoming/live, postponed, and finished.

    Fixtures pages must only render the first bucket. Finished games belong
    on /results — never a "Recent results" block under fixtures.
    """
    now = now or datetime.datetime.now(datetime.timezone.utc)
    today_str = today_str or now.astimezone(datetime.timezone.utc).date().isoformat()
    live_or_upcoming, postponed, finished = [], [], []
    for m in fixtures or []:
        if not isinstance(m, dict):
            continue
        status = (m.get("status") or "").strip().lower()
        if m.get("is_live") or status in ("in", "live"):
            live_or_upcoming.append(m)
            continue
        if m.get("postponed"):
            postponed.append(m)
            continue
        has_score = _has_final_score(m)
        if (m.get("completed") and has_score) or fixture_is_finished(m, sport_key=sport_key, now=now):
            finished.append(m)
            continue
        day = str(m.get("date") or m.get("DateUtc") or m.get("kickoff_iso") or "").replace("T", " ")[:10]
        if day and day < today_str and not has_score:
            postponed.append(m)
            continue
        live_or_upcoming.append(m)
    return live_or_upcoming, postponed, finished


def fixture_is_upcoming_or_live(obj, *, sport_key="", now=None) -> bool:
    """True only for live games or matches that have not kicked off yet."""
    if not isinstance(obj, dict):
        return False
    if obj.get("is_live") or (obj.get("status") or "").strip().lower() in ("in", "live"):
        return True
    if obj.get("completed") or obj.get("postponed"):
        return False
    kickoff = (
        obj.get("kickoff_iso")
        or obj.get("DateUtc")
        or obj.get("start_time_utc")
        or obj.get("date")
        or ""
    )
    now = now or datetime.datetime.now(datetime.timezone.utc)
    day = str(kickoff).strip()[:10]
    today = now.astimezone(datetime.timezone.utc).strftime("%Y-%m-%d")
    if day and day < today:
        return False
    if fixture_is_finished(obj, sport_key=sport_key, now=now):
        return False
    if fixture_off_public_board(obj, sport_key=sport_key, now=now):
        return False
    dt = parse_kickoff_utc(kickoff, time_str=obj.get("time") or "")
    if dt is None:
        # Date-only rows (Wikipedia draw slots, stale ESPN) are not real kickoffs.
        return False
    raw = str(kickoff).strip()
    date_only = len(raw) <= 10 and "T" not in raw and " " not in raw
    if date_only and not (obj.get("time") or "").strip():
        return False
    return dt <= now + PUBLIC_BOARD_HORIZON


def fixture_off_public_board(obj, *, sport_key="", now=None) -> bool:
    """Hide from Today's Picks / Combos: finished, or kicked off and not live."""
    if fixture_is_finished(obj, sport_key=sport_key, now=now):
        return True
    status = (obj.get("status") or "").strip().lower()
    if obj.get("is_live") or status in ("live", "in"):
        return False
    kickoff = (
        obj.get("kickoff_iso")
        or obj.get("DateUtc")
        or obj.get("start_time_utc")
        or obj.get("date")
        or ""
    )
    raw = str(kickoff).strip()
    date_only = len(raw) <= 10 and "T" not in raw and " " not in raw
    if date_only and not (obj.get("time") or "").strip():
        return False
    dt = parse_kickoff_utc(kickoff, time_str=obj.get("time") or "")
    if dt is None:
        return False
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return now >= dt + datetime.timedelta(minutes=10)


def filter_upcoming_public_legs(legs, fixtures=None):
    """Drop finished / kicked-off legs; if a live board exists, require a match."""
    live = list(fixtures) if fixtures is not None else todays_live_fixtures()
    leagues_with_live = {
        (fx.get("league_id") or "").strip().lower()
        for fx in live
        if (fx.get("league_id") or "").strip()
    }
    kept = []
    for leg in legs or []:
        if not isinstance(leg, dict):
            continue
        if fixture_off_public_board(leg, sport_key=leg.get("sport_key") or ""):
            continue
        league = (leg.get("league_id") or "").strip().lower()
        if league and league in leagues_with_live:
            if not leg_on_todays_live_board(leg, live):
                continue
        kept.append(leg)
    return kept


def filter_upcoming_public_combos(combos, fixtures=None):
    """Drop combo cards that still contain a finished or missing fixture."""
    live = list(fixtures) if fixtures is not None else todays_live_fixtures()
    out = {}
    for key, items in (combos or {}).items():
        kept = []
        for combo in items or []:
            if not isinstance(combo, dict):
                continue
            legs = [lg for lg in (combo.get("legs") or []) if isinstance(lg, dict)]
            if not legs:
                continue
            filtered = filter_upcoming_public_legs(legs, fixtures=live)
            if len(filtered) != len(legs):
                continue
            kept.append(combo)
        out[key] = kept
    return out


def _norm_team_token(value) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _name_hits(needle: str, names) -> bool:
    n = _norm_team_token(needle)
    if len(n) < 2:
        return False
    for raw in names or []:
        h = _norm_team_token(raw)
        if not h:
            continue
        if n == h or n in h or h in n:
            return True
    return False


def _split_matchup_names(matchup: str) -> tuple[str, str]:
    text = (matchup or "").strip()
    low = text.lower()
    for sep in (" vs. ", " vs ", " @ ", " v "):
        idx = low.find(sep)
        if idx >= 0:
            return text[:idx].strip(), text[idx + len(sep):].strip()
    return text, ""


def _fx_row(*, sport_key, league_id, home_names, away_names, kickoff_iso="", completed=False, is_live=False):
    return {
        "sport_key": sport_key,
        "league_id": league_id,
        "home_names": [n for n in (home_names or []) if n],
        "away_names": [n for n in (away_names or []) if n],
        "kickoff_iso": kickoff_iso or "",
        "completed": bool(completed),
        "is_live": bool(is_live),
        "status": "live" if is_live else ("final" if completed else "upcoming"),
    }


def _soccer_today_fixtures(league_id: str, tz_name: str) -> list[dict]:
    try:
        import app as flask_app
        df = flask_app.load_predictions(league_id)
    except Exception:
        return []
    if df is None or getattr(df, "empty", True):
        return []
    today = today_in_tz(tz_name)
    out = []
    for _, row in df.iterrows():
        kickoff = row.get("DateUtc")
        if local_date(kickoff, tz_name) != today:
            continue
        if fixture_is_finished(
            {"kickoff_iso": str(kickoff or ""), "sport_key": "soccer"},
            sport_key="soccer",
        ):
            continue
        out.append(_fx_row(
            sport_key="soccer",
            league_id=league_id,
            home_names=[row.get("HomeTeam")],
            away_names=[row.get("AwayTeam")],
            kickoff_iso=str(kickoff or ""),
        ))
    return out


def _mlb_today_fixtures(league_id: str, tz_name: str) -> list[dict]:
    import mlb_fixtures
    today = today_in_tz(tz_name)
    out = []
    for m in mlb_fixtures.get_fixtures() or []:
        kickoff = m.get("date") or m.get("start_time_utc") or ""
        is_live = (m.get("status") or "") == "in"
        if local_date(kickoff, tz_name) != today and not is_live:
            continue
        out.append(_fx_row(
            sport_key="baseball",
            league_id=league_id,
            home_names=[m.get("home_team"), m.get("home_abbr")],
            away_names=[m.get("away_team"), m.get("away_abbr")],
            kickoff_iso=str(kickoff or ""),
            completed=bool(m.get("completed")),
            is_live=is_live,
        ))
    return out


def _nfl_today_fixtures(league_id: str, tz_name: str) -> list[dict]:
    import nfl_fixtures
    today = today_in_tz(tz_name)
    out = []
    for m in nfl_fixtures.get_fixtures() or []:
        kickoff = m.get("date") or m.get("start_time_utc") or ""
        is_live = (m.get("status") or "") == "in"
        if local_date(kickoff, tz_name) != today and not is_live:
            continue
        out.append(_fx_row(
            sport_key="american_football",
            league_id=league_id,
            home_names=[m.get("home_team"), m.get("home_abbr")],
            away_names=[m.get("away_team"), m.get("away_abbr")],
            kickoff_iso=str(kickoff or ""),
            completed=bool(m.get("completed")),
            is_live=is_live,
        ))
    return out


def _nba_today_fixtures(league_id: str, tz_name: str) -> list[dict]:
    import nba_fixtures
    today = today_in_tz(tz_name)
    out = []
    for m in nba_fixtures.get_fixtures() or []:
        kickoff = m.get("date") or ""
        is_live = (m.get("status") or "") == "in"
        if local_date(kickoff, tz_name) != today and not is_live:
            continue
        out.append(_fx_row(
            sport_key="basketball",
            league_id=league_id,
            home_names=[m.get("home_team"), m.get("home_abbr")],
            away_names=[m.get("away_team"), m.get("away_abbr")],
            kickoff_iso=str(kickoff or ""),
            completed=bool(m.get("completed")),
            is_live=is_live,
        ))
    return out


def _nhl_today_fixtures(league_id: str, tz_name: str) -> list[dict]:
    import nhl_fixtures
    today = today_in_tz(tz_name)
    out = []
    for m in nhl_fixtures.get_fixtures() or []:
        kickoff = m.get("date") or ""
        is_live = (m.get("status") or "") == "in"
        if local_date(kickoff, tz_name) != today and not is_live:
            continue
        out.append(_fx_row(
            sport_key="hockey",
            league_id=league_id,
            home_names=[m.get("home_team"), m.get("home_abbr")],
            away_names=[m.get("away_team"), m.get("away_abbr")],
            kickoff_iso=str(kickoff or ""),
            completed=bool(m.get("completed")),
            is_live=is_live,
        ))
    return out


def _npb_today_fixtures(league_id: str, tz_name: str) -> list[dict]:
    import npb_fixtures
    today = today_in_tz(tz_name)
    out = []
    for m in npb_fixtures.get_fixtures(resolve_pitchers=False) or []:
        kickoff = m.get("date") or ""
        is_live = bool(m.get("is_live"))
        if local_date(kickoff, tz_name) != today and not is_live:
            continue
        out.append(_fx_row(
            sport_key="baseball",
            league_id=league_id,
            home_names=[m.get("home_team"), m.get("home_team_id")],
            away_names=[m.get("away_team"), m.get("away_team_id")],
            kickoff_iso=str(kickoff or ""),
            completed=bool(m.get("completed")),
            is_live=is_live,
        ))
    return out


def _cricket_today_fixtures(league_id: str, tz_name: str) -> list[dict]:
    import cricket_predict
    competition = {
        "hundred": "hnd_male", "mlc": "mlc_male", "ipl": "ipl_male",
        "bbl": "bbl_male", "cpl": "cpl_male",
    }.get(league_id)
    if not competition:
        return []
    today = today_in_tz(tz_name)
    out = []
    for m in cricket_predict.upcoming_fixtures(competition) or []:
        day = (m.get("date") or "")[:10]
        if day != today:
            continue
        kickoff = m.get("start_time_utc") or (
            f"{day}T{m.get('time')}" if m.get("time") else day
        )
        out.append(_fx_row(
            sport_key="cricket",
            league_id=league_id,
            home_names=[m.get("team1")],
            away_names=[m.get("team2")],
            kickoff_iso=str(kickoff or ""),
            completed=bool(m.get("completed")),
        ))
    return out


def _tennis_today_fixtures(league_id: str, tz_name: str) -> list[dict]:
    import tennis_predict
    today = today_in_tz(tz_name)
    out = []
    for m in tennis_predict.upcoming_with_predictions() or []:
        day = (m.get("date") or "")[:10]
        is_live = bool(m.get("is_live"))
        if day != today and not is_live:
            continue
        kickoff = f"{day}T{m.get('time')}" if m.get("time") else day
        out.append(_fx_row(
            sport_key="tennis",
            league_id=league_id,
            home_names=[m.get("player1")],
            away_names=[m.get("player2")],
            kickoff_iso=str(kickoff or ""),
            completed=bool(m.get("completed")),
            is_live=is_live,
        ))
    return out


# league["tz"] (mostly Asia/Kolkata) is for *displaying* kickoff times to
# site users and is fine for that. But todays_live_fixtures() uses the same
# value to decide whether a game falls on "today" -- for sports that play
# late in their home evening, converting to IST can push the date into
# tomorrow, so most of today's games silently fail the day-match and get
# dropped from the live board (while the prediction pipeline, which is not
# IST-anchored, still finds them fine). Use each sport's own scheduling-day
# timezone here instead so the two stay consistent.
_FIXTURE_DAY_TZ = {
    "mlb": "America/New_York",
    "nfl": "America/New_York",
    "npb": "Asia/Tokyo",
    "nba": "America/New_York",
    "nhl": "America/New_York",
}


def todays_live_fixtures() -> list[dict]:
    """Upcoming fixtures actually on today's live feeds (not combo cache)."""
    loaders = {
        "soccer": _soccer_today_fixtures,
        "mlb": _mlb_today_fixtures,
        "npb": _npb_today_fixtures,
        "nfl": _nfl_today_fixtures,
        "nba": _nba_today_fixtures,
        "nhl": _nhl_today_fixtures,
        "cricket": _cricket_today_fixtures,
        "tennis": _tennis_today_fixtures,
    }
    out: list[dict] = []
    for sport_key, sport in TODAY_SPORTS.items():
        for league_id, league in (sport.get("leagues") or {}).items():
            kind = league.get("kind") or ""
            loader = loaders.get(kind)
            if not loader:
                continue
            tz_name = _FIXTURE_DAY_TZ.get(kind) or league.get("tz") or "UTC"
            try:
                rows = loader(league_id, tz_name)
            except Exception:
                rows = []
            for row in rows:
                if not fixture_is_upcoming_or_live(row, sport_key=sport_key):
                    continue
                out.append(row)
    return out


def leg_on_todays_live_board(leg, fixtures=None) -> bool:
    """True when this pick's match is a real upcoming fixture today."""
    if not isinstance(leg, dict):
        return False
    sport = (leg.get("sport_key") or "").strip()
    if fixture_is_finished(leg, sport_key=sport):
        return False
    kickoff = leg.get("kickoff_iso") or leg.get("date") or ""
    if (kickoff or leg.get("time")) and not fixture_is_upcoming_or_live(leg, sport_key=sport):
        return False
    fixtures = list(fixtures) if fixtures is not None else todays_live_fixtures()
    league = (leg.get("league_id") or "").strip().lower()
    if not league:
        return False
    home = (leg.get("home") or "").strip()
    away = (leg.get("away") or "").strip()
    if not home or not away:
        left, right = _split_matchup_names(leg.get("matchup") or "")
        home = home or left
        away = away or right
    if not home or not away:
        return False
    for fx in fixtures:
        if (fx.get("league_id") or "").strip().lower() != league:
            continue
        if fixture_is_finished(fx, sport_key=fx.get("sport_key") or ""):
            continue
        homes = fx.get("home_names") or []
        aways = fx.get("away_names") or []
        if _name_hits(home, homes) and _name_hits(away, aways):
            return True
        if _name_hits(home, aways) and _name_hits(away, homes):
            return True
    return False


def _pick(
    *,
    away,
    home,
    kickoff_iso="",
    when="",
    status="upcoming",
    status_label="",
    pick="",
    detail_rows=None,
    confidence=None,
    url=None,
    matchup=None,
    market="",
):
    return {
        "away": away,
        "home": home,
        "matchup": matchup or f"{home} vs {away}",
        "kickoff_iso": kickoff_iso or "",
        "when": when or "",
        "status": status,
        "status_label": status_label or status.title(),
        "pick": pick or "",
        "market": market or "",
        "detail_rows": detail_rows or [],
        "confidence": confidence,
        "url": url,
    }


def _strongest_pick_per_matchup(picks):
    """One public Today's Picks card per fixture — keep the strongest market."""
    best = {}
    order = []
    for p in picks:
        key = (p.get("matchup") or "").strip().lower()
        if not key:
            key = f"{p.get('home') or ''}|{p.get('away') or ''}|{p.get('kickoff_iso') or ''}"
        if key not in best:
            order.append(key)
            best[key] = p
        elif float(p.get("confidence") or 0) > float(best[key].get("confidence") or 0):
            best[key] = p
    return [best[k] for k in order]


def select_high_confidence(picks, *, min_conf=HIGH_CONF_MIN, max_n=MAX_PICKS_PER_LEAGUE):
    """Keep actionable high-confidence bets only; strongest first; hard cap."""
    eligible = [
        p for p in picks
        if p.get("status") not in ("final", "completed")
        and not fixture_is_finished(p)
        and p.get("confidence") is not None
        and float(p["confidence"]) >= min_conf
        and p.get("pick") not in ("", "No rating yet", "No strong lean")
    ]
    eligible.sort(key=lambda p: float(p["confidence"] or 0), reverse=True)
    return eligible[:max_n]


def _safer_result_tip(
    row,
    *,
    league_key=None,
    t_dc=None,
    t_win=None,
    calibration=None,
):
    """Build one exact-win or double-chance Safer Result tip.

    Returns ``None`` when the league lacks evaluation support or the fixture
    does not clear the calibrated fixed bars.
    """
    home = row["HomeTeam"]
    away = row["AwayTeam"]
    p_home = float(row.get("P_Home_Win") or 0)
    p_draw = float(row.get("P_Draw") or 0)
    p_away = float(row.get("P_Away_Win") or 0)
    p_fav = row.get("P_Favorite_Avoids_Defeat")
    fav_team = row.get("Favorite_Team")
    if p_fav is None or (isinstance(p_fav, float) and pd.isna(p_fav)):
        return None
    p_fav = float(p_fav)
    if fav_team is None or (isinstance(fav_team, float) and pd.isna(fav_team)):
        fav_team = home if p_home >= p_away else away
    if league_key:
        thresholds = load_soccer_thresholds(league_key)
        if not thresholds.get("available", {}).get("safer_result"):
            return None
        if t_dc is None:
            t_dc = thresholds.get("markets", {}).get("safer_result")
        if t_win is None:
            t_win = thresholds.get("safer_result_win_threshold")
        if calibration is None:
            calibration = thresholds.get("safer_result_calibration")
    if t_dc is None:
        t_dc = safer_result.DOUBLE_CHANCE_THRESHOLD
    if t_win is None:
        t_win = safer_result.EXACT_WIN_THRESHOLD
    return safer_result.select_safer_result(
        home_team=home,
        away_team=away,
        favorite_team=fav_team,
        p_home_win=p_home,
        p_draw=p_draw,
        p_away_win=p_away,
        p_favorite_avoids_defeat=p_fav,
        calibration=calibration,
        double_chance_threshold=t_dc,
        exact_win_threshold=t_win,
    )


def _soccer_high_confidence_tips(row, league_key=None):
    """High-probability market tips from full soccer analysis.

    Includes Safer Result (thresholded 1X2/DC), Draw No Bet, BTTS, goals,
    corners (team + total), and team-to-score leans. Callers apply
    tip_min_conf floors — this function only builds candidate tips.
    """
    tips = []
    home = row["HomeTeam"]
    away = row["AwayTeam"]
    p_home = float(row.get("P_Home_Win") or 0)
    p_draw = float(row.get("P_Draw") or 0)
    p_away = float(row.get("P_Away_Win") or 0)
    p_btts = float(row.get("P_BTTS_Yes") or 0)
    p_over = float(row.get("P_Over_2.5_Goals") or 0)

    safer_tip = _safer_result_tip(row, league_key=league_key)
    if safer_tip:
        tips.append(safer_tip)

    # Draw No Bet: favourite vs underdog (draw voids). High when fav dominates.
    fav_team = row.get("Favorite_Team")
    if fav_team is None or (isinstance(fav_team, float) and pd.isna(fav_team)):
        fav_team = home if p_home >= p_away else away
    fav_team = str(fav_team)
    home_is_fav = fav_team == str(home)
    p_fav_win = p_home if home_is_fav else p_away
    p_dog_win = p_away if home_is_fav else p_home
    denom = p_fav_win + p_dog_win
    if denom >= 0.35 and p_fav_win >= p_dog_win:
        p_dnb = _shrink_favorite_cover_prob(p_fav_win / denom)
        # Skip if Safer Result already is the same outright win tip.
        already_win = any(
            t.get("market") == safer_result.MARKET_NAME
            and t.get("pick") == f"{fav_team} win"
            for t in tips
        )
        if not already_win:
            tips.append({
                "market": "Draw No Bet",
                "pick": f"{fav_team} (DNB)",
                "probability": p_dnb,
            })

    dog_ah = _soccer_dog_handicap_tip(home, away, p_home, p_draw, p_away, fav_team)
    if dog_ah:
        tips.append(dog_ah)

    conf_btts = max(p_btts, 1.0 - p_btts)
    tips.append({
        "market": "Both Teams to Score",
        "pick": "Yes" if p_btts >= 0.5 else "No",
        "probability": conf_btts,
    })

    conf_over = max(p_over, 1.0 - p_over)
    if p_over >= 0.5:
        tips.append({
            "market": "Total Goals",
            "pick": "Over 2.5",
            "probability": conf_over,
        })
    else:
        tips.append({
            "market": "Total Goals",
            "pick": "Under 2.5",
            "probability": _shrink_under_prob(conf_over),
        })

    p_corners = row.get("P_Home_More_Corners")
    if (
        p_corners is not None
        and str(p_corners).strip() != ""
        and not (isinstance(p_corners, float) and pd.isna(p_corners))
    ):
        p_corners = float(p_corners)
        conf_c = max(p_corners, 1.0 - p_corners)
        pick = f"{home} (more corners)" if p_corners >= 0.5 else f"{away} (more corners)"
        tips.append({"market": "Corners", "pick": pick, "probability": conf_c})

    exp_corners = row.get("Exp_Total_Corners")
    if exp_corners is not None and str(exp_corners).strip() != "" and not (
        isinstance(exp_corners, float) and pd.isna(exp_corners)
    ):
        ou = _normal_ou(float(exp_corners), std_dev=3.2)
        if ou:
            tips.append({
                "market": "Total Corners",
                "pick": f"{ou['pick']} {ou['line']}",
                "probability": ou["probability"],
            })

    # Team to score — derived from 1X2 + BTTS (no separate model).
    # P(home scores) ≈ H + D*0.8 + A*P(BTTS); same mirrored for away.
    p_home_score = min(0.97, p_home + 0.80 * p_draw + p_away * p_btts)
    p_away_score = min(0.97, p_away + 0.80 * p_draw + p_home * p_btts)
    if p_home_score >= p_away_score:
        tips.append({
            "market": "Team to Score",
            "pick": f"{home} to score",
            "probability": p_home_score,
        })
    else:
        tips.append({
            "market": "Team to Score",
            "pick": f"{away} to score",
            "probability": p_away_score,
        })

    return tips


def _data_confidence_ok(row):
    raw = row.get("Data_Confidence")
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return True
    return "low" not in str(raw).lower()


def _best_soccer_tip(tips_with_bars):
    """Pick one market without comparing unsupported scores as equals.

    A market with measured calibration evidence outranks a soft/derived
    market; probability only ranks candidates within the same evidence tier.
    """
    if not tips_with_bars:
        return None, []
    ranked = sorted(
        tips_with_bars,
        key=lambda item: (not bool(item[2]), float(item[0]["probability"])),
        reverse=True,
    )
    return ranked[0], ranked[1:]


def build_soccer_today(league_key, df, tz_name, *, max_n=MAX_PICKS_PER_LEAGUE, min_conf=None):
    """High-probability soccer tips for the league's local today.

    At most one card is emitted per fixture. Best Combos therefore cannot
    duplicate a soccer match through different markets.
    """
    floor = effective_high_prob_min() if min_conf is None else float(min_conf)
    today = today_in_tz(tz_name)
    candidates = []
    fixtures_today = 0
    if df is None or df.empty:
        return today, candidates, fixtures_today

    thr = load_soccer_thresholds(league_key)
    calibrated = bool(thr.get("calibrated"))

    for _, row in df.iterrows():
        kickoff = row.get("DateUtc")
        if local_date(kickoff, tz_name) != today:
            continue
        fixtures_today += 1
        if fixture_is_finished(
            {"kickoff_iso": str(kickoff or ""), "sport_key": "soccer"},
            sport_key="soccer",
        ):
            continue
        if not _data_confidence_ok(row):
            continue

        home, away = row["HomeTeam"], row["AwayTeam"]
        url = f"/{league_key}/match/{quote(str(home))}/{quote(str(away))}"
        when = row.get("MatchDate") or str(kickoff or "")[:16]

        qualified = []
        for tip in _soccer_high_confidence_tips(row, league_key=league_key):
            wise_bar = tip_min_conf(league_key, tip["market"])
            bar = _combo_gather_bar(floor, wise_bar, min_conf=min_conf)
            prob = float(tip["probability"])
            if prob < bar:
                continue
            wise = market_min_conf(league_key, tip["market"]) is None
            qualified.append((tip, bar, wise))

        selected, _alternatives = _best_soccer_tip(qualified)
        if selected:
            tip, bar, wise = selected
            prob = float(tip["probability"])
            candidates.append(_pick(
                away=away,
                home=home,
                matchup=f"{home} vs {away}",
                kickoff_iso=str(kickoff or ""),
                when=when,
                status="upcoming",
                status_label=_tier_status_label(calibrated and not wise, wise=wise),
                pick=tip["pick"],
                market=tip["market"],
                detail_rows=[
                    {"label": "Market", "value": tip["market"]},
                    {"label": "Prob", "value": f"{prob * 100:.0f}%"},
                    {"label": "Bar", "value": f"≥ {bar * 100:.0f}%"},
                ],
                confidence=prob,
                url=url,
            ))

    picks = select_high_confidence(candidates, min_conf=floor, max_n=max_n)
    return today, picks, fixtures_today


def _baseball_market_tips(
    pred,
    *,
    home_name,
    away_name,
    include_hits_so=True,
    include_moneyline=True,
    include_upset_lean=False,
):
    """Tips from an MLB/NPB prediction dict.

    Moneyline is optional: MLB Today's Picks skips favorite moneyline (coin-flip
    full slate) and can instead emit an Upset lean when the game is close.
    """
    tips = []
    if not pred:
        return tips

    home_p = float(pred.get("home_prob") or 0)
    away_p = float(pred.get("away_prob") or 0)
    if include_moneyline:
        # Combo-only market (never shown on the public baseball Today's Picks
        # page). Left unshrunk, unlike other sports' favorite-covers: the
        # dog run line already covers the ultra-safe side, so Moneyline here
        # is meant to be the genuine, medium-risk team-to-win leg — shrinking
        # it to the same "lock" bar as other sports left it clearing almost
        # nothing and ceded every combo slot to the +1.5 run line.
        if home_p >= away_p:
            tips.append({
                "market": "Moneyline",
                "pick": home_name,
                "probability": home_p,
            })
        else:
            tips.append({
                "market": "Moneyline",
                "pick": away_name,
                "probability": away_p,
            })

    if include_upset_lean:
        if home_p >= away_p:
            fav_p, dog_p, dog_name = home_p, away_p, away_name
        else:
            fav_p, dog_p, dog_name = away_p, home_p, home_name
        if fav_p <= BASEBALL_COINFLIP_FAV_MAX and dog_p >= BASEBALL_UPSET_LEAN_MIN and dog_name:
            tips.append({
                "market": "Upset lean",
                "pick": dog_name,
                "probability": dog_p,
            })

    if not include_moneyline:
        # Best Combos already carries Moneyline (medium-risk team-to-win);
        # keep the ultra-safe dog run line off combo slips so it doesn't
        # crowd Moneyline out. Still shown on the public Today's Picks page.
        dog_hc = _baseball_dog_handicap_tip(pred, home_name, away_name)
        if dog_hc:
            tips.append(dog_hc)

    total_p = _pct_to_prob(pred.get("total_runs_pick_probability"))
    if total_p is not None and pred.get("total_runs_pick") and pred.get("total_runs_line") is not None:
        pick = f"{pred['total_runs_pick']} {pred['total_runs_line']}"
        if _is_under_pick(pred["total_runs_pick"]):
            total_p = _shrink_under_prob(total_p)
        tips.append({
            "market": "Total runs",
            "pick": pick,
            "probability": total_p,
        })

    for name, pick_key, line_key, prob_key in (
        (home_name, "home_runs_pick", "home_runs_line", "home_runs_pick_probability"),
        (away_name, "away_runs_pick", "away_runs_line", "away_runs_pick_probability"),
    ):
        tp = _pct_to_prob(pred.get(prob_key))
        if tp is None or not pred.get(pick_key) or pred.get(line_key) is None:
            continue
        pick = f"{name} {pred[pick_key]} {pred[line_key]}"
        if _is_under_pick(pred[pick_key]) or _is_under_pick(pick):
            tp = _shrink_under_prob(tp)
        tips.append({
            "market": "Team runs",
            "pick": pick,
            "probability": tp,
        })

    if not include_hits_so:
        return tips

    # Hits + strikeouts only — skip total HRs (rarely offered as a game market).
    for market, point_key, std in (
        ("Total hits", "predicted_total_hits", 4.0),
        ("Total strikeouts", "predicted_total_strikeouts", 4.5),
    ):
        point = pred.get(point_key)
        if point is None:
            continue
        ou = _normal_ou(float(point), std_dev=std)
        if ou:
            tips.append({
                "market": market,
                "pick": f"{ou['pick']} {ou['line']}",
                "probability": ou["probability"],
            })
    return tips


def normalize_mlb_slate(slate, *, max_n=MAX_PICKS_PER_LEAGUE, min_conf=None):
    """Convert build_mlb_today_slate() into multi-market high-prob picks.

    Public Today's Picks skips favorite moneyline (coin-flip full slate) and
    can surface Upset leans. The Best Combos path (min_conf set) includes
    moneyline so slips are not totals-only.
    """
    floor = effective_high_prob_min() if min_conf is None else float(min_conf)
    today = slate.get("today_et") or today_in_tz("America/New_York")
    thr = load_sport_thresholds("mlb")
    calibrated = bool(thr.get("calibrated"))
    # Upset leans only on the public picks page — not in the combo pool.
    allow_upset = min_conf is None
    include_ml = min_conf is not None
    picks = []
    fixtures_today = 0
    for e in slate.get("board") or []:
        m = e["fixture"]
        fixtures_today += 1
        if m.get("status") == "in":
            status, status_label = "live", f"Live — {m.get('status_detail', '')} ({m.get('away_score')}-{m.get('home_score')})"
        elif m.get("completed"):
            status, status_label = "final", f"Final {m.get('away_score')}-{m.get('home_score')}"
        else:
            status, status_label = "upcoming", _tier_status_label(calibrated)

        pred = e.get("prediction")
        home_name = m.get("home_team", "")
        away_name = m.get("away_team", "")
        for tip in _baseball_market_tips(
            pred,
            home_name=home_name,
            away_name=away_name,
            include_moneyline=include_ml,
            include_upset_lean=allow_upset,
        ):
            if tip["market"] == "Upset lean":
                bar = BASEBALL_UPSET_LEAN_MIN
                wise = True
            else:
                wise_bar = tip_sport_min_conf("mlb", tip["market"])
                bar = _combo_gather_bar(floor, wise_bar, min_conf=min_conf)
                wise = sport_market_min_conf("mlb", tip["market"]) is None
            prob = float(tip["probability"])
            if status == "final" or prob < bar:
                continue
            detail = [
                {"label": "Market", "value": tip["market"]},
                {"label": "Prob", "value": f"{prob * 100:.0f}%"},
                {"label": "Bar", "value": f"≥ {bar * 100:.0f}%"},
            ]
            if tip["market"] == "Upset lean":
                detail.append({
                    "label": "Note",
                    "value": "Coin-flip game — underdog lean, not 70% tier",
                })
            picks.append(_pick(
                away=away_name,
                home=home_name,
                matchup=f"{home_name} vs {away_name}",
                kickoff_iso=m.get("date") or "",
                when=(m.get("date") or "")[:16],
                status=status,
                status_label=(
                    status_label if status != "upcoming"
                    else _tier_status_label(calibrated and not wise, wise=wise)
                ),
                pick=tip["pick"],
                market=tip["market"],
                detail_rows=detail,
                confidence=prob,
                url=e.get("match_url"),
            ))

    props = [p for p in picks if p["market"] != "Upset lean"]
    dogs = [p for p in picks if p["market"] == "Upset lean"]
    # Per-tip bars already applied; avoid re-filtering upset leans with 65% floor.
    selected = select_high_confidence(props, min_conf=0.0, max_n=max_n)
    if allow_upset and len(selected) < max_n:
        dog_slots = min(BASEBALL_MAX_UPSET_LEANS, max_n - len(selected))
        selected.extend(select_high_confidence(dogs, min_conf=0.0, max_n=dog_slots))
    return today, selected, fixtures_today


def _nfl_market_tips(
    pred,
    *,
    home_name,
    away_name,
    include_moneyline=True,
    include_upset_lean=False,
    include_favorite_spread=True,
):
    """Tips from an NFL prediction dict.

    Public Today's Picks skips favorite moneyline (coin-flip full slate) and
    can instead emit an Upset lean when the game is close. Combos include ML.
    """
    tips = []
    if not pred:
        return tips

    home_p = float(pred.get("home_prob") or 0)
    away_p = float(pred.get("away_prob") or 0)
    if include_moneyline:
        if home_p >= away_p:
            tips.append({
                "market": "Moneyline",
                "pick": home_name,
                "probability": _shrink_favorite_cover_prob(home_p),
            })
        else:
            tips.append({
                "market": "Moneyline",
                "pick": away_name,
                "probability": _shrink_favorite_cover_prob(away_p),
            })

    if include_upset_lean:
        if home_p >= away_p:
            fav_p, dog_p, dog_name = home_p, away_p, away_name
        else:
            fav_p, dog_p, dog_name = away_p, home_p, home_name
        if fav_p <= BASEBALL_COINFLIP_FAV_MAX and dog_p >= BASEBALL_UPSET_LEAN_MIN and dog_name:
            tips.append({
                "market": "Upset lean",
                "pick": dog_name,
                "probability": dog_p,
            })

    if include_favorite_spread:
        fav_spread_p = _pct_to_prob(pred.get("spread_pick_probability"))
        spread_pick = pred.get("spread_pick") or ""
        if fav_spread_p is not None and spread_pick:
            # Display team names, not ESPN abbreviations, on the public board.
            pick = spread_pick
            home_code = str(pred.get("home") or "")
            away_code = str(pred.get("away") or "")
            if home_code and pick.upper().startswith(home_code.upper()):
                pick = home_name + pick[len(home_code):]
            elif away_code and pick.upper().startswith(away_code.upper()):
                pick = away_name + pick[len(away_code):]
            tips.append({
                "market": "Spread",
                "pick": pick,
                "probability": _shrink_favorite_cover_prob(fav_spread_p),
            })
    else:
        dog_sp = _nfl_dog_spread_tip(pred, home_name, away_name)
        if dog_sp:
            tips.append(dog_sp)

    total_p = _pct_to_prob(pred.get("total_points_pick_probability"))
    if total_p is not None and pred.get("total_points_pick") and pred.get("total_points_line") is not None:
        pick = f"{pred['total_points_pick']} {pred['total_points_line']}"
        if _is_under_pick(pred["total_points_pick"]):
            total_p = _shrink_under_prob(total_p)
        tips.append({
            "market": "Total points",
            "pick": pick,
            "probability": total_p,
        })

    for name, pick_key, line_key, prob_key in (
        (home_name, "home_points_pick", "home_points_line", "home_points_pick_probability"),
        (away_name, "away_points_pick", "away_points_line", "away_points_pick_probability"),
    ):
        tp = _pct_to_prob(pred.get(prob_key))
        if tp is None or not pred.get(pick_key) or pred.get(line_key) is None:
            continue
        pick = f"{name} {pred[pick_key]} {pred[line_key]}"
        if _is_under_pick(pred[pick_key]) or _is_under_pick(pick):
            tp = _shrink_under_prob(tp)
        tips.append({
            "market": "Team points",
            "pick": pick,
            "probability": tp,
        })
    return tips


def normalize_nfl_slate(slate, *, max_n=MAX_PICKS_PER_LEAGUE, min_conf=None):
    """Convert build_nfl_today_slate() into multi-market high-prob picks."""
    floor = effective_high_prob_min() if min_conf is None else float(min_conf)
    today = slate.get("today_et") or today_in_tz("America/New_York")
    thr = load_sport_thresholds("nfl")
    calibrated = bool(thr.get("calibrated"))
    allow_upset = min_conf is None
    include_ml = min_conf is not None
    picks = []
    fixtures_today = 0
    for e in slate.get("board") or []:
        m = e["fixture"]
        fixtures_today += 1
        if m.get("status") == "in":
            status, status_label = "live", f"Live — {m.get('status_detail', '')} ({m.get('away_score')}-{m.get('home_score')})"
        elif m.get("completed"):
            status, status_label = "final", f"Final {m.get('away_score')}-{m.get('home_score')}"
        else:
            status, status_label = "upcoming", _tier_status_label(calibrated)

        pred = e.get("prediction")
        home_name = m.get("home_team", "")
        away_name = m.get("away_team", "")
        for tip in _nfl_market_tips(
            pred,
            home_name=home_name,
            away_name=away_name,
            include_moneyline=include_ml,
            include_upset_lean=allow_upset,
            include_favorite_spread=include_ml,
        ):
            if tip["market"] == "Upset lean":
                bar = BASEBALL_UPSET_LEAN_MIN
                wise = True
            else:
                wise_bar = tip_sport_min_conf("nfl", tip["market"])
                bar = _combo_gather_bar(floor, wise_bar, min_conf=min_conf)
                wise = sport_market_min_conf("nfl", tip["market"]) is None
            prob = float(tip["probability"])
            if status == "final" or prob < bar:
                continue
            detail = [
                {"label": "Market", "value": tip["market"]},
                {"label": "Prob", "value": f"{prob * 100:.0f}%"},
                {"label": "Bar", "value": f"≥ {bar * 100:.0f}%"},
            ]
            if tip["market"] == "Upset lean":
                detail.append({
                    "label": "Note",
                    "value": "Coin-flip game — underdog lean, not 70% tier",
                })
            picks.append(_pick(
                away=away_name,
                home=home_name,
                matchup=f"{home_name} vs {away_name}",
                kickoff_iso=m.get("date") or "",
                when=(m.get("date") or "")[:16],
                status=status,
                status_label=(
                    status_label if status != "upcoming"
                    else _tier_status_label(calibrated and not wise, wise=wise)
                ),
                pick=tip["pick"],
                market=tip["market"],
                detail_rows=detail,
                confidence=prob,
                url=e.get("match_url"),
            ))

    props = [p for p in picks if p["market"] != "Upset lean"]
    dogs = [p for p in picks if p["market"] == "Upset lean"]
    selected = select_high_confidence(props, min_conf=0.0, max_n=max_n)
    if allow_upset and len(selected) < max_n:
        dog_slots = min(BASEBALL_MAX_UPSET_LEANS, max_n - len(selected))
        selected.extend(select_high_confidence(dogs, min_conf=0.0, max_n=dog_slots))
    return today, selected, fixtures_today


def _nba_market_tips(
    pred,
    *,
    home_name,
    away_name,
    include_moneyline=True,
    include_upset_lean=False,
):
    """Tips from an NBA prediction dict -- same shape as _baseball_market_tips,
    Moneyline + Total/Team points instead of runs/hits/strikeouts (no NBA
    analog for those from a schedule-only data source)."""
    tips = []
    if not pred:
        return tips

    home_p = float(pred.get("home_prob") or 0)
    away_p = float(pred.get("away_prob") or 0)
    if include_moneyline:
        if home_p >= away_p:
            tips.append({
                "market": "Moneyline",
                "pick": home_name,
                "probability": _shrink_favorite_cover_prob(home_p),
            })
        else:
            tips.append({
                "market": "Moneyline",
                "pick": away_name,
                "probability": _shrink_favorite_cover_prob(away_p),
            })

    if include_upset_lean:
        if home_p >= away_p:
            fav_p, dog_p, dog_name = home_p, away_p, away_name
        else:
            fav_p, dog_p, dog_name = away_p, home_p, home_name
        if fav_p <= BASEBALL_COINFLIP_FAV_MAX and dog_p >= BASEBALL_UPSET_LEAN_MIN and dog_name:
            tips.append({
                "market": "Upset lean",
                "pick": dog_name,
                "probability": dog_p,
            })

    total_p = _pct_to_prob(pred.get("total_points_pick_probability"))
    if total_p is not None and pred.get("total_points_pick") and pred.get("total_points_line") is not None:
        pick = f"{pred['total_points_pick']} {pred['total_points_line']}"
        if _is_under_pick(pred["total_points_pick"]):
            total_p = _shrink_under_prob(total_p)
        tips.append({
            "market": "Total points",
            "pick": pick,
            "probability": total_p,
        })

    for name, pick_key, line_key, prob_key in (
        (home_name, "home_points_pick", "home_points_line", "home_points_pick_probability"),
        (away_name, "away_points_pick", "away_points_line", "away_points_pick_probability"),
    ):
        tp = _pct_to_prob(pred.get(prob_key))
        if tp is None or not pred.get(pick_key) or pred.get(line_key) is None:
            continue
        pick = f"{name} {pred[pick_key]} {pred[line_key]}"
        if _is_under_pick(pred[pick_key]) or _is_under_pick(pick):
            tp = _shrink_under_prob(tp)
        tips.append({
            "market": "Team points",
            "pick": pick,
            "probability": tp,
        })
    return tips


def normalize_nba_slate(slate, *, max_n=MAX_PICKS_PER_LEAGUE, min_conf=None):
    """Convert build_nba_today_slate() into multi-market high-prob picks --
    same shape as normalize_mlb_slate/normalize_nfl_slate."""
    floor = effective_high_prob_min() if min_conf is None else float(min_conf)
    today = slate.get("today_et") or today_in_tz("America/New_York")
    thr = load_sport_thresholds("nba")
    calibrated = bool(thr.get("calibrated"))
    allow_upset = min_conf is None
    include_ml = min_conf is not None
    picks = []
    fixtures_today = 0
    for e in slate.get("board") or []:
        m = e["fixture"]
        fixtures_today += 1
        if m.get("status") == "in":
            status, status_label = "live", f"Live — {m.get('status_detail', '')} ({m.get('away_score')}-{m.get('home_score')})"
        elif m.get("completed"):
            status, status_label = "final", f"Final {m.get('away_score')}-{m.get('home_score')}"
        else:
            status, status_label = "upcoming", _tier_status_label(calibrated)

        pred = e.get("prediction")
        home_name = m.get("home_team", "")
        away_name = m.get("away_team", "")
        for tip in _nba_market_tips(
            pred,
            home_name=home_name,
            away_name=away_name,
            include_moneyline=include_ml,
            include_upset_lean=allow_upset,
        ):
            if tip["market"] == "Upset lean":
                bar = BASEBALL_UPSET_LEAN_MIN
                wise = True
            else:
                wise_bar = tip_sport_min_conf("nba", tip["market"])
                bar = _combo_gather_bar(floor, wise_bar, min_conf=min_conf)
                wise = sport_market_min_conf("nba", tip["market"]) is None
            prob = float(tip["probability"])
            if status == "final" or prob < bar:
                continue
            detail = [
                {"label": "Market", "value": tip["market"]},
                {"label": "Prob", "value": f"{prob * 100:.0f}%"},
                {"label": "Bar", "value": f"≥ {bar * 100:.0f}%"},
            ]
            if tip["market"] == "Upset lean":
                detail.append({
                    "label": "Note",
                    "value": "Coin-flip game — underdog lean, not 70% tier",
                })
            picks.append(_pick(
                away=away_name,
                home=home_name,
                matchup=f"{home_name} vs {away_name}",
                kickoff_iso=m.get("date") or "",
                when=(m.get("date") or "")[:16],
                status=status,
                status_label=(
                    status_label if status != "upcoming"
                    else _tier_status_label(calibrated and not wise, wise=wise)
                ),
                pick=tip["pick"],
                market=tip["market"],
                detail_rows=detail,
                confidence=prob,
                url=e.get("match_url"),
            ))

    props = [p for p in picks if p["market"] != "Upset lean"]
    dogs = [p for p in picks if p["market"] == "Upset lean"]
    selected = select_high_confidence(props, min_conf=0.0, max_n=max_n)
    if allow_upset and len(selected) < max_n:
        dog_slots = min(BASEBALL_MAX_UPSET_LEANS, max_n - len(selected))
        selected.extend(select_high_confidence(dogs, min_conf=0.0, max_n=dog_slots))
    return today, selected, fixtures_today


def _nhl_market_tips(
    pred,
    *,
    home_name,
    away_name,
    include_moneyline=True,
    include_upset_lean=False,
):
    """Tips from an NHL prediction dict -- same shape as _baseball_market_tips
    (moneyline optional + upset lean), goals instead of runs. NHL's real
    holdout accuracy (~56%) is close to MLB's, not NBA's, so it gets the
    same coin-flip-skip treatment as baseball rather than always-on
    moneyline like NBA/NFL."""
    tips = []
    if not pred:
        return tips

    home_p = float(pred.get("home_prob") or 0)
    away_p = float(pred.get("away_prob") or 0)
    if include_moneyline:
        if home_p >= away_p:
            tips.append({
                "market": "Moneyline",
                "pick": home_name,
                "probability": _shrink_favorite_cover_prob(home_p),
            })
        else:
            tips.append({
                "market": "Moneyline",
                "pick": away_name,
                "probability": _shrink_favorite_cover_prob(away_p),
            })

    if include_upset_lean:
        if home_p >= away_p:
            fav_p, dog_p, dog_name = home_p, away_p, away_name
        else:
            fav_p, dog_p, dog_name = away_p, home_p, home_name
        if fav_p <= BASEBALL_COINFLIP_FAV_MAX and dog_p >= BASEBALL_UPSET_LEAN_MIN and dog_name:
            tips.append({
                "market": "Upset lean",
                "pick": dog_name,
                "probability": dog_p,
            })

    total_p = _pct_to_prob(pred.get("total_goals_pick_probability"))
    if total_p is not None and pred.get("total_goals_pick") and pred.get("total_goals_line") is not None:
        pick = f"{pred['total_goals_pick']} {pred['total_goals_line']}"
        if _is_under_pick(pred["total_goals_pick"]):
            total_p = _shrink_under_prob(total_p)
        tips.append({
            "market": "Total goals",
            "pick": pick,
            "probability": total_p,
        })

    for name, pick_key, line_key, prob_key in (
        (home_name, "home_goals_pick", "home_goals_line", "home_goals_pick_probability"),
        (away_name, "away_goals_pick", "away_goals_line", "away_goals_pick_probability"),
    ):
        tp = _pct_to_prob(pred.get(prob_key))
        if tp is None or not pred.get(pick_key) or pred.get(line_key) is None:
            continue
        pick = f"{name} {pred[pick_key]} {pred[line_key]}"
        if _is_under_pick(pred[pick_key]) or _is_under_pick(pick):
            tp = _shrink_under_prob(tp)
        tips.append({
            "market": "Team goals",
            "pick": pick,
            "probability": tp,
        })
    return tips


def normalize_nhl_slate(slate, *, max_n=MAX_PICKS_PER_LEAGUE, min_conf=None):
    """Convert build_nhl_today_slate() into multi-market high-prob picks --
    same shape as normalize_mlb_slate."""
    floor = effective_high_prob_min() if min_conf is None else float(min_conf)
    today = slate.get("today_et") or today_in_tz("America/New_York")
    thr = load_sport_thresholds("nhl")
    calibrated = bool(thr.get("calibrated"))
    allow_upset = min_conf is None
    include_ml = min_conf is not None
    picks = []
    fixtures_today = 0
    for e in slate.get("board") or []:
        m = e["fixture"]
        fixtures_today += 1
        if m.get("status") == "in":
            status, status_label = "live", f"Live — {m.get('status_detail', '')} ({m.get('away_score')}-{m.get('home_score')})"
        elif m.get("completed"):
            status, status_label = "final", f"Final {m.get('away_score')}-{m.get('home_score')}"
        else:
            status, status_label = "upcoming", _tier_status_label(calibrated)

        pred = e.get("prediction")
        home_name = m.get("home_team", "")
        away_name = m.get("away_team", "")
        for tip in _nhl_market_tips(
            pred,
            home_name=home_name,
            away_name=away_name,
            include_moneyline=include_ml,
            include_upset_lean=allow_upset,
        ):
            if tip["market"] == "Upset lean":
                bar = BASEBALL_UPSET_LEAN_MIN
                wise = True
            else:
                wise_bar = tip_sport_min_conf("nhl", tip["market"])
                bar = _combo_gather_bar(floor, wise_bar, min_conf=min_conf)
                wise = sport_market_min_conf("nhl", tip["market"]) is None
            prob = float(tip["probability"])
            if status == "final" or prob < bar:
                continue
            detail = [
                {"label": "Market", "value": tip["market"]},
                {"label": "Prob", "value": f"{prob * 100:.0f}%"},
                {"label": "Bar", "value": f"≥ {bar * 100:.0f}%"},
            ]
            if tip["market"] == "Upset lean":
                detail.append({
                    "label": "Note",
                    "value": "Coin-flip game — underdog lean, not 70% tier",
                })
            picks.append(_pick(
                away=away_name,
                home=home_name,
                matchup=f"{home_name} vs {away_name}",
                kickoff_iso=m.get("date") or "",
                when=(m.get("date") or "")[:16],
                status=status,
                status_label=(
                    status_label if status != "upcoming"
                    else _tier_status_label(calibrated and not wise, wise=wise)
                ),
                pick=tip["pick"],
                market=tip["market"],
                detail_rows=detail,
                confidence=prob,
                url=e.get("match_url"),
            ))

    props = [p for p in picks if p["market"] != "Upset lean"]
    dogs = [p for p in picks if p["market"] == "Upset lean"]
    selected = select_high_confidence(props, min_conf=0.0, max_n=max_n)
    if allow_upset and len(selected) < max_n:
        dog_slots = min(BASEBALL_MAX_UPSET_LEANS, max_n - len(selected))
        selected.extend(select_high_confidence(dogs, min_conf=0.0, max_n=dog_slots))
    return today, selected, fixtures_today


def build_npb_today(
    compute_prediction, get_fixtures, load_elo, tz_name="Asia/Tokyo",
    *, max_n=MAX_PICKS_PER_LEAGUE, min_conf=None,
):
    from urllib.parse import quote as q

    floor = effective_high_prob_min() if min_conf is None else float(min_conf)
    today = today_in_tz(tz_name)
    fixtures = get_fixtures()
    elo_df = load_elo()
    elo_teams = set(elo_df["TeamID"]) if not elo_df.empty else set()
    thr = load_sport_thresholds("npb")
    calibrated = bool(thr.get("calibrated"))
    allow_upset = min_conf is None
    include_ml = min_conf is not None
    picks = []
    fixtures_today = 0

    for m in fixtures:
        day = local_date(m.get("date") or "", tz_name)
        is_live = bool(m.get("is_live"))
        if day != today and not is_live:
            continue
        fixtures_today += 1

        if m.get("completed"):
            status, status_label = "final", f"Final {m.get('away_score')}-{m.get('home_score')}"
        elif is_live:
            status, status_label = "live", "Live"
        elif m.get("postponed"):
            status, status_label = "upcoming", "Postponed"
        else:
            status, status_label = "upcoming", _tier_status_label(calibrated)

        home_id, away_id = m.get("home_team_id"), m.get("away_team_id")
        has_rating = home_id in elo_teams and away_id in elo_teams
        if not has_rating or status == "final":
            continue

        pred = compute_prediction(home_id, away_id, as_of_date=(m.get("date") or "")[:10] or None)
        if pred is None:
            continue
        url = (
            f"/npb/match/{home_id}/{away_id}"
            f"?home_name={q(m.get('home_team') or '')}"
            f"&away_name={q(m.get('away_team') or '')}"
            f"&date={q(m.get('date') or '')}"
        )
        home_name = m.get("home_team", "")
        away_name = m.get("away_team", "")
        for tip in _baseball_market_tips(
            pred,
            home_name=home_name,
            away_name=away_name,
            include_hits_so=False,
            include_moneyline=include_ml,
            include_upset_lean=allow_upset,
        ):
            if tip["market"] == "Upset lean":
                bar = BASEBALL_UPSET_LEAN_MIN
                wise = True
            else:
                wise_bar = tip_sport_min_conf("npb", tip["market"])
                bar = _combo_gather_bar(floor, wise_bar, min_conf=min_conf)
                wise = sport_market_min_conf("npb", tip["market"]) is None
            prob = float(tip["probability"])
            if prob < bar:
                continue
            detail = [
                {"label": "Market", "value": tip["market"]},
                {"label": "Prob", "value": f"{prob * 100:.0f}%"},
                {"label": "Bar", "value": f"≥ {bar * 100:.0f}%"},
            ]
            if tip["market"] == "Upset lean":
                detail.append({
                    "label": "Note",
                    "value": "Coin-flip game — underdog lean, not 70% tier",
                })
            picks.append(_pick(
                away=away_name,
                home=home_name,
                matchup=f"{home_name} vs {away_name}",
                kickoff_iso=m.get("date") or "",
                when=(m.get("date") or "")[:16],
                status=status,
                status_label=(
                    status_label if status != "upcoming"
                    else _tier_status_label(calibrated and not wise, wise=wise)
                ),
                pick=tip["pick"],
                market=tip["market"],
                detail_rows=detail,
                confidence=prob,
                url=url,
            ))

    props = [p for p in picks if p["market"] != "Upset lean"]
    dogs = [p for p in picks if p["market"] == "Upset lean"]
    selected = select_high_confidence(props, min_conf=0.0, max_n=max_n)
    if allow_upset and len(selected) < max_n:
        dog_slots = min(BASEBALL_MAX_UPSET_LEANS, max_n - len(selected))
        selected.extend(select_high_confidence(dogs, min_conf=0.0, max_n=dog_slots))
    return today, selected, fixtures_today


def build_cricket_today(league_slug, tz_name, *, max_n=MAX_PICKS_PER_LEAGUE, min_conf=None):
    import cricket_predict

    floor = effective_high_prob_min() if min_conf is None else float(min_conf)
    today = today_in_tz(tz_name)
    competition = {
        "hundred": "hnd_male", "mlc": "mlc_male", "ipl": "ipl_male",
        "bbl": "bbl_male", "cpl": "cpl_male",
    }.get(league_slug)
    thr = load_sport_thresholds(league_slug)
    calibrated = bool(thr.get("calibrated"))
    allow_upset = min_conf is None
    picks = []
    fixtures_today = 0
    if not competition:
        return today, picks, fixtures_today

    fixtures = cricket_predict.upcoming_fixtures(competition)
    for m in fixtures:
        day = m.get("date") or ""
        if day != today:
            continue
        fixtures_today += 1
        if m.get("completed") or fixture_is_finished(
            {"kickoff_iso": m.get("date") or "", "time": m.get("time") or "",
             "completed": m.get("completed"), "sport_key": "cricket"},
            sport_key="cricket",
        ):
            continue
        pred = m.get("prediction")
        data_conf = (pred or {}).get("data_confidence") or m.get("data_confidence") or ""
        if str(data_conf).lower() == "low":
            continue
        if not pred:
            continue

        t1 = m.get("team1", "")
        t2 = m.get("team2", "")
        t1p = float(pred.get("team1_win_prob") or 0)
        t2p = float(pred.get("team2_win_prob") or 0)
        tips = []
        # Public Today's Picks: skip favorite Match winner (coin-flip full slate),
        # optionally surface Upset leans. Best Combos path: include Match winner.
        if min_conf is not None:
            if t1p >= t2p:
                tips.append({
                    "market": "Match winner",
                    "pick": t1,
                    "probability": _shrink_favorite_cover_prob(t1p),
                })
            else:
                tips.append({
                    "market": "Match winner",
                    "pick": t2,
                    "probability": _shrink_favorite_cover_prob(t2p),
                })
        elif allow_upset:
            if t1p >= t2p:
                fav_p, dog_p, dog_name = t1p, t2p, t2
            else:
                fav_p, dog_p, dog_name = t2p, t1p, t1
            if fav_p <= COINFLIP_FAV_MAX and dog_p >= UPSET_LEAN_MIN and dog_name:
                tips.append({
                    "market": "Upset lean",
                    "pick": dog_name,
                    "probability": dog_p,
                })

        total = pred.get("predicted_total_runs")
        if total is not None:
            ou = _normal_ou(float(total), std_dev=18.0)
            if ou:
                tips.append({
                    "market": "Total runs",
                    "pick": f"{ou['pick']} {ou['line']}",
                    "probability": ou["probability"],
                })
        for team_key, name_key in (("predicted_team1_runs", "team1"), ("predicted_team2_runs", "team2")):
            point = pred.get(team_key)
            if point is None:
                continue
            ou = _normal_ou(float(point), std_dev=12.0)
            if ou:
                tips.append({
                    "market": "Team runs",
                    "pick": f"{m.get(name_key)} {ou['pick']} {ou['line']}",
                    "probability": ou["probability"],
                })

        url = None
        if league_slug in ("hundred", "ipl", "bbl", "cpl") and m.get("slug"):
            url = f"/{league_slug}/fixture/{m['slug']}"
        when = m.get("date") or ""
        if m.get("time"):
            when = f"{when} · {m['time']}"

        for tip in tips:
            if tip["market"] == "Upset lean":
                bar = UPSET_LEAN_MIN
                wise = True
            else:
                wise_bar = tip_sport_min_conf(league_slug, tip["market"])
                bar = _combo_gather_bar(floor, wise_bar, min_conf=min_conf)
                wise = sport_market_min_conf(league_slug, tip["market"]) is None
            prob = float(tip["probability"])
            if prob < bar:
                continue
            detail = [
                {"label": "Market", "value": tip["market"]},
                {"label": "Prob", "value": f"{prob * 100:.0f}%"},
                {"label": "Bar", "value": f"≥ {bar * 100:.0f}%"},
            ]
            if tip["market"] == "Upset lean":
                detail.append({
                    "label": "Note",
                    "value": "Coin-flip game — underdog lean, not 70% tier",
                })
            picks.append(_pick(
                away=t2,
                home=t1,
                matchup=f"{t1} vs {t2}",
                kickoff_iso=(
                    f"{(m.get('date') or '')[:10]}T{m.get('time')}"
                    if m.get("time") else (m.get("date") or "")
                ),
                when=when,
                status="upcoming",
                status_label=_tier_status_label(calibrated and not wise, wise=wise),
                pick=tip["pick"],
                market=tip["market"],
                detail_rows=detail,
                confidence=prob,
                url=url,
            ))

    props = [p for p in picks if p["market"] != "Upset lean"]
    dogs = [p for p in picks if p["market"] == "Upset lean"]
    selected = select_high_confidence(props, min_conf=0.0, max_n=max_n)
    if allow_upset and len(selected) < max_n:
        dog_slots = min(MAX_UPSET_LEANS, max_n - len(selected))
        selected.extend(select_high_confidence(dogs, min_conf=0.0, max_n=dog_slots))
    return today, selected, fixtures_today


def _tennis_market_tips(pred, *, player1: str, player2: str, include_upset_lean: bool = False):
    """Match winner + total games / total sets / win-a-set. No set or game handicap."""
    tips = []
    if not pred:
        return tips
    t1p = float(pred.get("player1_win_prob") or 0)
    t2p = float(pred.get("player2_win_prob") or 0)
    if t1p <= 0 and t2p <= 0:
        return tips

    if t1p >= t2p:
        fav_p, dog_p, fav_name, dog_name = t1p, t2p, player1, player2
    else:
        fav_p, dog_p, fav_name, dog_name = t2p, t1p, player2, player1

    tips.append({
        "market": "Match winner",
        "pick": fav_name,
        "probability": _shrink_favorite_cover_prob(fav_p),
    })
    if include_upset_lean and fav_p <= COINFLIP_FAV_MAX and dog_p >= UPSET_LEAN_MIN and dog_name:
        tips.append({"market": "Upset lean", "pick": dog_name, "probability": dog_p})

    mkt = pred.get("markets") or {}
    sets_line = float(mkt.get("sets_line") or (3.5 if int(pred.get("best_of") or 3) >= 5 else 2.5))
    p_over = mkt.get("over_sets_prob")
    if p_over is not None:
        p_over = float(p_over)
        if p_over >= 0.5:
            tips.append({
                "market": "Total sets",
                "pick": f"Over {sets_line:g}",
                "probability": p_over,
            })
        else:
            tips.append({
                "market": "Total sets",
                "pick": f"Under {sets_line:g}",
                "probability": _shrink_under_prob(1.0 - p_over),
            })

    for pname, key in ((player1, "player1_wins_set_prob"), (player2, "player2_wins_set_prob")):
        p_set = mkt.get(key)
        if p_set is None:
            continue
        p_set = float(p_set)
        # Favorite almost always "should" win a set — that 90% is pedigree,
        # not a lock (Draper 0-2 vs Landaluce). Prefer the dog covering a set
        # or a shrunk favorite. Never publish a 90% favorite set-winner.
        if pname == fav_name:
            p_set = _shrink_favorite_cover_prob(p_set)
            if p_set < 0.72:
                continue
        if pname == dog_name and p_set < 0.55:
            continue
        tips.append({
            "market": "Player to win a set",
            "pick": pname,
            "probability": p_set,
        })

    pred_games = mkt.get("predicted_total_games")
    if pred_games is not None:
        ou = _normal_ou(float(pred_games), std_dev=float(mkt.get("total_games_std") or 4.5))
        if ou:
            tips.append({
                "market": "Total games",
                "pick": f"{ou['pick']} {ou['line']:g}",
                "probability": ou["probability"],
            })
    return tips


def build_tennis_today(league_slug, tz_name, *, max_n=MAX_PICKS_PER_LEAGUE, min_conf=None):
    """ATP men's singles Today's Picks / combo legs (winner + total games / sets)."""
    import tennis_predict

    floor = effective_high_prob_min() if min_conf is None else float(min_conf)
    today = today_in_tz(tz_name)
    if league_slug != "atp":
        return today, [], 0

    thr = load_sport_thresholds("atp")
    calibrated = bool(thr.get("calibrated"))
    allow_upset = min_conf is None
    picks = []
    fixtures_today = 0

    fixtures = tennis_predict.upcoming_with_predictions()
    for m in fixtures:
        day = m.get("date") or ""
        is_live = bool(m.get("is_live"))
        if day != today and not is_live:
            continue
        fixtures_today += 1
        if (m.get("completed") and not is_live) or (
            not is_live and fixture_is_finished(
                {"kickoff_iso": m.get("date") or "", "time": m.get("time") or "",
                 "completed": m.get("completed"), "sport_key": "tennis"},
                sport_key="tennis",
            )
        ):
            continue
        pred = m.get("prediction") or {}
        if str(pred.get("data_confidence") or "").lower() == "low":
            continue
        t1 = m.get("player1") or ""
        t2 = m.get("player2") or ""

        tips = _tennis_market_tips(
            pred, player1=t1, player2=t2, include_upset_lean=allow_upset,
        )
        # Public board: only tip Match winner when it clears the high-prob floor
        # (combo gather keeps all markets that clear soft/calibrated bars).
        if min_conf is None:
            tips = [
                tip for tip in tips
                if tip["market"] != "Match winner" or float(tip["probability"]) >= floor
            ]

        url = f"/tennis/fixture/{m['slug']}" if m.get("slug") else "/tennis/fixtures"
        when = day
        if m.get("time"):
            when = f"{when} · {m['time']}"
        if m.get("tournament"):
            when = f"{when} · {m['tournament']}"

        for tip in tips:
            if tip["market"] == "Upset lean":
                bar = UPSET_LEAN_MIN
                wise = True
            else:
                wise_bar = tip_sport_min_conf("atp", tip["market"])
                bar = _combo_gather_bar(floor, wise_bar, min_conf=min_conf)
                wise = sport_market_min_conf("atp", tip["market"]) is None
            prob = float(tip["probability"])
            if prob < bar:
                continue
            detail = [
                {"label": "Market", "value": tip["market"]},
                {"label": "Prob", "value": f"{prob * 100:.0f}%"},
                {"label": "Bar", "value": f"≥ {bar * 100:.0f}%"},
                {"label": "Surface", "value": pred.get("surface") or m.get("surface") or "—"},
            ]
            if tip["market"] == "Upset lean":
                detail.append({"label": "Note", "value": "Close match — underdog lean, not 70% tier"})
            picks.append(_pick(
                away=t2,
                home=t1,
                matchup=f"{t1} vs {t2}",
                kickoff_iso=(
                    f"{(m.get('date') or '')[:10]}T{m.get('time')}"
                    if m.get("time") else (m.get("date") or "")
                ),
                when=when,
                status="live" if is_live else "upcoming",
                status_label="Live" if is_live else _tier_status_label(calibrated and not wise, wise=wise),
                pick=tip["pick"],
                market=tip["market"],
                detail_rows=detail,
                confidence=prob,
                url=url,
            ))

    props = [p for p in picks if p["market"] != "Upset lean"]
    dogs = [p for p in picks if p["market"] == "Upset lean"]
    if min_conf is None:
        props = _strongest_pick_per_matchup(props)
    selected = select_high_confidence(props, min_conf=0.0, max_n=max_n)
    if allow_upset and len(selected) < max_n:
        taken = {(p.get("matchup") or "").strip().lower() for p in selected}
        dogs = [
            d for d in dogs
            if (d.get("matchup") or "").strip().lower() not in taken
        ]
        dog_slots = min(MAX_UPSET_LEANS, max_n - len(selected))
        selected.extend(select_high_confidence(dogs, min_conf=0.0, max_n=dog_slots))
    return today, selected, fixtures_today


def _leg_match_key(leg):
    """Same fixture should not appear twice in one combo (any market)."""
    return (
        leg.get("sport_key"),
        leg.get("league_id"),
        (leg.get("matchup") or "").strip().lower(),
    )


def _leg_ou_side(leg):
    """Over / Under / other from the pick text (totals, team runs, goals, …)."""
    pick = leg.get("pick") or ""
    if re.search(r"(?i)\bunder\b", pick):
        return "under"
    if re.search(r"(?i)\bover\b", pick):
        return "over"
    return "other"


# Market pairs that tend to move together even on different fixtures
# (scoring environment / same-side result bias). Keys are lowercased markets.
_CORRELATED_MARKET_PAIRS = frozenset({
    frozenset({"both teams to score", "total goals"}),
    frozenset({"btts", "total goals"}),
    frozenset({"both teams to score", "goals"}),
    frozenset({"safer result", "draw no bet"}),
    frozenset({"safer result", "moneyline"}),
    frozenset({"safer result", "match winner"}),
    frozenset({"moneyline", "handicap"}),
    frozenset({"match winner", "handicap"}),
    frozenset({"total runs", "team runs"}),
    frozenset({"game total", "team runs"}),
    frozenset({"total", "team runs"}),
})

_CORR_LABEL_LOW = 0.25
_CORR_LABEL_HIGH = 0.45


def _norm_market(market: str | None) -> str:
    return (market or "").strip().lower()


def _leg_team_tokens(leg) -> set[str]:
    """Normalize team-ish tokens from matchup / home / away / pick."""
    parts = [
        leg.get("matchup") or "",
        leg.get("home") or "",
        leg.get("away") or "",
        leg.get("pick") or "",
    ]
    text = " ".join(parts).lower()
    text = re.sub(r"\b(vs\.?|@|or|draw|1x|x2|12|over|under|yes|no)\b", " ", text)
    text = re.sub(r"[^a-z0-9\s+.-]", " ", text)
    tokens = set()
    for tok in text.split():
        tok = tok.strip(".-+")
        if len(tok) < 2:
            continue
        if tok.replace(".", "", 1).isdigit():
            continue
        tokens.add(tok)
    return tokens


def _corr_label(score: float) -> str:
    if score >= _CORR_LABEL_HIGH:
        return "High"
    if score >= _CORR_LABEL_LOW:
        return "Medium"
    return "Low"


def pair_correlation(leg_a, leg_b) -> dict:
    """Heuristic 0–1 dependence score between two legs (any sport).

    Same-fixture is extreme; same league / market family / O-U side / known
    market pairs add medium risk. Cross-sport independent fixtures stay low.
    """
    reasons = []
    score = 0.0

    if _leg_match_key(leg_a) == _leg_match_key(leg_b):
        return {
            "score": 1.0,
            "label": "High",
            "reasons": ["Same fixture"],
        }

    sport_a = (leg_a.get("sport_key") or "").lower()
    sport_b = (leg_b.get("sport_key") or "").lower()
    league_a = (leg_a.get("league_id") or "").lower()
    league_b = (leg_b.get("league_id") or "").lower()

    if league_a and league_a == league_b:
        score += 0.18
        reasons.append("Same league")
    elif sport_a and sport_a == sport_b:
        score += 0.10
        reasons.append("Same sport")

    m_a = _norm_market(leg_a.get("market"))
    m_b = _norm_market(leg_b.get("market"))
    fam_a = _market_family(leg_a.get("market"))
    fam_b = _market_family(leg_b.get("market"))

    if m_a and m_a == m_b:
        score += 0.22
        reasons.append(f"Same market ({leg_a.get('market') or m_a})")
    elif fam_a == fam_b and fam_a != "other":
        score += 0.12
        reasons.append(f"Same market family ({fam_a})")

    if (
        m_a and m_b
        and sport_a and sport_a == sport_b
        and frozenset({m_a, m_b}) in _CORRELATED_MARKET_PAIRS
    ):
        score += 0.18
        reasons.append("Related markets")

    side_a = _leg_ou_side(leg_a)
    side_b = _leg_ou_side(leg_b)
    if side_a in ("over", "under") and side_a == side_b:
        # Same-side totals across a slate often share weather / pace / league scoring.
        bump = 0.16 if sport_a == sport_b else 0.08
        score += bump
        reasons.append(f"Same O/U side ({side_a})")

    shared = _leg_team_tokens(leg_a) & _leg_team_tokens(leg_b)
    # Require a meaningful token overlap (avoid tiny words already filtered).
    shared = {t for t in shared if len(t) >= 3}
    if shared:
        score += 0.35
        sample = ", ".join(sorted(shared)[:3])
        reasons.append(f"Shared team token ({sample})")

    score = min(1.0, max(0.0, score))
    return {
        "score": score,
        "label": _corr_label(score),
        "reasons": reasons or ["Independent fixtures"],
    }


def analyze_combo_correlation(legs) -> dict:
    """Pairwise correlation summary for a combo slip."""
    legs = list(legs or [])
    if len(legs) < 2:
        return {
            "score": 0.0,
            "avg_score": 0.0,
            "label": "Low",
            "reasons": [],
            "pairs": [],
            "note": "Single leg",
        }

    pairs = []
    for i in range(len(legs)):
        for j in range(i + 1, len(legs)):
            pc = pair_correlation(legs[i], legs[j])
            pairs.append({
                "i": i,
                "j": j,
                "score": pc["score"],
                "label": pc["label"],
                "reasons": pc["reasons"],
                "a_pick": legs[i].get("pick"),
                "b_pick": legs[j].get("pick"),
                "a_matchup": legs[i].get("matchup"),
                "b_matchup": legs[j].get("matchup"),
            })

    pairs.sort(key=lambda p: p["score"], reverse=True)
    max_score = float(pairs[0]["score"]) if pairs else 0.0
    avg_score = sum(p["score"] for p in pairs) / len(pairs) if pairs else 0.0
    # Emphasize the worst pair — that is the failure mode for the slip.
    blend = 0.7 * max_score + 0.3 * avg_score
    top_reasons = []
    for p in pairs[:3]:
        for r in p["reasons"]:
            if r not in top_reasons and r != "Independent fixtures":
                top_reasons.append(r)
            if len(top_reasons) >= 4:
                break
        if len(top_reasons) >= 4:
            break

    label = _corr_label(blend)
    if label == "Low":
        note = "Legs look largely independent"
    elif label == "Medium":
        note = "Some shared league / market risk"
    else:
        note = "Elevated shared risk — treat joint % as optimistic"

    return {
        "score": blend,
        "max_score": max_score,
        "avg_score": avg_score,
        "label": label,
        "reasons": top_reasons,
        "pairs": pairs,
        "note": note,
    }


def analyze_slate_correlation(picks) -> dict:
    """Attach per-pick peer correlation and return a slate-level summary.

    Mutates each pick with a ``correlation`` dict pointing at its strongest peer.
    Used on Today's Picks (all sports) so correlated lean clusters are visible.
    """
    picks = list(picks or [])
    n = len(picks)
    empty = {
        "score": 0.0,
        "avg_score": 0.0,
        "label": "Low",
        "note": "Need at least two picks",
        "pair_count": 0,
        "high_pairs": 0,
        "medium_pairs": 0,
    }
    if n < 2:
        for p in picks:
            p["correlation"] = {
                "score": 0.0,
                "label": "Low",
                "peer_pick": None,
                "peer_matchup": None,
                "reasons": [],
                "note": "Standalone",
            }
        return empty

    # Pairwise once; keep max peer per index.
    best = [
        {"score": 0.0, "label": "Low", "peer_idx": None, "reasons": []}
        for _ in range(n)
    ]
    pair_scores = []
    high_pairs = 0
    medium_pairs = 0

    for i in range(n):
        for j in range(i + 1, n):
            pc = pair_correlation(picks[i], picks[j])
            pair_scores.append(pc["score"])
            if pc["score"] >= _CORR_LABEL_HIGH:
                high_pairs += 1
            elif pc["score"] >= _CORR_LABEL_LOW:
                medium_pairs += 1
            for idx, other in ((i, j), (j, i)):
                if pc["score"] >= best[idx]["score"]:
                    best[idx] = {
                        "score": pc["score"],
                        "label": pc["label"],
                        "peer_idx": other,
                        "reasons": pc["reasons"],
                    }

    for i, p in enumerate(picks):
        info = best[i]
        peer = picks[info["peer_idx"]] if info["peer_idx"] is not None else None
        p["correlation"] = {
            "score": info["score"],
            "label": info["label"],
            "peer_pick": peer.get("pick") if peer else None,
            "peer_matchup": peer.get("matchup") if peer else None,
            "peer_market": peer.get("market") if peer else None,
            "reasons": info["reasons"],
            "note": (
                "Elevated overlap with another pick"
                if info["label"] == "High"
                else (
                    "Some shared risk with another pick"
                    if info["label"] == "Medium"
                    else "Looks independent on this slate"
                )
            ),
        }

    avg = sum(pair_scores) / len(pair_scores) if pair_scores else 0.0
    mx = max(pair_scores) if pair_scores else 0.0
    blend = 0.55 * mx + 0.45 * avg
    label = _corr_label(blend)
    if label == "Low":
        note = "Picks look largely independent across fixtures"
    elif label == "Medium":
        note = "Some same-league or same-side market clustering"
    else:
        note = "Several picks share league / market / O-U risk"

    return {
        "score": blend,
        "max_score": mx,
        "avg_score": avg,
        "label": label,
        "note": note,
        "pair_count": len(pair_scores),
        "high_pairs": high_pairs,
        "medium_pairs": medium_pairs,
    }


def filter_pick_legs(
    legs,
    *,
    sport=None,
    league=None,
    market=None,
    side=None,
    status=None,
    min_conf=None,
):
    """Filter annotated today's-pick / combo legs for the admin board."""
    sport = (sport or "").strip().lower() or None
    league = (league or "").strip().lower() or None
    market = (market or "").strip().lower() or None
    side = (side or "").strip().lower() or None
    status = (status or "").strip().lower() or None
    floor = None if min_conf in (None, "") else float(min_conf)

    out = []
    for leg in legs or []:
        if sport and (leg.get("sport_key") or "").lower() != sport:
            continue
        if league and (leg.get("league_id") or "").lower() != league:
            continue
        if market and (leg.get("market") or "").lower() != market:
            continue
        if side and side != "all" and _leg_ou_side(leg) != side:
            continue
        if status and status != "all" and (leg.get("status") or "").lower() != status:
            continue
        if floor is not None:
            conf = leg.get("confidence")
            if conf is None or float(conf) < floor:
                continue
        out.append(leg)
    return out


def filter_combos(
    combos,
    *,
    sport=None,
    league=None,
    market=None,
    side=None,
    mix=None,
    min_joint=None,
):
    """Filter combo slips. Sport/league match if any leg matches; market/side same."""
    sport = (sport or "").strip().lower() or None
    league = (league or "").strip().lower() or None
    market = (market or "").strip().lower() or None
    side = (side or "").strip().lower() or None
    mix = (mix or "").strip().lower() or None
    joint_floor = None if min_joint in (None, "") else float(min_joint)

    out = []
    for combo in combos or []:
        legs = combo.get("legs") or []
        if not legs:
            continue
        if mix == "cross" and combo.get("mix_kind") != "Cross-sport":
            continue
        if mix == "league" and combo.get("mix_kind") != "Cross-league":
            continue
        if mix == "mixed" and combo.get("mix_kind") not in ("Cross-sport", "Cross-league"):
            continue
        if mix == "same" and combo.get("mix_kind") not in ("Same-sport", "Same-league"):
            continue
        if joint_floor is not None and float(combo.get("joint_prob") or 0) < joint_floor:
            continue
        if sport and not any((leg.get("sport_key") or "").lower() == sport for leg in legs):
            continue
        if league and not any((leg.get("league_id") or "").lower() == league for leg in legs):
            continue
        if market and not any((leg.get("market") or "").lower() == market for leg in legs):
            continue
        if side and side != "all" and not any(_leg_ou_side(leg) == side for leg in legs):
            continue
        out.append(combo)
    return out


def annotate_leg(pick, *, sport_key, sport_label, league_id, league_label, league_color=""):
    """Copy a today's-pick dict into a combo leg with sport/league tags."""
    out = dict(pick)
    out["sport_key"] = sport_key
    out["sport_label"] = sport_label
    out["league_id"] = league_id
    out["league_label"] = league_label
    out["league_color"] = league_color or ""
    return out


def _market_family(market: str | None) -> str:
    """Coarse family so the combo pool / slate mixes result, handicap, totals, props."""
    m = (market or "").strip().lower()
    if m in ("moneyline", "match winner", "safer result", "draw no bet", "upset lean"):
        return "result"
    if "handicap" in m or m in ("spread", "run line"):
        return "handicap"
    if m.startswith("team ") or m in ("team runs", "team to score"):
        return "team"
    if m in ("both teams to score", "btts", "corners", "player to win a set"):
        return "props"
    if ("total" in m or m in ("goals", "runs", "hits", "strikeouts", "ks", "sets", "games")
            or m.endswith(" ks")):
        return "totals"
    if "prop" in m or "win a set" in m:
        return "props"
    return "other"


def _diversify_leg_pool(legs, cap=COMBO_POOL_CAP):
    """Round-robin across sports/leagues and market families.

    Prevents one sport or one market type from filling the entire combo pool
    so mixed sports/leagues slips remain buildable.
    """
    from collections import defaultdict

    # Primary buckets: sport → league → market family.
    buckets = defaultdict(list)
    for leg in sorted(legs or [], key=lambda x: float(x.get("confidence") or 0), reverse=True):
        sport = (leg.get("sport_key") or "other").strip().lower() or "other"
        league = (leg.get("league_id") or "other").strip().lower() or "other"
        fam = _market_family(leg.get("market"))
        buckets[(sport, league, fam)].append(leg)

    fam_order = ["result", "handicap", "props", "team", "totals", "other"]
    # Stable sport order so soccer/baseball/cricket all get early slots.
    sport_order = ["soccer", "baseball", "cricket", "tennis"]
    sports = (
        [s for s in sport_order if any(k[0] == s for k in buckets)]
        + sorted({k[0] for k in buckets if k[0] not in sport_order})
    )
    leagues_by_sport = defaultdict(list)
    for sport, league, _fam in buckets:
        if league not in leagues_by_sport[sport]:
            leagues_by_sport[sport].append(league)

    out = []
    while len(out) < cap:
        progressed = False
        for sport in sports:
            for league in leagues_by_sport.get(sport) or []:
                for fam in fam_order + sorted({
                    k[2] for k in buckets if k[0] == sport and k[1] == league and k[2] not in fam_order
                }):
                    bucket = buckets.get((sport, league, fam)) or []
                    if not bucket:
                        continue
                    out.append(bucket.pop(0))
                    progressed = True
                    if len(out) >= cap:
                        return out
        if not progressed:
            break
    return out


def _combo_mix_meta(chosen):
    """Classify a slip as Cross-sport, Cross-league, or Same-league."""
    sport_keys = {(leg.get("sport_key") or "").strip().lower() for leg in chosen}
    sport_keys.discard("")
    league_ids = {(leg.get("league_id") or "").strip().lower() for leg in chosen}
    league_ids.discard("")
    league_tags = sorted({leg["league_label"] for leg in chosen if leg.get("league_label")})
    if len(sport_keys) > 1:
        mix_kind = "Cross-sport"
    elif len(league_ids) > 1:
        mix_kind = "Cross-league"
    else:
        mix_kind = "Same-league"
    return {
        "sport_keys": sport_keys,
        "league_ids": league_ids,
        "league_tags": league_tags,
        "mix_kind": mix_kind,
        "n_sports": len(sport_keys),
        "n_leagues": len(league_ids),
    }


def _make_combo(chosen, *, prefer_min=None):
    """Build a scored combo dict from distinct-fixture legs.

    Score is joint probability first, then hard preference for *mixing markets*
    (moneyline + handicap + totals + props) and for *mixing sports/leagues* —
    mono-market / same-league slips are penalized relative to mixed cards.
    """
    joint = 1.0
    for leg in chosen:
        joint *= float(leg["confidence"])
    mix = _combo_mix_meta(chosen)
    is_fallback = False
    if prefer_min is not None:
        is_fallback = any(float(leg["confidence"]) < float(prefer_min) for leg in chosen)
    parts = [f"{float(leg['confidence']) * 100:.0f}%" for leg in chosen]
    sides = [_leg_ou_side(leg) for leg in chosen]
    n = len(chosen)
    under_n = sum(1 for s in sides if s == "under")
    over_n = sum(1 for s in sides if s == "over")
    markets = {(leg.get("market") or "").strip() for leg in chosen}
    markets.discard("")
    families = {_market_family(m) for m in markets}
    corr = analyze_combo_correlation(chosen)
    combo = {
        "legs": chosen,
        "joint_prob": joint,
        "size": n,
        "mix_label": " + ".join(mix["league_tags"]),
        "mix_kind": mix["mix_kind"],
        "n_sports": mix["n_sports"],
        "n_leagues": mix["n_leagues"],
        "joint_formula": " × ".join(parts) + f" = {joint * 100:.1f}%",
        "is_fallback": is_fallback,
        "under_n": under_n,
        "over_n": over_n,
        "markets": sorted(markets),
        "families": sorted(families),
        "correlation": corr,
        "corr_score": corr["score"],
        "corr_label": corr["label"],
        "corr_note": corr["note"],
    }

    confs = [float(leg["confidence"]) for leg in chosen]
    min_c = min(confs)
    avg_c = sum(confs) / len(confs)
    outstanding = (
        min_c + 1e-12 >= float(COMBO_TARGET_HIT_RATE)
        and avg_c + 1e-12 >= float(COMBO_MIN_AVG_OUTSTANDING)
    )
    combo["is_outstanding"] = outstanding

    score = 1.45 * math.log(max(joint, 1e-12))
    # Hit-rate first: reward strong weakest-leg and average confidence.
    score += 1.35 * (min_c - 0.65)
    score += 0.90 * (avg_c - 0.65)
    if outstanding:
        score += 0.65
    elif min_c + 1e-12 >= float(COMBO_PREFER_MIN):
        score += 0.30
    # A card should use different angles, not merely differently named versions
    # of the same angle (for example Moneyline + Safer Result).
    n_markets = len(markets)
    if n_markets <= 1 and n > 1:
        score -= 1.20
    else:
        score += 0.15 * (n_markets - 1)
    if len(families) <= 1 and n > 1:
        score -= 0.35
    else:
        score += 0.45 * (len(families) - 1)
    if prefer_min is not None and not is_fallback:
        score += 0.28  # prefer true high-prob legs for accuracy
    # Prefer independence via different sports / leagues.
    if mix["mix_kind"] == "Cross-sport":
        score += 0.55 + 0.12 * max(0, mix["n_sports"] - 2)
    elif mix["mix_kind"] == "Cross-league":
        score += 0.32 + 0.08 * max(0, mix["n_leagues"] - 2)
    else:
        score -= 0.18  # same-league cards are the default failure mode
    # Mild same-side O/U spam penalty only (do not chase Overs).
    if under_n == n and under_n > 0:
        score -= 0.15
    if over_n == n and over_n > 0:
        score -= 0.10
    # Penalize correlated legs — joint % assumes independence.
    score -= 1.15 * float(corr["score"])
    if corr["label"] == "High":
        score -= 0.35
    elif corr["label"] == "Low":
        score += 0.12
    combo["score"] = score
    return combo


def _enumerate_combos(pool, n, *, prefer_min=None):
    """All distinct-fixture N-leg combos from the leg pool."""
    from itertools import combinations

    if len(pool) < n:
        return []
    out = []
    for idxs in combinations(range(len(pool)), n):
        chosen = [pool[i] for i in idxs]
        keys = [_leg_match_key(leg) for leg in chosen]
        if len(set(keys)) < n:
            continue
        out.append(_make_combo(chosen, prefer_min=prefer_min))
    return out


def _beam_combo_candidates(pool, n, *, prefer_min=None, beam_width=COMBO_BEAM_WIDTH):
    """Build bounded N-leg candidates, pruning partial cards before extension.

    Two-leg cards are only O(pool²) at the configured cap and form the beam.
    For larger cards only the best diverse partial slips are extended, avoiding
    full C(pool, n) enumeration while leaving a broad set for slate selection.
    """
    if len(pool) < n:
        return []
    if n <= 2:
        return _enumerate_combos(pool, n, prefer_min=prefer_min)

    # Keep index tuples so extensions are canonical and never duplicated.
    states = []
    for i in range(len(pool)):
        states.append(((i,), {_leg_match_key(pool[i])}, math.log(
            max(float(pool[i]["confidence"]), 1e-12)
        )))

    for depth in range(2, n + 1):
        expanded = []
        final_depth = depth == n
        for idxs, fixture_keys, _ in states:
            for j in range(idxs[-1] + 1, len(pool)):
                key = _leg_match_key(pool[j])
                if key in fixture_keys:
                    continue
                new_idxs = idxs + (j,)
                chosen = [pool[i] for i in new_idxs]
                combo = _make_combo(chosen, prefer_min=prefer_min)
                if final_depth:
                    expanded.append(combo)
                else:
                    expanded.append((new_idxs, fixture_keys | {key}, combo["score"]))

        if final_depth:
            return expanded
        expanded.sort(key=lambda state: state[2], reverse=True)
        states = expanded[:max(int(beam_width), 1)]
        if not states:
            break
    return []


_FAVORITE_COVER_MARKETS = {
    "moneyline",
    "match winner",
    "draw no bet",
    "player to win a set",
    "safer result",
    "spread",
}


def _leg_is_favorite_cover(leg: dict | None) -> bool:
    """High-conf favorite result that looks like a lock and glues every combo.

    Same trap on every sport: 90% ML / match winner / DNB / Safer Result /
    win-a-set copied onto the whole slate. NFL favorite spreads (minus line)
    count too; dog plus-spreads do not.
    """
    market = ((leg or {}).get("market") or "").strip().lower()
    pick = ((leg or {}).get("pick") or "").strip()
    if market == "spread":
        if not re.search(r"-\d", pick):
            return False
    elif market not in _FAVORITE_COVER_MARKETS:
        return False
    try:
        return float((leg or {}).get("confidence") or 0) >= FAVORITE_COVER_MIN
    except (TypeError, ValueError):
        return False


def _leg_is_fragile_total(leg: dict | None) -> bool:
    """Under totals that correlate and wipe slates (goals, sets, runs, games)."""
    pick = ((leg or {}).get("pick") or "").strip().lower()
    market = ((leg or {}).get("market") or "").strip().lower()
    if not _is_under_pick(pick):
        return False
    return (
        "total" in market
        or any(token in market for token in ("set", "goal", "run", "game", "corner", "point"))
    )


def _combo_signature(combo) -> frozenset:
    """Identity of a slip by fixture keys (order-independent)."""
    return frozenset(_leg_match_key(leg) for leg in (combo.get("legs") or []))


def _usable_combo_legs(legs, floor):
    usable = []
    for leg in (legs or []):
        if fixture_is_finished(leg):
            continue
        if leg.get("confidence") is None:
            continue
        if float(leg["confidence"]) < float(floor):
            continue
        if leg.get("pick") in ("", "No rating yet", "No strong lean"):
            continue
        # Coin-flip / upset leans drag slip hit rate — keep them out of combos.
        if (leg.get("market") or "").strip().lower() == "upset lean":
            continue
        # Unders need a higher bar as combo glue on every sport.
        if _leg_is_fragile_total(leg) and float(leg["confidence"]) < float(UNDER_COMBO_MIN):
            continue
        usable.append(leg)
    usable.sort(key=lambda x: float(x["confidence"]), reverse=True)
    return usable


def _combo_leg_confidences(combo) -> list[float]:
    out = []
    for leg in (combo.get("legs") or []):
        try:
            out.append(float(leg["confidence"]))
        except (TypeError, ValueError, KeyError):
            continue
    return out


def _combo_passes_quality(combo, min_leg_floor=COMBO_MIN_LEG) -> bool:
    """Reject weak / coin-flip cards before they hit the public board."""
    confs = _combo_leg_confidences(combo)
    if not confs:
        return False
    if min(confs) + 1e-12 < float(min_leg_floor):
        return False
    # Highly correlated + soft joint is the classic way to overstate hit rate.
    try:
        corr = float(combo.get("corr_score") or 0)
        joint = float(combo.get("joint_prob") or 0)
    except (TypeError, ValueError):
        corr, joint = 0.0, 0.0
    if corr >= 0.55 and joint < 0.45:
        return False
    for leg in combo.get("legs") or []:
        if (leg.get("market") or "").strip().lower() == "upset lean":
            return False
    covers = sum(1 for leg in (combo.get("legs") or []) if _leg_is_favorite_cover(leg))
    if covers > 1:
        return False
    unders = sum(1 for leg in (combo.get("legs") or []) if _leg_is_fragile_total(leg))
    if unders > 1:
        return False
    return True


def _combo_is_outstanding(combo) -> bool:
    """True when every leg clears the ~70% target (best hit-rate cards)."""
    confs = _combo_leg_confidences(combo)
    if not confs:
        return False
    target = float(COMBO_TARGET_HIT_RATE)
    return min(confs) + 1e-12 >= target and (sum(confs) / len(confs)) + 1e-12 >= target


def _filter_quality_candidates(candidates: list, min_leg_floor=COMBO_MIN_LEG) -> list:
    kept = [c for c in (candidates or []) if _combo_passes_quality(c, min_leg_floor)]
    for c in kept:
        c["is_outstanding"] = _combo_is_outstanding(c)
    # Outstanding first, then score — hit rate over novelty.
    kept.sort(
        key=lambda c: (1 if c.get("is_outstanding") else 0, float(c.get("score") or 0)),
        reverse=True,
    )
    return kept


def _select_combos_mixed(candidates, top_k, *, blocked=None, prefer_mixed=False,
                         allow_fixture_reuse=False, fixture_uses=None,
                         favorite_covers=0):
    """Pick slips while spreading markets / sports / leagues.

    Default is fixture-disjoint. ``fixture_uses`` is mutated in place so the
    full Best + Mixed slate shares one fixture budget (no reshuffling the same
    event across cards). Soft reuse is blocked once a fixture hits
    ``COMBO_MAX_SLIPS_PER_FIXTURE``.
    """
    if top_k <= 0 or not candidates:
        return []
    blocked = set(blocked or ())
    eligible = []
    for c in candidates:
        keys = {_leg_match_key(leg) for leg in c["legs"]}
        if (not allow_fixture_reuse) and (keys & blocked):
            continue
        if prefer_mixed and c.get("mix_kind") not in ("Cross-sport", "Cross-league"):
            continue
        eligible.append(c)
    if not eligible:
        return []

    kept = []
    used_fixtures = set(blocked)
    used_markets = set()
    used_families = set()
    used_sports = set()
    used_leagues = set()
    used_sigs = set()
    # Mutate caller's map so Best → Mixed → next size share one budget.
    if fixture_uses is None:
        fixture_uses = {}
    favorite_covers = int(favorite_covers or 0)

    remaining = list(eligible)
    while len(kept) < top_k and remaining:
        best = None
        best_key = None
        for c in remaining:
            sig = _combo_signature(c)
            if sig in used_sigs:
                continue
            keys = {_leg_match_key(leg) for leg in c["legs"]}
            if (not allow_fixture_reuse) and (keys & used_fixtures):
                continue
            if any(
                fixture_uses.get(k, 0) >= COMBO_MAX_SLIPS_PER_FIXTURE
                for k in keys
            ):
                continue
            cover_n = sum(
                1 for leg in c["legs"] if _leg_is_favorite_cover(leg)
            )
            if favorite_covers + cover_n > COMBO_MAX_FAVORITE_COVER_LEGS:
                continue
            markets = set(c.get("markets") or [])
            families = set(c.get("families") or [])
            sports = {
                (leg.get("sport_key") or "").strip().lower()
                for leg in c["legs"] if (leg.get("sport_key") or "").strip()
            }
            leagues = {
                (leg.get("league_id") or "").strip().lower()
                for leg in c["legs"] if (leg.get("league_id") or "").strip()
            }
            new_m = len(markets - used_markets)
            new_f = len(families - used_families)
            new_s = len(sports - used_sports)
            new_l = len(leagues - used_leagues)
            slate_score = (
                float(c["score"])
                + 0.55 * new_m
                + 0.40 * new_f
                + 0.60 * new_s
                + 0.35 * new_l
            )
            # Prefer outstanding (≥70% legs) when filling the public slate.
            if c.get("is_outstanding"):
                slate_score += 0.75
            elif min(_combo_leg_confidences(c) or [0.0]) + 1e-12 >= float(COMBO_PREFER_MIN):
                slate_score += 0.35
            if not kept and len(markets) >= 2:
                slate_score += 0.25
            if c.get("mix_kind") == "Cross-sport":
                slate_score += 0.55 if prefer_mixed else 0.35
            elif c.get("mix_kind") == "Cross-league":
                slate_score += 0.35 if prefer_mixed else 0.20
            if allow_fixture_reuse and (keys & used_fixtures):
                slate_score -= 0.55  # prefer fresh fixtures even in soft fill
            slate_score -= 0.45 * float(c.get("corr_score") or 0)
            if best is None or slate_score > best_key:
                best = c
                best_key = slate_score
        if best is None:
            break
        card = dict(best)
        if allow_fixture_reuse and (
            {_leg_match_key(leg) for leg in card["legs"]} & used_fixtures
        ):
            card["is_soft_fill"] = True
        kept.append(card)
        used_sigs.add(_combo_signature(card))
        card_keys = {_leg_match_key(leg) for leg in card["legs"]}
        used_fixtures |= card_keys
        for k in card_keys:
            fixture_uses[k] = fixture_uses.get(k, 0) + 1
        favorite_covers += sum(
            1 for leg in card["legs"] if _leg_is_favorite_cover(leg)
        )
        used_markets |= set(card.get("markets") or [])
        used_families |= set(card.get("families") or [])
        used_sports |= {
            (leg.get("sport_key") or "").strip().lower()
            for leg in card["legs"] if (leg.get("sport_key") or "").strip()
        }
        used_leagues |= {
            (leg.get("league_id") or "").strip().lower()
            for leg in card["legs"] if (leg.get("league_id") or "").strip()
        }
        if allow_fixture_reuse:
            remaining = [c for c in remaining if _combo_signature(c) not in used_sigs]
        else:
            remaining = [
                c for c in remaining
                if not ({_leg_match_key(leg) for leg in c["legs"]} & used_fixtures)
            ]

    kept.sort(key=lambda c: c["score"], reverse=True)
    return kept


def _fill_combo_quota(
    candidates,
    top_k,
    *,
    prefer_mixed=False,
    min_k=MIN_COMBOS_PER_SIZE,
    fixture_uses=None,
    favorite_covers=0,
):
    """Fill up to ``top_k`` slips with unique fixtures (shared slate budget).

    Prefer fewer strong multi-sport cards over soft-reshuffling the same
    events to hit a quota. ``fixture_uses`` is mutated in place.
    """
    target = max(int(top_k), int(min_k))
    uses = fixture_uses if fixture_uses is not None else {}
    # Disjoint only — never republish a fixture already on the slate.
    hard = _select_combos_mixed(
        candidates, target, prefer_mixed=prefer_mixed, allow_fixture_reuse=False,
        fixture_uses=uses, favorite_covers=favorite_covers,
    )
    return hard[:target]


def _combo_candidates(pool, n, top_k, *, prefer_min=None, blocked=None, prefer_mixed=False, candidates=None):
    """Select a quota-filled mixed slate from bounded N-leg candidates."""
    if top_k <= 0:
        return []
    if candidates is None:
        if len(pool) < n:
            return []
        candidates = _beam_combo_candidates(pool, n, prefer_min=prefer_min)
    return _fill_combo_quota(candidates, top_k, prefer_mixed=prefer_mixed)


def build_best_combos(
    legs,
    *,
    sizes=(2, 3),
    top_k=MAX_COMBOS_PER_SIZE,
    mixed_top_k=MAX_MIXED_COMBOS_PER_SIZE,
    prefer_min=None,
    fallback_min=None,
    soft_fill_min=None,
    near_miss_min=None,
    min_k=MIN_COMBOS_PER_SIZE,
):
    """Best N-leg combos targeting ~70%+ empirical slip hit rate.

    Pool is round-robined across sports, leagues, and market families.
    Floors try outstanding (≥70% legs) first, then selective ≥68%, then —
    only if that still can't fill ``min_k`` — a near-miss floor a bit below
    68%. Quality gate drops upset leans and high-correlation soft joints.
    The full published slate (Best 2/3 + Mixed 2/3) shares one fixture
    budget so the same event is not reshuffled across today's slips. Prefer
    fewer strong multi-sport cards over padding. ``mixed_2`` / ``mixed_3``
    are Cross-sport / Cross-league only.
    """
    if prefer_min is None:
        prefer_min = COMBO_PREFER_MIN
    if fallback_min is None:
        fallback_min = COMBO_FALLBACK_MIN
    if soft_fill_min is None:
        soft_fill_min = COMBO_SOFT_FILL_MIN
    if near_miss_min is None:
        near_miss_min = COMBO_NEAR_MISS_MIN
    target = max(int(top_k), int(min_k))
    mixed_target = max(int(mixed_top_k), int(min_k))
    # Outstanding first, then selective, then near-miss — unique floors only.
    floors = []
    for floor in (prefer_min, fallback_min, soft_fill_min, near_miss_min):
        f = float(floor)
        if not any(abs(f - x) < 1e-12 for x in floors):
            floors.append(f)

    out = {int(s): [] for s in sizes}
    favorite_covers = 0
    fixture_uses: dict = {}
    # Keep last quality candidate list per size for the Mixed pass.
    candidates_by_size: dict[int, list] = {}

    def _note_published(cards):
        nonlocal favorite_covers
        for card in cards or []:
            favorite_covers += sum(
                1 for leg in (card.get("legs") or [])
                if _leg_is_favorite_cover(leg)
            )

    def _select_best_for_size(n: int) -> list:
        nonlocal favorite_covers
        best_selected: list = []
        best_uses = None
        candidates: list = []
        for floor in floors:
            usable = _diversify_leg_pool(
                _usable_combo_legs(legs, floor), cap=COMBO_POOL_CAP,
            )
            if len(usable) < n:
                continue
            raw = _beam_combo_candidates(usable, n, prefer_min=prefer_min)
            candidates = _filter_quality_candidates(raw, min_leg_floor=floor)
            if not candidates:
                continue
            outstanding = [c for c in candidates if c.get("is_outstanding")]
            trial_uses = dict(fixture_uses)
            selected: list = []
            if len(outstanding) >= min_k:
                selected = _fill_combo_quota(
                    outstanding, target, prefer_mixed=False, min_k=min_k,
                    fixture_uses=trial_uses, favorite_covers=favorite_covers,
                )
            if len(selected) < min_k:
                trial_uses = dict(fixture_uses)
                selected = _fill_combo_quota(
                    candidates, target, prefer_mixed=False, min_k=min_k,
                    fixture_uses=trial_uses, favorite_covers=favorite_covers,
                )
            if len(selected) > len(best_selected):
                best_selected = list(selected)
                best_uses = trial_uses
            if (
                floor + 1e-12 >= float(prefer_min)
                and len(outstanding) >= min_k
                and len(selected) >= min_k
            ):
                best_selected = list(selected)
                best_uses = trial_uses
                break
            if len(selected) >= min_k:
                best_selected = list(selected)
                best_uses = trial_uses
                break
        candidates_by_size[n] = candidates
        if best_uses is not None:
            fixture_uses.clear()
            fixture_uses.update(best_uses)
        return best_selected[:target]

    def _select_mixed_for_size(n: int) -> list:
        candidates = candidates_by_size.get(n) or []
        if not candidates:
            usable = _diversify_leg_pool(
                _usable_combo_legs(legs, soft_fill_min), cap=COMBO_POOL_CAP,
            )
            if len(usable) >= n:
                candidates = _filter_quality_candidates(
                    _beam_combo_candidates(usable, n, prefer_min=prefer_min),
                    min_leg_floor=soft_fill_min,
                )
                candidates_by_size[n] = candidates
        trial_uses = dict(fixture_uses)
        mixed = _fill_combo_quota(
            candidates, mixed_target, prefer_mixed=True, min_k=min_k,
            fixture_uses=trial_uses, favorite_covers=favorite_covers,
        )
        # Thin slate: nothing at 0.68+ filled the mixed quota either — widen
        # once more to the near-miss floor before giving up.
        if len(mixed) < min_k and near_miss_min + 1e-12 < soft_fill_min:
            usable = _diversify_leg_pool(
                _usable_combo_legs(legs, near_miss_min), cap=COMBO_POOL_CAP,
            )
            if len(usable) >= n:
                soft_candidates = _filter_quality_candidates(
                    _beam_combo_candidates(usable, n, prefer_min=prefer_min),
                    min_leg_floor=near_miss_min,
                )
                trial2 = dict(fixture_uses)
                mixed2 = _fill_combo_quota(
                    soft_candidates, mixed_target, prefer_mixed=True, min_k=min_k,
                    fixture_uses=trial2, favorite_covers=favorite_covers,
                )
                if len(mixed2) > len(mixed):
                    mixed = mixed2
                    trial_uses = trial2
        fixture_uses.clear()
        fixture_uses.update(trial_uses)
        return mixed[:mixed_target]

    # Best sizes first (2 then 3), then Mixed from remaining fixtures —
    # so Mixed does not starve 3-leg Best of unique high-prob events.
    for n in sizes:
        n = int(n)
        out[n] = _select_best_for_size(n)
        _note_published(out[n])
    for n in sizes:
        n = int(n)
        out[f"mixed_{n}"] = _select_mixed_for_size(n)
        _note_published(out[f"mixed_{n}"])
    return out


def soccer_admin_market_tips(row, league_key=None):
    """Every available soccer market for a fixture (admin Telegram builder).

    Starts from the model tip set, then adds raw 1X2 / BTTS / O-U sides so
    the admin can pick any market the board can price — not only the 'best'
    high-confidence lean.
    """
    seen = set()
    out = []

    def _add(market, pick, prob):
        try:
            p = float(prob)
        except (TypeError, ValueError):
            return
        if p <= 0:
            return
        key = ((market or "").strip().lower(), str(pick or "").strip().lower())
        if not key[0] or not key[1] or key in seen:
            return
        seen.add(key)
        out.append({"market": market, "pick": pick, "probability": p})

    for tip in _soccer_high_confidence_tips(row, league_key=league_key):
        _add(tip.get("market"), tip.get("pick"), tip.get("probability"))

    home = row.get("HomeTeam")
    away = row.get("AwayTeam")
    p_home = float(row.get("P_Home_Win") or 0)
    p_draw = float(row.get("P_Draw") or 0)
    p_away = float(row.get("P_Away_Win") or 0)
    _add("1X2", f"{home} win", p_home)
    _add("1X2", "Draw", p_draw)
    _add("1X2", f"{away} win", p_away)

    p_btts = float(row.get("P_BTTS_Yes") or 0)
    _add("Both Teams to Score", "Yes", p_btts)
    _add("Both Teams to Score", "No", 1.0 - p_btts)

    p_over = float(row.get("P_Over_2.5_Goals") or 0)
    _add("Total Goals", "Over 2.5", p_over)
    _add("Total Goals", "Under 2.5", 1.0 - p_over)

    return out


def group_admin_events_by_fixture(legs):
    """Group flat annotated legs into expandable admin events."""
    order = []
    buckets = {}
    for idx, leg in enumerate(legs or []):
        if not isinstance(leg, dict):
            continue
        key = (
            (leg.get("sport_key") or "").lower(),
            (leg.get("league_id") or "").lower(),
            (leg.get("matchup") or "").strip().lower()
            or f"{leg.get('home', '')} vs {leg.get('away', '')}".strip().lower(),
        )
        if key not in buckets:
            buckets[key] = {
                "sport_key": leg.get("sport_key") or "",
                "sport_label": leg.get("sport_label") or "",
                "league_id": leg.get("league_id") or "",
                "league_label": leg.get("league_label") or "",
                "matchup": leg.get("matchup")
                or f"{leg.get('home', '?')} vs {leg.get('away', '?')}",
                "when": leg.get("when") or "",
                "url": leg.get("url") or "",
                "markets": [],
            }
            order.append(key)
        buckets[key]["markets"].append({
            "idx": idx,
            "pick": leg.get("pick") or "",
            "market": leg.get("market") or "",
            "confidence": leg.get("confidence"),
            "selection_line": leg.get("selection_line")
            or (
                f"{leg.get('pick') or ''}"
                + (f" · {leg.get('market')}" if leg.get("market") else "")
            ),
        })
    return [buckets[k] for k in order]
