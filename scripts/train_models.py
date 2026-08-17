"""
Trains a slim soccer stack per league: Elo features + tuned XGBoost, with an
optional Logistic/Ridge partner and a stacking meta-learner. RF/CatBoost were
dropped -- weekly retrain prefers one strong calibrated booster.

Pipeline notes:
  - Median imputation inside Pipelines (keeps early-season rows).
  - RandomizedSearchCV on shuffled CV for hyperparameters (leakage already
    blocked upstream by build_features.py shift(1) features).
  - Chronological last-20% holdout picks best_strategy (xgb / logistic /
    avg_ensemble / stacked) so predict_pl.py can honour the winner.
  - LightGBM stays out: Windows Application Control blocks its DLL here.

Saves models + feature list + metrics under model/<league>/.
"""
import os
import pickle
import sys
import time

import numpy as np
import pandas as pd
from scipy.stats import randint, uniform
from sklearn.calibration import CalibratedClassifierCV
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression, RidgeCV
from sklearn.metrics import accuracy_score, log_loss, mean_absolute_error
from sklearn.model_selection import (KFold, RandomizedSearchCV, StratifiedKFold,
                                      cross_val_predict)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier, XGBRegressor

from leagues import LEAGUES, resolve_league_paths

N_ITER_SEARCH = 20
CV_SPLITS = 5


def get_feature_cols(df):
    cols = [c for c in df.columns
            if (c.startswith("Home_") or c.startswith("Away_")) and c != "Home_GamesPlayed_awaydrop"]
    cols += ["Elo_Diff", "H2H_Meetings", "H2H_HomeWinRate",
              "H2H_AvgGoalsForHome", "H2H_AvgGoalsForAway",
              "Referee_AvgCards", "Referee_AvgGoals", "Referee_GamesSeen"]
    return [c for c in cols if c in df.columns]


def tuned_classifiers(seed):
    """XGBoost primary + logistic baseline for an optional 2-model stack."""
    return {
        "xgb": (
            Pipeline([("impute", SimpleImputer(strategy="median")),
                      ("m", XGBClassifier(random_state=42, n_jobs=-1, eval_metric="logloss"))]),
            {"m__n_estimators": randint(80, 500), "m__max_depth": randint(2, 7),
             "m__learning_rate": uniform(0.01, 0.19), "m__subsample": uniform(0.6, 0.4),
             "m__colsample_bytree": uniform(0.6, 0.4), "m__min_child_weight": randint(1, 10),
             "m__reg_lambda": uniform(0.5, 3.0)},
        ),
        "logistic": (
            Pipeline([
                ("impute", SimpleImputer(strategy="median")),
                ("scale", StandardScaler()),
                ("m", LogisticRegression(max_iter=2000, random_state=42)),
            ]),
            {"m__C": uniform(0.05, 4.95)},
        ),
    }


