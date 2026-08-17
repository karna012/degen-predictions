"""Builds a leakage-free, per-match training dataset for cricket (The
Hundred + MLC + IPL + BBL + CPL): one row per match, every feature computed
strictly from information available *before* that match's first ball
(chronological walk-forward, mirroring scripts/mlb/build_training_dataset.py),
with three targets: Team1Runs / Team2Runs (each side's innings total),
TotalRuns (both innings combined), and Winner (1 if Team1 won).

"Team1"/"Team2" = the side that batted first / second in that match (known
at the toss, before any ball is bowled -- not a leakage risk).

Feature groups (per the task spec):
  1. Playing XI: team-level aggregates over the actual named XI (info.players)
  2. Venue: historical avg 1st/2nd-innings totals at that specific ground
  3. Venue pitch type: data-driven runs/over + wickets/over percentile, not hardcoded
  4. Team form: rolling last-N-match win rate + run rate (not season-long)
  5. Player form: rolling last-N-innings batting SR / bowling economy, averaged over the XI
  6. Batter-vs-bowler H2H: aggregated to a team-level "matchup edge" with
     sample-size-weighted backoff (pair history -> batter's own recent SR -> league average)
  7. Per-over-phase splits: each player's death-overs SR vs their overall SR,
     averaged over the XI into a team-level "acceleration" feature
  8. Sentiment: webapp.sentiment.get_team_sentiment (same module as MLB/NPB)
  9. Missing data: explicit, visible handling + a per-row data_confidence
     score (mirrors investment_scoring/src/missing_values.py's philosophy)

Run from repo root: python scripts/cricket/build_training_dataset.py
Outputs data/cricket/training_dataset.csv
"""
import json
import os
import sys
from collections import deque

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cricket_config import (  # noqa: E402
    COMPETITIONS, DATA_DIR, ELO_HOME_ADVANTAGE, ELO_INITIAL, ELO_K,
    MIN_H2H_BALLS, MIN_PLAYER_INNINGS, MIN_VENUE_MATCHES,
    PLAYER_FORM_INNINGS, ROLLING_FORM_MATCHES,
    canonical_team, team_is_home_at_venue,
)
from cricket_data import innings_summary, iter_matches, match_result  # noqa: E402

# Bulk rebuild skips live Google News fetches (async cold-miss returns None
# anyway and would just spam the network). Live fixture pages still blend
# sentiment via webapp.sentiment at request time.
get_team_sentiment = None

LEAGUE_AVG_SR = 120.0     # runs per 100 balls, rough T20 league-average batting strike rate seed
LEAGUE_AVG_ECON = 8.0     # runs per over, rough T20 league-average bowling economy seed


def expected_score(elo_a, elo_b):
    return 1.0 / (1.0 + 10 ** (-(elo_a - elo_b) / 400.0))


def _mean_or_none(values):
    return sum(values) / len(values) if values else None


class RollingStore:
    """Per-player rolling batting/bowling history, updated after each
    innings they appear in. Overall + per-phase, so a team-level "death-
    overs acceleration" feature can be derived per XI."""

    def __init__(self):
        self.bat_runs = {}   # player -> deque of (runs, balls) per innings, maxlen=PLAYER_FORM_INNINGS
        self.bat_phase = {}  # player -> {"death": deque of (runs, balls), "overall": deque of (runs, balls)}
        self.bowl = {}       # player -> deque of (runs_conceded, balls) per innings

    def batter_sr(self, player):
        hist = self.bat_runs.get(player)
        if not hist:
            return None, 0
        runs = sum(r for r, b in hist)
        balls = sum(b for r, b in hist)
        if balls == 0:
            return None, len(hist)
        return 100.0 * runs / balls, len(hist)

    def batter_death_uplift(self, player):
        """Death-overs SR minus overall SR for this player, or None if too
        little death-overs history -- backoff to 0 (no known uplift) is
        applied by the caller, not here, so it stays explicit."""
        phases = self.bat_phase.get(player)
        if not phases:
            return None
        death_runs = sum(r for r, b in phases["death"])
        death_balls = sum(b for r, b in phases["death"])
        overall_sr, n = self.batter_sr(player)
        if death_balls < 6 or overall_sr is None:  # <6 balls of death-overs history is too thin to trust
            return None
        death_sr = 100.0 * death_runs / death_balls
        return death_sr - overall_sr

    def bowler_econ(self, player):
        hist = self.bowl.get(player)
        if not hist:
            return None, 0
        runs = sum(r for r, b in hist)
        balls = sum(b for r, b in hist)
        if balls == 0:
            return None, len(hist)
        return 6.0 * runs / balls, len(hist)

    def update_from_innings(self, summary):
        for player, b in summary["batting"].items():
            self.bat_runs.setdefault(player, deque(maxlen=PLAYER_FORM_INNINGS)).append((b["runs"], b["balls"]))
            phases = self.bat_phase.setdefault(player, {
                "death": deque(maxlen=PLAYER_FORM_INNINGS), "overall": deque(maxlen=PLAYER_FORM_INNINGS),
            })
            phases["death"].append((b["phase_runs"]["death"], b["phase_balls"]["death"]))
        for player, bw in summary["bowling"].items():
            self.bowl.setdefault(player, deque(maxlen=PLAYER_FORM_INNINGS)).append((bw["runs_conceded"], bw["balls"]))


