"""Backfills the in-progress current MLB season into the Elo pipeline.

Retrosheet only publishes a season's game log after that season ends, so the
2026 season isn't in data/mlb/raw/ yet from download_mlb_data.py. This pulls
completed 2026 games from the official MLB Stats API schedule and writes them
in the same trimmed format as the Retrosheet files, so build_elo.py picks
them up automatically alongside the historical seasons.

Falls back to ESPN CDN day-by-day if Stats API fails.

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
MLB_STATS_SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"
ESPN_CDN_SCOREBOARD_URL = "https://cdn.espn.com/core/mlb/scoreboard"
SEASON_START = datetime.date(2026, 3, 1)

# Stats API abbreviations that differ from ESPN keys in ESPN_TO_RETROSHEET.
MLB_TO_ESPN_ABBR = {
    "AZ": "ARI",
    "CWS": "CHW",
}

HEADERS = {
    "User-Agent": "DegenPredictions/1.0 (+https://degenpredictions.com; mlb-season-backfill)",
    "Accept": "application/json",
}


def _norm_abbr(abbr):
    text = (abbr or "").strip().upper()
    return MLB_TO_ESPN_ABBR.get(text, text)


def fetch_statsapi_range(start, end):
    resp = requests.get(
        MLB_STATS_SCHEDULE_URL,
        params={
            "sportId": 1,
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "hydrate": "team",
        },
        headers=HEADERS,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def rows_from_statsapi(payload):
    rows = []
    skipped = set()
    for day in payload.get("dates") or []:
        for game in day.get("games") or []:
            status = game.get("status") or {}
            if (status.get("abstractGameState") or "") != "Final":
                continue
            try:
                teams = game["teams"]
                home = teams["home"]
                away = teams["away"]
                home_abbr = _norm_abbr((home.get("team") or {}).get("abbreviation"))
                away_abbr = _norm_abbr((away.get("team") or {}).get("abbreviation"))
                home_code = ESPN_TO_RETROSHEET.get(home_abbr)
                away_code = ESPN_TO_RETROSHEET.get(away_abbr)
                if not home_code or not away_code:
                    skipped.add((home_abbr, away_abbr))
                    continue
                home_score = home.get("score")
                away_score = away.get("score")
                if home_score is None or away_score is None:
                    continue
                game_date = (game.get("officialDate") or (game.get("gameDate") or "")[:10] or "").replace("-", "")
                if len(game_date) != 8:
                    continue
                rows.append({
                    "Date": game_date,
                    "VisitorTeam": away_code,
                    "HomeTeam": home_code,
                    "VisitorScore": int(away_score),
                    "HomeScore": int(home_score),
                })
            except (KeyError, TypeError, ValueError):
                continue
    return rows, skipped


def fetch_espn_day(day):
    resp = requests.get(
        ESPN_CDN_SCOREBOARD_URL,
        params={"xhr": "1", "date": day.strftime("%Y%m%d")},
        headers=HEADERS,
        timeout=10,
    )
    resp.raise_for_status()
    payload = resp.json()
    if isinstance(payload.get("events"), list):
        return payload["events"]
    content = payload.get("content") or {}
    sb = content.get("sbData") or {}
    return sb.get("events") or []


def rows_from_espn_season(start, end):
    rows = []
    skipped = set()
    day = start
    while day <= end:
        try:
            events = fetch_espn_day(day)
        except Exception as exc:
            print(f"[{day}] ESPN fallback failed: {exc}")
            day += datetime.timedelta(days=1)
            time.sleep(0.1)
            continue
        for event in events:
            try:
                comp = event["competitions"][0]
                status = (comp.get("status") or {}).get("type") or {}
                if not status.get("completed"):
                    continue
                competitors = comp["competitors"]
                home = next(c for c in competitors if c["homeAway"] == "home")
                away = next(c for c in competitors if c["homeAway"] == "away")
                home_abbr = _norm_abbr(home["team"].get("abbreviation"))
                away_abbr = _norm_abbr(away["team"].get("abbreviation"))
                home_code = ESPN_TO_RETROSHEET.get(home_abbr)
                away_code = ESPN_TO_RETROSHEET.get(away_abbr)
                if not home_code or not away_code:
                    skipped.add((home_abbr, away_abbr))
                    continue
                rows.append({
                    "Date": day.strftime("%Y%m%d"),
                    "VisitorTeam": away_code,
                    "HomeTeam": home_code,
                    "VisitorScore": int(away["score"]),
                    "HomeScore": int(home["score"]),
                })
            except (KeyError, IndexError, StopIteration, TypeError, ValueError):
                continue
        day += datetime.timedelta(days=1)
        time.sleep(0.1)
    return rows, skipped


def main():
    yesterday = datetime.datetime.now(datetime.timezone.utc).date() - datetime.timedelta(days=1)
    rows = []
    skipped_unmapped = set()

    # Pull in ~30-day chunks so one bad window doesn't lose the season.
    cur = SEASON_START
    while cur <= yesterday:
        chunk_end = min(cur + datetime.timedelta(days=29), yesterday)
        try:
            payload = fetch_statsapi_range(cur, chunk_end)
            chunk_rows, skipped = rows_from_statsapi(payload)
            rows.extend(chunk_rows)
            skipped_unmapped |= skipped
            print(f"[{cur}..{chunk_end}] statsapi -> {len(chunk_rows)} finals")
        except Exception as exc:
            print(f"[{cur}..{chunk_end}] statsapi failed ({exc}); ESPN CDN day fallback")
            chunk_rows, skipped = rows_from_espn_season(cur, chunk_end)
            rows.extend(chunk_rows)
            skipped_unmapped |= skipped
            print(f"[{cur}..{chunk_end}] espn -> {len(chunk_rows)} finals")
        cur = chunk_end + datetime.timedelta(days=1)
        time.sleep(0.2)

    if skipped_unmapped:
        print(f"Skipped games for unmapped team abbreviations: {skipped_unmapped}")

    # Dedupe by date+matchup (chunk overlaps shouldn't exist, but be safe).
    seen = set()
    deduped = []
    for row in rows:
        key = (row["Date"], row["VisitorTeam"], row["HomeTeam"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)

    os.makedirs(RAW_DIR, exist_ok=True)
    out_path = os.path.join(RAW_DIR, "gl2026_espn.csv")
    pd.DataFrame(deduped).to_csv(out_path, index=False)
    print(f"Wrote {len(deduped)} completed 2026-season games (through {yesterday}) -> {out_path}")


if __name__ == "__main__":
    main()
