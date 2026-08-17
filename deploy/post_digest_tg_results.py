#!/usr/bin/env python3
"""Inspect + settle + post RESULT photos for today's digest Telegram slips."""
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

DAY = os.environ.get("RESULT_DAY") or daily_digest.today_key()


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
    if not digest_path.is_file():
        print("missing digest", digest_path)
        return 1
    digest = json.loads(digest_path.read_text(encoding="utf-8"))
    digest["date"] = DAY
    print("day", DAY)
    print("pillow check…")
    try:
        from PIL import Image
        print("pillow", getattr(Image, "__version__", "ok"))
    except Exception as exc:
        print("pillow missing", exc)
        return 2

    print("==> settle_pending")
    print(combo_results.settle_pending())

    slips = []
    picks = [p for p in (digest.get("picks") or []) if isinstance(p, dict)]
    print("digest picks", len(picks))
    for p in picks:
        print(" ", p.get("matchup"), "|", p.get("pick"), "|", p.get("market"))
    if picks:
        graded, status = grade_legs(picks)
        slips.append({
            "kind": "combo" if len(graded) > 1 else "single",
            "title": "Today's Picks",
            "slate_date": DAY,
            "status": status,
            "legs": graded,
        })

    featured = list(daily_digest.iter_featured_combo_slips(digest))
    print("featured combos", len(featured))
    for title, combo in featured:
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
    # Audience filter: all wins, at most 2 losses.
    losses_posted = 0
    to_post, to_skip = combo_results.select_telegram_results_for_channel(
        [s for s in slips if s["status"] != combo_results.PENDING],
        max_losses=2,
        losses_already_posted=0,
    )
    pending = [s for s in slips if s["status"] == combo_results.PENDING]
    print(
        "filter post=%s skip=%s pending=%s"
        % (len(to_post), len(to_skip), len(pending))
    )
    for s in to_skip:
        print("   skip", s.get("title"), s.get("status"))

    posted = failed = 0
    for i, item in enumerate(to_post, 1):
        status = item["status"]
        print(f"-- {i}/{len(to_post)} {item['title']} => {status}")
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
        text = combo_results.format_telegram_result_text(item)
        image = daily_digest.build_telegram_result_image(item)
        if not image or not image.get("png_bytes"):
            print("   IMAGE FAILED")
            png = None
            cap = text
        else:
            png = image["png_bytes"]
            cap = image.get("caption") or text
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
            if status == combo_results.LOST:
                losses_posted += 1
        else:
            failed += 1
        time.sleep(0.45)

    print(
        f"==> done posted={posted} failed={failed} "
        f"skipped={len(to_skip)} pending_skipped={len(pending)} "
        f"losses_posted={losses_posted}"
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
