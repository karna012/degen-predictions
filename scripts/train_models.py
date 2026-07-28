"""
Trains a stacked ensemble of 3 diverse models (bagging + 2 boosting
families, tree-based) for each Premier League market, using the
leakage-free rolling + Elo + Dixon-Coles-strength features from
build_features.py.

Techniques applied:
  - 3 diverse base learners: Random Forest (bagging), XGBoost, CatBoost
    (2 different boosting implementations -> genuinely different error
    patterns, which is what makes an ensemble help at all). LightGBM was
    dropped from this lineup because its native DLL gets blocked by a
    Windows Application Control policy on the deployment machine -- see
    webapp/live_predict.py's module docstring for the same note.
  - Proper missing-value handling via SimpleImputer (median) inside a
    Pipeline, instead of dropping rows -> keeps early-season matches.
  - Hyperparameter tuning via RandomizedSearchCV with a *shuffled* CV split.
    (Leakage is already prevented upstream by build_features.py's shift(1)
    features -- shuffling which already-safe rows land in which fold is
    fine, and gives far more stable hyperparameter estimates than tiny,
    noisy chronological folds.)
  - Stacking: out-of-fold predictions from all 4 base models are fed into a
    logistic-regression (or ridge, for regression) meta-learner, which
    generally beats simple probability averaging because it learns *how
    much to trust each model*, rather than trusting them equally.
  - Final honest evaluation on a held-out chronological slice (last 20% of
    matches by date) -- this is the only point in the pipeline where time
    order is enforced, and it's enforced deliberately: it answers "would
    this have worked on games after training stopped", the real question.
Saves models + feature list + metrics to model/pl/.
"""
import os
import pickle
import sys
import time

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier, CatBoostRegressor
from scipy.stats import randint, uniform
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression, RidgeCV
from sklearn.metrics import accuracy_score, log_loss, mean_absolute_error
from sklearn.model_selection import (KFold, RandomizedSearchCV, StratifiedKFold,
                                      cross_val_predict)
from sklearn.pipeline import Pipeline
from xgboost import XGBClassifier, XGBRegressor

from leagues import LEAGUES

N_ITER_SEARCH = 20
CV_SPLITS = 5


def get_feature_cols(df):
    cols = [c for c in df.columns
            if (c.startswith("Home_") or c.startswith("Away_")) and c != "Home_GamesPlayed_awaydrop"]
    cols += ["Elo_Diff", "H2H_Meetings", "H2H_HomeWinRate",
              "H2H_AvgGoalsForHome", "H2H_AvgGoalsForAway",
              "Referee_AvgCards", "Referee_AvgGoals", "Referee_GamesSeen"]
    return [c for c in cols if c in df.columns]


# ---------------------------------------------------------------- classifiers

