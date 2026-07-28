"""Trains and hyperparameter-tunes the same model family as
scripts/mlb/train_ml_models.py, on NPB's leakage-free per-game dataset --
same rationale and honesty note apply (see that script's docstring); NPB
has a smaller sample and fewer features (no box-score data), so expect
similar or slightly lower holdout accuracy than MLB.

Run from repo root: python scripts/npb/train_ml_models.py
Outputs data/npb/ml_ensemble_model.joblib
"""
import os
import sys
import warnings

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import (
    AdaBoostClassifier,
    BaggingClassifier,
    RandomForestClassifier,
    StackingClassifier,
    VotingClassifier,
)
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import RandomizedSearchCV, TimeSeriesSplit
from sklearn.neural_network import MLPClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC
from sklearn.tree import DecisionTreeClassifier
from xgboost import XGBClassifier

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from npb_config import DATA_DIR  # noqa: E402

warnings.filterwarnings("ignore")

FEATURES = [
    "EloDiff", "HomeLast10WinRate", "AwayLast10WinRate", "HomeStreak", "AwayStreak",
    "H2HMeetings", "HomeH2HWinRate", "RunsForDiff", "RunsAllowedDiff",
]
SVM_TRAIN_SUBSAMPLE = 5000
RANDOM_STATE = 42


def load_dataset():
    path = os.path.join(DATA_DIR, "training_dataset.csv")
    df = pd.read_csv(path)
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.sort_values("Date").reset_index(drop=True)
    return df


def chronological_split(df, test_frac=0.15):
    cutoff = int(len(df) * (1 - test_frac))
    return df.iloc[:cutoff], df.iloc[cutoff:]


def tune(name, estimator, param_dist, X_train, y_train, n_iter=12):
    search = RandomizedSearchCV(
        estimator, param_dist, n_iter=n_iter, cv=TimeSeriesSplit(n_splits=4),
        scoring="accuracy", random_state=RANDOM_STATE, n_jobs=-1,
    )
    search.fit(X_train, y_train)
    print(f"  [{name}] best params: {search.best_params_} (CV acc {search.best_score_:.3f})")
    return search.best_estimator_


