"""Download ATP tour-level yearly match CSVs (TennisMyLife / Sackmann schema).

Run from repo root: python scripts/tennis/download_data.py
"""
from __future__ import annotations

import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tennis_config import RAW_DIR, TML_YEAR_URL, YEARS  # noqa: E402

HEADERS = {"User-Agent": "DegenPredictions/1.0 (https://degenpredictions.com)"}


def download_year(year: int) -> int:
    os.makedirs(RAW_DIR, exist_ok=True)
    dest = os.path.join(RAW_DIR, f"atp_matches_{year}.csv")
    url = TML_YEAR_URL.format(year=year)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=90)
        resp.raise_for_status()
    except Exception as exc:
        print(f"[{year}] download failed: {exc}")
        return 0
    text = resp.text
    if "tourney_id" not in text.splitlines()[0]:
        print(f"[{year}] unexpected payload (missing header)")
        return 0
    with open(dest, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    n = max(0, len(text.splitlines()) - 1)
    print(f"[{year}] {n} match rows -> {dest}")
    return n


def main():
    total = 0
    for year in YEARS:
        total += download_year(year)
    print(f"Done: {total} total match rows across {len(YEARS)} seasons")


if __name__ == "__main__":
    main()
