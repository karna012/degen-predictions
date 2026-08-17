"""ATP men's tennis predictions — Elo + surface Elo + calibrated ML.

Historical rows: data/tennis/training_dataset.csv
Live fixtures: tennis_fixtures.py (ESPN) matched to Elo by player name.
"""
from __future__ import annotations

import datetime
import math
import os
import re
from functools import lru_cache

import joblib
import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data", "tennis")
SCRIPTS_TENNIS = os.path.join(BASE_DIR, "scripts", "tennis")

FEATURES = [
    "EloDiff", "SurfaceEloDiff", "RankDiff",
    "Player1RecentWinRate", "Player2RecentWinRate",
    "Player1SurfaceWinRate", "Player2SurfaceWinRate",
    "Player1DaysRest", "Player2DaysRest", "BestOf5",
    "Level_G", "Level_M", "Level_500", "Level_250",
    "Surface_hard", "Surface_clay", "Surface_grass",
    "H2HMeetings", "Player1H2HWinRate",
]


def _norm_name(name: str) -> str:
    import unicodedata
    text = unicodedata.normalize("NFKD", name or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text).strip()


@lru_cache(maxsize=1)
def load_elo() -> pd.DataFrame:
    path = os.path.join(DATA_DIR, "elo_ratings.csv")
    if not os.path.isfile(path):
        return pd.DataFrame()
    return pd.read_csv(path)


@lru_cache(maxsize=1)
def load_history() -> pd.DataFrame:
    path = os.path.join(DATA_DIR, "match_history.csv")
    if not os.path.isfile(path):
        return pd.DataFrame()
    df = pd.read_csv(path)
    if "Date" in df.columns:
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    return df


@lru_cache(maxsize=1)
def load_dataset() -> pd.DataFrame:
    path = os.path.join(DATA_DIR, "training_dataset.csv")
    if not os.path.isfile(path):
        return pd.DataFrame()
    df = pd.read_csv(path)
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    return df


@lru_cache(maxsize=1)
def load_model():
    path = os.path.join(DATA_DIR, "ml_ensemble_model.joblib")
    if not os.path.isfile(path):
        return None
    return joblib.load(path)


@lru_cache(maxsize=1)
def load_markets_model():
    path = os.path.join(DATA_DIR, "ml_markets_model.joblib")
    if not os.path.isfile(path):
        return None
    return joblib.load(path)


def _pos_proba(model, X):
    proba = model.predict_proba(X)
    classes = list(model.classes_)
    if 1 in classes:
        return float(proba[0, classes.index(1)])
    return float(proba[0, -1])


def _predict_markets(feat: dict, *, oriented: bool, best_of: int) -> dict:
    """Extra markets in Elo-oriented feature space, remapped to card order.

    Public tennis tips use match winner, total games, total sets, and win-a-set.
    Game/set handicap lines are not published.
    """
    out = {
        "sets_line": 3.5 if best_of >= 5 else 2.5,
    }
    bundle = load_markets_model()
    if bundle is None:
        return out
    features = bundle.get("features") or FEATURES
    try:
        X = pd.DataFrame([{k: feat.get(k, 0.0) for k in features}])
    except Exception:
        return out
    models = bundle.get("models") or {}
    stds = bundle.get("std_defaults") or {}

    over_m = models.get("over_sets")
    if over_m:
        p_over = _pos_proba(over_m["model"], X)
        out["over_sets_prob"] = p_over
        out["under_sets_prob"] = 1.0 - p_over

    p1_set = models.get("p1_wins_set")
    p2_set = models.get("p2_wins_set")
    p1_set_p = _pos_proba(p1_set["model"], X) if p1_set else None
    p2_set_p = _pos_proba(p2_set["model"], X) if p2_set else None
    if oriented:
        if p1_set_p is not None and p2_set_p is not None:
            out["player1_wins_set_prob"] = p2_set_p
            out["player2_wins_set_prob"] = p1_set_p
        elif p1_set_p is not None:
            out["player2_wins_set_prob"] = p1_set_p
        elif p2_set_p is not None:
            out["player1_wins_set_prob"] = p2_set_p
    else:
        if p1_set_p is not None:
            out["player1_wins_set_prob"] = p1_set_p
        if p2_set_p is not None:
            out["player2_wins_set_prob"] = p2_set_p

    tg = models.get("total_games")
    if tg:
        pred_games = float(tg["model"].predict(X)[0])
        std = float(tg.get("holdout_std") or stds.get("total_games") or 4.5)
        out["predicted_total_games"] = pred_games
        out["total_games_std"] = std
        line = math.floor(pred_games) + 0.5
        z = (line - pred_games) / max(std, 1e-6)
        p_over = 1.0 - 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
        if p_over >= 0.5:
            out["total_games_pick"] = f"Over {line:g}"
            out["total_games_prob"] = float(p_over)
        else:
            out["total_games_pick"] = f"Under {line:g}"
            out["total_games_prob"] = float(1.0 - p_over)

    return out


