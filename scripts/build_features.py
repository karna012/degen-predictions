"""
Builds leakage-free, pre-match rolling features per team from 7 seasons of
match data for a given league, plus a per-team squad-quality score from
Understat player data. Works for any league configured in leagues.py, not
just the Premier League. Produces, under that league's data_dir:
  - train_rows.csv       (one row per historical match, with pre-match
    rolling features for both teams + targets)
  - latest_team_form.csv (each team's most recent rolling form, used to
    featurize upcoming fixtures)
  - h2h_history.pkl
"""
import json
import os
import sys

import numpy as np
import pandas as pd

from leagues import LEAGUES, UNDERSTAT_SEASON_MAP, league_seasons, resolve_league_paths

ROLL_WINDOW = 5

_MATCH_STAT_COLS = ["HS", "AS", "HST", "AST", "HC", "AC", "HY", "AY", "HR", "AR", "HF", "AF"]


def load_matches(cfg):
    data_dir = cfg["data_dir"]
    fd_code = cfg["fd_code"]
    frames = []
    for s in league_seasons(cfg):
        path = f"{data_dir}/{fd_code}_{s}.csv"
        if not os.path.exists(path):
            continue
        df = pd.read_csv(path)
        df["Season"] = s
        for col in _MATCH_STAT_COLS:
            if col not in df.columns:
                df[col] = np.nan
        cols = ["Season", "Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR"] + _MATCH_STAT_COLS
        if "Referee" in df.columns:
            cols.append("Referee")
        frames.append(df[cols])
    if not frames:
        raise FileNotFoundError(f"No match CSVs found under {data_dir} for {fd_code}")
    matches = pd.concat(frames, ignore_index=True)
    # Mean-fill fouls when some rows have them; leave all-NaN (MLS) as NaN
    # for the train-time imputer rather than inventing a fake constant.
    if matches["HF"].notna().any():
        matches["HF"] = matches["HF"].fillna(matches["HF"].mean())
    if matches["AF"].notna().any():
        matches["AF"] = matches["AF"].fillna(matches["AF"].mean())
    if "Referee" not in matches.columns:
        matches["Referee"] = "Unknown"
    matches["Referee"] = matches["Referee"].fillna("Unknown")
    matches["Date"] = pd.to_datetime(matches["Date"], dayfirst=True)
    matches = matches.sort_values("Date").reset_index(drop=True)
    matches["MatchID"] = matches.index
    return matches


def _player_season_files(cfg):
    """Yield (match_season_code, players_json_path) for squad-quality lookup."""
    players_dir = os.path.join(cfg["data_dir"], "players")
    if cfg.get("understat_league"):
        for season_code, u_season in UNDERSTAT_SEASON_MAP.items():
            yield season_code, os.path.join(players_dir, f"players_{u_season}.json")
    elif cfg.get("asa_league"):
        # ASA / MLS use calendar-year season codes that match the match CSVs.
        for season in league_seasons(cfg):
            yield season, os.path.join(players_dir, f"players_{season}.json")


def squad_quality_by_season(cfg):
    """Per (season_code, team) -> normalized attacking-quality score.

    Understat (EPL etc.) or ASA (MLS) player files both expose goals/assists/xG
    under the same keys after download_data.py normalization.
    """
    if not (cfg.get("understat_league") or cfg.get("asa_league")):
        return {}
    name_map = cfg.get("name_map", {})
    raw = {}
    for season_code, path in _player_season_files(cfg):
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            players = json.load(f)
        team_scores = {}
        for p in players:
            team = name_map.get(p["team_title"], p["team_title"])
            # Prefer goals-added when ASA provides it; else classic xG blend.
            if p.get("goals_added") is not None and p.get("source") == "asa":
                contrib = float(p["goals_added"]) + 0.5 * float(p.get("xG_chain") or 0)
            else:
                contrib = float(p["goals"]) + float(p["assists"]) + float(p["xG"])
            team_scores[team] = team_scores.get(team, 0.0) + contrib
        vals = np.array(list(team_scores.values()))
        if len(vals) == 0:
            continue
        mean, std = vals.mean(), (vals.std() or 1.0)
        for team, val in team_scores.items():
            raw[(season_code, team)] = (val - mean) / std
    return raw


