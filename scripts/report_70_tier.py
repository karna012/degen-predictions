import sys
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "webapp"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import today_picks
from leagues import LEAGUES

today_picks.load_sport_thresholds.cache_clear()
today_picks.load_soccer_thresholds.cache_clear()

print("=== ALL SPORTS 70% TIER ===")
print("-- Soccer --")
for k, cfg in LEAGUES.items():
    t = today_picks.load_soccer_thresholds(k)
    hits = [
        f"{m}>={t['markets'][m]:.2f}"
        for m, a in t["available"].items()
        if a and t["markets"].get(m) is not None
    ]
    print(f"  {cfg['label']}: {', '.join(hits) or '(none in 70% tier)'}")

print("-- Baseball / Cricket --")
for lg in ["mlb", "npb", "hundred", "mlc", "ipl", "bbl", "cpl"]:
    t = today_picks.load_sport_thresholds(lg)
    label = "Moneyline" if lg in ("mlb", "npb") else "Match winner"
    key = "moneyline" if lg in ("mlb", "npb") else "match_winner"
    bar = today_picks.sport_market_min_conf(lg, label)
    full = t.get("full_slate_acc")
    full_s = f"{full*100:.1f}%" if isinstance(full, float) else "n/a"
    print(f"  {lg}: full-slate={full_s} 70% bar={bar} avail={t['available'].get(key)}")
