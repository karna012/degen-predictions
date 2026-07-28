"""NPB prediction helpers: team Elo win probability (with home-field edge),
head-to-head blending, streak blending, and a total-runs estimate --
mirrors webapp/mlb_predict.py, minus the hits/HR/strikeouts markets and the
live-pitcher-ERA blend, since spaia.jp's schedule API only gives a starting
pitcher's *name*, not their season stats (see scripts/npb/npb_config.py)."""
import math
import os

import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data", "npb")
HOME_ADVANTAGE = 24.0  # must match scripts/npb/npb_config.py


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


TOTAL_RUNS_STD_DEV = 1.5  # same fixed band used for MLB -- communicates that
                          # this is a central estimate, not an exact guaranteed total
TEAM_RUNS_STD_DEV = 1.1   # a single team's runs vary less than the two-team combined total


def total_runs_range(predicted_total_runs, std_dev=TOTAL_RUNS_STD_DEV):
    low = round(max(0.0, predicted_total_runs - std_dev), 1)
    high = round(predicted_total_runs + std_dev, 1)
    return low, high


def total_runs_over_under(predicted_total_runs, std_dev=TOTAL_RUNS_STD_DEV):
    """A sportsbook-style Over line instead of a raw point estimate --
    see mlb_predict.total_runs_over_under() for the full rationale."""
    line = math.floor(predicted_total_runs) + 0.5
    if predicted_total_runs - math.floor(predicted_total_runs) < 0.5:
        line = math.floor(predicted_total_runs) - 0.5
    z = (line - predicted_total_runs) / std_dev
    prob_over = 1 - 0.5 * (1 + math.erf(z / math.sqrt(2)))
    return {"line": line, "pick": "Over", "probability": round(prob_over * 100, 1)}


def _streak_label(streak):
    if streak > 0:
        return f"won {streak} straight"
    if streak < 0:
        return f"lost {-streak} straight"
    return None


def generate_insights(home_name, away_name, home_row, away_row, h2h,
                       home_streak, away_streak, favourite_name, predicted_total_runs):
    insights = []
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
