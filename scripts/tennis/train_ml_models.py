"""Slim ATP trainer: winner model + sets/games/handicap markets.

Run from repo root: python scripts/tennis/train_ml_models.py
Outputs:
  data/tennis/ml_ensemble_model.joblib
  data/tennis/ml_markets_model.joblib
"""
from __future__ import annotations

import os
import sys
import warnings

import joblib
import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import VotingClassifier
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import (
    accuracy_score,
    brier_score_loss,
    log_loss,
    mean_absolute_error,
)
from sklearn.model_selection import RandomizedSearchCV, TimeSeriesSplit
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier, XGBRegressor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tennis_config import DATA_DIR, NUMERIC_FEATURES  # noqa: E402

warnings.filterwarnings("ignore")
RANDOM_STATE = 42
FEATURES = list(NUMERIC_FEATURES)


def load_dataset():
    path = os.path.join(DATA_DIR, "training_dataset.csv")
    df = pd.read_csv(path)
    df["Date"] = pd.to_datetime(df["Date"])
    return df.sort_values("Date").reset_index(drop=True)


def chronological_split(df, test_frac=0.15):
    cutoff = int(len(df) * (1 - test_frac))
    return df.iloc[:cutoff], df.iloc[cutoff:]


def tune_xgb(X_train, y_train):
    search = RandomizedSearchCV(
        XGBClassifier(random_state=RANDOM_STATE, eval_metric="logloss", n_jobs=-1),
        {
            "n_estimators": [100, 200, 300],
            "max_depth": [3, 4, 5],
            "learning_rate": [0.01, 0.03, 0.05, 0.1],
            "subsample": [0.7, 0.85, 1.0],
            "colsample_bytree": [0.7, 0.85, 1.0],
        },
        n_iter=10, cv=TimeSeriesSplit(n_splits=4),
        scoring="accuracy", random_state=RANDOM_STATE, n_jobs=-1,
    )
    search.fit(X_train, y_train)
    print(f"  [XGBoost] best params: {search.best_params_} (CV acc {search.best_score_:.3f})")
    return search.best_estimator_


def _pos_proba(model, X):
    proba = model.predict_proba(X)
    classes = list(model.classes_)
    if 1 in classes:
        return proba[:, classes.index(1)]
    return proba[:, -1]


def train_winner(df):
    train_df, test_df = chronological_split(df)
    X_train, y_train = train_df[FEATURES], train_df["Player1Win"].astype(int)
    X_test, y_test = test_df[FEATURES], test_df["Player1Win"].astype(int)
    print(f"Winner train={len(X_train)} holdout={len(X_test)}")
    print(f"Baseline (always Player1): {y_test.mean():.3f}")

    xgb_raw = tune_xgb(X_train, y_train)
    if len(X_train) >= 300:
        xgb = CalibratedClassifierCV(xgb_raw, method="isotonic", cv=3)
        xgb.fit(X_train, y_train)
    else:
        xgb = xgb_raw

    logistic = Pipeline([
        ("scale", StandardScaler()),
        ("m", LogisticRegression(max_iter=2000, random_state=RANDOM_STATE)),
    ])
    logistic.fit(X_train, y_train)

    vote = VotingClassifier(estimators=[("xgb", xgb), ("logistic", logistic)], voting="soft")
    vote.fit(X_train, y_train)

    candidates = {"xgboost": xgb, "logistic": logistic, "xgb_logistic_vote": vote}
    best_name, best_model, best_acc = None, None, -1
    for name, model in candidates.items():
        pred = model.predict(X_test)
        proba = _pos_proba(model, X_test)
        acc = float((pred == y_test).mean())
        brier = float(brier_score_loss(y_test, proba))
        ll = float(log_loss(y_test, proba))
        print(f"  [{name}] holdout acc={acc:.3f} brier={brier:.4f} logloss={ll:.4f}")
        if acc > best_acc:
            best_name, best_model, best_acc = name, model, acc

    baseline = float(y_test.mean())
    print(f"Selected winner: {best_name} (acc {best_acc:.3f} vs baseline {baseline:.3f})")
    bundle = {
        "model": best_model,
        "features": FEATURES,
        "model_name": best_name,
        "holdout_accuracy": best_acc,
        "baseline_accuracy": baseline,
        "uses_scaled_features": False,
        "target": "Player1Win",
    }
    out = os.path.join(DATA_DIR, "ml_ensemble_model.joblib")
    joblib.dump(bundle, out)
    print(f"wrote {out}")
    return bundle


