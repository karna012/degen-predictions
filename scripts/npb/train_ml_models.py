"""Slim NPB trainer: Elo features + calibrated XGBoost (+ optional logistic vote).

Same slim stack as scripts/mlb/train_ml_models.py (no SVM/MLP/RF zoo).

Run from repo root: python scripts/npb/train_ml_models.py
Outputs data/npb/ml_ensemble_model.joblib
"""
import os
import sys
import warnings

import joblib
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import VotingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss
from sklearn.model_selection import RandomizedSearchCV, TimeSeriesSplit
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from npb_config import DATA_DIR  # noqa: E402

warnings.filterwarnings("ignore")

FEATURES = [
    "EloDiff", "HomeLast10WinRate", "AwayLast10WinRate", "HomeStreak", "AwayStreak",
    "H2HMeetings", "HomeH2HWinRate", "RunsForDiff", "RunsAllowedDiff",
    "HomeDaysRest", "AwayDaysRest",
]
RANDOM_STATE = 42
PREVIOUS_HOLDOUT_ACCURACY = 0.5813471502590674


def load_dataset():
    path = os.path.join(DATA_DIR, "training_dataset.csv")
    df = pd.read_csv(path)
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.sort_values("Date").reset_index(drop=True)
    return df


def chronological_split(df, test_frac=0.15):
    cutoff = int(len(df) * (1 - test_frac))
    return df.iloc[:cutoff], df.iloc[cutoff:]


def tune_xgb(X_train, y_train):
    search = RandomizedSearchCV(
        XGBClassifier(random_state=RANDOM_STATE, eval_metric="logloss", n_jobs=-1),
        {
            "n_estimators": [100, 200, 300], "max_depth": [3, 4, 5, 6],
            "learning_rate": [0.01, 0.03, 0.05, 0.1], "subsample": [0.7, 0.85, 1.0],
            "colsample_bytree": [0.7, 0.85, 1.0],
        },
        n_iter=12, cv=TimeSeriesSplit(n_splits=4),
        scoring="accuracy", random_state=RANDOM_STATE, n_jobs=-1,
    )
    search.fit(X_train, y_train)
    print(f"  [XGBoost] best params: {search.best_params_} (CV acc {search.best_score_:.3f})")
    return search.best_estimator_


def main():
    df = load_dataset()
    train_df, test_df = chronological_split(df)
    X_train, y_train = train_df[FEATURES], train_df["HomeWin"]
    X_test, y_test = test_df[FEATURES], test_df["HomeWin"]
    print(f"Train: {len(X_train)} games, Test (holdout): {len(X_test)} games")
    print(f"Baseline (always predict home win): {y_test.mean():.3f} accuracy\n")
    print("Slim stack: calibrated XGBoost (+ optional logistic soft-vote)\n")

    scaler = StandardScaler().fit(X_train)

    print("Tuning XGBoost...")
    xgb_raw = tune_xgb(X_train, y_train)
    if len(X_train) >= 300:
        xgb = CalibratedClassifierCV(xgb_raw, method="isotonic", cv=3)
        xgb.fit(X_train, y_train)
    else:
        xgb = xgb_raw

    print("Fitting logistic baseline...")
    logistic = Pipeline([
        ("scale", StandardScaler()),
        ("m", LogisticRegression(max_iter=2000, random_state=RANDOM_STATE)),
    ])
    logistic.fit(X_train, y_train)

    print("Fitting XGB+Logistic soft-vote...")
    vote = VotingClassifier(
        estimators=[("xgb", xgb), ("logistic", logistic)],
        voting="soft",
    )
    vote.fit(X_train, y_train)

    results = {
        "XGBoost (calibrated)": xgb.score(X_test, y_test),
        "Logistic": logistic.score(X_test, y_test),
        "XGB+Logistic vote": vote.score(X_test, y_test),
    }
    print("\n--- Holdout accuracy ---")
    for name, acc in results.items():
        print(f"  {name:28s} {acc:.3f}")

    best_name = max(results, key=results.get)
    best_acc = results[best_name]
    model_lookup = {
        "XGBoost (calibrated)": xgb,
        "Logistic": logistic,
        "XGB+Logistic vote": vote,
    }
    best_model = model_lookup[best_name]
    proba = best_model.predict_proba(X_test)[:, 1]
    brier = brier_score_loss(y_test, proba)
    ll = log_loss(y_test, proba)

    print(f"\nBest model on holdout: {best_name} ({best_acc:.3f})")
    print(f"Previous shipped holdout: {PREVIOUS_HOLDOUT_ACCURACY:.3f} "
          f"(delta {best_acc - PREVIOUS_HOLDOUT_ACCURACY:+.3f})")
    print(f"Holdout Brier: {brier:.4f}  log-loss: {ll:.4f}")

    out_path = os.path.join(DATA_DIR, "ml_ensemble_model.joblib")
    joblib.dump({"model": best_model, "scaler": scaler, "features": FEATURES,
                 "uses_scaled_features": False, "model_name": best_name,
                 "holdout_accuracy": best_acc, "holdout_brier": brier,
                 "holdout_log_loss": ll}, out_path)
    print(f"\nSaved model ({best_name}, acc {best_acc:.3f}) -> {out_path}")


if __name__ == "__main__":
    main()