def stack_classification_target(df, feature_cols, target_col, name, model_dir, label_map=None):
    t0 = time.time()
    X = df[feature_cols]
    y = df[target_col]
    if label_map is not None:
        y = y.map(label_map)

    split_idx = int(len(df) * 0.8)
    X_train, X_test = X.iloc[:split_idx].reset_index(drop=True), X.iloc[split_idx:].reset_index(drop=True)
    y_train, y_test = y.iloc[:split_idx].reset_index(drop=True), y.iloc[split_idx:].reset_index(drop=True)

    seed = abs(hash(name)) % (2**31)
    cv = StratifiedKFold(n_splits=CV_SPLITS, shuffle=True, random_state=seed)

    fitted = {}
    oof_probs = {}
    test_probs = {}
    test_accs = {}
    classes = None

    for i, (model_name, (pipe, param_dist)) in enumerate(tuned_classifiers(seed).items()):
        search = RandomizedSearchCV(pipe, param_dist, n_iter=N_ITER_SEARCH, cv=cv,
                                     scoring="accuracy", random_state=seed + i, n_jobs=-1)
        search.fit(X_train, y_train)
        best = search.best_estimator_

        # Isotonic calibration on XGB only (needs enough rows per class).
        if model_name == "xgb" and len(X_train) >= 200:
            cal = CalibratedClassifierCV(best, method="isotonic", cv=3)
            cal.fit(X_train, y_train)
            best = cal

        fitted[model_name] = best
        oof_probs[model_name] = cross_val_predict(best, X_train, y_train, cv=cv,
                                                    method="predict_proba", n_jobs=-1)
        best.fit(X_train, y_train)
        proba_test = best.predict_proba(X_test)
        test_probs[model_name] = proba_test
        classes = best.classes_ if hasattr(best, "classes_") else best.named_steps["m"].classes_
        test_accs[model_name] = accuracy_score(y_test, classes[np.argmax(proba_test, axis=1)])

    meta_X_train = np.hstack([oof_probs[m] for m in fitted])
    meta_X_test = np.hstack([test_probs[m] for m in fitted])
    meta = LogisticRegression(max_iter=2000)
    meta.fit(meta_X_train, y_train)

    meta_proba_test = meta.predict_proba(meta_X_test)
    meta_pred = meta.classes_[np.argmax(meta_proba_test, axis=1)]
    acc_stack = accuracy_score(y_test, meta_pred)
    ll_stack = log_loss(y_test, meta_proba_test, labels=meta.classes_)

    avg_proba_test = np.mean([test_probs[m] for m in fitted], axis=0)
    acc_avg = accuracy_score(y_test, classes[np.argmax(avg_proba_test, axis=1)])

    candidates = {**test_accs, "avg_ensemble": acc_avg, "stacked": acc_stack}
    best_strategy = max(candidates, key=candidates.get)

    acc_line = "  ".join(f"{m}={test_accs[m]:.3f}" for m in fitted)
    print(f"[{name}] {acc_line}  |  avg-ensemble={acc_avg:.3f}  "
          f"stacked={acc_stack:.3f}  logloss={ll_stack:.3f}  "
          f"-> BEST: {best_strategy} ({candidates[best_strategy]:.3f})  ({time.time()-t0:.0f}s)")

    with open(f"{model_dir}/{name}_base_models.pkl", "wb") as f:
        pickle.dump(fitted, f)
    with open(f"{model_dir}/{name}_meta.pkl", "wb") as f:
        pickle.dump(meta, f)

    return {"per_model_acc": test_accs, "avg_ensemble_acc": acc_avg,
            "stacked_acc": acc_stack, "logloss": ll_stack, "best_strategy": best_strategy,
            "stack": "xgb+logistic"}


def tuned_regressors(seed):
    return {
        "xgb": (
            Pipeline([("impute", SimpleImputer(strategy="median")),
                      ("m", XGBRegressor(random_state=42, n_jobs=-1))]),
            {"m__n_estimators": randint(100, 500), "m__max_depth": randint(2, 6),
             "m__learning_rate": uniform(0.01, 0.19), "m__subsample": uniform(0.6, 0.4),
             "m__colsample_bytree": uniform(0.6, 0.4), "m__reg_lambda": uniform(0.5, 3.0)},
        ),
        "ridge": (
            Pipeline([
                ("impute", SimpleImputer(strategy="median")),
                ("scale", StandardScaler()),
                ("m", RidgeCV(alphas=np.logspace(-2, 2, 20))),
            ]),
            {},
        ),
    }


def stack_regression_target(df, feature_cols, target_col, name, model_dir):
    t0 = time.time()
    X = df[feature_cols]
    y = df[target_col]

    split_idx = int(len(df) * 0.8)
    X_train, X_test = X.iloc[:split_idx].reset_index(drop=True), X.iloc[split_idx:].reset_index(drop=True)
    y_train, y_test = y.iloc[:split_idx].reset_index(drop=True), y.iloc[split_idx:].reset_index(drop=True)

    seed = abs(hash(name)) % (2**31)
    cv = KFold(n_splits=CV_SPLITS, shuffle=True, random_state=seed)

    fitted = {}
    oof_preds = {}
    test_preds = {}
    test_maes = {}

    for i, (model_name, (pipe, param_dist)) in enumerate(tuned_regressors(seed).items()):
        if param_dist:
            search = RandomizedSearchCV(pipe, param_dist, n_iter=N_ITER_SEARCH, cv=cv,
                                         scoring="neg_mean_absolute_error", random_state=seed + i, n_jobs=-1)
            search.fit(X_train, y_train)
            best = search.best_estimator_
        else:
            best = pipe
            best.fit(X_train, y_train)
        fitted[model_name] = best

        oof_preds[model_name] = cross_val_predict(best, X_train, y_train, cv=cv, n_jobs=-1)
        best.fit(X_train, y_train)
        pred_test = best.predict(X_test)
        test_preds[model_name] = pred_test
        test_maes[model_name] = mean_absolute_error(y_test, pred_test)

    meta_X_train = np.column_stack([oof_preds[m] for m in fitted])
    meta_X_test = np.column_stack([test_preds[m] for m in fitted])
    meta = RidgeCV(alphas=np.logspace(-2, 2, 20))
    meta.fit(meta_X_train, y_train)
    mae_stack = mean_absolute_error(y_test, meta.predict(meta_X_test))

    avg_pred_test = np.mean([test_preds[m] for m in fitted], axis=0)
    mae_avg = mean_absolute_error(y_test, avg_pred_test)

    candidates = {**test_maes, "avg_ensemble": mae_avg, "stacked": mae_stack}
    best_strategy = min(candidates, key=candidates.get)

    mae_line = "  ".join(f"{m}={test_maes[m]:.3f}" for m in fitted)
    print(f"[{name}] {mae_line}  |  avg-ensemble={mae_avg:.3f}  "
          f"stacked={mae_stack:.3f}  -> BEST: {best_strategy} ({candidates[best_strategy]:.3f})  "
          f"({time.time()-t0:.0f}s)")

    with open(f"{model_dir}/{name}_base_models.pkl", "wb") as f:
        pickle.dump(fitted, f)
    with open(f"{model_dir}/{name}_meta.pkl", "wb") as f:
        pickle.dump(meta, f)

    return {"per_model_mae": test_maes, "avg_ensemble_mae": mae_avg,
            "stacked_mae": mae_stack, "best_strategy": best_strategy,
            "stack": "xgb+ridge"}


