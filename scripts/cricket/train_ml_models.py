"""Slim cricket trainer: Elo features + XGBoost (primary) with Ridge/Logistic fallback.

Drops RandomForest from the candidate set. Holdout still picks the better of
XGBoost vs linear baseline so thin competitions can fall back safely.

Run from repo root: python scripts/cricket/train_ml_models.py
Outputs data/cricket/*.joblib
"""
import os
import sys
import warnings

import joblib
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import accuracy_score, mean_absolute_error
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier, XGBRegressor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cricket_config import DATA_DIR  # noqa: E402

warnings.filterwarnings("ignore")
RANDOM_STATE = 42
N_JOBS = 2  # never -1 -- concurrent jobs on this PC have OOMed with -1

NUMERIC_FEATURES = [
    "EloDiff", "HomeAdvantage", "Comp_ipl", "Comp_bbl", "Comp_hnd", "Comp_mlc", "Comp_cpl",
    "Team1RecentWinRate", "Team2RecentWinRate", "Team1RecentRunRate", "Team2RecentRunRate",
    "VenueAvgFirstInnings", "VenueAvgSecondInnings", "VenueAvgRunsPerOver", "VenueAvgWicketsPerOver",
    "VenueMatchesSeen", "Team1AvgSR", "Team2AvgSR", "Team1AvgEcon", "Team2AvgEcon",
    "Team1DeathUplift", "Team2DeathUplift", "Team1MatchupEdge", "Team2MatchupEdge", "SentimentDiff",
]


def load_dataset():
    path = os.path.join(DATA_DIR, "training_dataset.csv")
    df = pd.read_csv(path)
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.sort_values("Date").reset_index(drop=True)
    return df


def chronological_split(df, test_frac=0.2):
    cutoff = int(len(df) * (1 - test_frac))
    return df.iloc[:cutoff], df.iloc[cutoff:]


def build_pipeline(estimator):
    return Pipeline([
        ("impute", SimpleImputer(strategy="median")),
        ("scale", StandardScaler()),
        ("model", estimator),
    ])


def train_regression(df_train, df_test, target, label):
    X_train, y_train = df_train[NUMERIC_FEATURES], df_train[target]
    X_test, y_test = df_test[NUMERIC_FEATURES], df_test[target]

    candidates = {
        "ridge": build_pipeline(Ridge(alpha=2.0, random_state=RANDOM_STATE)),
        "xgboost": build_pipeline(XGBRegressor(
            n_estimators=200, max_depth=3, learning_rate=0.05,
            subsample=0.85, colsample_bytree=0.85,
            random_state=RANDOM_STATE, n_jobs=N_JOBS, verbosity=0,
        )),
    }
    results = {}
    for name, pipe in candidates.items():
        pipe.fit(X_train, y_train)
        mae = mean_absolute_error(y_test, pipe.predict(X_test))
        results[name] = (pipe, mae)
        print(f"  [{label}] {name:18s} holdout MAE = {mae:.2f}")

    baseline_mae = mean_absolute_error(y_test, [y_train.mean()] * len(y_test))
    print(f"  [{label}] {'baseline (mean)':18s} holdout MAE = {baseline_mae:.2f}")

    # Prefer XGBoost unless Ridge clearly wins (MAE lower by >2%).
    xgb_mae = results["xgboost"][1]
    ridge_mae = results["ridge"][1]
    if ridge_mae < xgb_mae * 0.98:
        best_name = "ridge"
    else:
        best_name = "xgboost"
    best_pipe, best_mae = results[best_name]
    print(f"  [{label}] selected: {best_name} (MAE {best_mae:.2f} vs baseline {baseline_mae:.2f})\n")
    return best_pipe, best_name, best_mae, baseline_mae