@lru_cache(maxsize=1)
def _elo_lookup() -> dict:
    df = load_elo()
    if df.empty:
        return {}
    return { _norm_name(r.Player): r for r in df.itertuples(index=False) }


@lru_cache(maxsize=1)
def _tourney_surface_map() -> dict:
    hist = load_history()
    if hist.empty:
        return {}
    # most recent surface per tournament name
    hist = hist.dropna(subset=["Tourney", "Surface"]).sort_values("Date")
    return dict(zip(hist["Tourney"], hist["Surface"]))


def resolve_player(name: str):
    """Map live display name -> Elo row (exact, then fuzzy contains)."""
    key = _norm_name(name)
    table = _elo_lookup()
    if key in table:
        return table[key]
    # last-name match when unique
    last = key.split()[-1].lower() if key else ""
    hits = [r for n, r in table.items() if n.split()[-1].lower() == last]
    if len(hits) == 1:
        return hits[0]
    # substring
    hits = [r for n, r in table.items() if key.lower() in n.lower() or n.lower() in key.lower()]
    if len(hits) == 1:
        return hits[0]
    return None


def infer_surface(tournament: str, venue: str = "") -> str:
    tmap = _tourney_surface_map()
    t = _norm_name(tournament)
    if t in tmap:
        return tmap[t]
    for name, surf in tmap.items():
        if t and (t.lower() in name.lower() or name.lower() in t.lower()):
            return surf
    blob = f"{tournament} {venue}".lower()
    if any(k in blob for k in ("roland", "french", "monte carlo", "madrid", "rome", "hamburg", "bastad", "geneva", "estoril", "bucharest", "clay")):
        return "Clay"
    if any(k in blob for k in ("wimbledon", "queen's", "halle", "s-hertogenbosch", "eastbourne", "newport", "grass")):
        return "Grass"
    return "Hard"


def infer_level(tournament: str) -> str:
    t = (tournament or "").lower()
    if any(k in t for k in ("australian open", "roland garros", "french open", "wimbledon", "us open")):
        return "G"
    if any(k in t for k in ("indian wells", "miami", "monte carlo", "madrid", "rome", "canada", "national bank", "cincinnati", "shanghai", "paris", "masters")):
        return "M"
    return "250"


def _surface_elo(row, surface: str) -> float:
    if row is None:
        return 1500.0
    col = {"Hard": "EloHard", "Clay": "EloClay", "Grass": "EloGrass"}.get(surface, "EloHard")
    val = getattr(row, col, None)
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return float(getattr(row, "Elo", 1500.0) or 1500.0)
    return float(val)


def _h2h(p1: str, p2: str) -> tuple[int, float]:
    hist = load_history()
    if hist.empty:
        return 0, 0.5
    mask = (
        ((hist["Winner"] == p1) & (hist["Loser"] == p2))
        | ((hist["Winner"] == p2) & (hist["Loser"] == p1))
    )
    sub = hist.loc[mask]
    if sub.empty:
        return 0, 0.5
    wins = int(((sub["Winner"] == p1)).sum())
    n = len(sub)
    return n, (wins / n) if n else 0.5


def _days_rest(row) -> float:
    if row is None:
        return 14.0
    raw = getattr(row, "LastMatchDate", None)
    if raw is None or (isinstance(raw, float) and np.isnan(raw)):
        return 14.0
    try:
        last = pd.to_datetime(raw)
        days = (pd.Timestamp.utcnow().tz_localize(None) - last).days
        return float(np.clip(days, 0, 60))
    except Exception:
        return 14.0


