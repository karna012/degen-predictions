#!/usr/bin/env python3
"""Promote-only-if-better gate for weekly retrain.

Compares newly trained artifacts against a pre-train backup. Any category
whose holdout score got *worse* is restored from backup so the site never
ships a regression. Categories that improved (or tied within epsilon) keep
the new models.

Usage (from repo root):
  # before training
  python scripts/promote_gate.py backup

  # after training -- restores losers, writes report, exit 0 if anything
  # to deploy / no regressions left; exit 2 if everything was rolled back
  python scripts/promote_gate.py apply
"""
from __future__ import annotations

import json
import os
import pickle
import shutil
import sys
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKUP_ROOT = os.path.join(REPO, "data", "feedback", "model_backup")
REPORT_PATH = os.path.join(REPO, "data", "feedback", "promote_gate_report.json")

# Accuracy-like metrics: higher is better. MAE-like: lower is better.
ACC_EPS = 1e-4  # treat float noise as tie
MAE_EPS = 1e-3


def _joblib_load(path):
    import joblib
    return joblib.load(path)


def _soccer_score(model_dir: str) -> float | None:
    """Mean of classification best-strategy holdout accuracies (higher better)."""
    path = os.path.join(model_dir, "training_metrics.pkl")
    if not os.path.isfile(path):
        return None
    with open(path, "rb") as f:
        metrics = pickle.load(f)
    accs = []
    for name, info in (metrics or {}).items():
        if not isinstance(info, dict) or info.get("skipped"):
            continue
        if "per_model_acc" in info or "stacked_acc" in info or "best_strategy" in info:
            strat = info.get("best_strategy")
            if strat and strat in (info.get("per_model_acc") or {}):
                accs.append(float(info["per_model_acc"][strat]))
            elif info.get("stacked_acc") is not None and strat == "stacked":
                accs.append(float(info["stacked_acc"]))
            elif info.get("avg_ensemble_acc") is not None and strat == "avg_ensemble":
                accs.append(float(info["avg_ensemble_acc"]))
            else:
                # best of known accuracy fields
                for k in ("stacked_acc", "avg_ensemble_acc"):
                    if info.get(k) is not None:
                        accs.append(float(info[k]))
                        break
                else:
                    per = info.get("per_model_acc") or {}
                    if per:
                        accs.append(float(max(per.values())))
    return sum(accs) / len(accs) if accs else None


def _joblib_acc(path: str) -> float | None:
    if not os.path.isfile(path):
        return None
    try:
        bundle = _joblib_load(path)
    except Exception:
        return None
    if not isinstance(bundle, dict):
        return None
    for key in ("holdout_accuracy", "ensemble_holdout_accuracy", "test_accuracy"):
        if bundle.get(key) is not None:
            return float(bundle[key])
    return None


def _joblib_mae(path: str) -> float | None:
    if not os.path.isfile(path):
        return None
    try:
        bundle = _joblib_load(path)
    except Exception:
        return None
    if not isinstance(bundle, dict):
        return None
    if bundle.get("holdout_mae") is not None:
        return float(bundle["holdout_mae"])
    return None


def _cricket_winner_acc(data_dir: str) -> float | None:
    return _joblib_acc(os.path.join(data_dir, "winner_model.joblib"))


def _cricket_runs_mae(data_dir: str) -> float | None:
    maes = []
    for name in ("team1_runs_model.joblib", "team2_runs_model.joblib", "total_runs_model.joblib"):
        m = _joblib_mae(os.path.join(data_dir, name))
        if m is not None:
            maes.append(m)
    return sum(maes) / len(maes) if maes else None


def _investment_ic(path: str) -> float | None:
    """Best mean IC across horizons if metrics JSON exists."""
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    ics = []
    # crypto metrics file shape varies; also try ranked companion
    if isinstance(raw, dict):
        if "model_ic" in raw:
            try:
                ics.append(float(raw["model_ic"]))
            except (TypeError, ValueError):
                pass
        for block in raw.values() if not any(k.startswith("horizon") for k in raw) else [raw]:
            if isinstance(block, dict):
                for key in ("model_ic", "mean_ic", "cross_sectional_ic"):
                    if block.get(key) is not None:
                        try:
                            ics.append(float(block[key]))
                        except (TypeError, ValueError):
                            pass
                mm = block.get("model_metrics") or {}
                if isinstance(mm, dict):
                    for m in mm.values():
                        if isinstance(m, dict) and m.get("mean_ic") is not None:
                            try:
                                ics.append(float(m["mean_ic"]))
                            except (TypeError, ValueError):
                                pass
    return max(ics) if ics else None


