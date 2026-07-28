"""Downloads Retrosheet season game logs (retrosheet.org/gamelogs) and trims
each one down to the columns the Elo pipeline actually needs, saved as
data/mlb/raw/gl{year}.csv.

Run from repo root: python scripts/mlb/download_mlb_data.py
"""
import io
import os
import sys
import zipfile

import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mlb_config import DATA_DIR, GAMELOG_URL, YEARS  # noqa: E402

RAW_DIR = os.path.join(DATA_DIR, "raw")

# Column indices in Retrosheet's gamelog format (documented at
# retrosheet.org/gamelogs/glfields.txt) -- only what the Elo model needs.
COLS = {
    0: "Date", 3: "VisitorTeam", 6: "HomeTeam", 9: "VisitorScore", 10: "HomeScore",
    12: "DayNight", 14: "ForfeitInfo", 15: "ProtestInfo",
    22: "VisitorHits", 25: "VisitorHR", 32: "VisitorSO",
    50: "HomeHits", 53: "HomeHR", 60: "HomeSO",
}


def download_year(year):
    out_path = os.path.join(RAW_DIR, f"gl{year}.csv")
    url = GAMELOG_URL.format(year=year)
    try:
        resp = requests.get(url, timeout=30)
        if resp.status_code != 200:
            print(f"[{year}] not available yet (HTTP {resp.status_code}) -- skipping")
            return False
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            txt_name = next(n for n in zf.namelist() if n.lower().endswith(".txt"))
            with zf.open(txt_name) as f:
                df = pd.read_csv(f, header=None, usecols=list(COLS.keys()), quotechar='"')
    except Exception as exc:
        print(f"[{year}] failed to download/parse: {exc}")
        return False

    df = df.rename(columns=COLS)
    total = len(df)

    # Forfeited/protested games didn't play out normally -- same reasoning as
    # dropping tennis walkovers: no real signal, would only add noise.
    forfeited = df["ForfeitInfo"].notna() & (df["ForfeitInfo"] != "")
    df = df[~forfeited]

    before_completeness = len(df)
    df = df.dropna(subset=["Date", "VisitorTeam", "HomeTeam", "VisitorScore", "HomeScore"])
    dropped_incomplete = before_completeness - len(df)

    os.makedirs(RAW_DIR, exist_ok=True)
    df = df.drop(columns=["ForfeitInfo", "ProtestInfo"])
    df.to_csv(out_path, index=False)
    print(f"[{year}] {total} raw rows -> dropped {int(forfeited.sum())} forfeited, "
          f"{dropped_incomplete} incomplete -> {len(df)} usable -> {out_path}")
    return True


def main():
    ok = 0
    for year in YEARS:
        if download_year(year):
            ok += 1
    print(f"Done: {ok}/{len(YEARS)} seasons downloaded")


if __name__ == "__main__":
    main()