def train_for_league(league_key):
    cfg = resolve_league_paths(LEAGUES[league_key])
    data_dir, model_dir = cfg["data_dir"], cfg["model_dir"]
    os.makedirs(model_dir, exist_ok=True)

    print(f"=== {cfg['label']} ===")
    df = pd.read_csv(f"{data_dir}/train_rows.csv")
    core_targets = [
        "Target_Result", "Target_BTTS", "Target_Over25", "Target_FavoriteAvoidsDefeat",
    ]
    before = len(df)
    df = df.dropna(subset=core_targets).reset_index(drop=True)
    if len(df) < before:
        print(f"Dropped {before - len(df)} row(s) with a missing core target value")

    feature_cols = get_feature_cols(df)
    print(f"Training on {len(df)} rows, {len(feature_cols)} features "
          f"(slim stack: calibrated XGBoost + logistic/ridge)")

    with open(f"{model_dir}/feature_cols.pkl", "wb") as f:
        pickle.dump(feature_cols, f)

    results = {}

    result_label_map = {"H": 0, "D": 1, "A": 2}
    results["result"] = stack_classification_target(
        df, feature_cols, "Target_Result", "result", model_dir, label_map=result_label_map)
    with open(f"{model_dir}/result_label_map.pkl", "wb") as f:
        pickle.dump(result_label_map, f)

    results["btts"] = stack_classification_target(df, feature_cols, "Target_BTTS", "btts", model_dir)
    results["over25"] = stack_classification_target(df, feature_cols, "Target_Over25", "over25", model_dir)
    results["favorite_avoids_defeat"] = stack_classification_target(
        df, feature_cols, "Target_FavoriteAvoidsDefeat", "favorite_avoids_defeat", model_dir)

    min_optional = 200
    corner_ok = (
        "Target_HomeMoreCorners" in df.columns
        and df["Target_HomeMoreCorners"].notna().sum() >= min_optional
        and df["Target_TotalCorners"].notna().sum() >= min_optional
    )
    if corner_ok:
        corner_df = df.dropna(subset=["Target_HomeMoreCorners", "Target_TotalCorners"]).reset_index(drop=True)
        results["home_more_corners"] = stack_classification_target(
            corner_df, feature_cols, "Target_HomeMoreCorners", "home_more_corners", model_dir)
        results["total_corners"] = stack_regression_target(
            corner_df, feature_cols, "Target_TotalCorners", "total_corners", model_dir)
    else:
        print("Skipping corner markets: insufficient HC/AC data in source matches")
        results["home_more_corners"] = {"skipped": True, "best_strategy": "none"}
        results["total_corners"] = {"skipped": True, "best_strategy": "none"}

    with open(f"{model_dir}/training_metrics.pkl", "wb") as f:
        pickle.dump(results, f)

    print("\nAll models trained and saved to", model_dir)
    print("\nHonest note: ~55-62% accuracy on 1X2/BTTS/Over-Under is near the")
    print("ceiling for this problem even with a calibrated booster --")
    print("bookmakers, who have far more information (injuries, lineups),")
    print("land in the same range.")


def main():
    keys = sys.argv[1:] or list(LEAGUES.keys())
    for key in keys:
        train_for_league(key)


if __name__ == "__main__":
    main()
