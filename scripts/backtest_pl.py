"""
Applies the trained models to the past 5 completed Premier League seasons
and compares predicted vs actual results.

Honesty note baked into the output: train_models.py trains on a chronological
80% slice and only truly holds out the most recent 20% of matches. That means
most of "the past 5 seasons" was material the model actually trained on --
predictions there are in-sample and will look better than genuine
out-of-sample performance. Every row here is tagged Sample_Type so the
website can (and does) report accuracy separately for each, rather than
quoting one inflated blended number.
"""
import os
import pickle
import sys

import numpy as np
import pandas as pd

from leagues import LEAGUES, REPO_ROOT, resolve_league_paths

DISPLAY_SEASONS = ["2122", "2223", "2324", "2425", "2526"]  # past 5 completed seasons


def load_models(MODEL_DIR):
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
        "result": load("result"), "btts": load("btts"), "over25": load("over25"),
        "home_more_corners": load("home_more_corners"),
        "favorite_avoids_defeat": load("favorite_avoids_defeat"),
        "total_corners": load("total_corners"),
    }
    return models, feature_cols, result_label_map


def classify(base_models, meta, best_strategy, X):
    if best_strategy in base_models:
        return base_models[best_strategy].predict_proba(X)
    probs = [base_models[m].predict_proba(X) for m in base_models]
    if best_strategy == "avg_ensemble":
        return np.mean(probs, axis=0)
    return meta.predict_proba(np.hstack(probs))


def regress(base_models, meta, best_strategy, X):
    if best_strategy in base_models:
        return base_models[best_strategy].predict(X)
    preds = [base_models[m].predict(X) for m in base_models]
    if best_strategy == "avg_ensemble":
        return np.mean(preds, axis=0)
    return meta.predict(np.column_stack(preds))