# Off-target shots count as half an on-target shot for ShotQuality features.
OFF_TARGET_WEIGHT = 0.5


def long_format(matches):
    """One row per team per match (both home and away appearances)."""
    home = matches.rename(columns={
        "HomeTeam": "Team", "AwayTeam": "Opponent",
        "FTHG": "GF", "FTAG": "GA", "HS": "Shots", "AS": "ShotsAgainst",
        "HST": "ShotsOT", "AST": "ShotsOTAgainst", "HC": "Corners", "AC": "CornersAgainst",
        "HY": "Yellow", "HR": "Red", "HF": "Fouls", "AF": "FoulsAgainst",
    }).copy()
    home["Venue"] = "H"
    home["Points"] = home["FTR"].map({"H": 3, "D": 1, "A": 0})

    away = matches.rename(columns={
        "AwayTeam": "Team", "HomeTeam": "Opponent",
        "FTAG": "GF", "FTHG": "GA", "AS": "Shots", "HS": "ShotsAgainst",
        "AST": "ShotsOT", "HST": "ShotsOTAgainst", "AC": "Corners", "HC": "CornersAgainst",
        "AY": "Yellow", "AR": "Red", "AF": "Fouls", "HF": "FoulsAgainst",
    }).copy()
    away["Venue"] = "A"
    away["Points"] = away["FTR"].map({"A": 3, "D": 1, "H": 0})

    cols = ["MatchID", "Season", "Date", "Team", "Opponent", "Venue", "Points",
            "GF", "GA", "Shots", "ShotsAgainst", "ShotsOT", "ShotsOTAgainst",
            "Corners", "CornersAgainst", "Yellow", "Red", "Fouls", "FoulsAgainst"]
    long_df = pd.concat([home[cols], away[cols]], ignore_index=True)

    long_df["ShotQuality"] = long_df["ShotsOT"] + OFF_TARGET_WEIGHT * (long_df["Shots"] - long_df["ShotsOT"])
    long_df["ShotQualityAgainst"] = long_df["ShotsOTAgainst"] + \
        OFF_TARGET_WEIGHT * (long_df["ShotsAgainst"] - long_df["ShotsOTAgainst"])

    return long_df.sort_values(["Team", "Date"]).reset_index(drop=True)


SHORT_WINDOW = 3
FEAT_COLS = ["Points", "GF", "GA", "Shots", "ShotsAgainst", "ShotsOT",
             "ShotsOTAgainst", "Corners", "CornersAgainst", "Yellow", "Red",
             "Fouls", "FoulsAgainst", "ShotQuality", "ShotQualityAgainst"]


def add_rolling_features(long_df):
    grouped = long_df.groupby("Team", group_keys=False)
    for col in FEAT_COLS:
        # shift(1) -> only uses matches strictly before the current one (no leakage)
        long_df[f"{col}_avg{ROLL_WINDOW}"] = grouped[col].transform(
            lambda s: s.shift(1).rolling(ROLL_WINDOW, min_periods=2).mean()
        )
        long_df[f"{col}_avg{SHORT_WINDOW}"] = grouped[col].transform(
            lambda s: s.shift(1).rolling(SHORT_WINDOW, min_periods=1).mean()
        )
        # season-to-date form (all prior matches, more stable early in a season)
        long_df[f"{col}_season_avg"] = grouped[col].transform(
            lambda s: s.shift(1).expanding(min_periods=1).mean()
        )
    long_df["GamesPlayed"] = grouped.cumcount()
    return long_df


def add_elo_ratings(matches, k=20, home_advantage=60, initial=1500):
    """Standard football Elo with goal-difference-scaled K and home-field edge.
    Computed strictly chronologically; only pre-match ratings are stored as
    features, so there is no leakage."""
    elo = {}

    def get(team):
        return elo.get(team, initial)

    home_elo_pre, away_elo_pre = [], []
    for _, row in matches.iterrows():
        h, a = row["HomeTeam"], row["AwayTeam"]
        eh, ea = get(h), get(a)
        home_elo_pre.append(eh)
        away_elo_pre.append(ea)

        exp_home = 1 / (1 + 10 ** (-((eh + home_advantage) - ea) / 400))
        if row["FTR"] == "H":
            actual_home, margin = 1.0, row["FTHG"] - row["FTAG"]
        elif row["FTR"] == "A":
            actual_home, margin = 0.0, row["FTHG"] - row["FTAG"]
        else:
            actual_home, margin = 0.5, 0

        g = 1 if abs(margin) <= 1 else (1.5 if abs(margin) == 2 else (1.75 + (abs(margin) - 3) / 8))
        delta = k * g * (actual_home - exp_home)
        elo[h] = eh + delta
        elo[a] = ea - delta

    matches = matches.copy()
    matches["Home_Elo_Pre"] = home_elo_pre
    matches["Away_Elo_Pre"] = away_elo_pre
    return matches, elo


