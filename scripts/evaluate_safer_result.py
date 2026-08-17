"""Evaluate soccer's fixed Safer Result rule without threshold chasing.

For each league, the model's chronological 20% holdout is divided in time:
the earlier half fits two Platt calibration maps and the later half is used
once for evaluation.  The decision thresholds are fixed globally at 70% for
favourite-or-draw and 75% for an exact favourite win.

Writes webapp/safer_result_oos.json so the deployed UI uses the same
calibration, rule, and measured evidence as this report.
"""
from __future__ import annotations

import datetime
import json
import os
import sys
import warnings

import numpy as np
import pandas as pd
from scipy.special import logit
from sklearn.linear_model import LogisticRegression

from backtest_pl import classify, load_models
from leagues import LEAGUES


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEBAPP_DIR = os.path.join(BASE_DIR, "webapp")
if WEBAPP_DIR not in sys.path:
    sys.path.insert(0, WEBAPP_DIR)

import safer_result  # noqa: E402


def _fit_platt(raw_probabilities, targets):
    x = logit(np.clip(np.asarray(raw_probabilities, dtype=float), 1e-6, 1 - 1e-6))
    model = LogisticRegression(C=1.0, solver="lbfgs")
    model.fit(x.reshape(-1, 1), np.asarray(targets, dtype=int))
    return {
        "intercept": float(model.intercept_[0]),
        "slope": float(model.coef_[0, 0]),
    }


def _date_text(series, index):
    if len(series) == 0:
        return None
    value = pd.to_datetime(series.iloc[index], errors="coerce")
    return None if pd.isna(value) else value.date().isoformat()


def evaluate_league(league_key):
    cfg = LEAGUES[league_key]
    models, feature_cols, result_label_map = load_models(cfg["model_dir"])
    full_df = pd.read_csv(f"{cfg['data_dir']}/train_rows.csv")
    holdout = full_df.iloc[int(len(full_df) * 0.8):].reset_index(drop=True)
    calibration_n = len(holdout) // 2
    evaluation_n = len(holdout) - calibration_n
    if calibration_n < safer_result.MIN_EVALUATION_PICKS or evaluation_n < safer_result.MIN_EVALUATION_PICKS:
        return {
            "label": cfg["label"],
            "available": False,
            "met_target": False,
            "reason": "insufficient chronological calibration/evaluation data",
            "holdout_n": len(holdout),
            "calibration_n": calibration_n,
            "evaluation_n": evaluation_n,
            "n": 0,
            "coverage": 0.0,
        }

    X = holdout[feature_cols]
    result_base, result_meta, result_strategy = models["result"]
    result_proba = classify(result_base, result_meta, result_strategy, X)
    fav_base, fav_meta, fav_strategy = models["favorite_avoids_defeat"]
    p_favorite_avoids = classify(fav_base, fav_meta, fav_strategy, X)[:, 1]

    home_is_favorite = holdout["Home_Is_Favorite"].astype(bool).to_numpy()
    p_home = result_proba[:, result_label_map["H"]]
    p_draw = result_proba[:, result_label_map["D"]]
    p_away = result_proba[:, result_label_map["A"]]
    p_favorite_win = np.where(home_is_favorite, p_home, p_away)

    actual_result = holdout["Target_Result"].astype(str).to_numpy()
    favorite_result_code = np.where(home_is_favorite, "H", "A")
    y_favorite_win = (actual_result == favorite_result_code).astype(int)
    y_favorite_avoids = holdout["Target_FavoriteAvoidsDefeat"].astype(int).to_numpy()

    calibration = {
        "favorite_win": _fit_platt(
            p_favorite_win[:calibration_n], y_favorite_win[:calibration_n]
        ),
        "favorite_avoids_defeat": _fit_platt(
            p_favorite_avoids[:calibration_n], y_favorite_avoids[:calibration_n]
        ),
    }

    correct = 0
    exact_n = 0
    exact_correct = 0
    double_chance_n = 0
    double_chance_correct = 0
    for i in range(calibration_n, len(holdout)):
        home = str(holdout.iloc[i]["HomeTeam"])
        away = str(holdout.iloc[i]["AwayTeam"])
        favorite = home if home_is_favorite[i] else away
        selection = safer_result.select_safer_result(
            home_team=home,
            away_team=away,
            favorite_team=favorite,
            p_home_win=p_home[i],
            p_draw=p_draw[i],
            p_away_win=p_away[i],
            p_favorite_avoids_defeat=p_favorite_avoids[i],
            calibration=calibration,
        )
        if selection is None:
            continue
        won = safer_result.settle_safer_result(selection, actual_result[i])
        correct += int(won)
        if selection["selection_type"] == "exact":
            exact_n += 1
            exact_correct += int(won)
        else:
            double_chance_n += 1
            double_chance_correct += int(won)

    n = exact_n + double_chance_n
    accuracy = correct / n if n else None
    coverage = n / evaluation_n if evaluation_n else 0.0
    met_target = bool(
        accuracy is not None
        and accuracy >= 0.70
        and n >= safer_result.MIN_EVALUATION_PICKS
    )
    date_col = holdout["Date"] if "Date" in holdout else holdout.get("DateUtc", pd.Series(dtype=str))
    return {
        "label": cfg["label"],
        "available": met_target,
        "met_target": met_target,
        "holdout_n": len(holdout),
        "calibration_n": calibration_n,
        "evaluation_n": evaluation_n,
        "evaluation_start": _date_text(date_col, calibration_n),
        "evaluation_end": _date_text(date_col, len(holdout) - 1),
        "n": n,
        "correct": correct,
        "accuracy": accuracy,
        "coverage": coverage,
        "exact_n": exact_n,
        "exact_correct": exact_correct,
        "double_chance_n": double_chance_n,
        "double_chance_correct": double_chance_correct,
        "calibration": calibration,
        "model_strategies": {
            "result": result_strategy,
            "favorite_avoids_defeat": fav_strategy,
        },
    }


