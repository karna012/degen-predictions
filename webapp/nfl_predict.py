"""NFL prediction helpers: team Elo win probability (home-field edge),
head-to-head blending, totals, and a spread from the Elo gap.

Ratings are built from ESPN completed games (same unofficial read-only
scoreboard as nfl_fixtures.py) and persisted under data/nfl/. There is no
Retrosheet-style archive, so the public ledger is "current model vs recent
finals", same honesty framing as MLB results.
"""
from __future__ import annotations

import math
import os
import threading
import time
from datetime import date, datetime

import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data", "nfl")
ELO_PATH = os.path.join(DATA_DIR, "elo_ratings.csv")
HISTORY_PATH = os.path.join(DATA_DIR, "game_history.csv")

START_ELO = 1500.0
K_FACTOR = 20.0
HOME_ADVANTAGE = 55.0  # ~2.2 points at 25 Elo per point
ELO_PER_POINT = 25.0
TOTAL_POINTS_STD_DEV = 13.5
TEAM_POINTS_STD_DEV = 10.0
SPREAD_MARGIN_STD = 13.5
LEAGUE_AVG_POINTS = 22.5
RATINGS_TTL_SECONDS = 6 * 60 * 60

_lock = threading.Lock()
_ratings_built_at = 0.0


def _ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def load_nfl_elo():
    if not os.path.exists(ELO_PATH):
        return pd.DataFrame()
    try:
        return pd.read_csv(ELO_PATH)
    except Exception:
        return pd.DataFrame()


def load_nfl_history():
    if not os.path.exists(HISTORY_PATH):
        return pd.DataFrame()
    try:
        return pd.read_csv(HISTORY_PATH)
    except Exception:
        return pd.DataFrame()


def get_h2h_record(history, home, away):
    if history is None or getattr(history, "empty", True):
        return {"home_wins": 0, "away_wins": 0, "meetings": 0}
    home_games = history[(history["HomeTeam"] == home) & (history["VisitorTeam"] == away)]
    away_games = history[(history["HomeTeam"] == away) & (history["VisitorTeam"] == home)]
    home_wins = int((home_games["HomeScore"] > home_games["VisitorScore"]).sum())
    home_wins += int((away_games["VisitorScore"] > away_games["HomeScore"]).sum())
    meetings = len(home_games) + len(away_games)
    return {"home_wins": home_wins, "away_wins": meetings - home_wins, "meetings": meetings}


def nfl_win_probability(elo_home, elo_away):
    return 1.0 / (1.0 + 10 ** (-((elo_home + HOME_ADVANTAGE) - elo_away) / 400.0))


def blend_with_h2h(prob_home, h2h):
    meetings = h2h["meetings"]
    if meetings == 0:
        return prob_home
    h2h_rate_home = h2h["home_wins"] / meetings
    capped = min(meetings, 10)
    weight = capped / (10 + capped)
    return (1 - weight) * prob_home + weight * h2h_rate_home


def blend_with_streak(prob_home, home_streak, away_streak, weight=0.05):
    diff = max(-10, min(10, home_streak - away_streak))
    streak_prob_home = 0.5 + (diff / 10.0) * 0.5
    return (1 - weight) * prob_home + weight * streak_prob_home


def blend_with_sentiment(prob_home, home_sentiment, away_sentiment, weight=0.05):
    if home_sentiment is None or away_sentiment is None:
        return prob_home
    diff = max(-1.0, min(1.0, home_sentiment - away_sentiment))
    sentiment_prob_home = 0.5 + (diff / 2.0) * 0.5
    return (1 - weight) * prob_home + weight * sentiment_prob_home


def _predict_pair(home_row, away_row, for_col, allowed_col):
    home_val = (float(home_row[for_col]) + float(away_row[allowed_col])) / 2
    away_val = (float(away_row[for_col]) + float(home_row[allowed_col])) / 2
    return round(home_val, 2), round(away_val, 2)


def predict_team_points(home_row, away_row):
    return _predict_pair(home_row, away_row, "AvgPointsScored", "AvgPointsAllowed")


def total_points_range(predicted_total, std_dev=TOTAL_POINTS_STD_DEV):
    low = round(max(0.0, predicted_total - std_dev), 1)
    high = round(predicted_total + std_dev, 1)
    return low, high


