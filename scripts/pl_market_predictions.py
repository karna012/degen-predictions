"""
Premier League market predictions using 5 seasons of team match data
(football-data.co.uk) blended with player-level squad-quality data
(Understat xG/goals/assists) for attack-strength adjustment.

Markets predicted for every unplayed PL fixture:
- 1X2 (match result)
- BTTS (both teams to score)
- Total goals (over/under 2.5) and expected total goals
- Total corners (expected) and which team has more corners
"""

import json
import math
import os

import numpy as np
import pandas as pd
import requests

DATA_DIR = "data/pl_5yr"
PLAYERS_DIR = os.path.join(DATA_DIR, "players")
OUTPUT_PATH = "output_tables/PremierLeague_market_predictions.csv"

SEASONS = ["2122", "2223", "2324", "2425", "2526"]
SEASON_WEIGHTS = {"2122": 0.6, "2223": 0.75, "2324": 0.9, "2425": 1.0, "2526": 1.1}
UNDERSTAT_SEASON_MAP = {
    "2122": "2021", "2223": "2022", "2324": "2023", "2425": "2024", "2526": "2025",
}

MAX_GOALS = 8  # truncate Poisson score grid


def load_matches():
    frames = []
    for s in SEASONS:
        df = pd.read_csv(f"{DATA_DIR}/E0_{s}.csv")
        df["Season"] = s
        df["Weight"] = SEASON_WEIGHTS[s]
        frames.append(df[["Season", "Weight", "Date", "HomeTeam", "AwayTeam",
                           "FTHG", "FTAG", "HC", "AC"]])
    return pd.concat(frames, ignore_index=True)


def load_squad_quality():
    """Per team, a normalized attacking-quality index from the most recent
    two seasons of Understat player data (goals + assists + xG, summed)."""
    scores = {}
    for season_code in ["2425", "2526"]:
        u_season = UNDERSTAT_SEASON_MAP[season_code]
        path = os.path.join(PLAYERS_DIR, f"players_{u_season}.json")
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            players = json.load(f)
        team_scores = {}
        for p in players:
            team = p["team_title"]
            contrib = float(p["goals"]) + float(p["assists"]) + float(p["xG"])
            team_scores[team] = team_scores.get(team, 0.0) + contrib
        weight = SEASON_WEIGHTS[season_code]
        for team, val in team_scores.items():
            scores[team] = scores.get(team, 0.0) + val * weight
    if not scores:
        return {}
    vals = np.array(list(scores.values()))
    mean, std = vals.mean(), vals.std() if vals.std() > 0 else 1.0
    return {team: (val - mean) / std for team, val in scores.items()}


# Understat team names occasionally differ from football-data.co.uk short names.
UNDERSTAT_NAME_MAP = {
    "Manchester United": "Man United", "Manchester City": "Man City",
    "Tottenham": "Tottenham", "Newcastle United": "Newcastle",
    "Wolverhampton Wanderers": "Wolves", "Nottingham Forest": "Nott'm Forest",
    "Leeds": "Leeds", "Leicester": "Leicester", "West Bromwich Albion": "West Brom",
    "Brighton": "Brighton", "West Ham": "West Ham", "Sheffield United": "Sheffield United",
}


def normalize_squad_quality(raw_scores):
    out = {}
    for team, val in raw_scores.items():
        mapped = UNDERSTAT_NAME_MAP.get(team, team)
        out[mapped] = val
    return out


def team_rates(matches):
    """Weighted-average goals/corners for/against, home and away, per team."""
    teams = pd.unique(matches[["HomeTeam", "AwayTeam"]].values.ravel())
    league_home_goals = np.average(matches["FTHG"], weights=matches["Weight"])
    league_away_goals = np.average(matches["FTAG"], weights=matches["Weight"])

    stats = {}
    for team in teams:
        home = matches[matches["HomeTeam"] == team]
        away = matches[matches["AwayTeam"] == team]

        def wavg(df, col, default):
            return np.average(df[col], weights=df["Weight"]) if len(df) else default

        stats[team] = {
            "attack_home": wavg(home, "FTHG", league_home_goals) / league_home_goals,
            "defense_home": wavg(home, "FTAG", league_away_goals) / league_away_goals,
            "attack_away": wavg(away, "FTAG", league_away_goals) / league_away_goals,
            "defense_away": wavg(away, "FTHG", league_home_goals) / league_home_goals,
            "corners_for_home": wavg(home, "HC", 5.5),
            "corners_against_home": wavg(home, "AC", 4.5),
            "corners_for_away": wavg(away, "AC", 4.5),
            "corners_against_away": wavg(away, "HC", 5.5),
            "n_home": len(home), "n_away": len(away),
        }
    return stats, league_home_goals, league_away_goals


