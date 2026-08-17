"""MLB prediction helpers: team Elo win probability (with home-field edge),
head-to-head blending, a light live-pitcher-quality blend, and a total-runs
estimate -- mirrors the structure already used for tennis (Elo -> +H2H ->
+ a secondary signal), adapted for baseball's home/away and pitcher-driven
game environment instead of tennis's surface/best-of-N structure.
"""
import math
import os
from datetime import date, datetime

import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data", "mlb")
HOME_ADVANTAGE = 24.0  # must match scripts/mlb/mlb_config.py -- kept in sync manually
                       # since the web app doesn't share Python path with scripts/.
MAX_REST_DAYS = 14.0
DEFAULT_REST_DAYS = 3.0
DEFAULT_PARK_HOME_WIN_RATE = 0.54

MLB_ML_FEATURE_NAMES = [
    "EloDiff", "HomeLast10WinRate", "AwayLast10WinRate", "HomeStreak", "AwayStreak",
    "H2HMeetings", "HomeH2HWinRate", "RunsForDiff", "RunsAllowedDiff",
    "HitsForDiff", "HRForDiff", "SOInducedDiff",
    "PitcherQualityDiff", "PitcherWinRateDiff", "PitcherStartsDiff",
    "HomeDaysRest", "AwayDaysRest", "IsDayGame", "ParkHomeWinRate",
]


def load_mlb_elo():
    path = os.path.join(DATA_DIR, "elo_ratings.csv")
    if not os.path.exists(path):
        return pd.DataFrame()
    return pd.read_csv(path)


def load_mlb_history():
    path = os.path.join(DATA_DIR, "game_history.csv")
    if not os.path.exists(path):
        return pd.DataFrame()
    return pd.read_csv(path)


def load_park_home_win_rates():
    path = os.path.join(DATA_DIR, "park_home_win_rate.csv")
    if not os.path.exists(path):
        return {}
    df = pd.read_csv(path)
    return {str(r.ParkID): float(r.HomeWinRate) for r in df.itertuples(index=False)}


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


def _pitcher_win_rate(wins, losses):
    try:
        w, l = int(wins), int(losses)
    except (TypeError, ValueError):
        return 0.5
    total = w + l
    return (w / total) if total else 0.5


def _pitcher_starts_proxy(wins, losses):
    """ESPN does not expose career starts here -- W+L is a rough activity proxy."""
    try:
        return max(0, int(wins) + int(losses))
    except (TypeError, ValueError):
        return 0


