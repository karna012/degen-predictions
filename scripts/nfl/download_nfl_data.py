"""Downloads the nflverse-data games.csv (github.com/nflverse/nflverse-data)
and trims it down to the seasons/columns/teams the Elo pipeline needs,
saved as data/nfl/raw/games.csv.

Run from repo root: python scripts/nfl/download_nfl_data.py
"""
import os
import sys
import time

import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nfl_config import DATA_DIR, SCHEDULE_URL, SEASONS, TEAM_CODE_ALIAS, TEAM_INFO  # noqa: E402

RAW_DIR = os.path.join(DATA_DIR, "raw")
COLS = [
    "season", "game_type", "gameday", "away_team", "home_team",
    "away_score", "home_score", "home_rest", "away_rest",
]
# Same reasoning as nba_config.py's cache TTL -- avoid re-hitting the CDN on
# every interactive test run, but still fresh enough for a weekly retrain.
CACHE_MAX_AGE_SECONDS = 20 * 60 * 60


def main():
    cache_path = os.path.join(RAW_DIR, "games_master.csv")
    cache_age = time.time() - os.path.getmtime(cache_path) if os.path.exists(cache_path) else None
    if cache_age is not None and cache_age < CACHE_MAX_AGE_SECONDS:
        print(f"Using already-downloaded {cache_path} ({cache_age / 3600:.1f}h old)")
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
    df["home_team"] = df["home_team"].replace(TEAM_CODE_ALIAS)
    df["away_team"] = df["away_team"].replace(TEAM_CODE_ALIAS)
    df = df[df["home_team"].isin(TEAM_INFO) & df["away_team"].isin(TEAM_INFO)]
    df = df.dropna(subset=["gameday", "home_score", "away_score"])
    df = df.rename(columns={
        "gameday": "Date", "home_team": "HomeTeam", "away_team": "VisitorTeam",
        "home_score": "HomeScore", "away_score": "VisitorScore",
        "home_rest": "HomeRest", "away_rest": "AwayRest",
    })[["Date", "VisitorTeam", "HomeTeam", "VisitorScore", "HomeScore",
        "HomeRest", "AwayRest", "season", "game_type"]]
    df = df.sort_values("Date").reset_index(drop=True)

    os.makedirs(RAW_DIR, exist_ok=True)
    out_path = os.path.join(RAW_DIR, "games.csv")
    df.to_csv(out_path, index=False)
    print(f"{total} raw rows -> {len(df)} usable games ({SEASONS[0]}-{SEASONS[-1]}) -> {out_path}")


if __name__ == "__main__":
    main()