def add_team_home_advantage(matches, min_games=3):
    """Some teams are genuinely a different animal at home than away (a
    fortress-like home record vs. a middling away one, or vice versa) --
    this captures that as an explicit per-team differential (home PPG minus
    away PPG), rather than leaving the model to infer it indirectly from
    separate home/away rolling stats. Computed strictly chronologically:
    only a team's matches strictly before the current one are used, so
    there's no leakage. Applied to both sides -- the home team's own
    home-boost, and the away team's own home-boost (relevant because a team
    that's much stronger at home than away is proportionally more exposed
    playing this match on the road)."""
    home_pts, home_games = {}, {}
    away_pts, away_games = {}, {}
    league_home_ppg_total, league_away_ppg_total, league_games = 0.0, 0.0, 0

    def ppg(pts, games, default):
        return pts / games if games >= min_games else default

    home_adv_for_home_team, home_adv_for_away_team = [], []
    for _, row in matches.iterrows():
        h, a = row["HomeTeam"], row["AwayTeam"]
        league_default_diff = (
            (league_home_ppg_total - league_away_ppg_total) / league_games if league_games else 0.35
        )

        h_home_ppg = ppg(home_pts.get(h, 0.0), home_games.get(h, 0), None)
        h_away_ppg = ppg(away_pts.get(h, 0.0), away_games.get(h, 0), None)
        home_adv_for_home_team.append(
            h_home_ppg - h_away_ppg if h_home_ppg is not None and h_away_ppg is not None else league_default_diff)

        a_home_ppg = ppg(home_pts.get(a, 0.0), home_games.get(a, 0), None)
        a_away_ppg = ppg(away_pts.get(a, 0.0), away_games.get(a, 0), None)
        home_adv_for_away_team.append(
            a_home_ppg - a_away_ppg if a_home_ppg is not None and a_away_ppg is not None else league_default_diff)

        home_result_pts = 3 if row["FTR"] == "H" else (1 if row["FTR"] == "D" else 0)
        away_result_pts = 3 if row["FTR"] == "A" else (1 if row["FTR"] == "D" else 0)
        home_pts[h] = home_pts.get(h, 0.0) + home_result_pts
        home_games[h] = home_games.get(h, 0) + 1
        away_pts[a] = away_pts.get(a, 0.0) + away_result_pts
        away_games[a] = away_games.get(a, 0) + 1
        league_home_ppg_total += home_result_pts
        league_away_ppg_total += away_result_pts
        league_games += 1

    matches = matches.copy()
    matches["Home_TeamHomeAdvantage"] = home_adv_for_home_team
    matches["Away_TeamHomeAdvantage"] = home_adv_for_away_team

    final = {
        "home_pts": home_pts, "home_games": home_games,
        "away_pts": away_pts, "away_games": away_games,
    }
    return matches, final


def h2h_key(team_a, team_b):
    return frozenset((team_a, team_b))


def h2h_features_from_history(history, home, away):
    """history: dict[frozenset({teamA,teamB})] -> list of prior meetings
    ({'home','away','fthg','ftag'}), all strictly before the query match."""
    prior = history.get(h2h_key(home, away), [])
    n = len(prior)
    if n == 0:
        return {"H2H_Meetings": 0, "H2H_HomeWinRate": 0.45,
                "H2H_AvgGoalsForHome": 1.4, "H2H_AvgGoalsForAway": 1.2}

    home_goals, away_goals, home_wins = [], [], 0
    for enc in prior:
        if enc["home"] == home:
            hg, ag = enc["fthg"], enc["ftag"]
        else:
            hg, ag = enc["ftag"], enc["fthg"]
        home_goals.append(hg)
        away_goals.append(ag)
        if hg > ag:
            home_wins += 1

    return {
        "H2H_Meetings": n,
        "H2H_HomeWinRate": home_wins / n,
        "H2H_AvgGoalsForHome": float(np.mean(home_goals)),
        "H2H_AvgGoalsForAway": float(np.mean(away_goals)),
    }


