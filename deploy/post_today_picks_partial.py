#!/usr/bin/env python3
"""Post Today's Picks RESULT photo even if one leg is still pending."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

os.chdir("/home/ubuntu/Degen_predictions/webapp")
sys.path.insert(0, ".")

import combo_results
import daily_digest

DAY = os.environ.get("RESULT_DAY") or "2026-08-12"


def grade_legs(legs):
    out = []
    for leg in legs or []:
        if not isinstance(leg, dict):
            continue
        score = combo_results.lookup_score_for_leg(leg) or {}
        status, note = combo_results.settle_leg_against_score(
            leg.get("market") or "",
            leg.get("pick") or "",
            score,
        )
        row = dict(leg)
        row["status"] = status
        row["actual_note"] = note
        out.append(row)
    slip_status = combo_results._roll_up_slip_status([r["status"] for r in out])
    return out, slip_status


def post_item(item, *, force_partial: bool = False) -> bool:
    status = item["status"]
    decided = sum(1 for leg in item["legs"] if leg.get("status") in (
        combo_results.WON, combo_results.LOST, combo_results.VOID, combo_results.PUSH,
    ))
    if status == combo_results.PENDING and not (force_partial and decided):
        print("   skip pending")
        return False
    # For partial cards, show overall as pending but still render image.
    render_status = status if status != combo_results.PENDING else "pending"
    item = dict(item)
    item["status"] = render_status
    text = combo_results.format_telegram_result_text(item)
    if status == combo_results.PENDING:
        text = text.replace("RESULT: PENDING", "RESULT: IN PROGRESS", 1)
    image = daily_digest.build_telegram_result_image(item)
    if image and image.get("png_bytes"):
        png = image["png_bytes"]
        cap = image.get("caption") or text
        if status == combo_results.PENDING:
            cap = cap.replace("RESULT · PENDING", "RESULT · IN PROGRESS", 1)
        print("   image_bytes", len(png))
    else:
        png = None
        cap = text
        print("   IMAGE FAILED; text only")
    ok, msg = daily_digest.post_telegram_result_reply(
        text, reply_to_message_id=None, png_bytes=png, caption=cap,
    )
    print("   post:", ok, msg)
    return bool(ok)


def main() -> int:
    digest = json.loads(
        Path(f"/home/ubuntu/Degen_predictions/data/daily_digest/{DAY}.json")
        .read_text(encoding="utf-8")
    )
    digest["date"] = DAY
    combo_results.bust_live_score_caches()
    print("settle", combo_results.settle_pending())

    posted = failed = 0
    picks = [p for p in (digest.get("picks") or []) if isinstance(p, dict)]
    if picks:
        graded, status = grade_legs(picks)
        item = {
            "kind": "combo",
            "title": "Today's Picks",
            "slate_date": DAY,
            "status": status,
            "legs": graded,
        }
        print(f"-- Today's Picks => {status}")
        for leg in graded:
            print("  ", leg.get("matchup"), "=>", leg.get("status"), leg.get("actual_note") or "")
        # Always post Today's Picks card if any leg decided (partial ok).
        if post_item(item, force_partial=True):
            posted += 1
        else:
            failed += 1
        time.sleep(0.45)

    for title, combo in daily_digest.iter_featured_combo_slips(digest):
        graded, status = grade_legs(list(combo.get("legs") or []))
        item = {
            "kind": "combo",
            "title": title,
            "slate_date": DAY,
            "status": status,
            "legs": graded,
        }
        print(f"-- {title} => {status}")
        for leg in graded:
            print("  ", leg.get("matchup"), "=>", leg.get("status"), leg.get("actual_note") or "")
        if status == combo_results.PENDING:
            print("   skip pending combo")
            continue
        if post_item(item):
            posted += 1
        else:
            failed += 1
        time.sleep(0.45)

    print(f"==> done posted={posted} failed={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