def total_points_over_under(predicted_total, std_dev=TOTAL_POINTS_STD_DEV):
    line = math.floor(predicted_total) + 0.5
    z = (line - predicted_total) / std_dev
    prob_over = 1 - 0.5 * (1 + math.erf(z / math.sqrt(2)))
    if prob_over >= 0.5:
        return {"line": line, "pick": "Over", "probability": round(prob_over * 100, 1)}
    return {"line": line, "pick": "Under", "probability": round((1 - prob_over) * 100, 1)}


def elo_to_spread(elo_home, elo_away):
    """Home spread (negative = home favoured). Rounded to a .5 line."""
    points = -((elo_home + HOME_ADVANTAGE) - elo_away) / ELO_PER_POINT
    line = round(points * 2) / 2.0
    if abs(line) < 0.5:
        line = -0.5 if points < 0 else 0.5
    return line


def _cover_probability(predicted_margin, cover_threshold, std_dev=SPREAD_MARGIN_STD):
    """P(actual margin > cover_threshold) from a normal around predicted_margin."""
    z = (cover_threshold - predicted_margin) / std_dev
    return 1 - 0.5 * (1 + math.erf(z / math.sqrt(2)))


def _empty_team(abbr, name):
    return {
        "Team": abbr,
        "TeamName": name or abbr,
        "Elo": START_ELO,
        "AvgPointsScored": LEAGUE_AVG_POINTS,
        "AvgPointsAllowed": LEAGUE_AVG_POINTS,
        "CurrentStreak": 0,
        "SeasonWins": 0,
        "SeasonLosses": 0,
        "Last10Wins": 0,
        "Last10Played": 0,
        "LastGameDate": "",
        "GamesPlayed": 0,
        "_pf": 0.0,
        "_pa": 0.0,
        "_recent": [],
    }


def rebuild_ratings(games):
    """Walk completed games in date order and return (elo_df, history_df)."""
    teams = {}
    history_rows = []
    ordered = sorted(
        [g for g in (games or []) if g.get("completed") and g.get("home_abbr") and g.get("away_abbr")],
        key=lambda g: g.get("date") or "",
    )
    for g in ordered:
        home = str(g["home_abbr"]).strip().upper()
        away = str(g["away_abbr"]).strip().upper()
        try:
            hs = float(g["home_score"])
            aws = float(g["away_score"])
        except (TypeError, ValueError, KeyError):
            continue
        if home not in teams:
            teams[home] = _empty_team(home, g.get("home_team") or home)
        if away not in teams:
            teams[away] = _empty_team(away, g.get("away_team") or away)
        teams[home]["TeamName"] = g.get("home_team") or teams[home]["TeamName"]
        teams[away]["TeamName"] = g.get("away_team") or teams[away]["TeamName"]

        elo_h = teams[home]["Elo"]
        elo_a = teams[away]["Elo"]
        expected = nfl_win_probability(elo_h, elo_a)
        if hs > aws:
            actual = 1.0
        elif aws > hs:
            actual = 0.0
        else:
            actual = 0.5
        teams[home]["Elo"] = elo_h + K_FACTOR * (actual - expected)
        teams[away]["Elo"] = elo_a + K_FACTOR * ((1.0 - actual) - (1.0 - expected))

        teams[home]["_pf"] += hs
        teams[home]["_pa"] += aws
        teams[away]["_pf"] += aws
        teams[away]["_pa"] += hs
        teams[home]["GamesPlayed"] += 1
        teams[away]["GamesPlayed"] += 1
        teams[home]["_recent"].append(1 if hs > aws else 0)
        teams[away]["_recent"].append(1 if aws > hs else 0)
        teams[home]["_recent"] = teams[home]["_recent"][-10:]
        teams[away]["_recent"] = teams[away]["_recent"][-10:]

        def _apply_result(row, won):
            if won is True:
                row["SeasonWins"] += 1
                row["CurrentStreak"] = row["CurrentStreak"] + 1 if row["CurrentStreak"] > 0 else 1
            elif won is False:
                row["SeasonLosses"] += 1
                row["CurrentStreak"] = row["CurrentStreak"] - 1 if row["CurrentStreak"] < 0 else -1
            # ties leave streak/record unchanged besides games played

        _apply_result(teams[home], True if hs > aws else False if aws > hs else None)
        _apply_result(teams[away], True if aws > hs else False if hs > aws else None)

        day = str(g.get("date") or "")[:10]
        teams[home]["LastGameDate"] = day
        teams[away]["LastGameDate"] = day
        history_rows.append({
            "Date": day,
            "HomeTeam": home,
            "VisitorTeam": away,
            "HomeScore": hs,
            "VisitorScore": aws,
            "HomeName": teams[home]["TeamName"],
            "AwayName": teams[away]["TeamName"],
        })

    rows = []
    for abbr, row in teams.items():
        gp = max(1, row["GamesPlayed"])
        recent = row["_recent"]
        rows.append({
            "Team": abbr,
            "TeamName": row["TeamName"],
            "Elo": round(row["Elo"], 1),
            "AvgPointsScored": round(row["_pf"] / gp, 2),
            "AvgPointsAllowed": round(row["_pa"] / gp, 2),
            "CurrentStreak": int(row["CurrentStreak"]),
            "SeasonWins": int(row["SeasonWins"]),
            "SeasonLosses": int(row["SeasonLosses"]),
            "Last10Wins": int(sum(recent)),
            "Last10Played": int(len(recent)),
            "LastGameDate": row["LastGameDate"],
            "GamesPlayed": int(row["GamesPlayed"]),
        })
    elo_df = pd.DataFrame(rows)
    history_df = pd.DataFrame(history_rows)
    return elo_df, history_df