def _fit_binary(name, train_df, test_df, target):
    tr = train_df.dropna(subset=[target])
    te = test_df.dropna(subset=[target])
    if len(tr) < 200 or len(te) < 40:
        print(f"  [{name}] skipped (n_train={len(tr)} n_test={len(te)})")
        return None
    y_tr = tr[target].astype(int)
    y_te = te[target].astype(int)
    if y_tr.nunique() < 2:
        print(f"  [{name}] skipped (single class)")
        return None
    model = XGBClassifier(
        n_estimators=200, max_depth=4, learning_rate=0.05,
        subsample=0.85, colsample_bytree=0.85,
        random_state=RANDOM_STATE, eval_metric="logloss", n_jobs=-1,
    )
    if len(tr) >= 300:
        model = CalibratedClassifierCV(model, method="isotonic", cv=3)
    model.fit(tr[FEATURES], y_tr)
    p = _pos_proba(model, te[FEATURES])
    acc = float(accuracy_score(y_te, (p >= 0.5).astype(int)))
    base = float(max(y_te.mean(), 1 - y_te.mean()))
    print(f"  [{name}] holdout acc={acc:.3f} base={base:.3f} n={len(te)}")
    return {
        "model": model,
        "kind": "classifier",
        "target": target,
        "holdout_accuracy": acc,
        "baseline_accuracy": base,
        "holdout_n": int(len(te)),
    }


def _fit_regressor(name, train_df, test_df, target):
    tr = train_df.dropna(subset=[target])
    te = test_df.dropna(subset=[target])
    if len(tr) < 200 or len(te) < 40:
        print(f"  [{name}] skipped (n_train={len(tr)} n_test={len(te)})")
        return None
    y_tr = tr[target].astype(float)
    y_te = te[target].astype(float)
    xgb = XGBRegressor(
        n_estimators=200, max_depth=4, learning_rate=0.05,
        subsample=0.85, colsample_bytree=0.85,
        random_state=RANDOM_STATE, n_jobs=-1,
    )
    xgb.fit(tr[FEATURES], y_tr)
    ridge = Pipeline([
        ("scale", StandardScaler()),
        ("m", Ridge(alpha=1.0, random_state=RANDOM_STATE)),
    ])
    ridge.fit(tr[FEATURES], y_tr)
    best_name, best_model, best_mae = None, None, 1e9
    for nm, model in (("xgb", xgb), ("ridge", ridge)):
        pred = model.predict(te[FEATURES])
        mae = float(mean_absolute_error(y_te, pred))
        print(f"  [{name}/{nm}] holdout MAE={mae:.3f}")
        if mae < best_mae:
            best_name, best_model, best_mae = nm, model, mae
    return {
        "model": best_model,
        "kind": "regressor",
        "model_name": best_name,
        "target": target,
        "holdout_mae": best_mae,
        "holdout_n": int(len(te)),
        "holdout_std": float(np.std(y_te.values)),
    }


def train_markets(df):
    print("\n=== Markets (sets / games / handicap) ===")
    mkt = df.dropna(subset=["TotalGames", "OverSets", "Player1WonSet", "Player2WonSet"]).copy()
    print(f"Market rows with scores: {len(mkt)}")
    train_df, test_df = chronological_split(mkt)

    models = {
        "over_sets": _fit_binary("over_sets", train_df, test_df, "OverSets"),
        "p1_wins_set": _fit_binary("p1_wins_set", train_df, test_df, "Player1WonSet"),
        "p2_wins_set": _fit_binary("p2_wins_set", train_df, test_df, "Player2WonSet"),
        "total_games": _fit_regressor("total_games", train_df, test_df, "TotalGames"),
        "games_margin": _fit_regressor("games_margin", train_df, test_df, "GamesMargin"),
    }
    models = {k: v for k, v in models.items() if v is not None}
    bundle = {
        "features": FEATURES,
        "models": models,
        "std_defaults": {
            "total_games": 4.5,
            "games_margin": 5.0,
        },
    }
    out = os.path.join(DATA_DIR, "ml_markets_model.joblib")
    joblib.dump(bundle, out)
    print(f"wrote {out} ({list(models)})")
    return bundle


def main():
    df = load_dataset()
    print(f"Loaded {len(df)} rows\n")
    print("Slim stack: calibrated XGBoost (+ optional logistic soft-vote)\n")
    print("Tuning XGBoost for match winner...")
    train_winner(df)
    train_markets(df)


if __name__ == "__main__":
    main()
