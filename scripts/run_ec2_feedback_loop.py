#!/usr/bin/env python3
"""Cheap EC2 feedback loop: settle published picks/combos + write metrics.

Designed for the small production instance (piggybacked on the existing
data-refresh timer). Does NOT retrain models or load sklearn ensembles.

Run from repo root:
  python scripts/run_ec2_feedback_loop.py
"""
from __future__ import annotations

import json
import os
import sys
import traceback

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEBAPP = os.path.join(REPO_ROOT, "webapp")
OUT_DIR = os.path.join(REPO_ROOT, "data", "feedback")
LATEST_PATH = os.path.join(OUT_DIR, "latest.json")
LIVE_CAL_PATH = os.path.join(OUT_DIR, "live_calibration.json")
HISTORY_PATH = os.path.join(OUT_DIR, "history.jsonl")


def main() -> int:
    sys.path.insert(0, WEBAPP)
    os.chdir(WEBAPP)

    import combo_results  # noqa: WPS433

    print("==> Bust live score caches then settle pending picks + combo slips")
    combo_results.bust_live_score_caches()
    settle = combo_results.settle_pending()
    print(
        "    slips_settled={slips_settled} legs_settled={legs_settled} "
        "picks_settled={picks_settled}".format(**settle)
    )

    print("==> Building rolling feedback report (14d)")
    report = combo_results.feedback_report(days=14)
    report["settle"] = settle

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(LATEST_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, sort_keys=True)
        f.write("\n")

    live = {
        "updated_at": report.get("updated_at"),
        "days": report.get("days"),
        "overall": report.get("overall"),
        "suggested_min_confidence": report.get("suggested_min_confidence"),
        "promote_baseline": report.get("promote_baseline"),
    }
    with open(LIVE_CAL_PATH, "w", encoding="utf-8") as f:
        json.dump(live, f, indent=2, sort_keys=True)
        f.write("\n")

    with open(HISTORY_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "updated_at": report.get("updated_at"),
            "overall": report.get("overall"),
            "suggested_min_confidence": report.get("suggested_min_confidence"),
            "settle": settle,
        }, sort_keys=True) + "\n")

    overall = report.get("overall") or {}
    print(
        "    decided={n} hit_rate={hr} brier={br} suggested_min={sug}".format(
            n=(overall.get("won") or 0) + (overall.get("lost") or 0),
            hr=overall.get("hit_rate"),
            br=overall.get("brier"),
            sug=report.get("suggested_min_confidence"),
        )
    )
    print(f"==> Wrote {LATEST_PATH}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
