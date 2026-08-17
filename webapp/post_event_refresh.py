#!/usr/bin/env python3
"""After-event automation: bust live caches, settle pending, flag leftovers.

Run on EC2 every 5 minutes (degen-post-event.timer and live-tracker). Does not retrain.
Live scores only — never treat Cricsheet / football-data history as the
sole source for just-finished games.

  python post_event_refresh.py
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime, timezone

WEBAPP = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(WEBAPP)
OUT_DIR = os.path.join(REPO, "data", "feedback")
REPORT_PATH = os.path.join(OUT_DIR, "post_event.json")
LOCK_PATH = os.path.join(OUT_DIR, "post_event.lock")


def _try_lock():
    """Skip this pass if the previous 5-minute run is still posting."""
    os.makedirs(OUT_DIR, exist_ok=True)
    lock_f = open(LOCK_PATH, "a+", encoding="utf-8")
    try:
        import fcntl
        fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_f.close()
        return None
    except ImportError:
        pass
    return lock_f


def _unlock(lock_f) -> None:
    if lock_f is None:
        return
    try:
        import fcntl
        fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass
    try:
        lock_f.close()
    except Exception:
        pass


def _load_production_env() -> None:
    """Pick up Telegram creds when systemd did not inject EnvironmentFile."""
    env_path = os.path.join(REPO, "deploy", "production.env")
    if not os.path.isfile(env_path):
        return
    try:
        with open(env_path, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                if not key or key in os.environ:
                    continue
                os.environ[key] = value.strip().strip('"').strip("'")
    except OSError:
        pass


def main() -> int:
    sys.path.insert(0, WEBAPP)
    os.chdir(WEBAPP)
    _load_production_env()

    lock_f = _try_lock()
    if lock_f is None:
        print("==> Skip: previous post-event run still active")
        return 0
    try:
        return _run()
    finally:
        _unlock(lock_f)


def _run() -> int:

    import combo_results
    import live_cache_bust

    print("==> Mark live caches stale (all gunicorn workers)")
    live_cache_bust.mark_stale()

    print("==> Bust live score caches")
    cleared = combo_results.bust_live_score_caches()
    dropped = combo_results.drop_html_result_caches()
    print("    cleared", ",".join(cleared))
    if dropped:
        print("    dropped html", ",".join(dropped))

    print("==> Drop stale Today's Picks / combo disk cache")
    dropped_combo = []
    for name in ("combo_full.pkl", "combo_fast.pkl"):
        path = os.path.join(WEBAPP, name)
        try:
            os.remove(path)
            dropped_combo.append(name)
        except FileNotFoundError:
            pass
        except OSError as exc:
            print("    keep", name, exc)
    if dropped_combo:
        print("    dropped", ",".join(dropped_combo))

    print("==> Settle pending combo legs + published picks")
    settle = combo_results.settle_pending()
    print(
        "    legs_settled={legs_settled}/{legs_checked} "
        "slips_settled={slips_settled} picks_settled={picks_settled}/{picks_checked}".format(
            **settle
        )
    )

    print("==> Re-check recently settled scores")
    resettled = combo_results.resettle_settled(days=5)
    print(
        "    legs_changed={legs_changed}/{legs_checked} "
        "slips_changed={slips_changed} "
        "picks_changed={picks_changed}/{picks_checked}".format(
            **resettled
        )
    )
    settle["resettled"] = resettled

    overdue = combo_results.list_overdue_pending(grace_hours=6.0)
    if overdue:
        print(f"==> {len(overdue)} overdue pending (kickoff >6h ago, still ungraded)")
        for row in overdue[:12]:
            print(
                "    {kind} {league_id} {matchup} {market} "
                "kickoff={kickoff_iso} +{hours_overdue}h".format(**row)
            )
    else:
        print("==> No overdue pending")

    indexnow_result = {"skipped": True}
    try:
        import indexnow
        indexnow_result = indexnow.notify_after_settle(settle)
        print("==> IndexNow", indexnow_result)
    except Exception as exc:
        print("==> IndexNow failed:", exc)
        indexnow_result = {"ok": False, "error": str(exc)}

    catchup = {"skipped": True, "reason": "admin-only channel posts"}
    tg_results = {"skipped": True, "reason": "admin-only channel posts"}
    digest_results = {"skipped": True, "reason": "admin-only channel posts"}
    results_waiting = {"telegram": 0, "twitter": 0}
    try:
        results_waiting = {
            "telegram": len(combo_results.list_telegram_results_due(channel="telegram")),
            "twitter": len(combo_results.list_telegram_results_due(channel="twitter")),
        }
        print(
            "==> Results waiting for admin "
            f"telegram={results_waiting['telegram']} x={results_waiting['twitter']}"
        )
    except Exception as exc:
        print("==> Results-waiting count failed:", exc)

    try:
        import daily_digest
        latest = daily_digest.load_latest_digest()
        if latest:
            cleaned = daily_digest.filter_digest_upcoming(latest)
            daily_digest.write_digest(cleaned)
            print(
                "==> Digest fixtures pruned "
                f"picks={len(cleaned.get('picks') or [])} "
                f"featured={cleaned.get('featured_combo_count') or 0}"
            )
    except Exception as exc:
        print("==> Digest fixture prune failed:", exc)

    try:
        import daily_digest
        if daily_digest.channel_auto_post_enabled():
            catchup = daily_digest.maybe_catch_up_channel_posts()
            print("==> Channel catch-up", catchup if catchup.get("skipped") else catchup.get("channels"))
        else:
            print("==> Channel posts skipped (admin sends Telegram/X)")
    except Exception as exc:
        print("==> Channel catch-up failed:", exc)
        catchup = {"ok": False, "error": str(exc)}

    try:
        import daily_digest
        if daily_digest.channel_auto_post_enabled():
            tg_results = daily_digest.post_due_telegram_results()
            print(
                "==> Telegram results "
                "checked={checked} posted={posted} failed={failed} "
                "x_posted={twitter_posted} x_failed={twitter_failed}".format(
                    checked=tg_results.get("checked", 0),
                    posted=tg_results.get("posted", 0),
                    failed=tg_results.get("failed", 0),
                    twitter_posted=tg_results.get("twitter_posted", 0),
                    twitter_failed=tg_results.get("twitter_failed", 0),
                )
            )
            digest_results = daily_digest.post_recent_digest_telegram_results()
            print(
                "==> Digest results "
                "posted={posted} failed={failed} pending={pending} "
                "x_posted={twitter_posted} x_failed={twitter_failed}".format(
                    posted=digest_results.get("posted", 0),
                    failed=digest_results.get("failed", 0),
                    pending=digest_results.get("pending", 0),
                    twitter_posted=digest_results.get("twitter_posted", 0),
                    twitter_failed=digest_results.get("twitter_failed", 0),
                )
            )
        else:
            print("==> Telegram/X results skipped (admin posts from /admin)")
    except Exception as exc:
        print("==> Telegram/X results failed:", exc)
        tg_results = {"ok": False, "error": str(exc)}

    os.makedirs(OUT_DIR, exist_ok=True)
    report = {
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "cleared_caches": cleared,
        "dropped_combo_cache": dropped_combo,
        "settle": settle,
        "overdue_pending": overdue,
        "overdue_count": len(overdue),
        "indexnow": indexnow_result,
        "channel_catchup": catchup,
        "telegram_results": tg_results,
        "digest_results": digest_results,
        "results_waiting": results_waiting,
    }
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
        f.write("\n")
    print(f"==> Wrote {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