def team_xi_features(store, xi):
    """Averages per-player rolling SR / economy / death-uplift across a
    playing XI into team-level features, tracking how many players had
    *any* rolling history (used for data_confidence -- an XI full of
    debutants/new signings is honestly thin data, not a modeling error)."""
    srs, sr_players_known = [], 0
    econs, econ_players_known = [], 0
    uplifts, uplift_players_known = [], 0
    for player in xi:
        sr, n = store.batter_sr(player)
        if sr is not None and n >= MIN_PLAYER_INNINGS:
            srs.append(sr)
            sr_players_known += 1
        econ, n2 = store.bowler_econ(player)
        if econ is not None and n2 >= MIN_PLAYER_INNINGS:
            econs.append(econ)
            econ_players_known += 1
        up = store.batter_death_uplift(player)
        if up is not None:
            uplifts.append(up)
            uplift_players_known += 1
    return {
        "avg_sr": _mean_or_none(srs) if srs else None,
        "sr_known_frac": sr_players_known / len(xi) if xi else 0,
        "avg_econ": _mean_or_none(econs) if econs else None,
        "econ_known_frac": econ_players_known / len(xi) if xi else 0,
        "death_uplift": _mean_or_none(uplifts) if uplifts else None,
        "uplift_known_frac": uplift_players_known / len(xi) if xi else 0,
    }


def pair_matchup_edge(pair_stats, batting_xi, bowling_xi, batter_overall_sr):
    """Team-level batter-vs-bowler matchup edge: for every (batter, bowler)
    combination across the two XIs, use pair-specific history if there are
    enough balls faced (MIN_H2H_BALLS), else back off to the batter's own
    recent overall SR (no bowling-style data is available from Cricsheet to
    interpolate through, so the backoff chain is pair -> batter-overall ->
    league average, weighted by pair sample size -- documented simplification,
    see module docstring item 6), then averages the edge (pair/backoff SR
    minus league-average SR) across all pairings weighted by sample size."""
    edges = []
    weights = []
    for batter in batting_xi:
        for bowler in bowling_xi:
            key = (batter, bowler)
            stats = pair_stats.get(key)
            if stats and stats["balls"] >= MIN_H2H_BALLS:
                pair_sr = 100.0 * stats["runs"] / stats["balls"]
                weight = min(stats["balls"], 30) / 30.0  # caps influence of a single deep pairing
                edges.append(pair_sr - LEAGUE_AVG_SR)
                weights.append(weight)
            else:
                fallback_sr = batter_overall_sr.get(batter)
                if fallback_sr is not None:
                    weight = 0.15  # thin/no pair history -> small fixed weight, mirrors mlb_predict's capped blends
                    edges.append(fallback_sr - LEAGUE_AVG_SR)
                    weights.append(weight)
                # else: truly no signal for this pairing (new player, no history at all) --
                # skipped entirely rather than assuming a fabricated 0 edge.
    if not edges:
        return None, 0
    total_w = sum(weights)
    if total_w == 0:
        return None, 0
    weighted = sum(e * w for e, w in zip(edges, weights)) / total_w
    return weighted, len(edges)