def add_h2h_features(matches):
    """Head-to-head history between the two specific teams (any venue),
    computed strictly chronologically -- only meetings before the current
    match are visible, so there is no leakage."""
    history = {}
    rows = []
    for _, row in matches.iterrows():
        h, a = row["HomeTeam"], row["AwayTeam"]
        rows.append(h2h_features_from_history(history, h, a))
        key = h2h_key(h, a)
        history.setdefault(key, []).append(
            {"home": h, "away": a, "fthg": row["FTHG"], "ftag": row["FTAG"]})

    matches = matches.copy()
    h2h_df = pd.DataFrame(rows)
    for col in h2h_df.columns:
        matches[col] = h2h_df[col].values
    return matches, history


def add_referee_features(matches, min_games=3, league_avg_cards=3.6, league_avg_goals=2.7):
    """A referee's card-strictness and game-openness tendencies, computed
    strictly chronologically from that referee's prior PL matches only."""
    ref_cards, ref_goals, ref_games = {}, {}, {}
    avg_cards_col, avg_goals_col, games_col = [], [], []

    for _, row in matches.iterrows():
        ref = row["Referee"]
        n = ref_games.get(ref, 0)
        if n >= min_games:
            avg_cards_col.append(ref_cards[ref] / n)
            avg_goals_col.append(ref_goals[ref] / n)
        else:
            avg_cards_col.append(league_avg_cards)
            avg_goals_col.append(league_avg_goals)
        games_col.append(n)

        cards = row["HY"] + row["AY"] + row["HR"] + row["AR"]
        ref_cards[ref] = ref_cards.get(ref, 0.0) + cards
        ref_goals[ref] = ref_goals.get(ref, 0.0) + row["FTHG"] + row["FTAG"]
        ref_games[ref] = n + 1

    matches = matches.copy()
    matches["Referee_AvgCards"] = avg_cards_col
    matches["Referee_AvgGoals"] = avg_goals_col
    matches["Referee_GamesSeen"] = games_col
    return matches


def add_dixon_coles_strengths(matches):
    """Classical Dixon-Coles-style attack/defense strength ratios: a team's
    goals-for (or against) rate at a given venue, relative to the league
    average for that venue -- a multiplicative signal, distinct from the
    additive rolling-mean features above. Computed with running, expanding
    (shift-equivalent) totals so only strictly-prior matches are used."""
    home_goals_for, home_goals_against, home_games = {}, {}, {}
    away_goals_for, away_goals_against, away_games = {}, {}, {}
    league_home_goals_total, league_away_goals_total, league_games = 0.0, 0.0, 0

    def ratio(num, den, default=1.0):
        return num / den if den > 0 else default

    h_att, h_def, a_att, a_def = [], [], [], []
    for _, row in matches.iterrows():
        h, a = row["HomeTeam"], row["AwayTeam"]
        # floor guards early-season league averages from collapsing to exactly
        # 0 (e.g. a 0-0 in the season's first match), which would otherwise
        # divide-by-zero on the very next iteration
        league_avg_home = max(ratio(league_home_goals_total, league_games, 1.4), 0.1)
        league_avg_away = max(ratio(league_away_goals_total, league_games, 1.2), 0.1)

        h_att.append(ratio(home_goals_for.get(h, 0.0), home_games.get(h, 0), league_avg_home) / league_avg_home)
        h_def.append(ratio(home_goals_against.get(h, 0.0), home_games.get(h, 0), league_avg_away) / league_avg_away)
        a_att.append(ratio(away_goals_for.get(a, 0.0), away_games.get(a, 0), league_avg_away) / league_avg_away)
        a_def.append(ratio(away_goals_against.get(a, 0.0), away_games.get(a, 0), league_avg_home) / league_avg_home)

        home_goals_for[h] = home_goals_for.get(h, 0.0) + row["FTHG"]
        home_goals_against[h] = home_goals_against.get(h, 0.0) + row["FTAG"]
        home_games[h] = home_games.get(h, 0) + 1
        away_goals_for[a] = away_goals_for.get(a, 0.0) + row["FTAG"]
        away_goals_against[a] = away_goals_against.get(a, 0.0) + row["FTHG"]
        away_games[a] = away_games.get(a, 0) + 1
        league_home_goals_total += row["FTHG"]
        league_away_goals_total += row["FTAG"]
        league_games += 1

    matches = matches.copy()
    matches["Home_AttackStrength"] = h_att
    matches["Home_DefenseStrength"] = h_def
    matches["Away_AttackStrength"] = a_att
    matches["Away_DefenseStrength"] = a_def

    league_avg_home = max(ratio(league_home_goals_total, league_games, 1.4), 0.1)
    league_avg_away = max(ratio(league_away_goals_total, league_games, 1.2), 0.1)
    final_strengths = {
        "home_goals_for": home_goals_for, "home_goals_against": home_goals_against, "home_games": home_games,
        "away_goals_for": away_goals_for, "away_goals_against": away_goals_against, "away_games": away_games,
        "league_avg_home": league_avg_home, "league_avg_away": league_avg_away,
    }
    return matches, final_strengths


