"""Downloads the hoopR NBA schedule CSV (github.com/sportsdataverse/hoopR-nba-data)
and trims it down to the seasons/columns/teams the Elo pipeline needs, saved
as data/nba/raw/games.csv.

Run from repo root: python scripts/nba/download_nba_data.py
"""
import os
import sys

import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nba_config import DATA_DIR, SCHEDULE_URL, SEASONS, TEAM_INFO  # noqa: E402

RAW_DIR = os.path.join(DATA_DIR, "raw")
COLS = [
    "game_date", "season", "season_type", "status_type_completed",
    "home_abbreviation", "away_abbreviation", "home_score", "away_score",
]


def main():
    cache_path = os.path.join(RAW_DIR, "nba_schedule_master.csv")
    if os.path.exists(cache_path):
        print(f"Using already-downloaded {cache_path}")
        df = pd.read_csv(cache_path, usecols=COLS, low_memory=False)
    else:
        print(f"Downloading {SCHEDULE_URL} ...")
        resp = requests.get(SCHEDULE_URL, timeout=120)
        resp.raise_for_status()
        os.makedirs(RAW_DIR, exist_ok=True)
        with open(cache_path, "wb") as f:
            f.write(resp.content)
        from io import BytesIO
        df = pd.read_csv(BytesIO(resp.content), usecols=COLS, low_memory=False)
    total = len(df)

    df = df[df["season"].isin(SEASONS)]
    df = df[df["season_type"].isin([2, 3, 5])]  # regular season, playoffs, play-in
    df = df[df["status_type_completed"] == True]  # noqa: E712
    # Drops All-Star Weekend exhibition rows (Team Chuck/Shaq, East/West,
    # Stars/Stripes, draft-captain codes) -- hoopR tags these with the same
    # season_type as real games, so only a real-franchise whitelist catches them.
    df = df[df["home_abbreviation"].isin(TEAM_INFO) & df["away_abbreviation"].isin(TEAM_INFO)]
    df = df.dropna(subset=["game_date", "home_score", "away_score"])
    df = df.rename(columns={
        "game_date": "Date", "home_abbreviation": "HomeTeam", "away_abbreviation": "VisitorTeam",
        "home_score": "HomeScore", "away_score": "VisitorScore",
    })[["Date", "VisitorTeam", "HomeTeam", "VisitorScore", "HomeScore", "season", "season_type"]]
    df = df.sort_values("Date").reset_index(drop=True)

    os.makedirs(RAW_DIR, exist_ok=True)
    out_path = os.path.join(RAW_DIR, "games.csv")
    df.to_csv(out_path, index=False)
    print(f"{total} raw rows -> {len(df)} usable games ({SEASONS[0]}-{SEASONS[-1]}) -> {out_path}")


if __name__ == "__main__":
    main()