def _save_ratings(elo_df, history_df):
    _ensure_data_dir()
    if elo_df is not None and not elo_df.empty:
        elo_df.to_csv(ELO_PATH, index=False)
    if history_df is not None and not history_df.empty:
        history_df.to_csv(HISTORY_PATH, index=False)


def _ratings_are_fresh():
    if not os.path.exists(ELO_PATH) or not os.path.exists(HISTORY_PATH):
        return False
    try:
        age = time.time() - os.path.getmtime(ELO_PATH)
    except OSError:
        return False
    return age <= RATINGS_TTL_SECONDS


def ensure_nfl_ratings(*, force=False):
    """Rebuild Elo from ESPN completed games when the on-disk cache is stale."""
    global _ratings_built_at
    if not force and _ratings_are_fresh():
        return load_nfl_elo()
    with _lock:
        if not force and _ratings_are_fresh():
            return load_nfl_elo()
        if not force and _ratings_built_at and (time.time() - _ratings_built_at) < 60:
            return load_nfl_elo()
        try:
            from nfl_fixtures import get_fixtures, get_history_fixtures
            games = get_history_fixtures()
            if not any(g.get("completed") for g in games or []):
                games = list(games or []) + list(get_fixtures() or [])
        except Exception:
            games = []
        if games:
            elo_df, history_df = rebuild_ratings(games)
            if not elo_df.empty:
                _save_ratings(elo_df, history_df)
                _ratings_built_at = time.time()
                return elo_df
        existing = load_nfl_elo()
        _ratings_built_at = time.time()
        return existing


def _streak_label(streak):
    if streak > 0:
        return f"won {streak} straight"
    if streak < 0:
        return f"lost {-streak} straight"
    return None


