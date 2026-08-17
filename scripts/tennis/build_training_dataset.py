"""Leakage-free ATP training rows (Player1 oriented by better pre-match rank).

Player1 = lower rank number when both ranks exist, else higher overall Elo.
Target Player1Win = 1 if Player1 is the match winner.

Run from repo root: python scripts/tennis/build_training_dataset.py
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict, deque

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tennis_config import (  # noqa: E402
    ALLOWED_LEVELS, DATA_DIR, ELO_INITIAL, ELO_K, LEVEL_FEATURE, MIN_H2H_MEETINGS,
    NUMERIC_FEATURES, RAW_DIR, ROLLING_FORM_MATCHES, SURFACE_FEATURE,
)
from score_parse import parse_tennis_score  # noqa: E402


def _norm_surface(raw: str) -> str:
    s = (raw or "Hard").strip().title()
    if s == "Carpet":
        return "Hard"
    if s not in ("Hard", "Clay", "Grass"):
        return "Hard"
    return s


def expected_score(elo_a, elo_b):
    return 1.0 / (1.0 + 10 ** (-(elo_a - elo_b) / 400.0))


def load_matches() -> pd.DataFrame:
    frames = []
    for name in sorted(os.listdir(RAW_DIR)):
        if not name.startswith("atp_matches_") or not name.endswith(".csv"):
            continue
        frames.append(pd.read_csv(os.path.join(RAW_DIR, name), low_memory=False))
    if not frames:
        return pd.DataFrame()
    games = pd.concat(frames, ignore_index=True)
    games = games[games["tourney_level"].astype(str).isin(ALLOWED_LEVELS)].copy()
    score = games["score"].astype(str)
    games = games[~score.str.contains(r"W/O|Walkover|DEF|Default", case=False, na=False)]
    games["surface"] = games["surface"].map(_norm_surface)
    games["tourney_date"] = pd.to_datetime(games["tourney_date"].astype(str), format="%Y%m%d", errors="coerce")
    games = games.dropna(subset=["tourney_date", "winner_name", "loser_name"])
    games = games.sort_values(["tourney_date", "match_num"]).reset_index(drop=True)
    return games


def build_dataset():
    games = load_matches()
    print(f"{len(games)} matches for training walk")

    elo = {}
    surface_elo = defaultdict(dict)
    recent = defaultdict(lambda: deque(maxlen=ROLLING_FORM_MATCHES))
    surface_recent = defaultdict(lambda: defaultdict(lambda: deque(maxlen=ROLLING_FORM_MATCHES)))
    last_date = {}
    h2h = defaultdict(lambda: defaultdict(int))  # a -> b -> wins of a vs b
    rows = []

    for row in games.itertuples(index=False):
        w, l = row.winner_name, row.loser_name
        surf = row.surface
        date = row.tourney_date
        level = str(row.tourney_level)
        best_of = int(row.best_of) if pd.notna(getattr(row, "best_of", None)) else 3
        wr = getattr(row, "winner_rank", None)
        lr = getattr(row, "loser_rank", None)
        wr = float(wr) if pd.notna(wr) else np.nan
        lr = float(lr) if pd.notna(lr) else np.nan

        ew, el = elo.get(w, ELO_INITIAL), elo.get(l, ELO_INITIAL)
        sew, sel = surface_elo[surf].get(w, ELO_INITIAL), surface_elo[surf].get(l, ELO_INITIAL)

        # Orient Player1 = better (lower) rank; fallback higher Elo.
        if pd.notna(wr) and pd.notna(lr) and wr != lr:
            p1_is_winner = wr < lr
        else:
            p1_is_winner = ew >= el

        if p1_is_winner:
            p1, p2 = w, l
            e1, e2 = ew, el
            se1, se2 = sew, sel
            r1, r2 = wr, lr
            won = 1
        else:
            p1, p2 = l, w
            e1, e2 = el, ew
            se1, se2 = sel, sew
            r1, r2 = lr, wr
            won = 0

        rec1, rec2 = list(recent[p1]), list(recent[p2])
        srec1, srec2 = list(surface_recent[surf][p1]), list(surface_recent[surf][p2])
        h2h_meet = h2h[p1][p2] + h2h[p2][p1]
        h2h_p1 = (h2h[p1][p2] / h2h_meet) if h2h_meet >= MIN_H2H_MEETINGS else 0.5

        d1 = (date - last_date[p1]).days if p1 in last_date else 14
        d2 = (date - last_date[p2]).days if p2 in last_date else 14
        d1 = float(np.clip(d1, 0, 60))
        d2 = float(np.clip(d2, 0, 60))

        feat = {
            "Date": date,
            "Tourney": row.tourney_name,
            "TourneyLevel": level,
            "Surface": surf,
            "Player1": p1,
            "Player2": p2,
            "EloDiff": e1 - e2,
            "SurfaceEloDiff": se1 - se2,
            "RankDiff": (r2 - r1) if (pd.notna(r1) and pd.notna(r2)) else 0.0,  # positive => p1 better ranked
            "Player1RecentWinRate": (sum(rec1) / len(rec1)) if rec1 else 0.5,
            "Player2RecentWinRate": (sum(rec2) / len(rec2)) if rec2 else 0.5,
            "Player1SurfaceWinRate": (sum(srec1) / len(srec1)) if srec1 else 0.5,
            "Player2SurfaceWinRate": (sum(srec2) / len(srec2)) if srec2 else 0.5,
            "Player1DaysRest": d1,
            "Player2DaysRest": d2,
            "BestOf5": 1.0 if best_of >= 5 else 0.0,
            "Level_G": 0.0, "Level_M": 0.0, "Level_500": 0.0, "Level_250": 0.0,
            "Surface_hard": 0.0, "Surface_clay": 0.0, "Surface_grass": 0.0,
            "H2HMeetings": float(h2h_meet),
            "Player1H2HWinRate": float(h2h_p1),
            "Player1Win": won,
            "Score": getattr(row, "score", ""),
        }
        parsed = parse_tennis_score(getattr(row, "score", ""))
        if parsed and not parsed.get("retired"):
            # Score strings are winner-first; remap into Player1/Player2 space.
            if won == 1:
                p1_sets, p2_sets = parsed["winner_sets"], parsed["loser_sets"]
                p1_games, p2_games = parsed["winner_games"], parsed["loser_games"]
            else:
                p1_sets, p2_sets = parsed["loser_sets"], parsed["winner_sets"]
                p1_games, p2_games = parsed["loser_games"], parsed["winner_games"]
            total_sets = int(parsed["total_sets"])
            over_line = 3.5 if best_of >= 5 else 2.5
            feat.update({
                "TotalSets": total_sets,
                "TotalGames": int(parsed["total_games"]),
                "Player1Sets": int(p1_sets),
                "Player2Sets": int(p2_sets),
                "Player1Games": int(p1_games),
                "Player2Games": int(p2_games),
                "GamesMargin": int(p1_games - p2_games),
                "Player1WonSet": 1 if p1_sets >= 1 else 0,
                "Player2WonSet": 1 if p2_sets >= 1 else 0,
                "SetsLine": float(over_line),
                "OverSets": 1 if total_sets > over_line else 0,
            })
        lf = LEVEL_FEATURE.get(level)
        if lf:
            feat[lf] = 1.0
        sf = SURFACE_FEATURE.get(surf)
        if sf:
            feat[sf] = 1.0
        rows.append(feat)

        # Post-match updates
        exp_w = expected_score(ew, el)
        elo[w] = ew + ELO_K * (1.0 - exp_w)
        elo[l] = el + ELO_K * (0.0 - (1.0 - exp_w))
        exp_sw = expected_score(sew, sel)
        surface_elo[surf][w] = sew + ELO_K * (1.0 - exp_sw)
        surface_elo[surf][l] = sel + ELO_K * (0.0 - (1.0 - exp_sw))
        recent[w].append(1)
        recent[l].append(0)
        surface_recent[surf][w].append(1)
        surface_recent[surf][l].append(0)
        last_date[w] = date
        last_date[l] = date
        h2h[w][l] += 1

    df = pd.DataFrame(rows)
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, "training_dataset.csv")
    df.to_csv(path, index=False)
    print(f"Wrote {len(df)} rows -> {path}")
    print("Surface counts:\n", df["Surface"].value_counts().to_string())
    print("Level counts:\n", df["TourneyLevel"].value_counts().to_string())
    missing = [c for c in NUMERIC_FEATURES if c not in df.columns]
    if missing:
        raise SystemExit(f"missing features: {missing}")


if __name__ == "__main__":
    build_dataset()
