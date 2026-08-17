"""
Find per-market confidence thresholds so that Out-of-Sample accuracy is at
least TARGET_ACCURACY (default 70%).

Pure 1X2 football match results often cannot honestly hit 70% on all matches.
This script sweeps each legacy market alone (including raw 1X2 and
favourite-avoids-defeat). Soccer's combined result market is deliberately
not tuned here: evaluate_safer_result.py fits calibration on an earlier
chronological slice and scores one fixed Safer Result rule on a later slice.

Coverage (share of matches with a pick) is reported so we don't pretend every
game qualifies for every market.

Writes model/<league>/confidence_thresholds.json
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd

from backtest_pl import classify, load_models
from leagues import LEAGUES, REPO_ROOT, resolve_league_paths

TARGET_ACCURACY = 0.70
MIN_PICKS = 40  # need enough OOS picks for a stable rate


def _sweep_binary(y_true, p_pos, target=TARGET_ACCURACY):
    """For a binary market: predict positive if p>=t else negative.
    Find lowest t in [0.50, 0.90] where accuracy >= target with enough picks
    that are 'confident' (max(p, 1-p) >= t).
    """
    best = None
    rows = []
    for t in np.round(np.arange(0.50, 0.91, 0.01), 2):
        conf = np.maximum(p_pos, 1.0 - p_pos)
        mask = conf >= t
        n = int(mask.sum())
        if n < MIN_PICKS:
            rows.append({"threshold": float(t), "n": n, "acc": None, "coverage": float(mask.mean())})
            continue
        pred = (p_pos >= 0.5).astype(int)
        # Only score confident rows; prediction is still side of 0.5
        acc = float((pred[mask] == y_true[mask]).mean())
        rows.append({"threshold": float(t), "n": n, "acc": acc, "coverage": float(mask.mean())})
        if acc >= target and best is None:
            best = {"threshold": float(t), "n": n, "acc": acc, "coverage": float(mask.mean())}
    if best is None and rows:
        # Fallback: threshold with best OOS acc among those with enough picks
        viable = [r for r in rows if r["acc"] is not None]
        if viable:
            best = max(viable, key=lambda r: (r["acc"], -r["threshold"]))
            best["met_target"] = best["acc"] >= target
    if best is not None:
        best.setdefault("met_target", best["acc"] >= target)
    return best, rows


def _sweep_result(y_true_codes, proba, inv_map, target=TARGET_ACCURACY):
    """1X2: only take picks where max class probability >= t."""
    best = None
    rows = []
    pred_idx = np.argmax(proba, axis=1)
    conf = proba.max(axis=1)
    pred_codes = np.array([inv_map[i] for i in pred_idx])
    for t in np.round(np.arange(0.40, 0.86, 0.01), 2):
        mask = conf >= t
        n = int(mask.sum())
        if n < MIN_PICKS:
            rows.append({"threshold": float(t), "n": n, "acc": None, "coverage": float(mask.mean())})
            continue
        acc = float((pred_codes[mask] == y_true_codes[mask]).mean())
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


def calibrate_league(league_key, target=TARGET_ACCURACY):
    cfg = resolve_league_paths(LEAGUES[league_key])
    data_dir, model_dir = cfg["data_dir"], cfg["model_dir"]
    print(f"=== {cfg['label']} confidence calibration (target {target:.0%}) ===")

    models, feature_cols, result_label_map = load_models(model_dir)
    inv = {v: k for k, v in result_label_map.items()}
    df = pd.read_csv(f"{data_dir}/train_rows.csv")
    split_idx = int(len(df) * 0.8)
    oos = df.iloc[split_idx:].reset_index(drop=True)
    X = oos[feature_cols]

    out = {"target_accuracy": target, "oos_n": len(oos), "markets": {}}

    # Result 1X2 (kept for honesty / accuracy hub — often cannot hit 70%)
    base, meta, strat = models["result"]
    proba = classify(base, meta, strat, X)
    best, curve = _sweep_result(oos["Target_Result"].astype(str).values, proba, inv, target)
    out["markets"]["result"] = {"best": best, "curve_tail": curve[-5:]}
    print(f"  result: {best}")

    # Binary markets
    for key, target_col, model_key in [
        ("btts", "Target_BTTS", "btts"),
        ("over25", "Target_Over25", "over25"),
        ("favorite_avoids_defeat", "Target_FavoriteAvoidsDefeat", "favorite_avoids_defeat"),
        ("home_more_corners", "Target_HomeMoreCorners", "home_more_corners"),
    ]:
        loaded = models.get(model_key)
        if loaded is None:
            out["markets"][key] = {"skipped": True}
            print(f"  {key}: skipped")
            continue
        base, meta, strat = loaded
        if target_col not in oos.columns or oos[target_col].isna().all():
            out["markets"][key] = {"skipped": True}
            print(f"  {key}: skipped (no target)")
            continue
        mask_valid = oos[target_col].notna()
        p = classify(base, meta, strat, X)[:, 1]
        best, curve = _sweep_binary(
            oos.loc[mask_valid, target_col].astype(int).values,
            p[mask_valid.values],
            target,
        )
        out["markets"][key] = {"best": best}
        print(f"  {key}: {best}")

    # Do not search combined-result thresholds on this evaluation set.
    out["markets"]["result_merged"] = {
        "skipped": True,
        "reason": "retired; use evaluate_safer_result.py",
    }

    path = os.path.join(model_dir, "confidence_thresholds.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print(f"  wrote {path}")
    return out


def main():
    keys = sys.argv[1:] or list(LEAGUES.keys())
    target = TARGET_ACCURACY
    summary = {}
    for key in keys:
        summary[key] = calibrate_league(key, target=target)
    # Print board
    print("\n=== High-confidence OOS accuracy (target 70%) ===")
    for key, rep in summary.items():
        label = LEAGUES[key]["label"]
        print(f"\n{label}:")
        for mkt, info in rep.get("markets", {}).items():
            if info.get("skipped"):
                print(f"  {mkt}: n/a")
                continue
            b = info.get("best") or {}
            met = "HIT" if b.get("met_target") else "BEST"
            print(
                f"  {mkt}: {met} acc={b.get('acc', 0)*100:.1f}% "
                f"@ p>={b.get('threshold')} "
                f"coverage={b.get('coverage', 0)*100:.1f}% n={b.get('n')}"
            )


if __name__ == "__main__":
    main()
