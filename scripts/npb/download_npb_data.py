"""Downloads NPB season schedules from spaia.jp's public API and trims each
one down to what the Elo pipeline needs, saved as data/npb/raw/{year}.csv.

Includes every game (played and still-scheduled) -- build_elo.py filters to
completed regular-season games itself. Keeping the full file means the same
download can also answer "what games are scheduled" without a second source.

Run from repo root: python scripts/npb/download_npb_data.py
"""
import os
import sys

import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from npb_config import DATA_DIR, HEADERS, SCHEDULE_URL, YEARS  # noqa: E402

RAW_DIR = os.path.join(DATA_DIR, "raw")

COLUMNS = [
    "DateJPN", "TimeJPN", "GameKindID", "GameState", "Round",
    "HTeamID", "HomeTeamNameE", "VTeamID", "VisitorTeamNameE",
    "HScore", "VScore",
    "Home_StarterPitcher_NameS", "Visitor_StarterPitcher_NameS",
]


def download_year(year):
    out_path = os.path.join(RAW_DIR, f"{year}.csv")
    try:
        resp = requests.get(SCHEDULE_URL, params={"Year": year}, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        games = resp.json()
    except Exception as exc:
        print(f"[{year}] failed to download: {exc}")
        return False

    if not games:
        print(f"[{year}] no games returned -- skipping")
        return False

    df = pd.DataFrame(games)
    missing_cols = [c for c in COLUMNS if c not in df.columns]
    if missing_cols:
        print(f"[{year}] missing expected columns {missing_cols} -- skipping")
        return False

    df = df[COLUMNS]
    os.makedirs(RAW_DIR, exist_ok=True)
    df.to_csv(out_path, index=False)
    completed = int((df["GameState"] == 2).sum())
    print(f"[{year}] {len(df)} games total ({completed} completed) -> {out_path}")
    return True


def main():
    ok = 0
    for year in YEARS:
        if download_year(year):
            ok += 1
    print(f"Done: {ok}/{len(YEARS)} seasons downloaded")


if __name__ == "__main__":
    main()
