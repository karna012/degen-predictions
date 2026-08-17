#!/usr/bin/env python3
"""One-off: post RESULT *photos* for 2026-08-11 digest Telegram slips."""
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

DAY = "2026-08-11"


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


def main() -> int:
    digest_path = Path(f"/home/ubuntu/Degen_predictions/data/daily_digest/{DAY}.json")
    digest = json.loads(digest_path.read_text(encoding="utf-8"))
    digest["date"] = DAY

    print("==> settle_pending")
    print(combo_results.settle_pending())

    slips = []
    picks = [p for p in (digest.get("picks") or []) if isinstance(p, dict)]
    if picks:
        graded, status = grade_legs(picks)
        slips.append({
            "kind": "combo" if len(graded) > 1 else "single",
            "title": "Today's Picks",
            "slate_date": DAY,
            "status": status,
            "legs": graded,
        })

    for title, combo in daily_digest.iter_featured_combo_slips(digest):
        graded, status = grade_legs(list(combo.get("legs") or []))
        if not graded:
            continue
        slips.append({
            "kind": "combo",
            "title": title,
            "slate_date": DAY,
            "status": status,
            "legs": graded,
        })

    print("==> slips", len(slips))
    posted = failed = 0
    for i, item in enumerate(slips, 1):
        status = item["status"]
        print(f"-- {i}/{len(slips)} {item['title']} => {status}")
        for leg in item["legs"]:
            print(
                "   ",
                (leg.get("matchup") or "")[:55],
                "|",
                leg.get("pick"),
                "=>",
                leg.get("status"),
                (leg.get("actual_note") or ""),
            )
        if status == combo_results.PENDING:
            print("   skip pending")
            continue
        text = f"Yesterday ({DAY})\n" + combo_results.format_telegram_result_text(item)
        image = daily_digest.build_telegram_result_image(item)
        if not image or not image.get("png_bytes"):
            print("   image render failed; falling back to text")
            png = None
            cap = text
        else:
            png = image["png_bytes"]
            cap = f"Yesterday ({DAY})\n{image.get('caption') or text}"
            print("   image_bytes", len(png))
        ok, msg = daily_digest.post_telegram_result_reply(
            text,
            reply_to_message_id=None,
            png_bytes=png,
            caption=cap,
        )
        print("   post:", ok, msg)
        if ok:
            posted += 1
        else:
            failed += 1
        time.sleep(0.45)

    pending = sum(1 for s in slips if s["status"] == combo_results.PENDING)
    print(f"==> done posted={posted} failed={failed} pending_skipped={pending}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