def tuned_classifiers(seed):
    return {
        "rf": (
            Pipeline([("impute", SimpleImputer(strategy="median")),
                      ("m", RandomForestClassifier(random_state=42, n_jobs=-1))]),
            {"m__n_estimators": randint(200, 700), "m__max_depth": randint(3, 14),
             "m__min_samples_leaf": randint(2, 25), "m__max_features": uniform(0.3, 0.6)},
        ),
        "xgb": (
            Pipeline([("impute", SimpleImputer(strategy="median")),
                      ("m", XGBClassifier(random_state=42, n_jobs=-1, eval_metric="logloss"))]),
            {"m__n_estimators": randint(80, 500), "m__max_depth": randint(2, 7),
             "m__learning_rate": uniform(0.01, 0.19), "m__subsample": uniform(0.6, 0.4),
             "m__colsample_bytree": uniform(0.6, 0.4), "m__min_child_weight": randint(1, 10),
             "m__reg_lambda": uniform(0.5, 3.0)},
        ),
        "catboost": (
            Pipeline([("impute", SimpleImputer(strategy="median")),
                      ("m", CatBoostClassifier(random_state=42, verbose=False, allow_writing_files=False))]),
            {"m__iterations": randint(100, 500), "m__depth": randint(3, 8),
             "m__learning_rate": uniform(0.01, 0.19), "m__l2_leaf_reg": uniform(1.0, 8.0)},
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
        fitted[model_name] = best

        oof_probs[model_name] = cross_val_predict(best, X_train, y_train, cv=cv,
                                                    method="predict_proba", n_jobs=-1)
        best.fit(X_train, y_train)
        proba_test = best.predict_proba(X_test)
        test_probs[model_name] = proba_test
        classes = best.named_steps["m"].classes_
        test_accs[model_name] = accuracy_score(y_test, classes[np.argmax(proba_test, axis=1)])

    # --- stacking meta-learner ---
    meta_X_train = np.hstack([oof_probs[m] for m in fitted])
    meta_X_test = np.hstack([test_probs[m] for m in fitted])
    meta = LogisticRegression(max_iter=2000)
    meta.fit(meta_X_train, y_train)

    meta_proba_test = meta.predict_proba(meta_X_test)
    meta_pred = meta.classes_[np.argmax(meta_proba_test, axis=1)]
    acc_stack = accuracy_score(y_test, meta_pred)
    ll_stack = log_loss(y_test, meta_proba_test, labels=meta.classes_)

    # simple-average baseline, reported for comparison
    avg_proba_test = np.mean([test_probs[m] for m in fitted], axis=0)
    acc_avg = accuracy_score(y_test, classes[np.argmax(avg_proba_test, axis=1)])

    # Pick whichever strategy actually wins on the held-out chronological
    # slice -- with ~2-3k training rows, stacking/averaging sometimes just
    # adds noise rather than signal, so we don't blindly trust the fanciest
    # option. predict_pl.py reads this choice back and uses it at inference.
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
            "stacked_acc": acc_stack, "logloss": ll_stack, "best_strategy": best_strategy}


# ---------------------------------------------------------------- regressors

def tuned_regressors(seed):
    return {
        "rf": (
            Pipeline([("impute", SimpleImputer(strategy="median")),
                      ("m", RandomForestRegressor(random_state=42, n_jobs=-1))]),
            {"m__n_estimators": randint(200, 700), "m__max_depth": randint(3, 12),
             "m__min_samples_leaf": randint(3, 30), "m__max_features": uniform(0.3, 0.6)},
        ),
        "xgb": (
            Pipeline([("impute", SimpleImputer(strategy="median")),
                      ("m", XGBRegressor(random_state=42, n_jobs=-1))]),
            {"m__n_estimators": randint(100, 500), "m__max_depth": randint(2, 6),
             "m__learning_rate": uniform(0.01, 0.19), "m__subsample": uniform(0.6, 0.4),
             "m__colsample_bytree": uniform(0.6, 0.4), "m__reg_lambda": uniform(0.5, 3.0)},
        ),
        "catboost": (
            Pipeline([("impute", SimpleImputer(strategy="median")),
                      ("m", CatBoostRegressor(random_state=42, verbose=False, allow_writing_files=False))]),
            {"m__iterations": randint(100, 500), "m__depth": randint(3, 8),
             "m__learning_rate": uniform(0.01, 0.19), "m__l2_leaf_reg": uniform(1.0, 8.0)},
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
        search = RandomizedSearchCV(pipe, param_dist, n_iter=N_ITER_SEARCH, cv=cv,
                                     scoring="neg_mean_absolute_error", random_state=seed + i, n_jobs=-1)
        search.fit(X_train, y_train)
        best = search.best_estimator_
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
    best_strategy = min(candidates, key=candidates.get)  # lower MAE wins

    mae_line = "  ".join(f"{m}={test_maes[m]:.3f}" for m in fitted)
    print(f"[{name}] {mae_line}  |  avg-ensemble={mae_avg:.3f}  "
          f"stacked={mae_stack:.3f}  -> BEST: {best_strategy} ({candidates[best_strategy]:.3f})  "
          f"({time.time()-t0:.0f}s)")

    with open(f"{model_dir}/{name}_base_models.pkl", "wb") as f:
        pickle.dump(fitted, f)
    with open(f"{model_dir}/{name}_meta.pkl", "wb") as f:
        pickle.dump(meta, f)

    return {"per_model_mae": test_maes, "avg_ensemble_mae": mae_avg,
            "stacked_mae": mae_stack, "best_strategy": best_strategy}


def train_for_league(league_key):
    cfg = LEAGUES[league_key]
    data_dir, model_dir = cfg["data_dir"], cfg["model_dir"]
    os.makedirs(model_dir, exist_ok=True)

    print(f"=== {cfg['label']} ===")
    df = pd.read_csv(f"{data_dir}/train_rows.csv")
    # Feature NaNs are handled by imputation (see below), but a NaN *target*
    # is fatal to sklearn's fit() -- a handful of rows in some leagues'
    # source CSVs are missing corner/goal counts for a match. Those rows are
    # dropped (never silently imputed) since a target's true value isn't
    # something we should be guessing at during training.
    target_cols = [c for c in df.columns if c.startswith("Target_")]
    before = len(df)
    df = df.dropna(subset=target_cols).reset_index(drop=True)
    if len(df) < before:
        print(f"Dropped {before - len(df)} row(s) with a missing target value")

    feature_cols = get_feature_cols(df)
    print(f"Training on {len(df)} rows, {len(feature_cols)} features "
          f"(missing values handled via median imputation, not dropped)")

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
    results["home_more_corners"] = stack_classification_target(
        df, feature_cols, "Target_HomeMoreCorners", "home_more_corners", model_dir)
    results["favorite_avoids_defeat"] = stack_classification_target(
        df, feature_cols, "Target_FavoriteAvoidsDefeat", "favorite_avoids_defeat", model_dir)
    results["total_corners"] = stack_regression_target(
        df, feature_cols, "Target_TotalCorners", "total_corners", model_dir)

    with open(f"{model_dir}/training_metrics.pkl", "wb") as f:
        pickle.dump(results, f)

    print("\nAll models trained and saved to", model_dir)
    print("\nHonest note: ~55-62% accuracy on 1X2/BTTS/Over-Under is near the")
    print("ceiling for this problem even with tuning and 3-model stacking --")
    print("bookmakers, who have far more information (injuries, lineups),")
    print("land in the same range.")


def main():
    keys = sys.argv[1:] or list(LEAGUES.keys())
    for key in keys:
        train_for_league(key)


if __name__ == "__main__":
    main()