def build_mlb_ml_features(home_row, away_row, h2h, home_era="", away_era="",
                          home_pitcher=None, away_pitcher=None, as_of_date=None,
                          is_day_game=0.0, park_id=None):
    """Feature vector matching scripts/mlb/train_ml_models.py FEATURES.

    Live ESPN starters usually lack Retrosheet IDs, so pitcher quality / win
    rate / starts are proxied from ERA and W-L when available (same inverse-ERA
    quality scale used in training). Missing pitcher info yields neutral diffs.
    """
    home_pitcher = home_pitcher or {}
    away_pitcher = away_pitcher or {}
    as_of = _parse_as_of_date(as_of_date)

    h2h_meetings = h2h["meetings"]
    h2h_home_rate = (h2h["home_wins"] / h2h_meetings) if h2h_meetings else 0.5
    home_last10_rate = (home_row.get("Last10Wins", 0) / home_row["Last10Played"]) if home_row.get("Last10Played") else 0.5
    away_last10_rate = (away_row.get("Last10Wins", 0) / away_row["Last10Played"]) if away_row.get("Last10Played") else 0.5

    home_q = _era_to_quality(home_era)
    away_q = _era_to_quality(away_era)
    if home_q is None or away_q is None:
        pitcher_quality_diff = 0.0
    else:
        pitcher_quality_diff = home_q - away_q

    home_wr = _pitcher_win_rate(home_pitcher.get("wins"), home_pitcher.get("losses"))
    away_wr = _pitcher_win_rate(away_pitcher.get("wins"), away_pitcher.get("losses"))
    # Only apply W-L diff when both sides have a real record; otherwise neutral.
    if home_pitcher.get("wins") is None and home_pitcher.get("losses") is None \
            and away_pitcher.get("wins") is None and away_pitcher.get("losses") is None:
        pitcher_win_rate_diff = 0.0
        pitcher_starts_diff = 0.0
    else:
        pitcher_win_rate_diff = home_wr - away_wr
        hs = _pitcher_starts_proxy(home_pitcher.get("wins"), home_pitcher.get("losses"))
        aws = _pitcher_starts_proxy(away_pitcher.get("wins"), away_pitcher.get("losses"))
        pitcher_starts_diff = math.log1p(hs) - math.log1p(aws)

    park_rates = load_park_home_win_rates()
    park_rate = park_rates.get(str(park_id), DEFAULT_PARK_HOME_WIN_RATE) if park_id else DEFAULT_PARK_HOME_WIN_RATE

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
        "HitsForDiff": float(home_row["AvgHitsFor"]) - float(away_row["AvgHitsFor"]),
        "HRForDiff": float(home_row["AvgHRFor"]) - float(away_row["AvgHRFor"]),
        "SOInducedDiff": float(home_row["AvgSOInduced"]) - float(away_row["AvgSOInduced"]),
        "PitcherQualityDiff": pitcher_quality_diff,
        "PitcherWinRateDiff": pitcher_win_rate_diff,
        "PitcherStartsDiff": pitcher_starts_diff,
        "HomeDaysRest": _days_rest_from_last_game(home_row.get("LastGameDate"), as_of),
        "AwayDaysRest": _days_rest_from_last_game(away_row.get("LastGameDate"), as_of),
        "IsDayGame": float(is_day_game),
        "ParkHomeWinRate": float(park_rate),
    }


def get_h2h_record(history, home, away):
    """Real head-to-head record between two teams (any venue), from every
    downloaded game."""
    if history.empty:
        return {"home_wins": 0, "away_wins": 0, "meetings": 0}
    home_games = history[(history["HomeTeam"] == home) & (history["VisitorTeam"] == away)]
    away_games = history[(history["HomeTeam"] == away) & (history["VisitorTeam"] == home)]
    home_wins = int((home_games["HomeScore"] > home_games["VisitorScore"]).sum())
    home_wins += int((away_games["VisitorScore"] > away_games["HomeScore"]).sum())
    meetings = len(home_games) + len(away_games)
    return {"home_wins": home_wins, "away_wins": meetings - home_wins, "meetings": meetings}


def mlb_win_probability(elo_home, elo_away):
    """Standard Elo expected-score formula with the home-field Elo bonus
    folded in (the home team's rating is never permanently boosted -- this
    only affects this one game's probability)."""
    return 1.0 / (1.0 + 10 ** (-((elo_home + HOME_ADVANTAGE) - elo_away) / 400.0))


def blend_with_h2h(prob_home, h2h):
    """Same capped-weight approach as tennis's blend_with_h2h: a short
    head-to-head history nudges the prediction but can never fully override
    the Elo signal (weight caps at 0.5)."""
    meetings = h2h["meetings"]
    if meetings == 0:
        return prob_home
    h2h_rate_home = h2h["home_wins"] / meetings
    capped = min(meetings, 10)
    weight = capped / (10 + capped)  # 1 meeting -> ~0.09, 10+ meetings -> 0.5 max
    return (1 - weight) * prob_home + weight * h2h_rate_home


def _era_to_quality(era):
    """Lower ERA is better -- converts to a positive "quality" score so it
    can be compared as a ratio, same idea as tennis's rank blending."""
    try:
        era = float(era)
    except (TypeError, ValueError):
        return None
    if era <= 0:
        return None
    return 1.0 / era


