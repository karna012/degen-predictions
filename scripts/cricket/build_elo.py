"""Computes chronological team Elo ratings and per-venue pitch statistics
for all registered cricket competitions (Hundred, MLC, IPL, BBL).

IPL/BBL apply a modest home-advantage term (ELO_HOME_ADVANTAGE) when the
batting/fielding side can be matched to a franchise home ground. Hundred
and MLC keep HA off -- multi-venue short seasons don't support it cleanly.

Run from repo root: python scripts/cricket/build_elo.py
Outputs data/cricket/elo_ratings.csv, data/cricket/venue_stats.csv,
data/cricket/match_history.csv, data/cricket/venue_home_splits.csv.
"""
import os
import sys
from collections import deque

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cricket_config import (  # noqa: E402
    COMPETITIONS, DATA_DIR, ELO_HOME_ADVANTAGE, ELO_INITIAL, ELO_K,
    ROLLING_FORM_MATCHES, canonical_team, team_is_home_at_venue,
)
from cricket_data import innings_summary, iter_matches, match_result  # noqa: E402


def expected_score(elo_a, elo_b):
    return 1.0 / (1.0 + 10 ** (-(elo_a - elo_b) / 400.0))


def build():
    matches = iter_matches()
    comps = sorted({m["_competition"] for m in matches})
    print(f"{len(matches)} matches loaded across {len(comps)} competitions: {comps}")

    elo = {}
    games_played = {}
    recent_results = {}
    recent_run_rate = {}
    venue_first_totals = {}
    venue_second_totals = {}
    venue_runs_per_over = {}
    venue_wkts_per_over = {}
    venue_home_runs = {}
    venue_away_runs = {}
    history_rows = []
    malformed_total = 0

    for match in matches:
        info = match.get("info", {})
        teams = info.get("teams", [])
        if len(teams) != 2:
            continue
        venue = info.get("venue", "Unknown")
        innings_list = match.get("innings", [])
        if len(innings_list) < 2:
            continue
        comp = match["_competition"]
        use_ha = bool(COMPETITIONS.get(comp, {}).get("home_advantage"))

        summaries = [innings_summary(inn) for inn in innings_list[:2]]
        malformed_total += sum(s["malformed_deliveries"] for s in summaries)
        team1 = canonical_team(summaries[0]["team"])
        team2 = canonical_team(summaries[1]["team"])
        runs1, runs2 = summaries[0]["total_runs"], summaries[1]["total_runs"]
        overs1 = len(summaries[0]["overs_data"]) or 1
        overs2 = len(summaries[1]["overs_data"]) or 1
        wkts1 = summaries[0]["total_wickets"]
        wkts2 = summaries[1]["total_wickets"]

        elo1 = elo.get(team1, ELO_INITIAL)
        elo2 = elo.get(team2, ELO_INITIAL)
        ha1 = ha2 = 0.0
        if use_ha:
            if team_is_home_at_venue(team1, venue):
                ha1 = ELO_HOME_ADVANTAGE
            elif team_is_home_at_venue(team2, venue):
                ha2 = ELO_HOME_ADVANTAGE
        winner_raw = match_result(match)
        winner = canonical_team(winner_raw) if winner_raw else None

        if winner in (team1, team2):
            score1 = 1.0 if winner == team1 else 0.0
            exp1 = expected_score(elo1 + ha1, elo2 + ha2)
            elo[team1] = elo1 + ELO_K * (score1 - exp1)
            elo[team2] = elo2 + ELO_K * ((1 - score1) - (1 - exp1))
            recent_results.setdefault(team1, deque(maxlen=ROLLING_FORM_MATCHES)).append(1 if winner == team1 else 0)
            recent_results.setdefault(team2, deque(maxlen=ROLLING_FORM_MATCHES)).append(1 if winner == team2 else 0)

        for team in (team1, team2):
            games_played[team] = games_played.get(team, 0) + 1
        recent_run_rate.setdefault(team1, deque(maxlen=ROLLING_FORM_MATCHES)).append(runs1 / overs1)
        recent_run_rate.setdefault(team2, deque(maxlen=ROLLING_FORM_MATCHES)).append(runs2 / overs2)

        venue_first_totals.setdefault(venue, []).append(runs1)
        venue_second_totals.setdefault(venue, []).append(runs2)
        for s, overs_n in ((summaries[0], overs1), (summaries[1], overs2)):
            for over_idx, over_runs, over_wkts in s["overs_data"]:
                venue_runs_per_over.setdefault(venue, []).append(over_runs)
                venue_wkts_per_over.setdefault(venue, []).append(over_wkts)

        # Home/away run splits at venue (for home-ground analysis panel).
        for team, runs in ((team1, runs1), (team2, runs2)):
            if team_is_home_at_venue(team, venue):
                venue_home_runs.setdefault(venue, []).append(runs)
            else:
                venue_away_runs.setdefault(venue, []).append(runs)

        history_rows.append({
            "Date": match["_date"], "Competition": comp, "Venue": venue,
            "Team1": team1, "Team2": team2, "Team1Runs": runs1, "Team2Runs": runs2,
            "Team1Wickets": wkts1, "Team2Wickets": wkts2, "Winner": winner,
            "HomeAdvantageTeam": (
                team1 if ha1 else (team2 if ha2 else "")
            ),
            "Team1EloAfter": round(elo.get(team1, ELO_INITIAL), 1),
            "Team2EloAfter": round(elo.get(team2, ELO_INITIAL), 1),
        })

    if malformed_total:
        print(f"NOTE: {malformed_total} malformed deliveries skipped across all matches")

    teams = sorted(elo.keys())
    ratings = pd.DataFrame({
        "Team": teams,
        "Elo": [round(elo[t], 1) for t in teams],
        "GamesPlayed": [games_played.get(t, 0) for t in teams],
        "RecentWinRate": [round(sum(recent_results.get(t, [])) / len(recent_results[t]), 3)
                          if recent_results.get(t) else None for t in teams],
        "RecentRunRate": [round(sum(recent_run_rate.get(t, [])) / len(recent_run_rate[t]), 2)
                          if recent_run_rate.get(t) else None for t in teams],
    }).sort_values("Elo", ascending=False).reset_index(drop=True)

    venues = sorted(venue_first_totals.keys())
    venue_df = pd.DataFrame({
        "Venue": venues,
        "MatchesPlayed": [len(venue_first_totals[v]) for v in venues],
        "AvgFirstInningsTotal": [round(sum(venue_first_totals[v]) / len(venue_first_totals[v]), 1) for v in venues],
        "AvgSecondInningsTotal": [round(sum(venue_second_totals[v]) / len(venue_second_totals[v]), 1) for v in venues],
        "AvgRunsPerOver": [round(sum(venue_runs_per_over[v]) / len(venue_runs_per_over[v]), 2)
                           if venue_runs_per_over.get(v) else None for v in venues],
        "AvgWicketsPerOver": [round(sum(venue_wkts_per_over[v]) / len(venue_wkts_per_over[v]), 3)
                              if venue_wkts_per_over.get(v) else None for v in venues],
        "AvgHomeTeamRuns": [
            round(sum(venue_home_runs[v]) / len(venue_home_runs[v]), 1) if venue_home_runs.get(v) else None
            for v in venues
        ],
        "AvgAwayTeamRuns": [
            round(sum(venue_away_runs[v]) / len(venue_away_runs[v]), 1) if venue_away_runs.get(v) else None
            for v in venues
        ],
        "HomeMatches": [len(venue_home_runs.get(v, [])) for v in venues],
        "AwayMatches": [len(venue_away_runs.get(v, [])) for v in venues],
    })
    if not venue_df.empty and venue_df["AvgRunsPerOver"].notna().any():
        league_rpo = venue_df["AvgRunsPerOver"].mean()
        league_wpo = venue_df["AvgWicketsPerOver"].mean()

        def pitch_label(row):
            if pd.isna(row["AvgRunsPerOver"]):
                return "unknown (insufficient data)"
            high_scoring = row["AvgRunsPerOver"] >= league_rpo * 1.05
            low_scoring = row["AvgRunsPerOver"] <= league_rpo * 0.95
            bowling_friendly = row["AvgWicketsPerOver"] >= league_wpo * 1.1
            if high_scoring and not bowling_friendly:
                return "batting-friendly"
            if low_scoring or bowling_friendly:
                return "bowling-friendly"
            return "balanced"

        venue_df["PitchType"] = venue_df.apply(pitch_label, axis=1)
    else:
        venue_df["PitchType"] = "unknown (insufficient data)"

    os.makedirs(DATA_DIR, exist_ok=True)
    ratings.to_csv(os.path.join(DATA_DIR, "elo_ratings.csv"), index=False)
    venue_df.to_csv(os.path.join(DATA_DIR, "venue_stats.csv"), index=False)
    pd.DataFrame(history_rows).to_csv(os.path.join(DATA_DIR, "match_history.csv"), index=False)
    print(f"wrote {len(ratings)} team ratings, {len(venue_df)} venues, {len(history_rows)} match history rows")
    print(ratings.head(15).to_string(index=False))


if __name__ == "__main__":
    build()
