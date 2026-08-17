"""NPB prediction helpers: team Elo win probability (with home-field edge),
head-to-head blending, streak blending, and a total-runs estimate --
mirrors webapp/mlb_predict.py, minus the hits/HR/strikeouts markets and the
live-pitcher-ERA blend, since spaia.jp's schedule API only gives a starting
pitcher's *name*, not their season stats (see scripts/npb/npb_config.py)."""
import math
import os
from datetime import date, datetime

import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data", "npb")
HOME_ADVANTAGE = 24.0  # must match scripts/npb/npb_config.py
MAX_REST_DAYS = 14.0
DEFAULT_REST_DAYS = 3.0

NPB_ML_FEATURE_NAMES = [
    "EloDiff", "HomeLast10WinRate", "AwayLast10WinRate", "HomeStreak", "AwayStreak",
    "H2HMeetings", "HomeH2HWinRate", "RunsForDiff", "RunsAllowedDiff",
    "HomeDaysRest", "AwayDaysRest",
]


def load_npb_elo():
    path = os.path.join(DATA_DIR, "elo_ratings.csv")
    if not os.path.exists(path):
        return pd.DataFrame()
    return pd.read_csv(path)


def load_npb_history():
    path = os.path.join(DATA_DIR, "game_history.csv")
    if not os.path.exists(path):
        return pd.DataFrame()
    return pd.read_csv(path)


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


def build_npb_ml_features(home_row, away_row, h2h, as_of_date=None):
    """Feature vector matching scripts/npb/train_ml_models.py FEATURES."""
    as_of = _parse_as_of_date(as_of_date)
    h2h_meetings = h2h["meetings"]
    h2h_home_rate = (h2h["home_wins"] / h2h_meetings) if h2h_meetings else 0.5
    home_last10_rate = (home_row.get("Last10Wins", 0) / home_row["Last10Played"]) if home_row.get("Last10Played") else 0.5
    away_last10_rate = (away_row.get("Last10Wins", 0) / away_row["Last10Played"]) if away_row.get("Last10Played") else 0.5
    return {
        "EloDiff": float(home_row["Elo"]) - float(away_row["Elo"]),
        "HomeLast10WinRate": float(home_last10_rate),
        "AwayLast10WinRate": float(away_last10_rate),
        "HomeStreak": int(home_row.get("CurrentStreak", 0)),
        "AwayStreak": int(away_row.get("CurrentStreak", 0)),
        "H2HMeetings": h2h_meetings,
        "HomeH2HWinRate": h2h_home_rate,
        "RunsForDiff": float(home_row["AvgRunsScored"]) - float(away_row["AvgRunsScored"]),
        "RunsAllowedDiff": float(away_row["AvgRunsAllowed"]) - float(home_row["AvgRunsAllowed"]),
        "HomeDaysRest": _days_rest_from_last_game(home_row.get("LastGameDate"), as_of),
        "AwayDaysRest": _days_rest_from_last_game(away_row.get("LastGameDate"), as_of),
    }


def get_h2h_record(history, home_id, away_id):
    if history.empty:
        return {"home_wins": 0, "away_wins": 0, "meetings": 0}
    home_games = history[(history["HomeTeamID"] == home_id) & (history["VisitorTeamID"] == away_id)]
    away_games = history[(history["HomeTeamID"] == away_id) & (history["VisitorTeamID"] == home_id)]
    home_wins = int((home_games["HomeScore"] > home_games["VisitorScore"]).sum())
    home_wins += int((away_games["VisitorScore"] > away_games["HomeScore"]).sum())
    meetings = len(home_games) + len(away_games)
    return {"home_wins": home_wins, "away_wins": meetings - home_wins, "meetings": meetings}


def npb_win_probability(elo_home, elo_away):
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
    """See mlb_predict.blend_with_sentiment() for the full rationale."""
    if home_sentiment is None or away_sentiment is None:
        return prob_home
    diff = max(-1.0, min(1.0, home_sentiment - away_sentiment))
    sentiment_prob_home = 0.5 + (diff / 2.0) * 0.5
    return (1 - weight) * prob_home + weight * sentiment_prob_home


