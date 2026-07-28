"""Backfills the in-progress current MLB season into the Elo pipeline.

Retrosheet only publishes a season's game log after that season ends, so the
2026 season isn't in data/mlb/raw/ yet from download_mlb_data.py. This pulls
completed 2026 games day-by-day from ESPN's public scoreboard (the same
endpoint mlb_fixtures.py already uses for live fixtures) and writes them out
in the same trimmed format as the Retrosheet files, so build_elo.py picks
them up automatically alongside the historical seasons.

Run from repo root: python scripts/mlb/download_current_season_espn.py
"""
import datetime
import os
import sys
import time

import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mlb_config import DATA_DIR, ESPN_TO_RETROSHEET  # noqa: E402

RAW_DIR = os.path.join(DATA_DIR, "raw")
ESPN_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard"
SEASON_START = datetime.date(2026, 3, 1)  # a couple weeks before Opening Day, covers any early games


def fetch_day(day):
    resp = requests.get(ESPN_SCOREBOARD_URL, params={"dates": day.strftime("%Y%m%d")}, timeout=10)
    resp.raise_for_status()
    return resp.json().get("events", [])


def main():
    yesterday = datetime.datetime.now(datetime.timezone.utc).date() - datetime.timedelta(days=1)
    rows = []
    skipped_unmapped = set()
    day = SEASON_START
    while day <= yesterday:
        try:
            events = fetch_day(day)
        except Exception as exc:
            print(f"[{day}] failed to fetch: {exc}")
            day += datetime.timedelta(days=1)
            continue

        for event in events:
            try:
                comp = event["competitions"][0]
                status = comp.get("status", {}).get("type", {})
                if not status.get("completed"):
                    continue
                competitors = comp["competitors"]
                home = next(c for c in competitors if c["homeAway"] == "home")
                away = next(c for c in competitors if c["homeAway"] == "away")
                home_code = ESPN_TO_RETROSHEET.get(home["team"].get("abbreviation"))
                away_code = ESPN_TO_RETROSHEET.get(away["team"].get("abbreviation"))
                if not home_code or not away_code:
                    skipped_unmapped.add((home["team"].get("abbreviation"), away["team"].get("abbreviation")))
                    continue
                rows.append({
                    "Date": day.strftime("%Y%m%d"),
                    "VisitorTeam": away_code, "HomeTeam": home_code,
                    "VisitorScore": int(away["score"]), "HomeScore": int(home["score"]),
                })
            except (KeyError, IndexError, StopIteration, TypeError, ValueError):
                continue

        day += datetime.timedelta(days=1)
        time.sleep(0.15)  # polite pacing -- this is ~140 sequential requests

    if skipped_unmapped:
        print(f"Skipped games for unmapped team abbreviations: {skipped_unmapped}")

    os.makedirs(RAW_DIR, exist_ok=True)
    out_path = os.path.join(RAW_DIR, "gl2026_espn.csv")
    pd.DataFrame(rows).to_csv(out_path, index=False)
    print(f"Wrote {len(rows)} completed 2026-season games (through {yesterday}) -> {out_path}")


if __name__ == "__main__":
    main()
