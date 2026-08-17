"""Chronological overall + surface Elo for ATP men (tour-level only).

Run from repo root: python scripts/tennis/build_elo.py
Outputs: data/tennis/elo_ratings.csv, data/tennis/match_history.csv
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict, deque

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tennis_config import (  # noqa: E402
    ALLOWED_LEVELS, DATA_DIR, ELO_INITIAL, ELO_K, RAW_DIR, ROLLING_FORM_MATCHES,
)


def _norm_surface(raw: str) -> str:
    s = (raw or "Hard").strip().title()
    if s == "Carpet":
        return "Hard"
    if s not in ("Hard", "Clay", "Grass"):
        return "Hard"
    return s


def load_matches() -> pd.DataFrame:
    frames = []
    if not os.path.isdir(RAW_DIR):
        return pd.DataFrame()
    for name in sorted(os.listdir(RAW_DIR)):
        if not name.startswith("atp_matches_") or not name.endswith(".csv"):
            continue
        path = os.path.join(RAW_DIR, name)
        df = pd.read_csv(path, low_memory=False)
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    games = pd.concat(frames, ignore_index=True)
    games = games[games["tourney_level"].astype(str).isin(ALLOWED_LEVELS)].copy()
    # Drop walkovers / retirements with empty score noise later; keep W/O out of Elo.
    score = games["score"].astype(str)
    games = games[~score.str.contains(r"W/O|Walkover|DEF|Default", case=False, na=False)]
    games["surface"] = games["surface"].map(_norm_surface)
    games["tourney_date"] = pd.to_datetime(games["tourney_date"].astype(str), format="%Y%m%d", errors="coerce")
    games = games.dropna(subset=["tourney_date", "winner_name", "loser_name"])
    games = games.sort_values(["tourney_date", "match_num"]).reset_index(drop=True)
    return games


def expected_score(elo_a, elo_b):
    return 1.0 / (1.0 + 10 ** (-(elo_a - elo_b) / 400.0))


def build_elo():
    games = load_matches()
    print(f"{len(games)} tour-level matches loaded")

    elo = {}
    surface_elo = defaultdict(dict)  # surface -> player -> elo
    games_played = defaultdict(int)
    last_date = {}
    recent = defaultdict(lambda: deque(maxlen=ROLLING_FORM_MATCHES))
    surface_recent = defaultdict(lambda: defaultdict(lambda: deque(maxlen=ROLLING_FORM_MATCHES)))
    history = []

    for row in games.itertuples(index=False):
        w, l = row.winner_name, row.loser_name
        surf = row.surface
        date = row.tourney_date

        ew, el = elo.get(w, ELO_INITIAL), elo.get(l, ELO_INITIAL)
        sew = surface_elo[surf].get(w, ELO_INITIAL)
        sel = surface_elo[surf].get(l, ELO_INITIAL)

        # Pre-match snapshot for history / training rebuild.
        history.append({
            "Date": date,
            "Tourney": row.tourney_name,
            "TourneyLevel": str(row.tourney_level),
            "Surface": surf,
            "BestOf": int(row.best_of) if pd.notna(getattr(row, "best_of", None)) else 3,
            "Round": getattr(row, "round", ""),
            "Winner": w,
            "Loser": l,
            "WinnerRank": getattr(row, "winner_rank", None),
            "LoserRank": getattr(row, "loser_rank", None),
            "WinnerEloPre": ew,
            "LoserEloPre": el,
            "WinnerSurfaceEloPre": sew,
            "LoserSurfaceEloPre": sel,
            "Score": getattr(row, "score", ""),
            "MatchNum": getattr(row, "match_num", 0),
            "TourneyId": getattr(row, "tourney_id", ""),
        })

        exp_w = expected_score(ew, el)
        elo[w] = ew + ELO_K * (1.0 - exp_w)
        elo[l] = el + ELO_K * (0.0 - (1.0 - exp_w))

        exp_sw = expected_score(sew, sel)
        surface_elo[surf][w] = sew + ELO_K * (1.0 - exp_sw)
        surface_elo[surf][l] = sel + ELO_K * (0.0 - (1.0 - exp_sw))

        games_played[w] += 1
        games_played[l] += 1
        last_date[w] = date
        last_date[l] = date
        recent[w].append(1)
        recent[l].append(0)
        surface_recent[surf][w].append(1)
        surface_recent[surf][l].append(0)

    rows = []
    for player, rating in elo.items():
        hard = surface_elo["Hard"].get(player, ELO_INITIAL)
        clay = surface_elo["Clay"].get(player, ELO_INITIAL)
        grass = surface_elo["Grass"].get(player, ELO_INITIAL)
        rec = list(recent[player])
        rows.append({
            "Player": player,
            "Elo": round(rating, 1),
            "EloHard": round(hard, 1),
            "EloClay": round(clay, 1),
            "EloGrass": round(grass, 1),
            "GamesPlayed": games_played[player],
            "RecentWinRate": (sum(rec) / len(rec)) if rec else 0.5,
            "LastMatchDate": last_date.get(player),
        })
    elo_df = pd.DataFrame(rows).sort_values("Elo", ascending=False)
    hist_df = pd.DataFrame(history)

    os.makedirs(DATA_DIR, exist_ok=True)
    elo_path = os.path.join(DATA_DIR, "elo_ratings.csv")
    hist_path = os.path.join(DATA_DIR, "match_history.csv")
    elo_df.to_csv(elo_path, index=False)
    hist_df.to_csv(hist_path, index=False)
    print(f"wrote {len(elo_df)} player ratings -> {elo_path}")
    print(f"wrote {len(hist_df)} match history rows -> {hist_path}")
    print(elo_df.head(12).to_string(index=False))


if __name__ == "__main__":
    build_elo()
