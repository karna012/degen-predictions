"""
Downloads historical match CSVs (football-data.co.uk) and player-level
season stats (Understat's internal AJAX API) for every league in
leagues.py. Run once per league before build_features.py. Idempotent --
skips files that already exist.
"""
import json
import os
import sys
import time

import requests

from leagues import LEAGUES, SEASONS, UNDERSTAT_SEASON_MAP

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"}


def download_matches(league_key, cfg):
    os.makedirs(cfg["data_dir"], exist_ok=True)
    # Past seasons are final and never change, so skip-if-exists is safe and
    # avoids needless refetches. The CURRENT season's file is different --
    # football-data.co.uk updates it after every matchday, so always
    # re-downloading it (never skipping) is what actually lets new results
    # flow into training data. Without this, a season file downloaded once
    # early in the season would silently go stale forever.
    current_season = SEASONS[-1]
    for season in SEASONS:
        path = os.path.join(cfg["data_dir"], f"{cfg['fd_code']}_{season}.csv")
        if os.path.exists(path) and season != current_season:
            continue
        url = f"https://www.football-data.co.uk/mmz4281/{season}/{cfg['fd_code']}.csv"
        resp = requests.get(url, headers=HEADERS, timeout=20)
        if resp.status_code != 200 or len(resp.content) < 500:
            print(f"  [{league_key}] {season}: no data yet (status {resp.status_code}), skipping")
            continue
        with open(path, "wb") as f:
            f.write(resp.content)
        print(f"  [{league_key}] {season}: saved {path} ({len(resp.content)} bytes)")
        time.sleep(0.3)


def download_players(league_key, cfg):
    players_dir = os.path.join(cfg["data_dir"], "players")
    os.makedirs(players_dir, exist_ok=True)
    for season_code, u_season in UNDERSTAT_SEASON_MAP.items():
        path = os.path.join(players_dir, f"players_{u_season}.json")
        if os.path.exists(path):
            continue
        resp = requests.post(
            "https://understat.com/main/getPlayersStats/",
            headers={**HEADERS, "X-Requested-With": "XMLHttpRequest"},
            data={"league": cfg["understat_league"], "season": u_season},
            timeout=20,
        )
        try:
            payload = resp.json()
        except ValueError:
            print(f"  [{league_key}] understat {u_season}: bad response, skipping")
            continue
        players = payload.get("players", [])
        if not players:
            print(f"  [{league_key}] understat {u_season}: no players returned, skipping")
            continue
        with open(path, "w", encoding="utf-8") as f:
            json.dump(players, f)
        print(f"  [{league_key}] understat {u_season}: saved {len(players)} players")
        time.sleep(0.3)


def main():
    keys = sys.argv[1:] or list(LEAGUES.keys())
    for key in keys:
        cfg = LEAGUES[key]
        print(f"=== {cfg['label']} ===")
        download_matches(key, cfg)
        download_players(key, cfg)


if __name__ == "__main__":
    main()
