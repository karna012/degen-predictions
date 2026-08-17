"""
On-demand, lineup-aware prediction for a single fixture, triggered by the
"Predict" button on the fixtures page. Reuses the same trained models as
the batch predict_pl.py run, but adjusts each side's SquadQuality feature
down if FotMob shows confirmed unavailable players for that match, using
each player's (goals + assists + xG) contribution from the current
season's Understat data as their weight. If FotMob hasn't published a
lineup yet (or the match isn't found this far out), it predicts with each
team's full current squad and flags the result as based on the "likely"
(unadjusted) lineup rather than waiting indefinitely. Works for any league
in leagues.py, not just the Premier League -- state is cached per league.
"""
import json
import os
import pickle
import re
import sys
import unicodedata

import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(BASE_DIR, "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import predict_pl  # noqa: E402
from leagues import LEAGUES  # noqa: E402

from fotmob_lineup import get_live_matchday_info  # noqa: E402

_cache = {}  # league_key -> state dict

# Confirmed lineups aren't published until ~1hr before kickoff, so for
# fixtures this far out FotMob never has anything but "not found" -- the
# live lookup was only adding network latency (and a source of hangs/
# timeouts) for zero benefit. Set this True once fixtures are close enough
# that live lineup data is actually likely to exist.
USE_LIVE_LINEUP = False


def _normalize_person(name):
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", name.lower())


def _load_state(league_key):
    if league_key in _cache:
        return _cache[league_key]

    predict_pl.configure_league(league_key, base_dir=BASE_DIR)
    cfg = LEAGUES[league_key]

    models, feature_cols, result_label_map = predict_pl.load_stacked_models()
    latest_form = pd.read_csv(f"{predict_pl.DATA_DIR}/latest_team_form.csv")
    with open(f"{predict_pl.DATA_DIR}/h2h_history.pkl", "rb") as f:
        h2h_history = pickle.load(f)

    players_dir = os.path.join(predict_pl.DATA_DIR, "players")
    current_season_players = None
    if os.path.isdir(players_dir):
        candidates = sorted(
            f for f in os.listdir(players_dir)
            if f.startswith("players_") and f.endswith(".json")
        )
        if candidates:
            current_season_players = os.path.join(players_dir, candidates[-1])
    players_by_team = {}
    if current_season_players and os.path.exists(current_season_players):
        with open(current_season_players, encoding="utf-8") as f:
            players = json.load(f)
        for p in players:
            team = cfg.get("name_map", {}).get(p.get("team_title"), p.get("team_title"))
            if not team:
                continue
            if p.get("source") == "asa":
                contrib = max(
                    float(p.get("goals_added") or 0) + 0.5 * float(p.get("xG_chain") or 0),
                    0.0,
                )
            else:
                contrib = max(
                    float(p.get("goals") or 0) + float(p.get("assists") or 0) + float(p.get("xG") or 0),
                    0.0,
                )
            pname = p.get("player_name") or p.get("player") or ""
            players_by_team.setdefault(team, []).append({
                "name": pname, "norm": _normalize_person(pname),
                "contrib": contrib,
            })

    state = {
        "models": models, "feature_cols": feature_cols, "result_label_map": result_label_map,
        "latest_form": latest_form, "h2h_history": h2h_history, "players_by_team": players_by_team,
        "data_dir": predict_pl.DATA_DIR, "model_dir": predict_pl.MODEL_DIR,
    }
    _cache[league_key] = state
    return state


def _squad_adjustment(state, team, unavailable_names):
    """Scales SquadQuality down by the fraction of the team's
    (goals+assists+xG) production that's currently out injured/suspended,
    floored at 0.5 so a single absence can't swing the prediction wildly."""
    roster = state["players_by_team"].get(team, [])
    if not roster or not unavailable_names:
        return 1.0, []

    total = sum(p["contrib"] for p in roster) or 1.0
    missing_norm = {_normalize_person(n) for n in unavailable_names}
    matched = [p for p in roster if p["norm"] in missing_norm]
    missing_total = sum(p["contrib"] for p in matched)
    factor = max(0.5, (total - missing_total) / total)
    return factor, [p["name"] for p in matched]


def predict_fixture_live(home, away, match_date, league_key="premier_league"):
    """match_date: 'YYYY-MM-DD'. Returns a dict shaped like one row of
    predict_pl.predict_all()'s output, plus lineup_status/adjustment info.

    lineup_status:
      "confirmed"  -- FotMob has published the starting XI for this match
      "provisional" -- FotMob has the match but only injury/unavailable
                       data so far, no confirmed XI yet (typical outside
                       the ~1hr pre-kickoff window)
      "unavailable" -- FotMob doesn't have this fixture yet (too far out);
                        prediction falls back to each team's full current
                        squad -- i.e. the "predicted" (undepleted) lineup
      "not_checked" -- live lookup is currently disabled (USE_LIVE_LINEUP),
                       since fixtures this far out never have lineup data
                       yet anyway; using last-known squad form instead
    """
    state = _load_state(league_key)
    feature_cols = state["feature_cols"]
    result_label_map = state["result_label_map"]
    inv_result_map = {v: k for k, v in result_label_map.items()}

    # Loading models mutates predict_pl's module globals -- re-point them
    # at this league in case another league's lookup ran in between.
    predict_pl.DATA_DIR = state["data_dir"]
    predict_pl.MODEL_DIR = state["model_dir"]

    home_row = predict_pl.get_team_row(state["latest_form"], home)
    away_row = predict_pl.get_team_row(state["latest_form"], away)

    lineup_info = get_live_matchday_info(home, away, match_date) if USE_LIVE_LINEUP else None

    lineup_status = "not_checked" if not USE_LIVE_LINEUP else "unavailable"
    adjustments = {"home": {"factor": 1.0, "missing": []}, "away": {"factor": 1.0, "missing": []}}

    if lineup_info:
        home_unavail = [p["name"] for p in lineup_info["home"].get("unavailable", [])]
        away_unavail = [p["name"] for p in lineup_info["away"].get("unavailable", [])]
        home_factor, home_matched = _squad_adjustment(state, home, home_unavail)
        away_factor, away_matched = _squad_adjustment(state, away, away_unavail)
        adjustments["home"] = {"factor": round(home_factor, 3), "missing": home_matched}
        adjustments["away"] = {"factor": round(away_factor, 3), "missing": away_matched}

        home_row = {**home_row, "SquadQuality": home_row.get("SquadQuality", 0.0) * home_factor}
        away_row = {**away_row, "SquadQuality": away_row.get("SquadQuality", 0.0) * away_factor}

        confirmed = bool(lineup_info["home"].get("confirmed") or lineup_info["away"].get("confirmed"))
        lineup_status = "confirmed" if confirmed else "provisional"

    X = predict_pl.build_feature_row(home_row, away_row, feature_cols, state["h2h_history"], home, away)
    models = state["models"]

    base_models, meta, best_strategy = models["result"]
    proba = predict_pl.best_classify(base_models, meta, best_strategy, X)
    result_proba = {inv_result_map[i]: proba[0][i] for i in range(proba.shape[1])}
    predicted_result_code = max(result_proba, key=result_proba.get)
    predicted_result = {"H": home, "D": "Draw", "A": away}[predicted_result_code]

    base_models, meta, best_strategy = models["btts"]
    p_btts = predict_pl.best_classify(base_models, meta, best_strategy, X)[0][1]

    base_models, meta, best_strategy = models["over25"]
    p_over25 = predict_pl.best_classify(base_models, meta, best_strategy, X)[0][1]

    # Corner markets are skipped at train time for leagues without corner
    # stats (MLS via ASA). load_stacked_models() returns None for those;
    # match predict_all()'s empty-string fallback so live predict doesn't
    # unpack None and 500 the fixture card.
    if models["home_more_corners"] is None:
        p_home_more_corners = None
        more_corners_pred = ""
    else:
        base_models, meta, best_strategy = models["home_more_corners"]
        p_home_more_corners = predict_pl.best_classify(base_models, meta, best_strategy, X)[0][1]
        more_corners_pred = home if p_home_more_corners >= 0.5 else away

    home_is_favorite = float(X["Elo_Diff"].iloc[0]) >= 0
    favorite_team = home if home_is_favorite else away
    base_models, meta, best_strategy = models["favorite_avoids_defeat"]
    p_favorite_avoids_defeat = predict_pl.best_classify(base_models, meta, best_strategy, X)[0][1]

    if models["total_corners"] is None:
        exp_total_corners = None
    else:
        base_models, meta, best_strategy = models["total_corners"]
        exp_total_corners = predict_pl.best_regress(base_models, meta, best_strategy, X)[0]

    return {
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
            "" if exp_total_corners is None else round(float(exp_total_corners), 2)
        ),
        "P_Home_More_Corners": (
            "" if p_home_more_corners is None else round(float(p_home_more_corners), 3)
        ),
        "More_Corners_Prediction": more_corners_pred,
        "Favorite_Team": favorite_team,
        "P_Favorite_Avoids_Defeat": round(float(p_favorite_avoids_defeat), 3),
        "Favorite_Avoids_Defeat_Prediction": "Yes" if p_favorite_avoids_defeat >= 0.5 else "No",
        "Lineup_Status": lineup_status,
        "Home_Adjustment": adjustments["home"],
        "Away_Adjustment": adjustments["away"],
    }