def categories():
    """(key, label, live_paths, higher_is_better, score_fn(live_root_relative))."""
    sys.path.insert(0, os.path.join(REPO, "scripts"))
    from leagues import LEAGUES  # noqa: WPS433

    cats = []
    for key, cfg in LEAGUES.items():
        # Canonical live dir = repo-root model/<league> (same as Flask + sync).
        live = os.path.join(REPO, cfg["model_dir"])
        cats.append({
            "key": f"soccer_{key}",
            "label": f"Soccer {cfg['label']}",
            "paths": [live],
            "higher_better": True,
            "score": lambda p=live: _soccer_score(p),
        })

    cats.append({
        "key": "mlb",
        "label": "MLB",
        "paths": [os.path.join(REPO, "data", "mlb", "ml_ensemble_model.joblib")],
        "higher_better": True,
        "score": lambda: _joblib_acc(os.path.join(REPO, "data", "mlb", "ml_ensemble_model.joblib")),
    })
    cats.append({
        "key": "npb",
        "label": "NPB",
        "paths": [os.path.join(REPO, "data", "npb", "ml_ensemble_model.joblib")],
        "higher_better": True,
        "score": lambda: _joblib_acc(os.path.join(REPO, "data", "npb", "ml_ensemble_model.joblib")),
    })
    cats.append({
        "key": "nfl",
        "label": "NFL",
        "paths": [os.path.join(REPO, "data", "nfl", "ml_ensemble_model.joblib")],
        "higher_better": True,
        "score": lambda: _joblib_acc(os.path.join(REPO, "data", "nfl", "ml_ensemble_model.joblib")),
    })
    cats.append({
        "key": "nba",
        "label": "NBA",
        "paths": [os.path.join(REPO, "data", "nba", "ml_ensemble_model.joblib")],
        "higher_better": True,
        "score": lambda: _joblib_acc(os.path.join(REPO, "data", "nba", "ml_ensemble_model.joblib")),
    })
    cats.append({
        "key": "nhl",
        "label": "NHL",
        "paths": [os.path.join(REPO, "data", "nhl", "ml_ensemble_model.joblib")],
        "higher_better": True,
        "score": lambda: _joblib_acc(os.path.join(REPO, "data", "nhl", "ml_ensemble_model.joblib")),
    })
    cricket_dir = os.path.join(REPO, "data", "cricket")
    cats.append({
        "key": "cricket_winner",
        "label": "Cricket winner",
        "paths": [
            os.path.join(cricket_dir, "winner_model.joblib"),
            os.path.join(cricket_dir, "team1_runs_model.joblib"),
            os.path.join(cricket_dir, "team2_runs_model.joblib"),
            os.path.join(cricket_dir, "total_runs_model.joblib"),
        ],
        "higher_better": True,
        "score": lambda: _cricket_winner_acc(cricket_dir),
        # Also gate runs MAE: if winner improves but runs much worse, still restore all cricket
        "secondary": {
            "higher_better": False,
            "score": lambda: _cricket_runs_mae(cricket_dir),
        },
    })
    inv = os.path.join(REPO, "data", "investment_scoring")
    cats.append({
        "key": "investment",
        "label": "Crypto/stocks",
        "paths": [
            os.path.join(inv, "ranked_assets.csv"),
            os.path.join(inv, "crypto_horizon_model_metrics.json"),
            os.path.join(inv, "crypto_history_span.json"),
        ],
        "higher_better": True,
        "score": lambda: _investment_ic(os.path.join(inv, "crypto_horizon_model_metrics.json")),
    })
    return cats


def _backup_path(live_path: str) -> str:
    rel = os.path.relpath(live_path, REPO)
    return os.path.join(BACKUP_ROOT, rel)