def blend_with_pitcher(prob_home, home_era, away_era, weight=0.12):
    """Folds in today's probable starting pitchers' current ERA as a light
    extra signal on top of the team Elo+H2H probability -- deliberately a
    small fixed weight since a single start's outcome depends on far more
    than the starter's season ERA (bullpen, offense, matchups)."""
    home_q = _era_to_quality(home_era)
    away_q = _era_to_quality(away_era)
    if home_q is None or away_q is None:
        return prob_home
    pitcher_prob_home = home_q / (home_q + away_q)
    return (1 - weight) * prob_home + weight * pitcher_prob_home


def _predict_pair(home_row, away_row, for_col, allowed_col):
    """Shared additive-average estimate used for runs, hits, and home runs:
    each side's own production rate blended against the opponent's allowing
    rate."""
    home_val = (home_row[for_col] + away_row[allowed_col]) / 2
    away_val = (away_row[for_col] + home_row[allowed_col]) / 2
    return round(home_val, 2), round(away_val, 2)


def predict_total_runs(home_row, away_row):
    """Simple additive run-environment estimate: average of each team's
    scoring rate against the opponent's allowing rate, for both sides."""
    return _predict_pair(home_row, away_row, "AvgRunsScored", "AvgRunsAllowed")


TOTAL_RUNS_STD_DEV = 4.6  # empirical std dev of HomeScore+VisitorScore across the full
                          # Retrosheet history (17k+ games) -- was hardcoded to 1.5 (a guess
                          # off by ~3x), which made every "Model confidence" figure on the
                          # site badly overstated (e.g. a genuine ~54% edge displayed as ~68%).
TEAM_RUNS_STD_DEV = 3.2   # empirical std dev of a single team's runs across the same history
                          # (home ~3.15, away ~3.29 -- close enough to share one constant)


def total_runs_range(predicted_total_runs, std_dev=TOTAL_RUNS_STD_DEV):
    """A plain +/-1 std-dev band around the point estimate -- communicates
    that this is a central estimate with real game-to-game variance around
    it, not a guaranteed exact total."""
    low = round(max(0.0, predicted_total_runs - std_dev), 1)
    high = round(predicted_total_runs + std_dev, 1)
    return low, high


def total_runs_over_under(predicted_total_runs, std_dev=TOTAL_RUNS_STD_DEV):
    """A sportsbook-style line instead of a raw point estimate -- floor(x)+0.5
    is always the single X.5 line nearest the point estimate (never a push),
    so it's used directly with no rounding-direction override. The pick is
    whichever side of that line the point estimate actually favours, from a
    normal approximation around the point estimate -- NOT hardcoded to
    Over, which used to make the reported "confidence" meaningless and let
    the model claim an edge it didn't have."""
    line = math.floor(predicted_total_runs) + 0.5
    z = (line - predicted_total_runs) / std_dev
    prob_over = 1 - 0.5 * (1 + math.erf(z / math.sqrt(2)))
    if prob_over >= 0.5:
        return {"line": line, "pick": "Over", "probability": round(prob_over * 100, 1)}
    return {"line": line, "pick": "Under", "probability": round((1 - prob_over) * 100, 1)}


def predict_total_hits(home_row, away_row):
    return _predict_pair(home_row, away_row, "AvgHitsFor", "AvgHitsAllowed")


def predict_total_home_runs(home_row, away_row):
    return _predict_pair(home_row, away_row, "AvgHRFor", "AvgHRAllowed")


def predict_total_strikeouts(home_row, away_row):
    """Strikeouts run the opposite direction from the other pairs: a team's
    own batters striking out (AvgSOAtBat) is a weakness, while what its
    pitching induces in opponents (AvgSOInduced) is a strength -- so each
    side's predicted K's come from blending its own at-bat K rate with the
    opponent's induced-K rate."""
    home_so = (home_row["AvgSOAtBat"] + away_row["AvgSOInduced"]) / 2
    away_so = (away_row["AvgSOAtBat"] + home_row["AvgSOInduced"]) / 2
    return round(home_so, 2), round(away_so, 2)