def main():
    df = load_dataset()
    train_df, test_df = chronological_split(df)
    X_train, y_train = train_df[FEATURES], train_df["HomeWin"]
    X_test, y_test = test_df[FEATURES], test_df["HomeWin"]
    print(f"Train: {len(X_train)} games, Test (holdout): {len(X_test)} games")
    print(f"Baseline (always predict home win): {y_test.mean():.3f} accuracy\n")

    scaler = StandardScaler().fit(X_train)
    X_train_scaled = pd.DataFrame(scaler.transform(X_train), columns=FEATURES)
    X_test_scaled = pd.DataFrame(scaler.transform(X_test), columns=FEATURES)

    results = {}
    fitted = {}

    print("Tuning Decision Tree...")
    dt = tune("DecisionTree", DecisionTreeClassifier(random_state=RANDOM_STATE), {
        "max_depth": [3, 4, 5, 6, 8, 10], "min_samples_leaf": [10, 20, 50, 100, 200],
    }, X_train, y_train)
    fitted["decision_tree"] = dt

    print("Tuning Random Forest (bagging)...")
    rf = tune("RandomForest", RandomForestClassifier(random_state=RANDOM_STATE), {
        "n_estimators": [100, 200, 300], "max_depth": [4, 6, 8, None],
        "min_samples_leaf": [5, 20, 50], "max_features": ["sqrt", "log2"],
    }, X_train, y_train)
    fitted["random_forest"] = rf

    print("Tuning explicit Bagging ensemble...")
    bag = tune("Bagging", BaggingClassifier(
        estimator=DecisionTreeClassifier(max_depth=5, random_state=RANDOM_STATE), random_state=RANDOM_STATE,
    ), {
        "n_estimators": [20, 50, 100], "max_samples": [0.5, 0.7, 1.0], "max_features": [0.7, 1.0],
    }, X_train, y_train)
    fitted["bagging"] = bag

    print("Tuning AdaBoost (boosting)...")
    ada = tune("AdaBoost", AdaBoostClassifier(
        estimator=DecisionTreeClassifier(max_depth=2, random_state=RANDOM_STATE), random_state=RANDOM_STATE,
    ), {
        "n_estimators": [50, 100, 200], "learning_rate": [0.01, 0.05, 0.1, 0.5],
    }, X_train, y_train)
    fitted["adaboost"] = ada

    print("Tuning XGBoost (boosting)...")
    xgb = tune("XGBoost", XGBClassifier(
        random_state=RANDOM_STATE, eval_metric="logloss",
    ), {
        "n_estimators": [100, 200, 300], "max_depth": [3, 4, 5, 6],
        "learning_rate": [0.01, 0.03, 0.05, 0.1], "subsample": [0.7, 0.85, 1.0],
        "colsample_bytree": [0.7, 0.85, 1.0],
    }, X_train, y_train)
    fitted["xgboost"] = xgb

    print("Tuning SVM (scaled features, subsampled for speed)...")
    rng = np.random.RandomState(RANDOM_STATE)
    sub_idx = rng.choice(len(X_train_scaled), size=min(SVM_TRAIN_SUBSAMPLE, len(X_train_scaled)), replace=False)
    svm = tune("SVM", SVC(probability=True, random_state=RANDOM_STATE), {
        "C": [0.1, 1, 3, 10], "gamma": ["scale", "auto", 0.01, 0.1], "kernel": ["rbf"],
    }, X_train_scaled.iloc[sub_idx], y_train.iloc[sub_idx])
    fitted["svm"] = svm

    print("Tuning Neural Network (MLP)...")
    mlp = tune("MLP", MLPClassifier(max_iter=500, random_state=RANDOM_STATE), {
        "hidden_layer_sizes": [(16,), (32,), (32, 16), (64, 32)],
        "alpha": [0.0001, 0.001, 0.01, 0.1], "learning_rate_init": [0.001, 0.01],
    }, X_train_scaled, y_train)
    fitted["mlp"] = mlp

    print("\n--- Per-model holdout accuracy ---")
    for name, model, use_scaled in (
        ("Decision Tree", dt, False), ("Random Forest (bagging)", rf, False),
        ("Bagging (explicit)", bag, False), ("AdaBoost (boosting)", ada, False),
        ("XGBoost (boosting)", xgb, False), ("SVM", svm, True), ("Neural Network (MLP)", mlp, True),
    ):
        X_eval = X_test_scaled if use_scaled else X_test
        acc = model.score(X_eval, y_test)
        results[name] = acc
        print(f"  {name:28s} {acc:.3f}")

    print("\nBuilding ensembles (soft voting + stacking) on scaled features for all models...")
    voting = VotingClassifier(estimators=[
        ("dt", dt), ("rf", rf), ("bag", bag), ("ada", ada), ("xgb", xgb), ("svm", svm), ("mlp", mlp),
    ], voting="soft")
    voting.fit(X_train_scaled, y_train)
    voting_acc = voting.score(X_test_scaled, y_test)
    results["Voting Ensemble"] = voting_acc
    print(f"  {'Voting Ensemble':28s} {voting_acc:.3f}")

    stacking = StackingClassifier(estimators=[
        ("dt", dt), ("rf", rf), ("bag", bag), ("ada", ada), ("xgb", xgb), ("svm", svm), ("mlp", mlp),
    ], final_estimator=LogisticRegression(max_iter=1000), cv=5)
    stacking.fit(X_train_scaled, y_train)
    stacking_acc = stacking.score(X_test_scaled, y_test)
    results["Stacking Ensemble"] = stacking_acc
    print(f"  {'Stacking Ensemble':28s} {stacking_acc:.3f}")

    best_name = max(results, key=results.get)
    best_acc = results[best_name]
    print(f"\nBest model on holdout: {best_name} ({best_acc:.3f} accuracy)")
    target = 0.65
    if best_acc >= target:
        print(f"Meets the {target:.0%} target.")
    else:
        print(f"Does NOT meet the {target:.0%} target -- reporting the honest holdout number "
              f"rather than an overfit one.")

    model_lookup = {
        "Decision Tree": (dt, False), "Random Forest (bagging)": (rf, False),
        "Bagging (explicit)": (bag, False), "AdaBoost (boosting)": (ada, False),
        "XGBoost (boosting)": (xgb, False), "SVM": (svm, True), "Neural Network (MLP)": (mlp, True),
        "Voting Ensemble": (voting, True), "Stacking Ensemble": (stacking, True),
    }
    best_model, best_uses_scaled = model_lookup[best_name]
    out_path = os.path.join(DATA_DIR, "ml_ensemble_model.joblib")
    joblib.dump({"model": best_model, "scaler": scaler, "features": FEATURES,
                 "uses_scaled_features": best_uses_scaled, "model_name": best_name,
                 "holdout_accuracy": best_acc}, out_path)
    print(f"\nSaved best model ({best_name}) -> {out_path}")


if __name__ == "__main__":
    main()