def build_training_rows(matches_with_elo, long_df, quality):
    roll_cols = [c for c in long_df.columns
                 if c.endswith(f"_avg{ROLL_WINDOW}") or c.endswith(f"_avg{SHORT_WINDOW}")
                 or c.endswith("_season_avg")]
    home_feats = long_df[long_df["Venue"] == "H"][["MatchID", "Team"] + roll_cols + ["GamesPlayed", "Season"]]
    away_feats = long_df[long_df["Venue"] == "A"][["MatchID", "Team"] + roll_cols + ["GamesPlayed", "Season"]]

    home_feats = home_feats.rename(columns={c: f"Home_{c}" for c in roll_cols})
    home_feats = home_feats.rename(columns={"GamesPlayed": "Home_GamesPlayed"})
    away_feats = away_feats.rename(columns={c: f"Away_{c}" for c in roll_cols})
    away_feats = away_feats.rename(columns={"GamesPlayed": "Away_GamesPlayed"})

    df = matches_with_elo.merge(home_feats.drop(columns=["Team"]), on="MatchID")
    df = df.merge(away_feats.drop(columns=["Team"]), on="MatchID", suffixes=("", "_awaydrop"))

    df["Home_SquadQuality"] = df.apply(
        lambda r: quality.get((r["Season"], r["HomeTeam"]), 0.0), axis=1)
    df["Away_SquadQuality"] = df.apply(
        lambda r: quality.get((r["Season"], r["AwayTeam"]), 0.0), axis=1)
    df["Elo_Diff"] = df["Home_Elo_Pre"] - df["Away_Elo_Pre"]

    # Only require 1 prior match so we keep early-season rows; genuine NaNs
    # (a team's very first match) are handled by imputation at train time,
    # not by dropping data.
    df = df[(df["Home_GamesPlayed"] >= 1) & (df["Away_GamesPlayed"] >= 1)].reset_index(drop=True)

    # targets
    df["Target_Result"] = df["FTR"]  # H / D / A
    df["Target_BTTS"] = ((df["FTHG"] > 0) & (df["FTAG"] > 0)).astype(int)
    df["Target_Over25"] = ((df["FTHG"] + df["FTAG"]) > 2.5).astype(int)
    # Corner targets are NaN when match files lack HC/AC (MLS /new/ format).
    has_corners = df["HC"].notna() & df["AC"].notna()
    df["Target_TotalCorners"] = np.where(has_corners, df["HC"] + df["AC"], np.nan)
    df["Target_HomeMoreCorners"] = np.where(
        has_corners, (df["HC"] > df["AC"]).astype(float), np.nan
    )

    # Higher-base-rate reframing: does the pre-match favourite (by Elo, a
    # feature already known before kickoff) avoid losing outright? Favourites
    # in the PL lose outright only ~25-30% of the time, so this target is
    # genuinely easier than picking the exact 1X2 result -- and its accuracy
    # numbers are honest precisely because the target itself is different,
    # not because of any modeling trick.
    df["Home_Is_Favorite"] = df["Elo_Diff"] >= 0
    df["Target_FavoriteAvoidsDefeat"] = np.where(
        df["Home_Is_Favorite"], df["FTR"].isin(["H", "D"]), df["FTR"].isin(["A", "D"])
    ).astype(int)

    return df


