"""
Predicts markets for all upcoming fixtures in a given league using the
trained models from train_models.py. For each market, uses whichever
strategy (single best base model / simple average / stacked meta-learner)
actually won on the held-out chronological test set during training --
recorded per target in <model_dir>/training_metrics.pkl's "best_strategy"
field. With only ~2-3k rows to train on, the fanciest option (stacking)
doesn't always win.

Despite the filename (kept for backward compatibility -- webapp/live_predict.py
imports this module and overrides its globals directly), this now supports
every league in leagues.py via configure_league(), not just the Premier
League.
"""
import os
import pickle

import numpy as np
import pandas as pd
import requests

from build_features import h2h_features_from_history
from leagues import LEAGUES, REPO_ROOT, correct_fixture_dateutc

# Defaults to the Premier League; configure_league() (or webapp/live_predict.py
# overriding these attributes directly) switches to any other league.
# Paths are always under REPO_ROOT so cwd (scripts/ vs repo root) cannot split brains.
DATA_DIR = os.path.join(REPO_ROOT, "data/pl_5yr")
MODEL_DIR = os.path.join(REPO_ROOT, "model/pl")
OUTPUT_PATH = os.path.join(REPO_ROOT, "output_tables/PremierLeague_ml_predictions.csv")
FIXTURE_SLUG = "epl-2026"
FIXTURE_NAME_MAP = LEAGUES["premier_league"]["fixture_name_map"]
CURRENT_LEAGUE_KEY = "premier_league"

LEAGUE_AVG_ROW = {
    "Points_avg5": 1.35, "GF_avg5": 1.45, "GA_avg5": 1.45, "Shots_avg5": 12.5,
    "ShotsAgainst_avg5": 12.5, "ShotsOT_avg5": 4.3, "ShotsOTAgainst_avg5": 4.3,
    "Corners_avg5": 5.0, "CornersAgainst_avg5": 5.0, "Yellow_avg5": 1.8,
    "Red_avg5": 0.06, "SquadQuality": 0.0,
    "HomeAttackStrength": 1.0, "HomeDefenseStrength": 1.0,
    "AwayAttackStrength": 1.0, "AwayDefenseStrength": 1.0,
    "TeamHomeAdvantage": 0.35,
    "Fouls_avg5": 10.5, "FoulsAgainst_avg5": 10.5,
    "ShotQuality_avg5": 4.3 + 0.5 * (12.5 - 4.3),
    "ShotQualityAgainst_avg5": 4.3 + 0.5 * (12.5 - 4.3),
}

# Referee is not known this far ahead of kickoff -> always fall back to the
# league-wide average tendencies used during training for "unseen" referees.
REFEREE_UNKNOWN_ROW = {"Referee_AvgCards": 3.6, "Referee_AvgGoals": 2.7, "Referee_GamesSeen": 0}


def configure_league(league_key, base_dir=None):
    """Points every module-level path/config at the given league.

    Paths are always absolute under the repo root (or ``base_dir`` when the
    webapp passes its BASE_DIR). This prevents train/predict writing under
    ``scripts/model`` when cwd is ``scripts/``.
    """
    global DATA_DIR, MODEL_DIR, OUTPUT_PATH, FIXTURE_SLUG, FIXTURE_NAME_MAP, CURRENT_LEAGUE_KEY
    cfg = LEAGUES[league_key]
    root = base_dir if base_dir is not None else REPO_ROOT
    DATA_DIR = os.path.join(root, cfg["data_dir"])
    MODEL_DIR = os.path.join(root, cfg["model_dir"])
    OUTPUT_PATH = os.path.join(root, "output_tables", f"{cfg['output_name']}_ml_predictions.csv")
    FIXTURE_SLUG = cfg["fixturedownload_slug"]
    FIXTURE_NAME_MAP = cfg["fixture_name_map"]
    CURRENT_LEAGUE_KEY = league_key