def build():
    matches = iter_matches()
    elo = {}
    recent_results = {}
    recent_run_rate = {}
    venue_first = {}
    venue_second = {}
    venue_rpo = {}
    venue_wpo = {}
    pair_stats = {}  # (batter, bowler) -> {"runs": int, "balls": int, "dismissals": int}
    store = RollingStore()
    last_xi = {}  # (competition, team) -> list of player names
    player_apps = {}  # (competition, team, player) -> appearances
    team_season_form = {}  # (competition, team) -> {wins, matches, runs_for, ...}

    rows = []
    missing_log = {"venue_thin": 0, "sr_thin": 0, "econ_thin": 0, "uplift_thin": 0, "sentiment_missing": 0,
                    "h2h_thin": 0}

    for match in matches:
        info = match.get("info", {})
        teams = info.get("teams", [])
        if len(teams) != 2:
            continue
        innings_list = match.get("innings", [])
        if len(innings_list) < 2:
            continue
        venue = info.get("venue", "Unknown")
        players_by_team = info.get("players", {})
        comp = match["_competition"]
        use_ha = bool(COMPETITIONS.get(comp, {}).get("home_advantage"))

        summaries = [innings_summary(inn) for inn in innings_list[:2]]
        team1 = canonical_team(summaries[0]["team"])
        team2 = canonical_team(summaries[1]["team"])
        # Map XI lists through original names in players_by_team
        xi1_raw = players_by_team.get(summaries[0]["team"], []) or players_by_team.get(team1, [])
        xi2_raw = players_by_team.get(summaries[1]["team"], []) or players_by_team.get(team2, [])
        if team1 not in [canonical_team(t) for t in teams] or team2 not in [canonical_team(t) for t in teams]:
            continue
        runs1, runs2 = summaries[0]["total_runs"], summaries[1]["total_runs"]
        xi1, xi2 = list(xi1_raw), list(xi2_raw)

        elo1, elo2 = elo.get(team1, ELO_INITIAL), elo.get(team2, ELO_INITIAL)
        ha1 = ha2 = 0.0
        if use_ha:
            if team_is_home_at_venue(team1, venue):
                ha1 = ELO_HOME_ADVANTAGE
            elif team_is_home_at_venue(team2, venue):
                ha2 = ELO_HOME_ADVANTAGE
        elo_diff = (elo1 + ha1) - (elo2 + ha2)

        # ---- venue features (history strictly before this match) ----
        v_first_hist = venue_first.get(venue, [])
        v_second_hist = venue_second.get(venue, [])
        v_rpo_hist = venue_rpo.get(venue, [])
        v_wpo_hist = venue_wpo.get(venue, [])
        venue_matches_seen = len(v_first_hist)
        if venue_matches_seen < MIN_VENUE_MATCHES:
            missing_log["venue_thin"] += 1
        venue_avg_first = _mean_or_none(v_first_hist)
        venue_avg_second = _mean_or_none(v_second_hist)
        venue_avg_rpo = _mean_or_none(v_rpo_hist)
        venue_avg_wpo = _mean_or_none(v_wpo_hist)

        # ---- team form (rolling, not season-long) ----
        t1_results = recent_results.get(team1, deque(maxlen=ROLLING_FORM_MATCHES))
        t2_results = recent_results.get(team2, deque(maxlen=ROLLING_FORM_MATCHES))
        t1_rr = recent_run_rate.get(team1, deque(maxlen=ROLLING_FORM_MATCHES))
        t2_rr = recent_run_rate.get(team2, deque(maxlen=ROLLING_FORM_MATCHES))

        # ---- player form aggregated over the named XI ----
        xi1_feat = team_xi_features(store, xi1)
        xi2_feat = team_xi_features(store, xi2)
        if xi1_feat["sr_known_frac"] < 0.5 or xi2_feat["sr_known_frac"] < 0.5:
            missing_log["sr_thin"] += 1
        if xi1_feat["econ_known_frac"] < 0.5 or xi2_feat["econ_known_frac"] < 0.5:
            missing_log["econ_thin"] += 1
        if xi1_feat["uplift_known_frac"] < 0.3 or xi2_feat["uplift_known_frac"] < 0.3:
            missing_log["uplift_thin"] += 1

        batter_overall_sr = {p: store.batter_sr(p)[0] for p in xi1 + xi2}
        edge_1_batting, n_pairs_1 = pair_matchup_edge(pair_stats, xi1, xi2, batter_overall_sr)
        edge_2_batting, n_pairs_2 = pair_matchup_edge(pair_stats, xi2, xi1, batter_overall_sr)
        if n_pairs_1 == 0 or n_pairs_2 == 0:
            missing_log["h2h_thin"] += 1

        # ---- sentiment left NaN at bulk build; live pages fill it ----
        sentiment_diff = None
        missing_log["sentiment_missing"] += 1

        winner_raw = match_result(match)
        winner = canonical_team(winner_raw) if winner_raw else None
        has_winner = winner in (team1, team2)

        thin_flags = [
            venue_matches_seen < MIN_VENUE_MATCHES,
            xi1_feat["sr_known_frac"] < 0.5 or xi2_feat["sr_known_frac"] < 0.5,
            xi1_feat["econ_known_frac"] < 0.5 or xi2_feat["econ_known_frac"] < 0.5,
            len(t1_results) < 3 or len(t2_results) < 3,
            n_pairs_1 == 0 or n_pairs_2 == 0,
        ]
        n_thin = sum(thin_flags)
        if n_thin == 0:
            confidence = "high"
        elif n_thin <= 2:
            confidence = "medium"
        else:
            confidence = "low"

        rows.append({
            "Date": match["_date"], "Competition": comp, "Venue": venue,
            "Team1": team1, "Team2": team2,
            "EloDiff": elo_diff,
            "HomeAdvantage": 1.0 if (ha1 or ha2) else 0.0,
            "Comp_ipl": 1.0 if comp == "ipl_male" else 0.0,
            "Comp_bbl": 1.0 if comp == "bbl_male" else 0.0,
            "Comp_hnd": 1.0 if comp == "hnd_male" else 0.0,
            "Comp_mlc": 1.0 if comp == "mlc_male" else 0.0,
            "Comp_cpl": 1.0 if comp == "cpl_male" else 0.0,
            "Team1RecentWinRate": (sum(t1_results) / len(t1_results)) if t1_results else 0.5,
            "Team2RecentWinRate": (sum(t2_results) / len(t2_results)) if t2_results else 0.5,
            "Team1RecentRunRate": _mean_or_none(list(t1_rr)) if t1_rr else np.nan,
            "Team2RecentRunRate": _mean_or_none(list(t2_rr)) if t2_rr else np.nan,
            "VenueAvgFirstInnings": venue_avg_first if venue_avg_first is not None else np.nan,
            "VenueAvgSecondInnings": venue_avg_second if venue_avg_second is not None else np.nan,
            "VenueAvgRunsPerOver": venue_avg_rpo if venue_avg_rpo is not None else np.nan,
            "VenueAvgWicketsPerOver": venue_avg_wpo if venue_avg_wpo is not None else np.nan,
            "VenueMatchesSeen": venue_matches_seen,
            "Team1AvgSR": xi1_feat["avg_sr"] if xi1_feat["avg_sr"] is not None else np.nan,
            "Team2AvgSR": xi2_feat["avg_sr"] if xi2_feat["avg_sr"] is not None else np.nan,
            "Team1AvgEcon": xi1_feat["avg_econ"] if xi1_feat["avg_econ"] is not None else np.nan,
            "Team2AvgEcon": xi2_feat["avg_econ"] if xi2_feat["avg_econ"] is not None else np.nan,
            "Team1DeathUplift": xi1_feat["death_uplift"] if xi1_feat["death_uplift"] is not None else np.nan,
            "Team2DeathUplift": xi2_feat["death_uplift"] if xi2_feat["death_uplift"] is not None else np.nan,
            "Team1MatchupEdge": edge_1_batting if edge_1_batting is not None else np.nan,
            "Team2MatchupEdge": edge_2_batting if edge_2_batting is not None else np.nan,
            "SentimentDiff": sentiment_diff if sentiment_diff is not None else np.nan,
            "DataConfidence": confidence,
            "Team1Runs": runs1, "Team2Runs": runs2, "TotalRuns": runs1 + runs2,
            "Winner": (1 if winner == team1 else 0) if has_winner else np.nan,
        })

        # ---- update rolling state AFTER recording this row's features ----
        if has_winner:
            elo1_new = elo1 + ELO_K * ((1.0 if winner == team1 else 0.0) - expected_score(elo1 + ha1, elo2 + ha2))
            elo2_new = elo2 + ELO_K * ((1.0 if winner == team2 else 0.0) - expected_score(elo2 + ha2, elo1 + ha1))
            elo[team1], elo[team2] = elo1_new, elo2_new
            recent_results.setdefault(team1, deque(maxlen=ROLLING_FORM_MATCHES)).append(1 if winner == team1 else 0)
            recent_results.setdefault(team2, deque(maxlen=ROLLING_FORM_MATCHES)).append(1 if winner == team2 else 0)

        overs1 = len(summaries[0]["overs_data"]) or 1
        overs2 = len(summaries[1]["overs_data"]) or 1
        recent_run_rate.setdefault(team1, deque(maxlen=ROLLING_FORM_MATCHES)).append(runs1 / overs1)
        recent_run_rate.setdefault(team2, deque(maxlen=ROLLING_FORM_MATCHES)).append(runs2 / overs2)

        venue_first.setdefault(venue, []).append(runs1)
        venue_second.setdefault(venue, []).append(runs2)
        for s in summaries:
            for over_idx, over_runs, over_wkts in s["overs_data"]:
                venue_rpo.setdefault(venue, []).append(over_runs)
                venue_wpo.setdefault(venue, []).append(over_wkts)

        for s in summaries:
            for (batter, bowler, bat_runs, dismissed) in s["pair_events"]:
                stats = pair_stats.setdefault((batter, bowler), {"runs": 0, "balls": 0, "dismissals": 0})
                stats["runs"] += bat_runs
                stats["balls"] += 1
                if dismissed:
                    stats["dismissals"] += 1
            store.update_from_innings(s)

        # Caches for live provisional XI / analysis panels
        last_xi[(comp, team1)] = xi1
        last_xi[(comp, team2)] = xi2
        for team, xi in ((team1, xi1), (team2, xi2)):
            for p in xi:
                key = (comp, team, p)
                player_apps[key] = player_apps.get(key, 0) + 1
            tf = team_season_form.setdefault((comp, team), {
                "wins": 0, "matches": 0, "runs_for": 0, "runs_against": 0,
            })
            tf["matches"] += 1
            if team == team1:
                tf["runs_for"] += runs1
                tf["runs_against"] += runs2
            else:
                tf["runs_for"] += runs2
                tf["runs_against"] += runs1
            if has_winner and winner == team:
                tf["wins"] += 1

    df = pd.DataFrame(rows)
    os.makedirs(DATA_DIR, exist_ok=True)
    out_path = os.path.join(DATA_DIR, "training_dataset.csv")
    df.to_csv(out_path, index=False)

    # ---- export caches for live provisional XI + analysis UI ----
    player_form = {}
    for (comp, team, player), apps in player_apps.items():
        sr, n_bat = store.batter_sr(player)
        econ, n_bowl = store.bowler_econ(player)
        uplift = store.batter_death_uplift(player)
        player_form.setdefault(comp, {}).setdefault(team, {})[player] = {
            "appearances": apps,
            "strike_rate": round(sr, 1) if sr is not None else None,
            "economy": round(econ, 2) if econ is not None else None,
            "death_uplift": round(uplift, 1) if uplift is not None else None,
            "bat_innings": n_bat,
            "bowl_innings": n_bowl,
        }
    provisional = {}
    for (comp, team), xi in last_xi.items():
        provisional.setdefault(comp, {})[team] = {
            "xi": xi,
            "label": "provisional (last known XI from Cricsheet — not official announced lineup)",
        }
    team_form_out = {}
    for (comp, team), tf in team_season_form.items():
        team_form_out.setdefault(comp, {})[team] = {
            **tf,
            "win_rate": round(tf["wins"] / tf["matches"], 3) if tf["matches"] else None,
            "avg_runs_for": round(tf["runs_for"] / tf["matches"], 1) if tf["matches"] else None,
            "avg_runs_against": round(tf["runs_against"] / tf["matches"], 1) if tf["matches"] else None,
        }
    with open(os.path.join(DATA_DIR, "player_form_cache.json"), "w", encoding="utf-8") as f:
        json.dump(player_form, f)
    with open(os.path.join(DATA_DIR, "provisional_xi.json"), "w", encoding="utf-8") as f:
        json.dump(provisional, f)
    with open(os.path.join(DATA_DIR, "team_form_cache.json"), "w", encoding="utf-8") as f:
        json.dump(team_form_out, f)

    print(f"\n{'=' * 70}\nMISSING DATA REPORT -- Cricket (Hundred + MLC + IPL + BBL + CPL)\n{'=' * 70}")
    print(f"Total rows: {len(df)}")
    print("By competition:")
    print(df["Competition"].value_counts().to_string())
    for key, count in missing_log.items():
        print(f"  {key:20s} {count} rows ({count / max(len(df), 1) * 100:.1f}%) flagged thin")
    print("\ndata_confidence distribution:")
    print(df["DataConfidence"].value_counts().to_string())
    print(f"{'=' * 70}\n")
    print(f"Wrote {len(df)} rows -> {out_path}")
    print(f"Caches: player_form_cache.json, provisional_xi.json, team_form_cache.json")
    return df


if __name__ == "__main__":
    build()