def build_latest_form(long_df, quality, final_elo, final_strengths, final_home_adv):
    """Most recent rolling stats per team (post most-recent match) for
    featurizing upcoming fixtures."""

    def ratio(num, den, default=1.0):
        return num / den if den > 0 else default

    latest_rows = []
    for team, g in long_df.groupby("Team"):
        g = g.sort_values("Date")
        row = {"Team": team, "GamesPlayed": len(g), "Season": g["Season"].iloc[-1]}
        for col in FEAT_COLS:
            row[f"{col}_avg{ROLL_WINDOW}"] = g[col].tail(ROLL_WINDOW).mean()
            row[f"{col}_avg{SHORT_WINDOW}"] = g[col].tail(SHORT_WINDOW).mean()
            row[f"{col}_season_avg"] = g[g["Season"] == g["Season"].iloc[-1]][col].mean()

        lah, laa = final_strengths["league_avg_home"], final_strengths["league_avg_away"]
        row["HomeAttackStrength"] = ratio(
            final_strengths["home_goals_for"].get(team, 0.0),
            final_strengths["home_games"].get(team, 0), lah) / lah
        row["HomeDefenseStrength"] = ratio(
            final_strengths["home_goals_against"].get(team, 0.0),
            final_strengths["home_games"].get(team, 0), laa) / laa
        row["AwayAttackStrength"] = ratio(
            final_strengths["away_goals_for"].get(team, 0.0),
            final_strengths["away_games"].get(team, 0), laa) / laa
        row["AwayDefenseStrength"] = ratio(
            final_strengths["away_goals_against"].get(team, 0.0),
            final_strengths["away_games"].get(team, 0), lah) / lah

        hp, hg = final_home_adv["home_pts"].get(team, 0.0), final_home_adv["home_games"].get(team, 0)
        ap, ag = final_home_adv["away_pts"].get(team, 0.0), final_home_adv["away_games"].get(team, 0)
        row["TeamHomeAdvantage"] = (hp / hg if hg >= 3 else 1.5) - (ap / ag if ag >= 3 else 1.15)

        latest_rows.append(row)
    latest = pd.DataFrame(latest_rows)
    latest["SquadQuality"] = latest.apply(
        lambda r: quality.get((r["Season"], r["Team"]), 0.0), axis=1)
    latest["Elo"] = latest["Team"].map(lambda t: final_elo.get(t, 1500))
    return latest


def build_for_league(league_key):
    import pickle

    cfg = resolve_league_paths(LEAGUES[league_key])
    data_dir = cfg["data_dir"]
    print(f"=== {cfg['label']} ===")

    matches = load_matches(cfg)
    quality = squad_quality_by_season(cfg)
    long_df = long_format(matches)
    long_df = add_rolling_features(long_df)
    matches_with_elo, final_elo = add_elo_ratings(matches)
    matches_with_elo, final_strengths = add_dixon_coles_strengths(matches_with_elo)
    matches_with_elo, final_home_adv = add_team_home_advantage(matches_with_elo)
    matches_with_elo, h2h_history = add_h2h_features(matches_with_elo)
    matches_with_elo = add_referee_features(matches_with_elo)

    train_rows = build_training_rows(matches_with_elo, long_df, quality)
    train_rows.to_csv(f"{data_dir}/train_rows.csv", index=False)
    print(f"train_rows: {train_rows.shape}")

    latest_form = build_latest_form(long_df, quality, final_elo, final_strengths, final_home_adv)
    latest_form.to_csv(f"{data_dir}/latest_team_form.csv", index=False)
    print(f"latest_team_form: {latest_form.shape}")

    with open(f"{data_dir}/h2h_history.pkl", "wb") as f:
        pickle.dump(h2h_history, f)
    print(f"h2h_history: {len(h2h_history)} team pairs")


def main():
    keys = sys.argv[1:] or list(LEAGUES.keys())
    for key in keys:
        build_for_league(key)


if __name__ == "__main__":
    main()
