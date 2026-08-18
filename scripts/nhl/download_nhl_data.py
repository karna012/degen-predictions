"""Downloads NHL game results from the NHL's own public API
(api-web.nhle.com), looping every team x season and deduplicating by game
id (no single bulk file exists here, unlike NBA's hoopR CSV). Saved as
data/nhl/raw/games.csv.

Run from repo root: python scripts/nhl/download_nhl_data.py
"""
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nhl_config import DATA_DIR, SCHEDULE_URL, SEASONS, TEAM_CODE_ALIAS, TEAM_INFO  # noqa: E402

RAW_DIR = os.path.join(DATA_DIR, "raw")
GAME_TYPE_REGULAR = 2
GAME_TYPE_PLAYOFFS = 3


def _fetch_team_season(team: str, season: str) -> list[dict]:
    url = SCHEDULE_URL.format(team=team, season=season)
    data = None
    for attempt in range(5):
        try:
            resp = requests.get(url, timeout=20)
            if resp.status_code == 429:
                time.sleep(2.0 * (attempt + 1))
                continue
            if resp.status_code != 200:
                return []
            data = resp.json()
            break
        except Exception:
            time.sleep(1.0 * (attempt + 1))
    if data is None:
        return []
    rows = []
    for g in data.get("games") or []:
        if g.get("gameType") not in (GAME_TYPE_REGULAR, GAME_TYPE_PLAYOFFS):
            continue
        if g.get("gameState") not in ("FINAL", "OFF"):
            continue
        home = g.get("homeTeam") or {}
        away = g.get("awayTeam") or {}
        if home.get("score") is None or away.get("score") is None:
            continue
        rows.append({
            "GameID": g.get("id"),
            "Date": g.get("gameDate"),
            "HomeTeam": TEAM_CODE_ALIAS.get(home.get("abbrev"), home.get("abbrev")),
            "VisitorTeam": TEAM_CODE_ALIAS.get(away.get("abbrev"), away.get("abbrev")),
            "HomeScore": home.get("score"),
            "VisitorScore": away.get("score"),
        })
    return rows


def main():
    tasks = [(team, season) for team in TEAM_INFO for season in SEASONS]
    print(f"Fetching {len(tasks)} team-seasons from the NHL API...")
    all_rows = []
    empty = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(_fetch_team_season, t, s): (t, s) for t, s in tasks}
        done = 0
        for fut in as_completed(futures):
            rows = fut.result()
            key = futures[fut]
            if not rows:
                empty.append(key)
            all_rows.extend(rows)
            done += 1
            if done % 50 == 0:
                print(f"  ...{done}/{len(tasks)} team-seasons fetched")

    # Gentle single-threaded retry pass for anything that came back empty
    # (rate-limit or transient failure) -- deliberately slow to avoid
    # tripping the same 429 that caused the gap in the first place.
    if empty:
        print(f"Retrying {len(empty)} empty team-seasons (sequential, slow)...")
        for team, season in empty:
            time.sleep(1.0)
            rows = _fetch_team_season(team, season)
            all_rows.extend(rows)

    df = pd.DataFrame(all_rows)
    if df.empty:
        print("No games fetched -- aborting.")
        return
    df = df.drop_duplicates(subset="GameID").drop(columns=["GameID"])
    df = df.dropna(subset=["Date", "HomeTeam", "VisitorTeam", "HomeScore", "VisitorScore"])
    df = df[df["HomeTeam"].isin(TEAM_INFO) & df["VisitorTeam"].isin(TEAM_INFO)]
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.sort_values("Date").reset_index(drop=True)

    os.makedirs(RAW_DIR, exist_ok=True)
    out_path = os.path.join(RAW_DIR, "games.csv")
    df.to_csv(out_path, index=False)
    print(f"{len(all_rows)} raw rows -> {len(df)} deduplicated usable games -> {out_path}")


if __name__ == "__main__":
    main()