def blend_with_streak(prob_home, home_streak, away_streak, weight=0.05):
    """Folds in current winning/losing streaks as a small extra signal --
    deliberately light weight since a streak is already partly reflected in
    Elo (recent games already moved the rating), this just adds a touch more
    weight to "hot"/"cold" form. Streak difference is capped so an
    extreme streak (e.g. -9) doesn't dominate."""
    diff = max(-10, min(10, home_streak - away_streak))
    streak_prob_home = 0.5 + (diff / 10.0) * 0.5  # linear map [-10,10] -> [0,1]
    return (1 - weight) * prob_home + weight * streak_prob_home


def blend_with_sentiment(prob_home, home_sentiment, away_sentiment, weight=0.05):
    """Folds in recent news-headline sentiment (VADER score in [-1, 1] per
    team, see sentiment.py) as a small extra signal, same light weight as
    blend_with_streak for the same reason -- this is a minor tilt, not a
    primary signal. If either team has no sentiment data (no headlines
    found), the blend is skipped entirely rather than treating missing
    sentiment as neutral -- a fabricated 0.0 would silently pull real
    predictions toward 50/50 for teams that simply have less news
    coverage, not less momentum."""
    if home_sentiment is None or away_sentiment is None:
        return prob_home
    diff = max(-1.0, min(1.0, home_sentiment - away_sentiment))
    sentiment_prob_home = 0.5 + (diff / 2.0) * 0.5
    return (1 - weight) * prob_home + weight * sentiment_prob_home


def _streak_label(streak):
    if streak > 0:
        return f"won {streak} straight"
    if streak < 0:
        return f"lost {-streak} straight"
    return None


def generate_insights(home_code, away_code, home_row, away_row, h2h, home_era, away_era,
                       home_streak, away_streak, favourite, predicted_total_runs,
                       home_prob=None, away_prob=None):
    """Turns the raw prediction inputs into plain-language reasons -- same
    idea as football's generate_betting_tips(), but framed as "why" rather
    than separate market-by-market tips, since MLB's card already surfaces
    each number individually. Only raised when a signal shows real
    separation, same conservative-thresholds approach as football."""
    insights = []
    fav_name, dog_code = (home_code, away_code) if favourite == home_code else (away_code, home_code)

    if home_prob is not None and away_prob is not None:
        fav_p = float(home_prob) if favourite == home_code else float(away_prob)
        dog_p = float(away_prob) if favourite == home_code else float(home_prob)
        if fav_p <= 0.57:
            insights.append(
                f"This is a coin-flip moneyline ({fav_name} {fav_p * 100:.0f}% / "
                f"{dog_code} {dog_p * 100:.0f}%). Underdog {dog_code} is live — "
                f"winner markets are noisy in MLB; prefer totals, team runs, hits, or Ks."
            )

    elo_diff = float(home_row["Elo"]) - float(away_row["Elo"])
    fav_elo_diff = abs(elo_diff)
    if fav_elo_diff >= 15:
        insights.append(
            f"{fav_name} is favoured on team strength -- rated {fav_elo_diff:.0f} Elo points "
            f"higher than {dog_code} based on results over the last several seasons."
        )

    if h2h["meetings"] >= 5:
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

    home_q, away_q = _era_to_quality(home_era), _era_to_quality(away_era)
    if home_q is not None and away_q is not None:
        fav_era, dog_era = (home_era, away_era) if favourite == home_code else (away_era, home_era)
        if float(fav_era) < float(dog_era) - 0.5:
            insights.append(
                f"{fav_name}'s probable starter has a real edge on the mound tonight "
                f"({fav_era} ERA vs {dog_era})."
            )

    if predicted_total_runs >= 9.5:
        insights.append(
            f"Both offenses are trending hot and neither pitching staff has been stingy lately -- "
            f"projected total of {predicted_total_runs} runs leans toward a high-scoring game."
        )
    elif predicted_total_runs <= 7.0:
        insights.append(
            f"Both teams' recent run-scoring/allowing form points to a lower-scoring game -- "
            f"projected total of just {predicted_total_runs} runs."
        )

    if not insights:
        insights.append(
            f"{fav_name} is a slight favourite, but no single signal (Elo, head-to-head, "
            f"streak, or starting pitcher) shows a clear separation -- this one looks close."
        )
    return insights