def elo_win_probability(elo1: float, elo2: float) -> float:
    return 1.0 / (1.0 + 10 ** (-(elo1 - elo2) / 400.0))


def build_feature_row(player1: str, player2: str, *, surface: str, level: str, best_of: int = 3) -> dict | None:
    r1, r2 = resolve_player(player1), resolve_player(player2)
    # Orient like training: better overall Elo as Player1 when both known
    e1 = float(getattr(r1, "Elo", 1500.0) or 1500.0) if r1 is not None else 1500.0
    e2 = float(getattr(r2, "Elo", 1500.0) or 1500.0) if r2 is not None else 1500.0
    oriented = False
    if r1 is not None and r2 is not None and e2 > e1:
        player1, player2 = player2, player1
        r1, r2 = r2, r1
        e1, e2 = e2, e1
        oriented = True

    se1, se2 = _surface_elo(r1, surface), _surface_elo(r2, surface)
    h2h_n, h2h_rate = _h2h(
        getattr(r1, "Player", player1) if r1 is not None else player1,
        getattr(r2, "Player", player2) if r2 is not None else player2,
    )
    feat = {
        "EloDiff": e1 - e2,
        "SurfaceEloDiff": se1 - se2,
        "RankDiff": 0.0,
        "Player1RecentWinRate": float(getattr(r1, "RecentWinRate", 0.5) or 0.5) if r1 is not None else 0.5,
        "Player2RecentWinRate": float(getattr(r2, "RecentWinRate", 0.5) or 0.5) if r2 is not None else 0.5,
        "Player1SurfaceWinRate": 0.5,
        "Player2SurfaceWinRate": 0.5,
        "Player1DaysRest": _days_rest(r1),
        "Player2DaysRest": _days_rest(r2),
        "BestOf5": 1.0 if best_of >= 5 else 0.0,
        "Level_G": 1.0 if level == "G" else 0.0,
        "Level_M": 1.0 if level == "M" else 0.0,
        "Level_500": 1.0 if level == "500" else 0.0,
        "Level_250": 1.0 if level == "250" else 0.0,
        "Surface_hard": 1.0 if surface == "Hard" else 0.0,
        "Surface_clay": 1.0 if surface == "Clay" else 0.0,
        "Surface_grass": 1.0 if surface == "Grass" else 0.0,
        "H2HMeetings": float(h2h_n),
        "Player1H2HWinRate": float(h2h_rate),
    }
    return {
        "features": feat,
        "player1": getattr(r1, "Player", player1) if r1 is not None else player1,
        "player2": getattr(r2, "Player", player2) if r2 is not None else player2,
        "display_player1": player1,
        "display_player2": player2,
        "elo1": e1,
        "elo2": e2,
        "surface_elo1": se1,
        "surface_elo2": se2,
        "surface": surface,
        "level": level,
        "oriented": oriented,
        "known": r1 is not None and r2 is not None,
    }


