"""
Calibrate high-confidence probability bars for non-soccer sports so that
Out-of-Sample (chronological holdout) accuracy is at least TARGET_ACCURACY
(default 70%).

Same honesty contract as scripts/calibrate_confidence.py (soccer):
  - Full-slate win/loss cannot honestly hit 70% for MLB/NPB/cricket.
  - This finds the min predicted-probability bar where holdout picks that
    clear the bar historically land at >= 70%.
  - Coverage is reported; markets that never hit the target are marked
    met_target=False so Today's Picks will not advertise them as 70% picks.

Writes:
  data/mlb/confidence_thresholds.json
  data/npb/confidence_thresholds.json
  data/cricket/confidence_thresholds.json
  data/tennis/confidence_thresholds.json
"""
from __future__ import annotations

import json
import os
import sys

import joblib
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET_ACCURACY = 0.70
MIN_PICKS_BASEBALL = 40
MIN_PICKS_CRICKET = 15  # young competitions; small holdout
MIN_PICKS_TENNIS = 40


def _sweep_binary(y_true, p_pos, target=TARGET_ACCURACY, min_picks=MIN_PICKS_BASEBALL):
    """Predict side of 0.5; only score rows where max(p, 1-p) >= t."""
    best = None
    rows = []
    y_true = np.asarray(y_true).astype(int)
    p_pos = np.asarray(p_pos, dtype=float)
    for t in np.round(np.arange(0.50, 0.91, 0.01), 2):
        conf = np.maximum(p_pos, 1.0 - p_pos)
        mask = conf >= t
        n = int(mask.sum())
        if n < min_picks:
            rows.append({"threshold": float(t), "n": n, "acc": None, "coverage": float(mask.mean())})
            continue
        pred = (p_pos >= 0.5).astype(int)
        acc = float((pred[mask] == y_true[mask]).mean())
        rows.append({"threshold": float(t), "n": n, "acc": acc, "coverage": float(mask.mean())})
        if acc >= target and best is None:
            best = {"threshold": float(t), "n": n, "acc": acc, "coverage": float(mask.mean())}
    if best is None:
        viable = [r for r in rows if r["acc"] is not None]
        if viable:
            best = max(viable, key=lambda r: (r["acc"], -r["threshold"]))
            best["met_target"] = best["acc"] >= target
    if best is not None:
        best.setdefault("met_target", best["acc"] >= target)
    return best, rows


def _predict_home_proba(bundle, X):
    model = bundle["model"]
    features = bundle["features"]
    uses_scaled = bundle.get("uses_scaled_features", False)
    X = X[features]
    if uses_scaled and bundle.get("scaler") is not None:
        X_in = pd.DataFrame(bundle["scaler"].transform(X), columns=features)
    else:
        X_in = X
    proba = model.predict_proba(X_in)
    classes = list(model.classes_)
    # HomeWin=1 is the positive class in MLB/NPB training sets.
    if 1 in classes:
        return proba[:, classes.index(1)]
    return proba[:, -1]


