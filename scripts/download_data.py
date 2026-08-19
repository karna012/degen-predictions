"""
Downloads historical match CSVs (football-data.co.uk) and player-level
season stats (Understat's internal AJAX API) for every league in
leagues.py. Run once per league before build_features.py. Idempotent --
skips files that already exist.
"""
import io
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import requests

from leagues import LEAGUES, UNDERSTAT_SEASON_MAP, league_seasons, resolve_league_paths

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"}

# Match-stat columns present in European mmz4281 files but absent from /new/
# USA.csv. Filled with NaN so build_features can still run (goals/Elo/H2H carry
# the signal; shot/corner markets are skipped at train time when empty).
_STAT_FILL_COLS = ["HS", "AS", "HST", "AST", "HC", "AC", "HY", "AY", "HR", "AR", "HF", "AF"]


def download_matches_mmz(league_key, cfg):
    os.makedirs(cfg["data_dir"], exist_ok=True)
    seasons = league_seasons(cfg)
    # Past seasons are final and never change, so skip-if-exists is safe and
    # avoids needless refetches. The CURRENT season's file is different --
    # football-data.co.uk updates it after every matchday, so always
    # re-downloading it (never skipping) is what actually lets new results
    # flow into training data. Without this, a season file downloaded once
    # early in the season would silently go stale forever.
    current_season = seasons[-1]
    for season in seasons:
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


def download_matches_fd_new(league_key, cfg):
    """Split football-data.co.uk /new/USA.csv (etc.) into per-season files with
    European-style column names so the rest of the pipeline is unchanged."""
    os.makedirs(cfg["data_dir"], exist_ok=True)
    seasons = league_seasons(cfg)
    current_season = seasons[-1]
    url = cfg.get("fd_new_url") or f"https://www.football-data.co.uk/new/{cfg['fd_code']}.csv"
    resp = requests.get(url, headers=HEADERS, timeout=60)
    resp.raise_for_status()
    raw = pd.read_csv(io.BytesIO(resp.content))
    league_name = cfg.get("fd_new_league", "MLS")
    raw = raw[raw["League"] == league_name].copy()
    raw["Season"] = raw["Season"].astype(str)
    print(f"  [{league_key}] fetched {url}: {len(raw)} {league_name} rows")

    for season in seasons:
        # Season codes that span two calendar years (e.g. Liga MX's
        # "2019/2020", vs MLS's plain "2019") contain a "/" -- sanitize for
        # the filename only, so it doesn't get read as a subdirectory. The
        # raw season string is still what's matched against the CSV below.
        season_file = str(season).replace("/", "-")
        path = os.path.join(cfg["data_dir"], f"{cfg['fd_code']}_{season_file}.csv")
        if os.path.exists(path) and season != current_season:
            continue
        sub = raw[raw["Season"] == str(season)]
        if sub.empty:
            print(f"  [{league_key}] {season}: no rows yet, skipping")
            continue
        out = pd.DataFrame({
            "Date": sub["Date"].values,
            "HomeTeam": sub["Home"].values,
            "AwayTeam": sub["Away"].values,
            "FTHG": sub["HG"].values,
            "FTAG": sub["AG"].values,
            "FTR": sub["Res"].values,
        })
        for col in _STAT_FILL_COLS:
            out[col] = np.nan
        out["Referee"] = "Unknown"
        out.to_csv(path, index=False)
        print(f"  [{league_key}] {season}: saved {path} ({len(out)} matches)")