def backtest_for_league(league_key):
    cfg = resolve_league_paths(LEAGUES[league_key])
    data_dir, model_dir = cfg["data_dir"], cfg["model_dir"]
    output_path = os.path.join(REPO_ROOT, "output_tables", f"{cfg['output_name']}_backtest.csv")
    print(f"=== {cfg['label']} ===")

    models, feature_cols, result_label_map = load_models(model_dir)
    inv_result_map = {v: k for k, v in result_label_map.items()}

    full_df = pd.read_csv(f"{data_dir}/train_rows.csv", dtype={"Season": str})
    # Same chronological split train_models.py used -- rows before this index
    # were seen during training (in-sample); rows after were genuinely held out.
    split_idx = int(len(full_df) * 0.8)
    full_df["Sample_Type"] = ["In-Sample (model trained on this match)"] * split_idx + \
        ["Out-of-Sample (genuine holdout)"] * (len(full_df) - split_idx)

    display_seasons = cfg.get("display_seasons", DISPLAY_SEASONS)
    df = full_df[full_df["Season"].isin(display_seasons)].reset_index(drop=True)
    X_all = df[feature_cols]

    base_models, meta, strat = models["result"]
    result_proba = classify(base_models, meta, strat, X_all)
    result_pred_idx = np.argmax(result_proba, axis=1)
    df["Predicted_Result_Code"] = [inv_result_map[i] for i in result_pred_idx]
    df["P_Predicted_Result"] = result_proba[np.arange(len(df)), result_pred_idx]

    base_models, meta, strat = models["btts"]
    df["P_BTTS_Yes"] = classify(base_models, meta, strat, X_all)[:, 1]
    df["Predicted_BTTS"] = np.where(df["P_BTTS_Yes"] >= 0.5, "Yes", "No")

    base_models, meta, strat = models["over25"]
    df["P_Over25"] = classify(base_models, meta, strat, X_all)[:, 1]
    df["Predicted_Goals"] = np.where(df["P_Over25"] >= 0.5, "Over 2.5", "Under 2.5")

    if models["home_more_corners"] is None:
        df["P_HomeMoreCorners"] = np.nan
        df["Predicted_HomeMoreCorners"] = False
    else:
        base_models, meta, strat = models["home_more_corners"]
        df["P_HomeMoreCorners"] = classify(base_models, meta, strat, X_all)[:, 1]
        df["Predicted_HomeMoreCorners"] = df["P_HomeMoreCorners"] >= 0.5

    base_models, meta, strat = models["favorite_avoids_defeat"]
    df["P_FavoriteAvoidsDefeat"] = classify(base_models, meta, strat, X_all)[:, 1]
    df["Predicted_FavoriteAvoidsDefeat"] = df["P_FavoriteAvoidsDefeat"] >= 0.5

    if models["total_corners"] is None:
        df["Predicted_TotalCorners"] = np.nan
    else:
        base_models, meta, strat = models["total_corners"]
        df["Predicted_TotalCorners"] = regress(base_models, meta, strat, X_all)

    actual_home_more = df["Target_HomeMoreCorners"]
    corners_winner_correct = np.where(
        actual_home_more.notna(),
        actual_home_more.astype(bool) == df["Predicted_HomeMoreCorners"],
        np.nan,
    )

    out = pd.DataFrame({
        "Season": df["Season"], "Date": df["Date"],
        "HomeTeam": df["HomeTeam"], "AwayTeam": df["AwayTeam"],
        "Actual_Result": df["FTR"], "Predicted_Result": df["Predicted_Result_Code"],
        "Result_Correct": df["FTR"] == df["Predicted_Result_Code"],
        "P_Predicted_Result": df["P_Predicted_Result"].round(3),
        "Actual_BTTS": np.where(df["Target_BTTS"] == 1, "Yes", "No"),
        "Predicted_BTTS": df["Predicted_BTTS"],
        "BTTS_Correct": df["Target_BTTS"] == (df["Predicted_BTTS"] == "Yes").astype(int),
        "Actual_Goals": np.where(df["Target_Over25"] == 1, "Over 2.5", "Under 2.5"),
        "Predicted_Goals": df["Predicted_Goals"],
        "Goals_Correct": df["Target_Over25"] == (df["Predicted_Goals"] == "Over 2.5").astype(int),
        "Actual_TotalCorners": df["Target_TotalCorners"],
        "Predicted_TotalCorners": df["Predicted_TotalCorners"].round(1),
        "Actual_HomeMoreCorners": actual_home_more,
        "Predicted_HomeMoreCorners": df["Predicted_HomeMoreCorners"],
        "Corners_Winner_Correct": corners_winner_correct,
        "Favorite_Team": np.where(df["Home_Is_Favorite"], df["HomeTeam"], df["AwayTeam"]),
        "Actual_FavoriteAvoidsDefeat": df["Target_FavoriteAvoidsDefeat"].astype(bool),
        "Predicted_FavoriteAvoidsDefeat": df["Predicted_FavoriteAvoidsDefeat"],
        "FavoriteAvoidsDefeat_Correct": df["Target_FavoriteAvoidsDefeat"].astype(bool) == df["Predicted_FavoriteAvoidsDefeat"],
        "Sample_Type": df["Sample_Type"],
    })

    os.makedirs("output_tables", exist_ok=True)
    out.to_csv(output_path, index=False)
    print(f"Saved {len(out)} backtest rows to {output_path}")

    for sample_type, g in out.groupby("Sample_Type"):
        print(f"\n{sample_type} ({len(g)} matches):")
        print(f"  Result accuracy: {g['Result_Correct'].mean():.3f}")
        print(f"  BTTS accuracy: {g['BTTS_Correct'].mean():.3f}")
        print(f"  Goals O/U accuracy: {g['Goals_Correct'].mean():.3f}")
        corner_acc = g["Corners_Winner_Correct"].dropna().mean()
        print(f"  Corners winner accuracy: {corner_acc if pd.notna(corner_acc) else float('nan'):.3f}")
        print(f"  Favorite-avoids-defeat accuracy: {g['FavoriteAvoidsDefeat_Correct'].mean():.3f}")
        corner_mae = (g["Actual_TotalCorners"] - g["Predicted_TotalCorners"]).abs().dropna().mean()
        print(f"  Total corners MAE: {corner_mae if pd.notna(corner_mae) else float('nan'):.2f}")


def main():
    keys = sys.argv[1:] or list(LEAGUES.keys())
    for key in keys:
        backtest_for_league(key)


if __name__ == "__main__":
    main()
