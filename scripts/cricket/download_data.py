"""Downloads Cricsheet ball-by-ball JSON archives (cricsheet.org/downloads/)
for Men's The Hundred, MLC, IPL, and BBL, unpacking each match's JSON into
data/cricket/raw/<competition>/. Idempotent: re-running only downloads a
competition's zip again and overwrites with whatever Cricsheet currently
has (new matches get picked up automatically as they're played).

Run from repo root: python scripts/cricket/download_data.py
"""
import io
import os
import sys
import zipfile

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cricket_config import COMPETITIONS, PEOPLE_CSV_URL, RAW_DIR  # noqa: E402


def download_competition(comp_key, comp_info):
    out_dir = os.path.join(RAW_DIR, comp_key)
    os.makedirs(out_dir, exist_ok=True)
    url = comp_info["zip_url"]
    try:
        resp = requests.get(url, timeout=180)
        resp.raise_for_status()
    except Exception as exc:
        print(f"[{comp_key}] failed to download {url}: {exc}")
        return 0
    n_written = 0
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        for name in zf.namelist():
            if not name.endswith(".json"):
                continue
            with zf.open(name) as f:
                data = f.read()
            with open(os.path.join(out_dir, os.path.basename(name)), "wb") as out_f:
                out_f.write(data)
            n_written += 1
    print(f"[{comp_key}] {comp_info['label']}: {n_written} match JSON files -> {out_dir}")
    return n_written


def download_people_registry():
    """Cricsheet's people.csv maps player names/IDs used inconsistently
    across match files (same player can appear with slightly different
    name strings) to a stable identifier -- used so a player's rolling form
    isn't fragmented by name-string variance across matches."""
    try:
        resp = requests.get(PEOPLE_CSV_URL, timeout=60)
        resp.raise_for_status()
    except Exception as exc:
        print(f"people.csv download failed (non-fatal -- player IDs will fall back to raw name "
              f"strings): {exc}")
        return
    os.makedirs(DATA_DIR := os.path.dirname(RAW_DIR), exist_ok=True)
    out_path = os.path.join(DATA_DIR, "people.csv")
    with open(out_path, "wb") as f:
        f.write(resp.content)
    print(f"people registry -> {out_path}")


def main():
    total = 0
    for comp_key, comp_info in COMPETITIONS.items():
        total += download_competition(comp_key, comp_info)
    download_people_registry()
    print(f"Done: {total} total match files across {len(COMPETITIONS)} competitions")


if __name__ == "__main__":
    main()