def download_matches_espn_historical(league_key, cfg):
    """Historical results scraped from ESPN's soccer scoreboard, for a
    league with zero football-data.co.uk coverage at all (currently just
    the Saudi Pro League -- see leagues.py's comment there). Produces the
    same per-season CSV shape as download_matches_fd_new: stat columns are
    NaN (ESPN's scoreboard has neither shot/corner/card stats nor odds).

    No custom User-Agent on these requests: ESPN 403s this project's usual
    identifying UA specifically when a "dates" param is present (same
    finding documented in predict_pl.py's ESPN fallback).
    """
    os.makedirs(cfg["data_dir"], exist_ok=True)
    slug = cfg["espn_slug"]
    seasons = league_seasons(cfg)
    current_season = seasons[-1]
    today = datetime.now(timezone.utc).replace(tzinfo=None)
    url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard"

    for season in seasons:
        season_file = str(season).replace("/", "-")
        path = os.path.join(cfg["data_dir"], f"{cfg['fd_code']}_{season_file}.csv")
        if os.path.exists(path) and season != current_season:
            continue

        start_year, end_year = season.split("/")
        start = datetime(int(start_year), 8, 1)
        end = min(datetime(int(end_year), 6, 15), today)
        if start > end:
            print(f"  [{league_key}] {season}: not started yet, skipping")
            continue

        rows_by_id = {}
        cur = start
        while cur <= end:
            chunk_end = min(cur + timedelta(days=29), end)
            date_range = f"{cur:%Y%m%d}-{chunk_end:%Y%m%d}"
            try:
                resp = requests.get(url, params={"dates": date_range}, timeout=20)
                resp.raise_for_status()
                events = resp.json().get("events") or []
            except (requests.RequestException, ValueError) as exc:
                print(f"  [{league_key}] {season} {date_range}: fetch failed ({exc}), skipping chunk")
                events = []
            for e in events:
                comp = (e.get("competitions") or [{}])[0]
                status = (comp.get("status") or {}).get("type") or {}
                if not status.get("completed"):
                    continue
                competitors = comp.get("competitors") or []
                home = next((c for c in competitors if c.get("homeAway") == "home"), None)
                away = next((c for c in competitors if c.get("homeAway") == "away"), None)
                if not home or not away:
                    continue
                try:
                    hg, ag = int(home.get("score")), int(away.get("score"))
                except (TypeError, ValueError):
                    continue
                eid = e.get("id") or f"{e.get('date')}-{home.get('team', {}).get('displayName')}"
                try:
                    dt_obj = datetime.strptime((e.get("date") or "").strip(), "%Y-%m-%dT%H:%MZ")
                except ValueError:
                    continue
                rows_by_id[eid] = {
                    "dt": dt_obj,
                    "HomeTeam": (home.get("team") or {}).get("displayName"),
                    "AwayTeam": (away.get("team") or {}).get("displayName"),
                    "FTHG": hg, "FTAG": ag,
                    "FTR": "H" if hg > ag else ("A" if ag > hg else "D"),
                }
            time.sleep(0.25)
            cur = chunk_end + timedelta(days=1)

        if not rows_by_id:
            print(f"  [{league_key}] {season}: no completed matches found, skipping")
            continue

        out = pd.DataFrame(sorted(rows_by_id.values(), key=lambda r: r["dt"]))
        out["Date"] = out["dt"].dt.strftime("%d/%m/%Y")
        out = out[["Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR"]]
        for col in _STAT_FILL_COLS:
            out[col] = np.nan
        out["Referee"] = "Unknown"
        out.to_csv(path, index=False)
        print(f"  [{league_key}] {season}: saved {path} ({len(out)} matches)")


def download_matches(league_key, cfg):
    fmt = cfg.get("data_format")
    if fmt == "fd_new":
        download_matches_fd_new(league_key, cfg)
    elif fmt == "espn_historical":
        download_matches_espn_historical(league_key, cfg)
    else:
        download_matches_mmz(league_key, cfg)


def download_players_understat(league_key, cfg):
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


def _asa_team_id(team_id):
    if isinstance(team_id, list):
        return team_id[0] if team_id else None
    return team_id