def generate_insights(home_code, away_code, home_row, away_row, h2h,
                      home_streak, away_streak, favourite, predicted_total,
                      home_prob=None, away_prob=None, spread_home=None):
    insights = []
    fav_name = home_code if favourite == home_code else away_code
    dog_code = away_code if favourite == home_code else home_code

    if home_prob is not None and away_prob is not None:
        fav_p = float(home_prob) if favourite == home_code else float(away_prob)
        dog_p = float(away_prob) if favourite == home_code else float(home_prob)
        if fav_p <= 0.57:
            insights.append(
                f"This is a coin-flip moneyline ({fav_name} {fav_p * 100:.0f}% / "
                f"{dog_code} {dog_p * 100:.0f}%). NFL winners are noisy — prefer "
                f"totals or the spread."
            )

    elo_diff = abs(float(home_row["Elo"]) - float(away_row["Elo"]))
    if elo_diff >= 40:
        insights.append(
            f"{fav_name} is favoured on team strength — rated {elo_diff:.0f} Elo "
            f"points higher than {dog_code} from recent completed games."
        )

    if h2h["meetings"] >= 3:
        fav_wins = h2h["home_wins"] if favourite == home_code else h2h["away_wins"]
        dog_wins = h2h["meetings"] - fav_wins
        if fav_wins > dog_wins:
            insights.append(
                f"{fav_name} leads the head-to-head {fav_wins}-{dog_wins} "
                f"across their last {h2h['meetings']} meetings in this sample."
            )

    fav_streak = home_streak if favourite == home_code else away_streak
    streak_text = _streak_label(fav_streak)
    if streak_text and abs(fav_streak) >= 3:
        insights.append(f"{fav_name} is on form, having {streak_text}.")

    if predicted_total >= 49.5:
        insights.append(
            f"Projected total of {predicted_total} points leans toward a high-scoring game."
        )
    elif predicted_total <= 39.5:
        insights.append(
            f"Projected total of {predicted_total} points leans toward a lower-scoring game."
        )

    if spread_home is not None:
        if spread_home < 0:
            insights.append(
                f"Model spread: {home_code} {spread_home:g} (home-field Elo folded in)."
            )
        else:
            insights.append(
                f"Model spread: {away_code} {-spread_home:g} (home-field Elo folded in)."
            )

    if not insights:
        insights.append(
            f"{fav_name} is a slight favourite, but Elo, head-to-head, and form "
            f"do not show a clear separation — this one looks close."
        )
    return insights


def compute_nfl_prediction(home_code, away_code, as_of_date=None):
    """Shared by the JSON predict API, match page, and Today's Picks."""
    del as_of_date  # Elo is current-model, same honesty note as MLB
    df = load_nfl_elo()
    if df.empty:
        df = ensure_nfl_ratings()
    if df.empty:
        return None
    home_code = str(home_code or "").strip().upper()
    away_code = str(away_code or "").strip().upper()
    row_home = df[df["Team"] == home_code]
    row_away = df[df["Team"] == away_code]
    if row_home.empty or row_away.empty:
        return None

    home_row, away_row = row_home.iloc[0], row_away.iloc[0]
    elo_home = float(home_row["Elo"])
    elo_away = float(away_row["Elo"])
    history = load_nfl_history()
    h2h = get_h2h_record(history, home_code, away_code)
    home_streak = int(home_row.get("CurrentStreak", 0) or 0)
    away_streak = int(away_row.get("CurrentStreak", 0) or 0)

    prob_home = nfl_win_probability(elo_home, elo_away)
    prob_home = blend_with_h2h(prob_home, h2h)
    prob_home = blend_with_streak(prob_home, home_streak, away_streak)

    try:
        import sentiment
        home_sentiment = sentiment.get_team_sentiment(str(home_row["TeamName"]))
        away_sentiment = sentiment.get_team_sentiment(str(away_row["TeamName"]))
        prob_home = blend_with_sentiment(prob_home, home_sentiment, away_sentiment)
    except Exception:
        pass

    home_pts, away_pts = predict_team_points(home_row, away_row)
    favourite = home_code if prob_home >= 0.5 else away_code
    total_pts = round(home_pts + away_pts, 1)
    pts_low, pts_high = total_points_range(total_pts)
    pts_ou = total_points_over_under(total_pts)
    home_ou = total_points_over_under(home_pts, std_dev=TEAM_POINTS_STD_DEV)
    away_ou = total_points_over_under(away_pts, std_dev=TEAM_POINTS_STD_DEV)
    spread_home = elo_to_spread(elo_home, elo_away)
    predicted_margin = home_pts - away_pts
    # Home covers home spread_home (e.g. -3.5) if home_margin > -spread_home.
    home_cover_p = _cover_probability(predicted_margin, -spread_home)
    if spread_home < 0:
        spread_pick = f"{home_code} {spread_home:g}"
        spread_prob = home_cover_p
    else:
        spread_pick = f"{away_code} {-spread_home:g}"
        spread_prob = 1.0 - home_cover_p
    insights = generate_insights(
        home_code, away_code, home_row, away_row, h2h,
        home_streak, away_streak, favourite, total_pts,
        home_prob=prob_home, away_prob=1 - prob_home, spread_home=spread_home,
    )
    return {
        "home": home_code, "away": away_code,
        "home_name": str(home_row.get("TeamName") or home_code),
        "away_name": str(away_row.get("TeamName") or away_code),
        "home_prob": prob_home, "away_prob": 1 - prob_home,
        "elo_home": round(elo_home, 1), "elo_away": round(elo_away, 1),
        "h2h_home_wins": h2h["home_wins"], "h2h_away_wins": h2h["away_wins"],
        "h2h_meetings": h2h["meetings"],
        "favourite": favourite,
        "home_streak": home_streak, "away_streak": away_streak,
        "home_season_record": f"{int(home_row.get('SeasonWins', 0) or 0)}-{int(home_row.get('SeasonLosses', 0) or 0)}",
        "away_season_record": f"{int(away_row.get('SeasonWins', 0) or 0)}-{int(away_row.get('SeasonLosses', 0) or 0)}",
        "predicted_total_points": total_pts,
        "predicted_total_points_low": pts_low,
        "predicted_total_points_high": pts_high,
        "total_points_line": pts_ou["line"],
        "total_points_pick": pts_ou["pick"],
        "total_points_pick_probability": pts_ou["probability"],
        "home_predicted_points": home_pts,
        "away_predicted_points": away_pts,
        "home_points_line": home_ou["line"],
        "home_points_pick": home_ou["pick"],
        "home_points_pick_probability": home_ou["probability"],
        "away_points_line": away_ou["line"],
        "away_points_pick": away_ou["pick"],
        "away_points_pick_probability": away_ou["probability"],
        "spread_home": spread_home,
        "spread_pick": spread_pick,
        "spread_pick_probability": round(float(spread_prob) * 100, 1),
        "insights": insights,
    }


