#!/usr/bin/env bash
# Warms the combo-picks disk cache right after (re)start so the first real
# visitor doesn't pay the cold-build cost (soccer models + MLB/NPB
# predictions across every league, ~10-15s) that _rebuild_combo_bundle()
# takes when combo_fast.pkl/combo_full.pkl is missing or expired.
#
# Runs as ExecStartPost from degen-predictions.service. Backgrounds its own
# work and exits immediately so it can never delay systemd's startup
# sequence or block real traffic, regardless of how ExecStartPost timing
# is interpreted -- fire-and-forget, safe to fail silently (worst case:
# the next visitor pays the normal cold-cache cost, same as before this
# existed).
(
    sleep 10
    curl -s -o /dev/null --max-time 30 "http://127.0.0.1:8000/todays-picks/combos" || true
) &
disown
exit 0