def predict_total_runs(home_row, away_row):
    home_runs = (home_row["AvgRunsScored"] + away_row["AvgRunsAllowed"]) / 2
    away_runs = (away_row["AvgRunsScored"] + home_row["AvgRunsAllowed"]) / 2
    return round(home_runs, 2), round(away_runs, 2)


TOTAL_RUNS_STD_DEV = 4.1  # empirical std dev of HomeScore+VisitorScore across NPB history
                          # (6.4k+ games) -- was hardcoded to 1.5, same ~3x-too-confident
                          # bug as MLB's constant of the same name; see mlb_predict.py.
TEAM_RUNS_STD_DEV = 2.9   # empirical std dev of a single team's runs across the same history
                          # (home ~2.83, away ~2.93 -- close enough to share one constant)


def total_runs_range(predicted_total_runs, std_dev=TOTAL_RUNS_STD_DEV):
    low = round(max(0.0, predicted_total_runs - std_dev), 1)
    high = round(predicted_total_runs + std_dev, 1)
    return low, high


def total_runs_over_under(predicted_total_runs, std_dev=TOTAL_RUNS_STD_DEV):
    """A sportsbook-style line instead of a raw point estimate --
    see mlb_predict.total_runs_over_under() for the full rationale."""
    line = math.floor(predicted_total_runs) + 0.5
    z = (line - predicted_total_runs) / std_dev
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


def generate_insights(home_name, away_name, home_row, away_row, h2h,
                       home_streak, away_streak, favourite_name, predicted_total_runs,
                       home_prob=None, away_prob=None):
    insights = []
    dog_name = away_name if favourite_name == home_name else home_name

    if home_prob is not None and away_prob is not None:
        fav_p = float(home_prob) if favourite_name == home_name else float(away_prob)
        dog_p = float(away_prob) if favourite_name == home_name else float(home_prob)
        if fav_p <= 0.57:
            insights.append(
                f"This is a coin-flip moneyline ({favourite_name} {fav_p * 100:.0f}% / "
                f"{dog_name} {dog_p * 100:.0f}%). Underdog {dog_name} is live — "
                f"winner markets are noisy in NPB; prefer totals or team runs."
            )

    elo_diff = abs(float(home_row["Elo"]) - float(away_row["Elo"]))
    if elo_diff >= 15:
        insights.append(
            f"{favourite_name} is favoured on team strength -- rated {elo_diff:.0f} Elo points "
            f"higher based on results over recent seasons."
        )

    if h2h["meetings"] >= 5:
        fav_is_home = favourite_name == home_row["TeamName"]
        fav_wins = h2h["home_wins"] if fav_is_home else h2h["away_wins"]
        dog_wins = h2h["meetings"] - fav_wins
        if fav_wins > dog_wins:
            insights.append(
                f"{favourite_name} leads the head-to-head series {fav_wins}-{dog_wins} "
                f"across their last {h2h['meetings']} meetings."
            )

    fav_streak = home_streak if favourite_name == home_row["TeamName"] else away_streak
    streak_text = _streak_label(fav_streak)
    if streak_text and abs(fav_streak) >= 3:
        insights.append(f"{favourite_name} is on form, having {streak_text}.")

    if predicted_total_runs >= 8.0:
        insights.append(
            f"Both offenses are trending hot -- projected total of {predicted_total_runs} runs "
            f"leans toward a high-scoring game."
        )
    elif predicted_total_runs <= 5.5:
        insights.append(
            f"Both teams' recent run-scoring/allowing form points to a lower-scoring game -- "
            f"projected total of just {predicted_total_runs} runs."
        )

    if not insights:
        insights.append(
            f"{favourite_name} is a slight favourite, but no single signal (Elo, head-to-head, "
            f"or streak) shows a clear separation -- this one looks close."
        )
    return insights