def calibrate_baseball(league: str, test_frac: float = 0.15):
    data_dir = os.path.join(ROOT, "data", league)
    model_path = os.path.join(data_dir, "ml_ensemble_model.joblib")
    ds_path = os.path.join(data_dir, "training_dataset.csv")
    print(f"=== {league.upper()} moneyline calibration (target {TARGET_ACCURACY:.0%}) ===")
    if not os.path.isfile(model_path) or not os.path.isfile(ds_path):
        print(f"  skipped (missing model or dataset)")
        return None

    bundle = joblib.load(model_path)
    df = pd.read_csv(ds_path)
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.sort_values("Date").reset_index(drop=True)
    cutoff = int(len(df) * (1 - test_frac))
    hold = df.iloc[cutoff:].reset_index(drop=True)
    p = _predict_home_proba(bundle, hold)
    y = hold["HomeWin"].astype(int).values
    full_acc = float(((p >= 0.5).astype(int) == y).mean())
    best, curve = _sweep_binary(y, p, min_picks=MIN_PICKS_BASEBALL)
    out = {
        "sport": "baseball",
        "league": league,
        "target_accuracy": TARGET_ACCURACY,
        "oos_n": len(hold),
        "full_slate_acc": full_acc,
        "model_name": bundle.get("model_name"),
        "shipped_holdout_accuracy": bundle.get("holdout_accuracy"),
        "markets": {
            "moneyline": {"best": best, "curve_tail": curve[-5:]},
        },
    }
    path = os.path.join(data_dir, "confidence_thresholds.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print(f"  full-slate OOS: {full_acc*100:.1f}% n={len(hold)}")
    print(f"  moneyline: {best}")
    print(f"  wrote {path}")
    return out


def calibrate_cricket(test_frac: float = 0.2):
    data_dir = os.path.join(ROOT, "data", "cricket")
    model_path = os.path.join(data_dir, "winner_model.joblib")
    ds_path = os.path.join(data_dir, "training_dataset.csv")
    print(f"=== Cricket winner calibration (target {TARGET_ACCURACY:.0%}) ===")
    if not os.path.isfile(model_path) or not os.path.isfile(ds_path):
        print("  skipped (missing model or dataset)")
        return None

    bundle = joblib.load(model_path)
    df = pd.read_csv(ds_path)
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.sort_values("Date").reset_index(drop=True)
    cutoff = int(len(df) * (1 - test_frac))
    hold = df.iloc[cutoff:].dropna(subset=["Winner"]).reset_index(drop=True)
    features = bundle["features"]
    X = hold[features]
    model = bundle["model"]
    proba = model.predict_proba(X)
    classes = list(model.classes_)
    # Winner=1 means Team1 won in the cricket training set.
    if 1 in classes:
        p_team1 = proba[:, classes.index(1)]
    else:
        p_team1 = proba[:, 0]
    y = hold["Winner"].astype(int).values
    full_acc = float(((p_team1 >= 0.5).astype(int) == y).mean())
    best, curve = _sweep_binary(y, p_team1, min_picks=MIN_PICKS_CRICKET)

    # Also report per-competition slices when a competition column exists.
    by_comp = {}
    comp_col = next((c for c in ("Competition", "competition", "League", "league") if c in hold.columns), None)
    if comp_col:
        for comp, sub in hold.groupby(comp_col):
            Xs = sub[features]
            ps = model.predict_proba(Xs)
            if 1 in classes:
                p1 = ps[:, classes.index(1)]
            else:
                p1 = ps[:, 0]
            ys = sub["Winner"].astype(int).values
            if len(ys) < max(8, MIN_PICKS_CRICKET // 2):
                by_comp[str(comp)] = {"n": int(len(ys)), "full_slate_acc": None, "skipped": True}
                continue
            b, _ = _sweep_binary(ys, p1, min_picks=max(8, MIN_PICKS_CRICKET // 2))
            by_comp[str(comp)] = {
                "n": int(len(ys)),
                "full_slate_acc": float(((p1 >= 0.5).astype(int) == ys).mean()),
                "best": b,
            }

    out = {
        "sport": "cricket",
        "league": "cricket",
        "target_accuracy": TARGET_ACCURACY,
        "oos_n": len(hold),
        "full_slate_acc": full_acc,
        "model_name": bundle.get("model_name"),
        "shipped_holdout_accuracy": bundle.get("holdout_accuracy"),
        "markets": {
            "match_winner": {"best": best, "curve_tail": curve[-5:]},
        },
        "by_competition": by_comp,
    }
    path = os.path.join(data_dir, "confidence_thresholds.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print(f"  full-slate OOS: {full_slate_acc_str(full_acc)} n={len(hold)}")
    print(f"  match_winner: {best}")
    for comp, info in by_comp.items():
        print(f"  [{comp}] {info}")
    print(f"  wrote {path}")
    return out


def calibrate_tennis(test_frac: float = 0.15):
    from math import erf, sqrt

    data_dir = os.path.join(ROOT, "data", "tennis")
    model_path = os.path.join(data_dir, "ml_ensemble_model.joblib")
    markets_path = os.path.join(data_dir, "ml_markets_model.joblib")
    ds_path = os.path.join(data_dir, "training_dataset.csv")
    print(f"=== Tennis (ATP) calibration (target {TARGET_ACCURACY:.0%}) ===")
    if not os.path.isfile(model_path) or not os.path.isfile(ds_path):
        print("  skipped (missing model or dataset)")
        return None

    bundle = joblib.load(model_path)
    df = pd.read_csv(ds_path)
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.sort_values("Date").reset_index(drop=True)
    cutoff = int(len(df) * (1 - test_frac))
    hold = df.iloc[cutoff:].dropna(subset=["Player1Win"]).reset_index(drop=True)
    features = bundle["features"]
    X = hold[features]
    model = bundle["model"]
    proba = model.predict_proba(X)
    classes = list(model.classes_)
    p1 = proba[:, classes.index(1)] if 1 in classes else proba[:, -1]
    y = hold["Player1Win"].astype(int).values
    full_acc = float(((p1 >= 0.5).astype(int) == y).mean())
    best, curve = _sweep_binary(y, p1, min_picks=MIN_PICKS_TENNIS)
    markets_out = {"match_winner": {"best": best, "curve_tail": curve[-5:]}}

    def _pos(mdl, Xframe):
        pr = mdl.predict_proba(Xframe)
        cls = list(mdl.classes_)
        return pr[:, cls.index(1)] if 1 in cls else pr[:, -1]

    def _sweep_side_correct(conf, side_correct):
        best_local = None
        rows = []
        for t in np.round(np.arange(0.50, 0.91, 0.01), 2):
            mask = conf >= t
            n = int(mask.sum())
            if n < MIN_PICKS_TENNIS:
                rows.append({"threshold": float(t), "n": n, "acc": None, "coverage": float(mask.mean())})
                continue
            acc = float(side_correct[mask].mean())
            rows.append({"threshold": float(t), "n": n, "acc": acc, "coverage": float(mask.mean())})
            if acc >= TARGET_ACCURACY and best_local is None:
                best_local = {
                    "threshold": float(t), "n": n, "acc": acc,
                    "coverage": float(mask.mean()), "met_target": True,
                }
        if best_local is None:
            viable = [r for r in rows if r["acc"] is not None]
            if viable:
                best_local = max(viable, key=lambda r: (r["acc"], -r["threshold"]))
                best_local["met_target"] = best_local["acc"] >= TARGET_ACCURACY
        return best_local, rows

    if os.path.isfile(markets_path):
        mb = joblib.load(markets_path)
        m_models = mb.get("models") or {}
        m_features = mb.get("features") or features
        hold_m = hold.dropna(subset=["OverSets", "TotalGames", "GamesMargin", "Player2WonSet"]).copy()
        if not hold_m.empty:
            Xm = hold_m[m_features]
            if "over_sets" in m_models:
                p_over = _pos(m_models["over_sets"]["model"], Xm)
                y_over = hold_m["OverSets"].astype(int).values
                conf = np.maximum(p_over, 1.0 - p_over)
                side_correct = ((p_over >= 0.5).astype(int) == y_over)
                best_sets, rows = _sweep_side_correct(conf, side_correct)
                markets_out["total_sets"] = {"best": best_sets, "curve_tail": rows[-5:]}
                print(f"  total_sets: {best_sets}")
            if "p2_wins_set" in m_models:
                p_set = _pos(m_models["p2_wins_set"]["model"], Xm)
                y_set = hold_m["Player2WonSet"].astype(int).values
                best_set, curve_set = _sweep_binary(y_set, p_set, min_picks=MIN_PICKS_TENNIS)
                markets_out["player_win_set"] = {"best": best_set, "curve_tail": curve_set[-5:]}
                print(f"  player_win_set: {best_set}")
            if "total_games" in m_models:
                pred_g = np.asarray(m_models["total_games"]["model"].predict(Xm), dtype=float)
                actual_g = hold_m["TotalGames"].astype(float).values
                lines = np.floor(pred_g) + 0.5
                std = max(float(m_models["total_games"].get("holdout_std") or 4.5), 1e-6)
                p_over_g = np.array([
                    1.0 - 0.5 * (1.0 + erf(((line - pred) / std) / sqrt(2.0)))
                    for pred, line in zip(pred_g, lines)
                ])
                conf_g = np.maximum(p_over_g, 1.0 - p_over_g)
                side_hit = ((p_over_g >= 0.5) & (actual_g > lines)) | ((p_over_g < 0.5) & (actual_g < lines))
                best_g, rows_g = _sweep_side_correct(conf_g, side_hit)
                markets_out["total_games"] = {"best": best_g, "curve_tail": rows_g[-5:]}
                print(f"  total_games: {best_g}")
            if "games_margin" in m_models:
                pred_m = np.asarray(m_models["games_margin"]["model"].predict(Xm), dtype=float)
                actual_m = hold_m["GamesMargin"].astype(float).values
                line_h = 3.5
                std_m = max(float(m_models["games_margin"].get("holdout_std") or 5.0), 1e-6)
                dog_minus_fav = -pred_m
                p_cover = np.array([
                    1.0 - 0.5 * (1.0 + erf((((-line_h) - mu) / std_m) / sqrt(2.0)))
                    for mu in dog_minus_fav
                ])
                actual_cover = (-actual_m) > -line_h
                conf_h = np.maximum(p_cover, 1.0 - p_cover)
                tip_cover = p_cover >= 0.5
                hit_h = (tip_cover & actual_cover) | ((~tip_cover) & (~actual_cover))
                best_h, rows_h = _sweep_side_correct(conf_h, hit_h)
                markets_out["handicap"] = {"best": best_h, "curve_tail": rows_h[-5:]}
                print(f"  handicap: {best_h}")

    out = {
        "sport": "tennis",
        "league": "atp",
        "target_accuracy": TARGET_ACCURACY,
        "oos_n": len(hold),
        "full_slate_acc": full_acc,
        "model_name": bundle.get("model_name"),
        "shipped_holdout_accuracy": bundle.get("holdout_accuracy"),
        "markets": markets_out,
    }
    path = os.path.join(data_dir, "confidence_thresholds.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print(f"  full-slate OOS: {full_acc*100:.1f}% n={len(hold)}")
    print(f"  match_winner: {best}")
    print(f"  wrote {path}")
    return out


def full_slate_acc_str(acc):
    return f"{acc*100:.1f}%"


def main():
    keys = sys.argv[1:] or ["mlb", "npb", "nba", "nhl", "cricket", "tennis"]
    summary = {}
    for key in keys:
        if key in ("mlb", "npb", "nba", "nhl"):
            # NBA/NHL use the exact same Elo+ML/moneyline shape as MLB/NPB
            # (HomeWin label, "Date" column, bundle["features"] selects the
            # right columns) -- calibrate_baseball() needs no changes.
            summary[key] = calibrate_baseball(key)
        elif key == "cricket":
            summary[key] = calibrate_cricket()
        elif key == "tennis":
            summary[key] = calibrate_tennis()
        else:
            print(f"Unknown key: {key} (use mlb|npb|nba|nhl|cricket|tennis)")
    print("\n=== High-confidence OOS (target 70%) ===")
    for key, rep in summary.items():
        if not rep:
            print(f"\n{key}: skipped")
            continue
        print(f"\n{key} full-slate={rep.get('full_slate_acc', 0)*100:.1f}%:")
        for mkt, info in rep.get("markets", {}).items():
            b = info.get("best") or {}
            met = "HIT" if b.get("met_target") else "BEST"
            print(
                f"  {mkt}: {met} acc={b.get('acc', 0)*100:.1f}% "
                f"@ p>={b.get('threshold')} "
                f"coverage={b.get('coverage', 0)*100:.1f}% n={b.get('n')}"
            )


if __name__ == "__main__":
    main()