def nfl_track_record(n=20):
    """Current model vs the last n finished games (not a frozen backtest)."""
    history = load_nfl_history()
    if history.empty:
        ensure_nfl_ratings()
        history = load_nfl_history()
    if history.empty:
        return [], {}

    recent = history.tail(n).iloc[::-1]
    rows = []
    for _, g in recent.iterrows():
        home_code, away_code = g["HomeTeam"], g["VisitorTeam"]
        pred = compute_nfl_prediction(home_code, away_code)
        if pred is None:
            continue
        actual_home = float(g["HomeScore"])
        actual_away = float(g["VisitorScore"])
        actual_total = actual_home + actual_away
        actual_winner = home_code if actual_home > actual_away else away_code
        line, pick = pred["total_points_line"], pred["total_points_pick"]
        points_correct = (actual_total > line) if pick == "Over" else (actual_total < line)
        spread = float(pred["spread_home"])
        home_covered = (actual_home + spread) > actual_away
        spread_fav_is_home = spread < 0
        spread_correct = home_covered if spread_fav_is_home else (not home_covered)
        rows.append({
            "date": g["Date"],
            "home": g.get("HomeName") or home_code,
            "away": g.get("AwayName") or away_code,
            "predicted_winner": pred["home_name"] if pred["favourite"] == home_code else pred["away_name"],
            "actual_winner": (g.get("HomeName") or home_code) if actual_winner == home_code else (g.get("AwayName") or away_code),
            "winner_correct": pred["favourite"] == actual_winner,
            "total_points_pick": pick,
            "total_points_line": line,
            "actual_total_points": actual_total,
            "points_correct": points_correct,
            "spread_pick": pred["spread_pick"],
            "spread_correct": spread_correct,
        })
    n_rows = len(rows)
    summary = {
        "n": n_rows,
        "winner_acc": (sum(1 for r in rows if r["winner_correct"]) / n_rows) if n_rows else 0.0,
        "points_acc": (sum(1 for r in rows if r["points_correct"]) / n_rows) if n_rows else 0.0,
        "spread_acc": (sum(1 for r in rows if r["spread_correct"]) / n_rows) if n_rows else 0.0,
    }
    return rows, summary