def build_report(keys=None):
    keys = list(keys or LEAGUES.keys())
    leagues = {key: evaluate_league(key) for key in keys}
    supported = [row for row in leagues.values() if row.get("n")]
    evaluation_n = sum(int(row["evaluation_n"]) for row in supported)
    n = sum(int(row["n"]) for row in supported)
    correct = sum(int(row["correct"]) for row in supported)
    exact_n = sum(int(row["exact_n"]) for row in supported)
    exact_correct = sum(int(row["exact_correct"]) for row in supported)
    dc_n = sum(int(row["double_chance_n"]) for row in supported)
    dc_correct = sum(int(row["double_chance_correct"]) for row in supported)
    return {
        "schema_version": 1,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "rule": {
            "name": safer_result.MARKET_NAME,
            "double_chance_threshold": safer_result.DOUBLE_CHANCE_THRESHOLD,
            "exact_win_threshold": safer_result.EXACT_WIN_THRESHOLD,
            "minimum_raw_exact_win": safer_result.MIN_RAW_EXACT_WIN,
            "minimum_evaluation_picks": safer_result.MIN_EVALUATION_PICKS,
            "calibration": (
                "Platt scaling fitted on the earlier half of the existing "
                "chronological model holdout"
            ),
            "evaluation": (
                "Fixed rule scored on the later half; thresholds were not "
                "searched on evaluation results"
            ),
            "inputs": "Pre-kickoff model probabilities and Elo favourite only",
            "model_strategy_note": (
                "Uses each league's already-deployed model strategy, frozen "
                "before the Safer Result rule was specified"
            ),
        },
        "leagues": leagues,
        "aggregate": {
            "evaluation_n": evaluation_n,
            "n": n,
            "correct": correct,
            "accuracy": correct / n if n else None,
            "coverage": n / evaluation_n if evaluation_n else 0.0,
            "exact_n": exact_n,
            "exact_correct": exact_correct,
            "double_chance_n": dc_n,
            "double_chance_correct": dc_correct,
            "met_target": bool(
                n >= safer_result.MIN_EVALUATION_PICKS
                and n > 0
                and correct / n >= 0.70
            ),
        },
    }


def main():
    warnings.filterwarnings(
        "ignore",
        message="Skipping features without any observed values",
        category=UserWarning,
    )
    report = build_report(sys.argv[1:] or None)
    path = safer_result.REPORT_PATH
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
        handle.write("\n")
    for key, row in report["leagues"].items():
        accuracy = row.get("accuracy")
        acc_text = "n/a" if accuracy is None else f"{accuracy:.1%}"
        print(
            f"{row['label']}: {acc_text}, n={row.get('n', 0)}, "
            f"coverage={row.get('coverage', 0):.1%}, "
            f"supported={row.get('met_target', False)}"
        )
    aggregate = report["aggregate"]
    print(
        f"Aggregate: {aggregate['accuracy']:.1%}, n={aggregate['n']}, "
        f"coverage={aggregate['coverage']:.1%}"
    )
    print(f"Wrote {path}")


if __name__ == "__main__":
    main()