def poisson_pmf(k, lam):
    return math.exp(-lam) * lam**k / math.factorial(k)


def score_matrix(lam_home, lam_away):
    m = np.zeros((MAX_GOALS + 1, MAX_GOALS + 1))
    for i in range(MAX_GOALS + 1):
        for j in range(MAX_GOALS + 1):
            m[i, j] = poisson_pmf(i, lam_home) * poisson_pmf(j, lam_away)
    return m / m.sum()


def predict_fixture(home, away, stats, league_home_goals, league_away_goals, squad_quality):
    QUALITY_WEIGHT = 0.12  # small blend from player-level squad strength

    if home in stats:
        h = stats[home]
        home_attack = h["attack_home"] * (1 + QUALITY_WEIGHT * squad_quality.get(home, 0.0))
        home_defense = h["defense_home"]
    else:
        home_attack, home_defense = 1.0, 1.0

    if away in stats:
        a = stats[away]
        away_attack = a["attack_away"] * (1 + QUALITY_WEIGHT * squad_quality.get(away, 0.0))
        away_defense = a["defense_away"]
    else:
        away_attack, away_defense = 1.0, 1.0

    lam_home = home_attack * away_defense * league_home_goals
    lam_away = away_attack * home_defense * league_away_goals
    lam_home, lam_away = max(lam_home, 0.15), max(lam_away, 0.15)

    m = score_matrix(lam_home, lam_away)
    p_home = np.tril(m, -1).sum()
    p_draw = np.trace(m)
    p_away = np.triu(m, 1).sum()
    p_btts = m[1:, 1:].sum()
    totals = np.add.outer(np.arange(MAX_GOALS + 1), np.arange(MAX_GOALS + 1))
    p_over25 = m[totals > 2.5].sum()

    home_corners = stats.get(home, {}).get("corners_for_home", 5.5) * 0.5 + \
        stats.get(away, {}).get("corners_against_away", 4.5) * 0.5
    away_corners = stats.get(away, {}).get("corners_for_away", 4.5) * 0.5 + \
        stats.get(home, {}).get("corners_against_home", 5.5) * 0.5
    total_corners = home_corners + away_corners
    p_home_more_corners = home_corners / (home_corners + away_corners)

    return {
        "HomeTeam": home, "AwayTeam": away,
        "Exp_Home_Goals": round(lam_home, 2), "Exp_Away_Goals": round(lam_away, 2),
        "Exp_Total_Goals": round(lam_home + lam_away, 2),
        "P_Home_Win": round(p_home, 3), "P_Draw": round(p_draw, 3), "P_Away_Win": round(p_away, 3),
        "Predicted_Result": home if p_home == max(p_home, p_draw, p_away)
                             else ("Draw" if p_draw == max(p_home, p_draw, p_away) else away),
        "P_BTTS_Yes": round(p_btts, 3),
        "BTTS_Prediction": "Yes" if p_btts >= 0.5 else "No",
        "P_Over_2.5_Goals": round(p_over25, 3),
        "Goals_Prediction": "Over 2.5" if p_over25 >= 0.5 else "Under 2.5",
        "Exp_Home_Corners": round(home_corners, 2), "Exp_Away_Corners": round(away_corners, 2),
        "Exp_Total_Corners": round(total_corners, 2),
        "P_Home_More_Corners": round(p_home_more_corners, 3),
        "More_Corners_Prediction": home if p_home_more_corners > 0.5 else away,
        "Data_Confidence": "Low (promoted/new team)" if (home not in stats or away not in stats) else "OK",
    }


def get_upcoming_fixtures():
    resp = requests.get("https://fixturedownload.com/feed/json/epl-2026")
    resp.raise_for_status()
    fixtures = resp.json()
    return [f for f in fixtures if f.get("HomeTeamScore") is None]


def main():
    matches = load_matches()
    stats, league_home_goals, league_away_goals = team_rates(matches)
    squad_quality = normalize_squad_quality(load_squad_quality())

    fixtures = get_upcoming_fixtures()
    rows = []
    for f in fixtures:
        rows.append(predict_fixture(f["HomeTeam"], f["AwayTeam"], stats,
                                     league_home_goals, league_away_goals, squad_quality))

    out = pd.DataFrame(rows)
    os.makedirs("output_tables", exist_ok=True)
    out.to_csv(OUTPUT_PATH, index=False)
    print(f"Saved {len(out)} fixture predictions to {OUTPUT_PATH}")
    print(out.head(10).to_string(index=False))


if __name__ == "__main__":
    main()