def download_players_asa(league_key, cfg):
    """American Soccer Analysis public API -- xG, xA, xPass, goals-added."""
    asa = cfg["asa_league"]
    base = f"https://app.americansocceranalysis.com/api/v1/{asa}"
    players_dir = os.path.join(cfg["data_dir"], "players")
    os.makedirs(players_dir, exist_ok=True)
    name_map = cfg.get("name_map", {})

    teams_resp = requests.get(f"{base}/teams", headers=HEADERS, timeout=60)
    teams_resp.raise_for_status()
    teams = {t["team_id"]: t for t in teams_resp.json()}

    players_resp = requests.get(f"{base}/players", headers=HEADERS, timeout=90)
    players_resp.raise_for_status()
    players_meta = {p["player_id"]: p for p in players_resp.json()}

    seasons = league_seasons(cfg)
    current = seasons[-1]
    for season in seasons:
        path = os.path.join(players_dir, f"players_{season}.json")
        if os.path.exists(path) and season != current:
            continue
        xg = requests.get(
            f"{base}/players/xgoals",
            params={"season_name": season},
            headers=HEADERS,
            timeout=90,
        )
        if xg.status_code != 200 or not xg.json():
            print(f"  [{league_key}] ASA {season}: no xgoals yet, skipping")
            continue
        xg_rows = {r["player_id"]: r for r in xg.json()}

        xpass = requests.get(
            f"{base}/players/xpass",
            params={"season_name": season},
            headers=HEADERS,
            timeout=90,
        )
        xpass_rows = {r["player_id"]: r for r in xpass.json()} if xpass.status_code == 200 else {}

        gplus = requests.get(
            f"{base}/players/goals-added",
            params={"season_name": season},
            headers=HEADERS,
            timeout=90,
        )
        gplus_rows = {r["player_id"]: r for r in gplus.json()} if gplus.status_code == 200 else {}

        out = []
        for pid, row in xg_rows.items():
            meta = players_meta.get(pid, {})
            tid = _asa_team_id(row.get("team_id"))
            team_info = teams.get(tid, {})
            asa_team = team_info.get("team_name", "Unknown")
            team_title = name_map.get(asa_team, asa_team)
            xp = xpass_rows.get(pid, {})
            gp = gplus_rows.get(pid, {})
            gplus_total = 0.0
            gplus_by_action = {}
            for action in gp.get("data") or []:
                val = float(action.get("goals_added_raw") or 0)
                gplus_total += val
                gplus_by_action[action.get("action_type", "?")] = round(val, 4)

            out.append({
                # Understat-compatible keys used by squad_quality / live_predict
                "id": pid,
                "player_name": meta.get("player_name", pid),
                "team_title": team_title,
                "games_title_asa": asa_team,
                "goals": float(row.get("goals") or 0),
                "assists": float(row.get("primary_assists") or 0),
                "xG": float(row.get("xgoals") or 0),
                "xA": float(row.get("xassists") or 0),
                "npxG": float(row.get("xgoals") or 0),
                "shots": float(row.get("shots") or 0),
                "key_passes": float(row.get("key_passes") or 0),
                "time": float(row.get("minutes_played") or 0),
                "position": row.get("general_position") or "",
                "games": team_info.get("team_abbreviation", ""),
                # ASA extras for the players analysis page
                "shots_on_target": float(row.get("shots_on_target") or 0),
                "xG_chain": float(row.get("xgoals_plus_xassists") or 0),
                "goals_minus_xG": float(row.get("goals_minus_xgoals") or 0),
                "pass_completion_pct": float(xp.get("pass_completion_percentage") or 0),
                "xpass_completion_pct": float(xp.get("xpass_completion_percentage") or 0),
                "passes_over_expected": float(xp.get("passes_completed_over_expected") or 0),
                "goals_added": round(gplus_total, 4),
                "goals_added_by_action": gplus_by_action,
                "nationality": meta.get("nationality") or "",
                "source": "asa",
                "season": season,
            })

        with open(path, "w", encoding="utf-8") as f:
            json.dump(out, f)
        print(f"  [{league_key}] ASA {season}: saved {len(out)} players -> {path}")
        time.sleep(0.2)


def download_players(league_key, cfg):
    if cfg.get("understat_league"):
        download_players_understat(league_key, cfg)
    elif cfg.get("asa_league"):
        download_players_asa(league_key, cfg)
    else:
        print(f"  [{league_key}] no player stats source configured, skipping players")


def main():
    keys = sys.argv[1:] or list(LEAGUES.keys())
    for key in keys:
        cfg = resolve_league_paths(LEAGUES[key])
        print(f"=== {cfg['label']} ===")
        download_matches(key, cfg)
        download_players(key, cfg)


if __name__ == "__main__":
    main()