def train_classification(df_train, df_test):
    X_train, y_train = df_train[NUMERIC_FEATURES], df_train["Winner"]
    X_test, y_test = df_test[NUMERIC_FEATURES], df_test["Winner"]

    candidates = {
        "logistic": build_pipeline(LogisticRegression(max_iter=1000, random_state=RANDOM_STATE)),
        "xgboost": build_pipeline(XGBClassifier(
            n_estimators=200, max_depth=3, learning_rate=0.05,
            subsample=0.85, colsample_bytree=0.85, eval_metric="logloss",
            random_state=RANDOM_STATE, n_jobs=N_JOBS, verbosity=0,
        )),
    }
    results = {}
    for name, pipe in candidates.items():
        pipe.fit(X_train, y_train)
        acc = accuracy_score(y_test, pipe.predict(X_test))
        results[name] = (pipe, acc)
        print(f"  [Winner] {name:18s} holdout accuracy = {acc:.3f}")

    baseline_acc = max(y_test.mean(), 1 - y_test.mean())
    print(f"  [Winner] {'baseline (majority)':18s} holdout accuracy = {baseline_acc:.3f}")

    xgb_acc = results["xgboost"][1]
    log_acc = results["logistic"][1]
    # Prefer XGBoost unless logistic wins by >1pp.
    best_name = "logistic" if log_acc > xgb_acc + 0.01 else "xgboost"
    best_pipe, best_acc = results[best_name]
    print(f"  [Winner] selected: {best_name} (accuracy {best_acc:.3f} vs baseline {baseline_acc:.3f})\n")
    return best_pipe, best_name, best_acc, baseline_acc


def main():
    df = load_dataset()
    print(f"Loaded {len(df)} matches")
    df_train, df_test = chronological_split(df)
    print(f"Train: {len(df_train)} matches, Holdout (most recent): {len(df_test)} matches\n")
    print("Slim stack: XGBoost primary, Ridge/Logistic fallback\n")

    df_train_w = df_train.dropna(subset=["Winner"])
    df_test_w = df_test.dropna(subset=["Winner"])

    os.makedirs(DATA_DIR, exist_ok=True)

    print("Training Team1Runs model...")
    pipe1, name1, mae1, base1 = train_regression(df_train, df_test, "Team1Runs", "Team1Runs")
    joblib.dump({"model": pipe1, "features": NUMERIC_FEATURES, "model_name": name1,
                 "holdout_mae": mae1, "baseline_mae": base1},
                os.path.join(DATA_DIR, "team1_runs_model.joblib"))

    print("Training Team2Runs model...")
    pipe2, name2, mae2, base2 = train_regression(df_train, df_test, "Team2Runs", "Team2Runs")
    joblib.dump({"model": pipe2, "features": NUMERIC_FEATURES, "model_name": name2,
                 "holdout_mae": mae2, "baseline_mae": base2},
                os.path.join(DATA_DIR, "team2_runs_model.joblib"))

    print("Training TotalRuns model...")
    pipe3, name3, mae3, base3 = train_regression(df_train, df_test, "TotalRuns", "TotalRuns")
    joblib.dump({"model": pipe3, "features": NUMERIC_FEATURES, "model_name": name3,
                 "holdout_mae": mae3, "baseline_mae": base3},
                os.path.join(DATA_DIR, "total_runs_model.joblib"))

    print("Training Winner classification model...")
    pipe4, name4, acc4, base4 = train_classification(df_train_w, df_test_w)
    joblib.dump({"model": pipe4, "features": NUMERIC_FEATURES, "model_name": name4,
                 "holdout_accuracy": acc4, "baseline_accuracy": base4},
                os.path.join(DATA_DIR, "winner_model.joblib"))

    print("=" * 70)
    print("SUMMARY")
    print(f"  Team1Runs   : {name1:18s} MAE {mae1:.2f} (baseline {base1:.2f})")
    print(f"  Team2Runs   : {name2:18s} MAE {mae2:.2f} (baseline {base2:.2f})")
    print(f"  TotalRuns   : {name3:18s} MAE {mae3:.2f} (baseline {base3:.2f})")
    print(f"  Winner      : {name4:18s} accuracy {acc4:.3f} (baseline {base4:.3f})")
    print("=" * 70)


if __name__ == "__main__":
    main()