def load_stacked_models():
    with open(f"{MODEL_DIR}/feature_cols.pkl", "rb") as f:
        feature_cols = pickle.load(f)
    with open(f"{MODEL_DIR}/result_label_map.pkl", "rb") as f:
        result_label_map = pickle.load(f)
    with open(f"{MODEL_DIR}/training_metrics.pkl", "rb") as f:
        training_metrics = pickle.load(f)

    def load(name):
        metrics = training_metrics.get(name, {})
        if metrics.get("skipped"):
            return None
        with open(f"{MODEL_DIR}/{name}_base_models.pkl", "rb") as f:
            base_models = pickle.load(f)
        with open(f"{MODEL_DIR}/{name}_meta.pkl", "rb") as f:
            meta = pickle.load(f)
        best_strategy = metrics.get("best_strategy", "stacked")
        return base_models, meta, best_strategy

    models = {
        "result": load("result"),
        "btts": load("btts"),
        "over25": load("over25"),
        "home_more_corners": load("home_more_corners"),
        "favorite_avoids_defeat": load("favorite_avoids_defeat"),
        "total_corners": load("total_corners"),
    }
    return models, feature_cols, result_label_map


def best_classify(base_models, meta, best_strategy, X):
    """Dispatches to whichever strategy won on held-out data at training
    time: a single base model, the plain average of all of them, or the
    stacked meta-learner on top of them."""
    if best_strategy in base_models:
        return base_models[best_strategy].predict_proba(X)
    probs = [base_models[m].predict_proba(X) for m in base_models]
    if best_strategy == "avg_ensemble":
        return np.mean(probs, axis=0)
    meta_X = np.hstack(probs)
    return meta.predict_proba(meta_X)


def best_regress(base_models, meta, best_strategy, X):
    if best_strategy in base_models:
        return base_models[best_strategy].predict(X)
    preds = [base_models[m].predict(X) for m in base_models]
    if best_strategy == "avg_ensemble":
        return np.mean(preds, axis=0)
    meta_X = np.column_stack(preds)
    return meta.predict(meta_X)


def get_team_row(latest_form, team):
    row = latest_form[latest_form["Team"] == team]
    if len(row):
        return row.iloc[0].to_dict()
    return {**LEAGUE_AVG_ROW, "Team": team, "GamesPlayed": 0}


def build_feature_row(home_row, away_row, feature_cols, h2h_history, home, away):
    data = {}
    for col in feature_cols:
        if col.startswith("Home_"):
            base = col[len("Home_"):]
            data[col] = home_row.get(base, LEAGUE_AVG_ROW.get(base, 0.0))
        elif col.startswith("Away_"):
            base = col[len("Away_"):]
            data[col] = away_row.get(base, LEAGUE_AVG_ROW.get(base, 0.0))

    if "Elo_Diff" in feature_cols:
        data["Elo_Diff"] = home_row.get("Elo", 1500) - away_row.get("Elo", 1500)

    h2h = h2h_features_from_history(h2h_history, home, away)
    for col in ["H2H_Meetings", "H2H_HomeWinRate", "H2H_AvgGoalsForHome", "H2H_AvgGoalsForAway"]:
        if col in feature_cols:
            data[col] = h2h[col]

    for col, default in REFEREE_UNKNOWN_ROW.items():
        if col in feature_cols:
            data[col] = default

    return pd.DataFrame([data])[feature_cols]


def get_upcoming_fixtures():
    resp = requests.get(f"https://fixturedownload.com/feed/json/{FIXTURE_SLUG}")
    resp.raise_for_status()
    fixtures = resp.json()
    return [f for f in fixtures if f.get("HomeTeamScore") is None]


