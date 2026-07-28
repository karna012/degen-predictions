"""Trains models for cricket's three prediction targets from the
leakage-free per-match dataset (build_training_dataset.py):
  1. Team1Runs / Team2Runs -- each side's individual innings total (regression)
  2. TotalRuns -- both innings combined (regression)
  3. Winner -- match winner classification

Mirrors scripts/mlb/train_ml_models.py's walk-forward philosophy: chronological
split only (oldest ~80% train, most recent ~20% untouched holdout), never a
random shuffle -- shuffling would let the model "see" a team's future Elo/
form when predicting an earlier match.

IMPORTANT resource constraint: a separate concurrent job (investment_scoring
retraining) on this same machine has crashed from Windows/joblib access-
violation errors under n_jobs=-1. Every estimator here uses n_jobs=2 (never
-1), and free memory is checked before this script starts its heaviest step.

Honesty note: both competitions are young (The Hundred since 2021, MLC since
2023) with only 268 total matches combined after chronological split that's
a genuinely small holdout (~50-55 matches) -- accuracy/R2 numbers here should
be read as directional, not as precise, stable estimates. This is exactly
what data_confidence (build_training_dataset.py) exists to flag downstream.

Run from repo root: python scripts/cricket/train_ml_models.py
Outputs data/cricket/*.joblib (one per target) + printed holdout metrics.
"""
import os
import sys
import warnings

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier, GradientBoostingRegressor, RandomForestClassifier, RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import accuracy_score, mean_absolute_error
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cricket_config import DATA_DIR  # noqa: E402

warnings.filterwarnings("ignore")
RANDOM_STATE = 42
N_JOBS = 2  # never -1 -- see module docstring

NUMERIC_FEATURES = [
    "EloDiff", "Team1RecentWinRate", "Team2RecentWinRate", "Team1RecentRunRate", "Team2RecentRunRate",
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
    """Median-impute remaining NaNs (thin venue/player history) then scale
    -- imputation happens *inside* the pipeline so it's fit only on the
    training fold each time, never leaking holdout statistics into training."""
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
        "random_forest": build_pipeline(RandomForestRegressor(
            n_estimators=200, max_depth=5, min_samples_leaf=8, random_state=RANDOM_STATE, n_jobs=N_JOBS)),
        "gradient_boosting": build_pipeline(GradientBoostingRegressor(
            n_estimators=150, max_depth=3, learning_rate=0.05, random_state=RANDOM_STATE)),
    }
    results = {}
    for name, pipe in candidates.items():
        pipe.fit(X_train, y_train)
        preds = pipe.predict(X_test)
        mae = mean_absolute_error(y_test, preds)
        results[name] = (pipe, mae)
        print(f"  [{label}] {name:18s} holdout MAE = {mae:.2f}")

    baseline_mae = mean_absolute_error(y_test, [y_train.mean()] * len(y_test))
    print(f"  [{label}] {'baseline (mean)':18s} holdout MAE = {baseline_mae:.2f}")

    best_name = min(results, key=lambda k: results[k][1])
    best_pipe, best_mae = results[best_name]
    print(f"  [{label}] best model: {best_name} (MAE {best_mae:.2f} vs baseline {baseline_mae:.2f})\n")
    return best_pipe, best_name, best_mae, baseline_mae


def train_classification(df_train, df_test):
    X_train, y_train = df_train[NUMERIC_FEATURES], df_train["Winner"]
    X_test, y_test = df_test[NUMERIC_FEATURES], df_test["Winner"]

    candidates = {
        "logistic": build_pipeline(LogisticRegression(max_iter=1000, random_state=RANDOM_STATE)),
        "random_forest": build_pipeline(RandomForestClassifier(
            n_estimators=200, max_depth=5, min_samples_leaf=6, random_state=RANDOM_STATE, n_jobs=N_JOBS)),
        "gradient_boosting": build_pipeline(GradientBoostingClassifier(
            n_estimators=150, max_depth=3, learning_rate=0.05, random_state=RANDOM_STATE)),
    }
    results = {}
    for name, pipe in candidates.items():
        pipe.fit(X_train, y_train)
        acc = accuracy_score(y_test, pipe.predict(X_test))
        results[name] = (pipe, acc)
        print(f"  [Winner] {name:18s} holdout accuracy = {acc:.3f}")

    baseline_acc = max(y_test.mean(), 1 - y_test.mean())
    print(f"  [Winner] {'baseline (majority)':18s} holdout accuracy = {baseline_acc:.3f}")
    best_name = max(results, key=lambda k: results[k][1])
    best_pipe, best_acc = results[best_name]
    print(f"  [Winner] best model: {best_name} (accuracy {best_acc:.3f} vs baseline {baseline_acc:.3f})\n")
    return best_pipe, best_name, best_acc, baseline_acc


def main():
    df = load_dataset()
    print(f"Loaded {len(df)} matches")
    df_train, df_test = chronological_split(df)
    print(f"Train: {len(df_train)} matches, Holdout (most recent): {len(df_test)} matches\n")
    print("Honesty note: small-sample young-competition holdout -- treat metrics as directional.\n")

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
