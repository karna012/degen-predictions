"""NBA prediction helpers: team Elo win probability (with an empirically
derived home-court edge), head-to-head blending, rest/back-to-back and
streak blends, sentiment blend, and a trained ML model blend on top --
mirrors mlb_predict.py's structure, adapted for basketball (points-for/
points-against instead of runs/hits/HR/SO, no starting-pitcher analog,
back-to-backs as a real NBA-specific signal instead of park factors).
"""
import math
import os
from datetime import date, datetime

import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data", "nba")
MAX_REST_DAYS = 10.0
DEFAULT_REST_DAYS = 2.0

NBA_ML_FEATURE_NAMES = [
    "EloDiff", "HomeLast10WinRate", "AwayLast10WinRate", "HomeStreak", "AwayStreak",
    "H2HMeetings", "HomeH2HWinRate", "PointsForDiff", "PointsAllowedDiff",
    "HomeDaysRest", "AwayDaysRest", "HomeBackToBack", "AwayBackToBack",
]


def load_nba_elo():
    path = os.path.join(DATA_DIR, "elo_ratings.csv")
    if not os.path.exists(path):
        return pd.DataFrame()
    return pd.read_csv(path)


def load_nba_history():
    path = os.path.join(DATA_DIR, "game_history.csv")
    if not os.path.exists(path):
        return pd.DataFrame()
    return pd.read_csv(path)


def load_home_advantage() -> float:
    path = os.path.join(DATA_DIR, "home_advantage.txt")
    if not os.path.exists(path):
        return 100.0
    try:
        with open(path, "r", encoding="utf-8") as f:
            return float(f.read().strip())
    except (OSError, ValueError):
        return 100.0


def _parse_as_of_date(as_of_date):
    if as_of_date is None or as_of_date == "":
        return date.today()
    if isinstance(as_of_date, date) and not isinstance(as_of_date, datetime):
        return as_of_date
    try:
        return pd.to_datetime(as_of_date).date()
    except (ValueError, TypeError):
        return date.today()


def _days_rest_from_last_game(last_game_date, as_of):
    if last_game_date is None or (isinstance(last_game_date, float) and math.isnan(last_game_date)):
        return DEFAULT_REST_DAYS
    try:
        prev = pd.to_datetime(last_game_date).date()
    except (ValueError, TypeError):
        return DEFAULT_REST_DAYS
    return float(min(max((as_of - prev).days, 0), MAX_REST_DAYS))


def build_nba_ml_features(home_row, away_row, h2h, as_of_date=None):
    """Feature vector matching scripts/nba/train_ml_models.py FEATURES."""
    as_of = _parse_as_of_date(as_of_date)

    h2h_meetings = h2h["meetings"]
    h2h_home_rate = (h2h["home_wins"] / h2h_meetings) if h2h_meetings else 0.5
    home_last10_rate = (home_row.get("Last10Wins", 0) / home_row["Last10Played"]) if home_row.get("Last10Played") else 0.5
    away_last10_rate = (away_row.get("Last10Wins", 0) / away_row["Last10Played"]) if away_row.get("Last10Played") else 0.5

    home_rest = _days_rest_from_last_game(home_row.get("LastGameDate"), as_of)
    away_rest = _days_rest_from_last_game(away_row.get("LastGameDate"), as_of)

    return {
        "EloDiff": float(home_row["Elo"]) - float(away_row["Elo"]),
        "HomeLast10WinRate": float(home_last10_rate),
        "AwayLast10WinRate": float(away_last10_rate),
        "HomeStreak": int(home_row.get("CurrentStreak", 0)),
        "AwayStreak": int(away_row.get("CurrentStreak", 0)),
        "H2HMeetings": h2h_meetings,
        "HomeH2HWinRate": h2h_home_rate,
        "PointsForDiff": float(home_row["AvgPointsFor"]) - float(away_row["AvgPointsFor"]),
        "PointsAllowedDiff": float(away_row["AvgPointsAllowed"]) - float(home_row["AvgPointsAllowed"]),
        "HomeDaysRest": home_rest,
        "AwayDaysRest": away_rest,
        "HomeBackToBack": 1.0 if home_rest <= 0 else 0.0,
        "AwayBackToBack": 1.0 if away_rest <= 0 else 0.0,
    }


def get_h2h_record(history, home, away):
    if history.empty:
        return {"home_wins": 0, "away_wins": 0, "meetings": 0}
    home_games = history[(history["HomeTeam"] == home) & (history["VisitorTeam"] == away)]
    away_games = history[(history["HomeTeam"] == away) & (history["VisitorTeam"] == home)]
    home_wins = int((home_games["HomeScore"] > home_games["VisitorScore"]).sum())
    home_wins += int((away_games["VisitorScore"] > away_games["HomeScore"]).sum())
    meetings = len(home_games) + len(away_games)
    return {"home_wins": home_wins, "away_wins": meetings - home_wins, "meetings": meetings}


def nba_win_probability(elo_home, elo_away, home_advantage=None):
    ha = load_home_advantage() if home_advantage is None else home_advantage
    return 1.0 / (1.0 + 10 ** (-((elo_home + ha) - elo_away) / 400.0))


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