def predict_all():
    models, feature_cols, result_label_map = load_stacked_models()
    inv_result_map = {v: k for k, v in result_label_map.items()}
    latest_form = pd.read_csv(f"{DATA_DIR}/latest_team_form.csv")

    with open(f"{DATA_DIR}/h2h_history.pkl", "rb") as f:
        h2h_history = pickle.load(f)

    fixtures = get_upcoming_fixtures()
    rows = []
    for fx in fixtures:
        home = FIXTURE_NAME_MAP.get(fx["HomeTeam"], fx["HomeTeam"])
        away = FIXTURE_NAME_MAP.get(fx["AwayTeam"], fx["AwayTeam"])

        home_row = get_team_row(latest_form, home)
        away_row = get_team_row(latest_form, away)
        confidence = "OK" if (home_row.get("GamesPlayed", 0) and away_row.get("GamesPlayed", 0)) \
            else "Low (promoted/new team)"

        X = build_feature_row(home_row, away_row, feature_cols, h2h_history, home, away)

        base_models, meta, best_strategy = models["result"]
        proba = best_classify(base_models, meta, best_strategy, X)
        result_proba = {inv_result_map[i]: proba[0][i] for i in range(proba.shape[1])}
        predicted_result_code = max(result_proba, key=result_proba.get)
        predicted_result = {"H": home, "D": "Draw", "A": away}[predicted_result_code]

        base_models, meta, best_strategy = models["btts"]
        p_btts = best_classify(base_models, meta, best_strategy, X)[0][1]

        base_models, meta, best_strategy = models["over25"]
        p_over25 = best_classify(base_models, meta, best_strategy, X)[0][1]

        if models["home_more_corners"] is None:
            p_home_more_corners = float("nan")
            more_corners_pred = ""
        else:
            base_models, meta, best_strategy = models["home_more_corners"]
            p_home_more_corners = best_classify(base_models, meta, best_strategy, X)[0][1]
            more_corners_pred = home if p_home_more_corners >= 0.5 else away

        home_is_favorite = float(X["Elo_Diff"].iloc[0]) >= 0
        favorite_team = home if home_is_favorite else away
        base_models, meta, best_strategy = models["favorite_avoids_defeat"]
        p_favorite_avoids_defeat = best_classify(base_models, meta, best_strategy, X)[0][1]

        if models["total_corners"] is None:
            exp_total_corners = float("nan")
        else:
            base_models, meta, best_strategy = models["total_corners"]
            exp_total_corners = best_regress(base_models, meta, best_strategy, X)[0]

        rows.append({
            "RoundNumber": fx["RoundNumber"],
            "DateUtc": correct_fixture_dateutc(
                CURRENT_LEAGUE_KEY, fx["DateUtc"], home, away,
            ),
            "HomeTeam": home, "AwayTeam": away,
            "P_Home_Win": round(float(result_proba.get("H", 0)), 3),
            "P_Draw": round(float(result_proba.get("D", 0)), 3),
            "P_Away_Win": round(float(result_proba.get("A", 0)), 3),
            "Predicted_Result": predicted_result,
            "P_BTTS_Yes": round(float(p_btts), 3),
            "BTTS_Prediction": "Yes" if p_btts >= 0.5 else "No",
            "P_Over_2.5_Goals": round(float(p_over25), 3),
            "Goals_Prediction": "Over 2.5" if p_over25 >= 0.5 else "Under 2.5",
            "Exp_Total_Corners": (
                "" if (isinstance(exp_total_corners, float) and np.isnan(exp_total_corners))
                else round(float(exp_total_corners), 2)
            ),
            "P_Home_More_Corners": (
                "" if (isinstance(p_home_more_corners, float) and np.isnan(p_home_more_corners))
                else round(float(p_home_more_corners), 3)
            ),
            "More_Corners_Prediction": more_corners_pred,
            "Favorite_Team": favorite_team,
            "P_Favorite_Avoids_Defeat": round(float(p_favorite_avoids_defeat), 3),
            "Favorite_Avoids_Defeat_Prediction": "Yes" if p_favorite_avoids_defeat >= 0.5 else "No",
            "Data_Confidence": confidence,
        })

    out = pd.DataFrame(rows)
    os.makedirs(os.path.dirname(OUTPUT_PATH) or ".", exist_ok=True)
    out.to_csv(OUTPUT_PATH, index=False)
    print(f"Saved {len(out)} ML predictions to {OUTPUT_PATH}")
    return out


def main():
    import sys
    keys = sys.argv[1:] or list(LEAGUES.keys())
    for key in keys:
        configure_league(key)
        print(f"=== {LEAGUES[key]['label']} ===")
        df = predict_all()
        print(df.head(5).to_string(index=False))


if __name__ == "__main__":
    main()
