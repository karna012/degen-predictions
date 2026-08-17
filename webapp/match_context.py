"""
Real, match-specific written context for soccer match pages: recent form
and head-to-head history, computed directly from the same historical CSVs
(data/<league>_5yr/) used to train the models -- not generated/filler text.

Exists because Google's AdSense review flagged the site's match pages as
"low value content": each one was ~430 words of stat cards and percentages
with no actual written analysis, and there are ~1,900 of these pages across
5 leagues. Genuine, specific sentences ("Arsenal have won 4 of their last 5
meetings with Chelsea") are the fix -- not padding with generic text.
"""
import os
from functools import lru_cache

import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEASONS = ["1920", "2021", "2122", "2223", "2324", "2425", "2526"]


@lru_cache(maxsize=None)
def _load_league_history(league_key):
    from leagues import LEAGUES
    cfg = LEAGUES[league_key]
    data_dir = os.path.join(BASE_DIR, cfg["data_dir"])
    fd_code = cfg["fd_code"]

    frames = []
    for season in SEASONS:
        path = os.path.join(data_dir, f"{fd_code}_{season}.csv")
        if os.path.exists(path):
            frames.append(pd.read_csv(path, usecols=["Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG"]))
    if not frames:
        return pd.DataFrame()

    history = pd.concat(frames, ignore_index=True)
    history["Date"] = pd.to_datetime(history["Date"], dayfirst=True, format="mixed")
    return history.sort_values("Date").reset_index(drop=True)


def _team_result(row, team):
    if row["HomeTeam"] == team:
        gf, ga = row["FTHG"], row["FTAG"]
    else:
        gf, ga = row["FTAG"], row["FTHG"]
    outcome = "W" if gf > ga else ("L" if gf < ga else "D")
    return outcome, int(gf), int(ga)


def get_recent_form(league_key, team, before_date, n=5):
    """Last n completed matches for `team` before `before_date`, oldest first."""
    history = _load_league_history(league_key)
    if history.empty:
        return []
    before = pd.to_datetime(before_date)
    matches = history[
        ((history["HomeTeam"] == team) | (history["AwayTeam"] == team)) & (history["Date"] < before)
    ].tail(n)

    results = []
    for _, row in matches.iterrows():
        outcome, gf, ga = _team_result(row, team)
        results.append({"outcome": outcome, "gf": gf, "ga": ga})
    return results


def get_head_to_head(league_key, home, away, before_date, n=5):
    """Last n meetings between these two teams before `before_date`, oldest first."""
    history = _load_league_history(league_key)
    if history.empty:
        return []
    before = pd.to_datetime(before_date)
    matches = history[
        (((history["HomeTeam"] == home) & (history["AwayTeam"] == away))
         | ((history["HomeTeam"] == away) & (history["AwayTeam"] == home)))
        & (history["Date"] < before)
    ].tail(n)

    results = []
    for _, row in matches.iterrows():
        results.append({
            "home": row["HomeTeam"], "away": row["AwayTeam"],
            "home_goals": int(row["FTHG"]), "away_goals": int(row["FTAG"]),
        })
    return results


def form_sentence(team, form, league_label):
    """'Arsenal head into this match having won 3, drawn 1, and lost 1 of their last 5 Premier League games (WWDLW), scoring 9 and conceding 3.'"""
    if len(form) < 3:
        return None
    wins = sum(1 for m in form if m["outcome"] == "W")
    draws = sum(1 for m in form if m["outcome"] == "D")
    losses = sum(1 for m in form if m["outcome"] == "L")
    gf = sum(m["gf"] for m in form)
    ga = sum(m["ga"] for m in form)
    form_str = "".join(m["outcome"] for m in form)
    n = len(form)
    return (
        f"{team} head into this match having won {wins}, drawn {draws}, and lost {losses} "
        f"of their last {n} {league_label} games ({form_str}), scoring {gf} and conceding {ga} "
        f"over that stretch."
    )


def h2h_sentence(home, away, h2h):
    """'In their last 5 meetings, Arsenal have won 3, Chelsea have won 1, with 1 draw -- the most recent finished Arsenal 2-1 Chelsea.' or a first-ever-meeting note."""
    if not h2h:
        return f"This will be the first meeting between {home} and {away} in the data this model was trained on."

    home_wins = sum(1 for m in h2h if (m["home"] == home and m["home_goals"] > m["away_goals"])
                     or (m["away"] == home and m["away_goals"] > m["home_goals"]))
    away_wins = sum(1 for m in h2h if (m["home"] == away and m["home_goals"] > m["away_goals"])
                     or (m["away"] == away and m["away_goals"] > m["home_goals"]))
    draws = len(h2h) - home_wins - away_wins

    last = h2h[-1]
    last_score = f"{last['home']} {last['home_goals']}-{last['away_goals']} {last['away']}"

    n = len(h2h)
    if n == 1:
        return f"These two haven't met since {last_score} -- their only meeting in this model's data."

    return (
        f"In their last {n} meetings, {home} have won {home_wins}, {away} have won {away_wins}, "
        f"with {draws} draw{'s' if draws != 1 else ''} -- the most recent finished {last_score}."
    )


def build_match_context(league_key, league_label, home, away, match_date, match=None, lineup_info=None):
    """Everything match.html needs for written analysis. Never raises.

    Includes classic form/H2H sentences plus the richer scout-style report from
    soccer_match_analysis (explain-only; does not alter ML odds).
    """
    home_form = get_recent_form(league_key, home, match_date)
    away_form = get_recent_form(league_key, away, match_date)
    h2h = get_head_to_head(league_key, home, away, match_date)

    analysis = None
    try:
        import soccer_match_analysis
        analysis = soccer_match_analysis.build_match_analysis(
            league_key, league_label, home, away, match_date,
            match=match, lineup_info=lineup_info,
        )
    except Exception:
        analysis = None

    return {
        "home_form_sentence": form_sentence(home, home_form, league_label),
        "away_form_sentence": form_sentence(away, away_form, league_label),
        "h2h_sentence": h2h_sentence(home, away, h2h),
        "analysis": analysis,
    }