def predict_match(
    player1: str,
    player2: str,
    *,
    tournament: str = "",
    venue: str = "",
    best_of: int | None = None,
    surface: str | None = None,
    level: str | None = None,
) -> dict | None:
    """Win probs are always for the caller’s player1/player2 order (card order)."""
    surface = surface or infer_surface(tournament, venue)
    if surface == "Carpet":
        surface = "Hard"
    level = level or infer_level(tournament)
    if best_of is None:
        best_of = 5 if level == "G" else 3
    built = build_feature_row(player1, player2, surface=surface, level=level, best_of=best_of)
    if built is None:
        return None

    feat = built["features"]
    # Model Player1Win is for Elo-oriented pair; map back to caller's order.
    oriented_p1_win = elo_win_probability(built["surface_elo1"], built["surface_elo2"])
    model_name = "surface_elo"
    bundle = load_model()
    if bundle is not None and built["known"]:
        try:
            X = pd.DataFrame([{k: feat[k] for k in bundle["features"]}])
            proba = bundle["model"].predict_proba(X)[0]
            classes = list(bundle["model"].classes_)
            idx = classes.index(1) if 1 in classes else -1
            oriented_p1_win = float(proba[idx])
            model_name = bundle.get("model_name") or "ml"
        except Exception:
            pass

    if built["oriented"]:
        call_p1_win = 1.0 - oriented_p1_win
        elo1, elo2 = built["elo2"], built["elo1"]
        se1, se2 = built["surface_elo2"], built["surface_elo1"]
        elo_name1, elo_name2 = built["player2"], built["player1"]
        h2h_p1 = 1.0 - float(feat["Player1H2HWinRate"]) if feat["H2HMeetings"] else 0.5
    else:
        call_p1_win = oriented_p1_win
        elo1, elo2 = built["elo1"], built["elo2"]
        se1, se2 = built["surface_elo1"], built["surface_elo2"]
        elo_name1, elo_name2 = built["player1"], built["player2"]
        h2h_p1 = float(feat["Player1H2HWinRate"])

    markets = {}
    if built["known"]:
        try:
            markets = _predict_markets(feat, oriented=built["oriented"], best_of=best_of)
        except Exception:
            markets = {}

    return {
        "player1": player1,
        "player2": player2,
        "player1_elo_name": elo_name1,
        "player2_elo_name": elo_name2,
        "player1_win_prob": call_p1_win,
        "player2_win_prob": 1.0 - call_p1_win,
        "elo1": elo1,
        "elo2": elo2,
        "surface_elo1": se1,
        "surface_elo2": se2,
        "surface": surface,
        "level": level,
        "best_of": best_of,
        "model_name": model_name,
        "data_confidence": "high" if built["known"] else "low",
        "h2h_meetings": feat["H2HMeetings"],
        "player1_h2h_win_rate": h2h_p1,
        "markets": markets,
    }


def enrich_fixture(fixture: dict) -> dict:
    pred = predict_match(
        fixture["player1"], fixture["player2"],
        tournament=fixture.get("tournament") or "",
        venue=fixture.get("venue") or "",
        surface=fixture.get("surface"),
        level=fixture.get("level"),
    )
    out = {**fixture, "prediction": pred, "slug": None}
    try:
        import tennis_fixtures
        out["slug"] = tennis_fixtures.slug_for_fixture(fixture)
    except Exception:
        pass
    if pred:
        out["surface"] = pred["surface"]
        out["level"] = pred["level"]
    return out


def upcoming_with_predictions() -> list:
    import tennis_fixtures
    raw = tennis_fixtures.get_upcoming_and_live()
    return [enrich_fixture(f) for f in raw]


def recent_results(limit: int = 40) -> list:
    """Recent completed training rows with model replay (for /tennis/results)."""
    df = load_dataset()
    if df.empty:
        return []
    bundle = load_model()
    sub = df.sort_values("Date", ascending=False).head(limit).copy()
    out = []
    for _, row in sub.iterrows():
        p1_prob = None
        if bundle is not None:
            try:
                X = pd.DataFrame([{k: row[k] for k in bundle["features"]}])
                proba = bundle["model"].predict_proba(X)[0]
                classes = list(bundle["model"].classes_)
                idx = classes.index(1) if 1 in classes else -1
                p1_prob = float(proba[idx])
            except Exception:
                p1_prob = None
        actual = int(row["Player1Win"])
        pred_side = None if p1_prob is None else (1 if p1_prob >= 0.5 else 0)
        out.append({
            "date": row["Date"].strftime("%Y-%m-%d") if pd.notna(row["Date"]) else "",
            "tournament": row.get("Tourney"),
            "surface": row.get("Surface"),
            "player1": row["Player1"],
            "player2": row["Player2"],
            "score": row.get("Score"),
            "player1_win_prob": p1_prob,
            "actual_player1_win": actual,
            "correct": (pred_side == actual) if pred_side is not None else None,
            "level": row.get("TourneyLevel"),
        })
    return out


def build_match_detail(fixture: dict) -> dict:
    enriched = enrich_fixture(fixture)
    pred = enriched.get("prediction") or {}
    return {
        **enriched,
        "prediction": pred,
        "analysis": {
            "surface": pred.get("surface"),
            "level": pred.get("level"),
            "elo1": pred.get("elo1"),
            "elo2": pred.get("elo2"),
            "surface_elo1": pred.get("surface_elo1"),
            "surface_elo2": pred.get("surface_elo2"),
            "h2h_meetings": pred.get("h2h_meetings"),
            "h2h_p1": pred.get("player1_h2h_win_rate"),
            "model": pred.get("model_name"),
        },
    }