def blend_with_rest(prob_home, home_rest, away_rest, weight=0.06):
    """Folds in the well-documented NBA back-to-back effect: a team on 0
    days rest measurably underperforms a normally-rested opponent. Capped
    diff keeps a single long-rest outlier (e.g. All-Star break) from
    dominating."""
    diff = max(-3.0, min(3.0, home_rest - away_rest))
    rest_prob_home = 0.5 + (diff / 3.0) * 0.5
    return (1 - weight) * prob_home + weight * rest_prob_home


def blend_with_sentiment(prob_home, home_sentiment, away_sentiment, weight=0.05):
    if home_sentiment is None or away_sentiment is None:
        return prob_home
    diff = max(-1.0, min(1.0, home_sentiment - away_sentiment))
    sentiment_prob_home = 0.5 + (diff / 2.0) * 0.5
    return (1 - weight) * prob_home + weight * sentiment_prob_home


def _predict_pair(home_row, away_row, for_col, allowed_col):
    home_val = (home_row[for_col] + away_row[allowed_col]) / 2
    away_val = (away_row[for_col] + home_row[allowed_col]) / 2
    return round(home_val, 1), round(away_val, 1)


def predict_total_points(home_row, away_row):
    return _predict_pair(home_row, away_row, "AvgPointsFor", "AvgPointsAllowed")


TOTAL_POINTS_STD_DEV = 13.5  # NBA game-total std dev is well-documented in the
                             # 12-14 point range across public models; refined
                             # once enough live results accumulate to measure
                             # this project's own number directly (same fix
                             # already applied to MLB's runs total, which was
                             # previously a guess off by ~3x).
TEAM_POINTS_STD_DEV = 10.0


def total_points_range(predicted_total_points, std_dev=TOTAL_POINTS_STD_DEV):
    low = round(max(0.0, predicted_total_points - std_dev), 1)
    high = round(predicted_total_points + std_dev, 1)
    return low, high


def total_points_over_under(predicted_total_points, std_dev=TOTAL_POINTS_STD_DEV):
    line = math.floor(predicted_total_points) + 0.5
    z = (line - predicted_total_points) / std_dev
    prob_over = 1 - 0.5 * (1 + math.erf(z / math.sqrt(2)))
    if prob_over >= 0.5:
        return {"line": line, "pick": "Over", "probability": round(prob_over * 100, 1)}
    return {"line": line, "pick": "Under", "probability": round((1 - prob_over) * 100, 1)}


def _streak_label(streak):
    if streak > 0:
        return f"won {streak} straight"
    if streak < 0:
        return f"lost {-streak} straight"
    return None


def generate_insights(home_code, away_code, home_row, away_row, h2h,
                       home_streak, away_streak, favourite, predicted_total_points,
                       home_rest, away_rest, home_prob=None, away_prob=None):
    insights = []
    fav_name, dog_code = (home_code, away_code) if favourite == home_code else (away_code, home_code)

    if home_prob is not None and away_prob is not None:
        fav_p = float(home_prob) if favourite == home_code else float(away_prob)
        dog_p = float(away_prob) if favourite == home_code else float(home_prob)
        if fav_p <= 0.57:
            insights.append(
                f"This is close to a coin-flip moneyline ({fav_name} {fav_p * 100:.0f}% / "
                f"{dog_code} {dog_p * 100:.0f}%). Underdog {dog_code} is live."
            )

    elo_diff = float(home_row["Elo"]) - float(away_row["Elo"])
    fav_elo_diff = abs(elo_diff)
    if fav_elo_diff >= 40:
        insights.append(
            f"{fav_name} is favoured on team strength -- rated {fav_elo_diff:.0f} Elo points "
            f"higher than {dog_code} based on results this season and last."
        )

    if h2h["meetings"] >= 3:
        fav_wins = h2h["home_wins"] if favourite == home_code else h2h["away_wins"]
        dog_wins = h2h["meetings"] - fav_wins
        if fav_wins > dog_wins:
            insights.append(
                f"{fav_name} leads the head-to-head series {fav_wins}-{dog_wins} "
                f"across their last {h2h['meetings']} meetings."
            )

    fav_streak = home_streak if favourite == home_code else away_streak
    streak_text = _streak_label(fav_streak)
    if streak_text and abs(fav_streak) >= 3:
        insights.append(f"{fav_name} is on form, having {streak_text}.")

    b2b_team, b2b_rest = (away_code, away_rest) if favourite == home_code else (home_code, home_rest)
    if b2b_rest <= 0:
        insights.append(f"{b2b_team} is on the second night of a back-to-back tonight.")

    if predicted_total_points >= 232:
        insights.append(
            f"Both offenses are trending hot -- projected total of {predicted_total_points} "
            f"points leans toward a high-scoring game."
        )
    elif predicted_total_points <= 214:
        insights.append(
            f"Both teams' recent scoring/defensive form points to a lower-scoring game -- "
            f"projected total of just {predicted_total_points} points."
        )

    if not insights:
        insights.append(
            f"{fav_name} is a slight favourite, but no single signal (Elo, head-to-head, "
            f"streak, or rest) shows a clear separation -- this one looks close."
        )
    return insights