def cmd_backup() -> int:
    os.makedirs(BACKUP_ROOT, exist_ok=True)
    # wipe previous backup so stale files don't linger
    if os.path.isdir(BACKUP_ROOT):
        shutil.rmtree(BACKUP_ROOT)
    os.makedirs(BACKUP_ROOT, exist_ok=True)

    copied = []
    for cat in categories():
        for path in cat["paths"]:
            if not os.path.exists(path):
                continue
            dest = _backup_path(path)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            if os.path.isdir(path):
                if os.path.isdir(dest):
                    shutil.rmtree(dest)
                shutil.copytree(path, dest)
            else:
                shutil.copy2(path, dest)
            copied.append(os.path.relpath(path, REPO))
    meta = {
        "backed_up_at": datetime.now(timezone.utc).isoformat(),
        "paths": copied,
    }
    with open(os.path.join(BACKUP_ROOT, "backup_meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    print(f"Backed up {len(copied)} paths -> {BACKUP_ROOT}")
    return 0


def _score_from_backup(cat, score_fn_live):
    """Run the same scorer but against backup copies by temporarily swapping
    is hard; instead re-implement path-aware scoring for known keys."""
    key = cat["key"]
    if key.startswith("soccer_"):
        live_dir = cat["paths"][0]
        bak = _backup_path(live_dir)
        return _soccer_score(bak) if os.path.isdir(bak) else None
    if key == "mlb":
        return _joblib_acc(_backup_path(cat["paths"][0]))
    if key == "npb":
        return _joblib_acc(_backup_path(cat["paths"][0]))
    if key in ("nfl", "nba", "nhl"):
        return _joblib_acc(_backup_path(cat["paths"][0]))
    if key == "cricket_winner":
        bak_dir = _backup_path(os.path.join(REPO, "data", "cricket"))
        # _backup_path of a file goes to .../data/cricket/winner_model.joblib
        # parent dir:
        bak_dir = os.path.dirname(_backup_path(cat["paths"][0]))
        return _cricket_winner_acc(bak_dir)
    if key == "investment":
        return _investment_ic(_backup_path(cat["paths"][1]))
    return None


def _secondary_from_backup(cat):
    if cat["key"] != "cricket_winner":
        return None
    bak_dir = os.path.dirname(_backup_path(cat["paths"][0]))
    return _cricket_runs_mae(bak_dir)


def _improved(old, new, higher_better: bool, eps: float) -> str:
    """Return 'promote' | 'keep_old' | 'tie' | 'no_baseline' | 'no_new' | 'unscored'."""
    if new is None and old is None:
        return "unscored"  # no metric either side -- don't block deploy
    if new is None:
        return "no_new"
    if old is None:
        return "no_baseline"
    if higher_better:
        if new > old + eps:
            return "promote"
        if new >= old - eps:
            return "tie"
        return "keep_old"
    if new < old - eps:
        return "promote"
    if new <= old + eps:
        return "tie"
    return "keep_old"


def _restore_cat(cat) -> None:
    for path in cat["paths"]:
        bak = _backup_path(path)
        if not os.path.exists(bak):
            continue
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.isdir(bak):
            if os.path.isdir(path):
                shutil.rmtree(path)
            shutil.copytree(bak, path)
        else:
            shutil.copy2(bak, path)


def cmd_apply() -> int:
    if not os.path.isdir(BACKUP_ROOT):
        print("No backup found -- refusing to promote (run backup first).")
        return 2

    decisions = []
    promoted = 0
    restored = 0
    for cat in categories():
        old = _score_from_backup(cat, cat["score"])
        new = cat["score"]()
        eps = ACC_EPS if cat["higher_better"] else MAE_EPS
        decision = _improved(old, new, cat["higher_better"], eps)

        # Cricket secondary MAE gate
        if cat.get("secondary") and decision in ("promote", "tie", "no_baseline"):
            old2 = _secondary_from_backup(cat)
            new2 = cat["secondary"]["score"]()
            d2 = _improved(old2, new2, cat["secondary"]["higher_better"], MAE_EPS)
            if d2 == "keep_old":
                decision = "keep_old"

        action = "keep_new"
        if decision == "keep_old":
            _restore_cat(cat)
            action = "restored_old"
            restored += 1
        elif decision == "no_new":
            _restore_cat(cat)
            action = "restored_old_missing_new"
            restored += 1
        elif decision in ("promote", "tie", "no_baseline", "unscored"):
            action = "keep_new"
            if decision == "promote":
                promoted += 1
        else:
            action = "keep_new"

        row = {
            "key": cat["key"],
            "label": cat["label"],
            "old": old,
            "new": new,
            "decision": decision,
            "action": action,
            "higher_better": cat["higher_better"],
        }
        decisions.append(row)
        arrow = "↑" if (new is not None and old is not None and (
            (cat["higher_better"] and new > old) or
            ((not cat["higher_better"]) and new < old)
        )) else ("↓" if (new is not None and old is not None) else "·")
        print(
            f"[{cat['label']}] old={old} new={new} {arrow} -> {action} ({decision})"
        )

    report = {
        "applied_at": datetime.now(timezone.utc).isoformat(),
        "promoted": promoted,
        "restored": restored,
        "decisions": decisions,
    }
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    # Deploy if at least one category kept new models (including ties / first run).
    kept_new = sum(1 for d in decisions if d["action"] == "keep_new")
    if kept_new == 0:
        print("All categories rolled back -- skipping deploy.")
        return 2
    if restored and not promoted:
        print(f"Kept {kept_new} categories (ties/first-run); restored {restored} regressions.")
    else:
        print(f"Promote gate OK: {promoted} improved, {restored} restored, {kept_new} shipping.")
    return 0


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("backup", "apply"):
        print(__doc__)
        return 1
    if sys.argv[1] == "backup":
        return cmd_backup()
    return cmd_apply()


if __name__ == "__main__":
    raise SystemExit(main())
