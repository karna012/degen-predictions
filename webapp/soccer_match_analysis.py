"""
Human-style soccer match analysis for match detail pages.

Explain-only: produces scout-style sections (squad roles, player↔team mapping,
home/away, multi-year form, fair play, underdog, attack/counter, transfers,
weather). Does NOT change ML probabilities.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import sys
from functools import lru_cache

import pandas as pd
import requests

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(BASE_DIR, "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)
OFF_TARGET_WEIGHT = 0.5  # off-target shot = half an on-target shot

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
WEATHER_CACHE_TTL_SECONDS = 3 * 3600
_weather_cache: dict = {}

# Approximate stadium / city coords for home-team weather (fail soft if missing).
STADIUM_COORDS = {
    # Premier League / England
    "Arsenal": (51.5549, -0.1084),
    "Aston Villa": (52.5092, -1.8849),
    "Bournemouth": (50.7352, -1.8383),
    "Brentford": (51.4908, -0.2887),
    "Brighton": (50.8616, -0.0837),
    "Chelsea": (51.4817, -0.1910),
    "Crystal Palace": (51.3983, -0.0855),
    "Everton": (53.4388, -2.9663),
    "Fulham": (51.4749, -0.2216),
    "Ipswich": (52.0550, 1.1450),
    "Leicester": (52.6203, -1.1422),
    "Liverpool": (53.4308, -2.9608),
    "Man City": (53.4831, -2.2004),
    "Man United": (53.4631, -2.2913),
    "Newcastle": (54.9756, -1.6217),
    "Nott'm Forest": (52.9400, -1.1328),
    "Southampton": (50.9058, -1.3911),
    "Spurs": (51.6042, -0.0665),
    "West Ham": (51.5387, -0.0166),
    "Wolves": (52.5902, -2.1304),
    # La Liga
    "Barcelona": (41.3809, 2.1228),
    "Real Madrid": (40.4531, -3.6883),
    "Ath Madrid": (40.4362, -3.5995),
    "Sevilla": (37.3841, -5.9706),
    "Valencia": (39.4746, -0.3583),
    # Serie A
    "Inter": (45.4781, 9.1240),
    "Milan": (45.4781, 9.1240),
    "Juventus": (45.1096, 7.6412),
    "Napoli": (40.8279, 14.1931),
    "Roma": (41.9341, 12.4547),
    # Bundesliga
    "Bayern Munich": (48.2188, 11.6247),
    "Dortmund": (51.4926, 7.4519),
    "Leverkusen": (51.0382, 7.0022),
    # Ligue 1
    "Paris SG": (48.8414, 2.2530),
    "Marseille": (43.2698, 5.3959),
    "Lyon": (45.7651, 4.9820),
    # MLS
    "Atlanta Utd": (33.7554, -84.4010),
    "Austin FC": (30.2708, -97.7331),
    "CF Montreal": (45.5630, -73.5527),
    "Charlotte": (35.1860, -80.8231),
    "Chicago Fire": (41.8623, -87.6167),
    "Colorado Rapids": (39.8056, -104.8919),
    "Columbus Crew": (39.9685, -83.0152),
    "DC United": (38.8681, -77.0129),
    "FC Cincinnati": (39.1114, -84.5222),
    "FC Dallas": (33.1543, -96.8352),
    "Houston Dynamo": (29.7520, -95.3524),
    "Inter Miami": (26.1932, -80.1607),
    "Los Angeles FC": (34.0129, -118.2841),
    "Los Angeles Galaxy": (33.8644, -118.2611),
    "Minnesota United": (44.9530, -93.1650),
    "Nashville SC": (36.1627, -86.7705),
    "New England Revolution": (42.0912, -71.2643),
    "New York City": (40.8296, -73.9262),
    "New York Red Bulls": (40.7368, -74.1503),
    "Orlando City": (28.5411, -81.3892),
    "Philadelphia Union": (39.8328, -75.3789),
    "Portland Timbers": (45.5216, -122.6915),
    "Real Salt Lake": (40.5829, -111.8934),
    "San Diego FC": (32.7847, -117.0730),
    "San Jose Earthquakes": (37.3514, -121.9240),
    "Seattle Sounders": (47.5952, -122.3316),
    "Sporting Kansas City": (39.1216, -94.8235),
    "St. Louis City": (38.6313, -90.2105),
    "Toronto FC": (43.6333, -79.4186),
    "Vancouver Whitecaps": (49.2767, -123.1119),
}


def _role_bucket(position: str) -> str:
    """Map Understat / ASA position strings to GK / DEF / MID / ATT / WING."""
    raw = (position or "").strip().upper()
    if not raw:
        return "UNK"
    # ASA single codes
    if raw in {"GK"}:
        return "GK"
    if raw in {"CB", "FB", "LB", "RB", "LWB", "RWB"}:
        return "DEF"
    if raw in {"DM", "CM", "AM", "M"}:
        return "MID"
    if raw in {"W", "LW", "RW"}:
        return "WING"
    if raw in {"ST", "CF", "F"}:
        return "ATT"
    # Understat multi-letter strings (ignore trailing S = appeared as sub)
    tokens = [t for t in re.split(r"\s+", raw) if t and t != "S"]
    if not tokens:
        return "UNK"
    if "GK" in tokens:
        return "GK"
    if "F" in tokens:
        return "ATT"
    if "M" in tokens:
        return "MID"
    if "D" in tokens:
        return "DEF"
    return "UNK"


def shot_quality(shots_ot, shots) -> float:
    """On-target + 0.5 * off-target."""
    try:
        sot = float(shots_ot or 0)
        sh = float(shots or 0)
    except (TypeError, ValueError):
        return 0.0
    off = max(sh - sot, 0.0)
    return sot + OFF_TARGET_WEIGHT * off


def _normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def _player_id(p: dict) -> str:
    return str(p.get("id") or p.get("player_id") or _normalize_name(p.get("player_name") or ""))


def _player_contrib(p: dict) -> float:
    if p.get("source") == "asa" and p.get("goals_added") is not None:
        return float(p.get("goals_added") or 0) + 0.5 * float(p.get("xG_chain") or 0)
    return float(p.get("goals") or 0) + float(p.get("assists") or 0) + float(p.get("xG") or 0)


@lru_cache(maxsize=64)
def _load_players_season(league_key: str, season: str) -> tuple:
    from leagues import LEAGUES
    cfg = LEAGUES[league_key]
    path = os.path.join(BASE_DIR, cfg["data_dir"], "players", f"players_{season}.json")
    if not os.path.exists(path):
        # Pipeline sometimes writes under scripts/data/
        alt = os.path.join(SCRIPTS_DIR, cfg["data_dir"], "players", f"players_{season}.json")
        path = alt if os.path.exists(alt) else path
    if not os.path.exists(path):
        return tuple()
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    out = []
    for p in raw:
        row = dict(p)
        row["player_name"] = p.get("player_name") or p.get("player") or "?"
        row["team_title"] = p.get("team_title") or ""
        row["position"] = p.get("position") or ""
        row["role"] = _role_bucket(row["position"])
        row["minutes"] = float(p.get("time") or p.get("minutes") or 0)
        for k in ("goals", "assists", "xG", "xA", "shots", "key_passes", "goals_added", "xG_chain"):
            if k in row and row[k] is not None:
                try:
                    row[k] = float(row[k])
                except (TypeError, ValueError):
                    row[k] = 0.0
        row["contrib"] = _player_contrib(row)
        mins = row["minutes"]
        row["per90_xG"] = (row.get("xG") or 0) * 90 / mins if mins >= 90 else 0.0
        row["finishing"] = (row.get("goals") or 0) - (row.get("xG") or 0)
        out.append(row)
    return tuple(out)


def _available_player_seasons(league_key: str) -> list[str]:
    from leagues import LEAGUES, UNDERSTAT_SEASON_MAP, league_seasons
    cfg = LEAGUES[league_key]
    players_dir = os.path.join(BASE_DIR, cfg["data_dir"], "players")
    if not os.path.isdir(players_dir):
        players_dir = os.path.join(SCRIPTS_DIR, cfg["data_dir"], "players")
    if not os.path.isdir(players_dir):
        return []
    if cfg.get("asa_league"):
        candidates = [str(s) for s in league_seasons(cfg)]
    elif cfg.get("understat_league"):
        candidates = [u for _, u in UNDERSTAT_SEASON_MAP.items()]
    else:
        return []
    return [s for s in candidates if os.path.exists(os.path.join(players_dir, f"players_{s}.json"))]


def _team_players(players: tuple, team: str, min_minutes: float = 90) -> list:
    return [
        p for p in players
        if (p.get("team_title") or "") == team and (p.get("minutes") or 0) >= min_minutes
    ]


def _top_by_role(team_players: list, n: int = 3) -> dict:
    roles = {"GK": [], "DEF": [], "MID": [], "WING": [], "ATT": []}
    for p in team_players:
        role = p.get("role") or "UNK"
        if role in roles:
            roles[role].append(p)
    out = {}
    for role, rows in roles.items():
        ranked = sorted(rows, key=lambda r: r.get("contrib") or 0, reverse=True)[:n]
        out[role] = [
            {
                "name": r["player_name"],
                "position": r.get("position"),
                "contrib": round(r.get("contrib") or 0, 2),
                "goals": r.get("goals") or 0,
                "xG": round(r.get("xG") or 0, 2),
                "assists": r.get("assists") or 0,
                "minutes": int(r.get("minutes") or 0),
            }
            for r in ranked
        ]
    return out


def _squad_sentence(team: str, by_role: dict) -> str | None:
    parts = []
    labels = [("GK", "GK"), ("DEF", "defence"), ("MID", "midfield"), ("WING", "wide attack"), ("ATT", "attack")]
    for key, label in labels:
        names = [p["name"] for p in by_role.get(key) or []]
        if names:
            parts.append(f"{label}: {', '.join(names)}")
    if not parts:
        return None
    return f"{team} key roles this season — " + "; ".join(parts) + "."


@lru_cache(maxsize=32)
def _load_match_history(league_key: str) -> pd.DataFrame:
    from leagues import LEAGUES, league_seasons
    cfg = LEAGUES[league_key]
    data_dir = os.path.join(BASE_DIR, cfg["data_dir"])
    if not os.path.isdir(data_dir):
        data_dir = os.path.join(SCRIPTS_DIR, cfg["data_dir"])
    fd = cfg["fd_code"]
    frames = []
    usecols = None
    for season in league_seasons(cfg):
        path = os.path.join(data_dir, f"{fd}_{season}.csv")
        if not os.path.exists(path):
            continue
        df = pd.read_csv(path)
        df["Season"] = str(season)
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    history = pd.concat(frames, ignore_index=True)
    history["Date"] = pd.to_datetime(history["Date"], dayfirst=True, format="mixed", errors="coerce")
    return history.dropna(subset=["Date"]).sort_values("Date").reset_index(drop=True)


def _load_team_form(league_key: str) -> pd.DataFrame:
    from leagues import LEAGUES
    cfg = LEAGUES[league_key]
    for base in (BASE_DIR, SCRIPTS_DIR):
        path = os.path.join(base, cfg["data_dir"], "latest_team_form.csv")
        if os.path.exists(path):
            return pd.read_csv(path)
    return pd.DataFrame()


def _team_form_row(form_df: pd.DataFrame, team: str) -> dict:
    if form_df.empty:
        return {}
    hit = form_df[form_df["Team"] == team]
    if hit.empty:
        return {}
    return hit.iloc[0].to_dict()


def _venue_record(history: pd.DataFrame, team: str, venue: str, before: pd.Timestamp, n: int = 8) -> dict:
    """venue: 'H' or 'A'."""
    if history.empty:
        return {"played": 0}
    if venue == "H":
        matches = history[(history["HomeTeam"] == team) & (history["Date"] < before)].tail(n)
        wins = (matches["FTHG"] > matches["FTAG"]).sum()
        draws = (matches["FTHG"] == matches["FTAG"]).sum()
        gf = matches["FTHG"].sum()
        ga = matches["FTAG"].sum()
    else:
        matches = history[(history["AwayTeam"] == team) & (history["Date"] < before)].tail(n)
        wins = (matches["FTAG"] > matches["FTHG"]).sum()
        draws = (matches["FTHG"] == matches["FTAG"]).sum()
        gf = matches["FTAG"].sum()
        ga = matches["FTHG"].sum()
    played = len(matches)
    return {
        "played": played,
        "wins": int(wins),
        "draws": int(draws),
        "losses": int(played - wins - draws),
        "gf": float(gf) if played else 0.0,
        "ga": float(ga) if played else 0.0,
        "ppg": round((3 * wins + draws) / played, 2) if played else 0.0,
    }


def _fair_play(history: pd.DataFrame, team: str, before: pd.Timestamp, n: int = 10) -> dict | None:
    if history.empty or "HY" not in history.columns:
        return None
    # Need at least some non-null card data
    sample = history[["HY", "AY"]].dropna()
    if sample.empty or (sample["HY"].isna().all()):
        return None
    matches = history[
        ((history["HomeTeam"] == team) | (history["AwayTeam"] == team)) & (history["Date"] < before)
    ].tail(n)
    if matches.empty:
        return None
    yellow = red = fouls = 0.0
    count = 0
    for _, row in matches.iterrows():
        if pd.isna(row.get("HY")) and pd.isna(row.get("AY")):
            continue
        count += 1
        if row["HomeTeam"] == team:
            yellow += float(row.get("HY") or 0)
            red += float(row.get("HR") or 0)
            fouls += float(row.get("HF") or 0) if "HF" in row and pd.notna(row.get("HF")) else 0
        else:
            yellow += float(row.get("AY") or 0)
            red += float(row.get("AR") or 0)
            fouls += float(row.get("AF") or 0) if "AF" in row and pd.notna(row.get("AF")) else 0
    if count == 0:
        return None
    return {
        "games": count,
        "yellow_per_game": round(yellow / count, 2),
        "red_per_game": round(red / count, 2),
        "fouls_per_game": round(fouls / count, 2) if fouls else None,
    }


def _underdog_upset_rate(history: pd.DataFrame, elo_diff: float) -> dict | None:
    """Empirical away/home underdog win rate in similar |Elo_Diff| buckets.
    Uses favorite = higher Elo side; requires Elo in latest form applied to history
    is hard — approximate with goal-diff proxy from recent scores is weak.
    Instead: when |elo_diff| >= 80, report share of underdog outright wins from
    historical matches where home scored fewer than away often... Better:
    use train_rows Elo_Diff if available.
    """
    return None  # filled via train_rows helper below


def _underdog_from_train_rows(league_key: str, elo_diff: float) -> dict | None:
    """elo_diff = home_elo - away_elo. Underdog is the lower-Elo side."""
    from leagues import LEAGUES
    cfg = LEAGUES[league_key]
    path = None
    for base in (BASE_DIR, SCRIPTS_DIR):
        cand = os.path.join(base, cfg["data_dir"], "train_rows.csv")
        if os.path.exists(cand):
            path = cand
            break
    if not path or abs(elo_diff) < 60:
        return None
    try:
        df = pd.read_csv(path, usecols=["Elo_Diff", "Target_Result"])
    except (ValueError, OSError):
        return None
    gap = abs(elo_diff)
    lo, hi = gap * 0.7, gap * 1.4
    bucket = df[(df["Elo_Diff"].abs() >= lo) & (df["Elo_Diff"].abs() <= hi)]
    if len(bucket) < 40:
        bucket = df[df["Elo_Diff"].abs() >= 60]
    if len(bucket) < 30:
        return None
    # Underdog win: if Elo_Diff > 0 home is favorite → underdog win is A;
    # if Elo_Diff < 0 away is favorite → underdog win is H.
    underdog_wins = 0
    for _, row in bucket.iterrows():
        ed = row["Elo_Diff"]
        res = row["Target_Result"]
        if ed >= 0 and res == "A":
            underdog_wins += 1
        elif ed < 0 and res == "H":
            underdog_wins += 1
    rate = underdog_wins / len(bucket)
    return {
        "sample": int(len(bucket)),
        "underdog_win_rate": round(rate, 3),
        "elo_gap": round(gap, 1),
    }


def _attack_profile(form: dict) -> dict | None:
    if not form:
        return None
    gf = float(form.get("GF_avg5") or 0)
    ga = float(form.get("GA_avg5") or 0)
    shots = form.get("Shots_avg5")
    shots_against = form.get("ShotsAgainst_avg5")
    sot = form.get("ShotsOT_avg5")
    sq = None
    if sot is not None and shots is not None and pd.notna(shots) and pd.notna(sot):
        sq = shot_quality(sot, shots)
    elif form.get("ShotQuality_avg5") is not None and pd.notna(form.get("ShotQuality_avg5")):
        # Rebuild with 0.5 weight when raw shots exist; else report stored value with note
        sq = float(form["ShotQuality_avg5"])
    style = "balanced"
    if shots is not None and shots_against is not None and pd.notna(shots) and pd.notna(shots_against):
        if float(shots_against) > float(shots) * 1.15 and gf >= 1.2:
            style = "counter"
        elif float(shots) > float(shots_against) * 1.15 and gf >= 1.2:
            style = "possession_attack"
        elif ga >= 1.6 and gf < 1.0:
            style = "under_pressure"
    return {
        "gf_avg5": round(gf, 2),
        "ga_avg5": round(ga, 2),
        "shots_avg5": None if shots is None or pd.isna(shots) else round(float(shots), 1),
        "shots_against_avg5": None if shots_against is None or pd.isna(shots_against) else round(float(shots_against), 1),
        "shot_quality_avg5": None if sq is None else round(float(sq), 2),
        "style": style,
    }


def _transfer_churn(curr: list, prev: list) -> dict:
    curr_map = {_player_id(p): p for p in curr}
    prev_map = {_player_id(p): p for p in prev}
    arrived_ids = [i for i in curr_map if i and i not in prev_map]
    left_ids = [i for i in prev_map if i and i not in curr_map]

    def pack(ids, src):
        rows = [src[i] for i in ids]
        rows.sort(key=lambda r: r.get("contrib") or 0, reverse=True)
        return [
            {
                "name": r["player_name"],
                "contrib": round(r.get("contrib") or 0, 2),
                "minutes": int(r.get("minutes") or 0),
                "xG": round(r.get("xG") or 0, 2),
            }
            for r in rows[:5]
        ]

    return {
        "arrived": pack(arrived_ids, curr_map),
        "left": pack(left_ids, prev_map),
        "n_arrived": len(arrived_ids),
        "n_left": len(left_ids),
    }


def _multi_year_key_players(league_key: str, team: str, seasons: list[str], names: list[str]) -> list:
    """Per named player, last up to 5 seasons of xG / goals."""
    out = []
    for name in names[:6]:
        series = []
        for season in seasons[-5:]:
            players = _load_players_season(league_key, season)
            hits = [
                p for p in players
                if _normalize_name(p.get("player_name")) == _normalize_name(name)
            ]
            if not hits:
                continue
            # Prefer same team; else any club that season
            same = [p for p in hits if p.get("team_title") == team] or hits
            best = max(same, key=lambda p: p.get("minutes") or 0)
            series.append({
                "season": season,
                "team": best.get("team_title"),
                "xG": round(best.get("xG") or 0, 2),
                "goals": best.get("goals") or 0,
                "minutes": int(best.get("minutes") or 0),
                "per90_xG": round(best.get("per90_xG") or 0, 2),
            })
        if len(series) >= 2:
            first, last = series[0], series[-1]
            trend = "rising" if (last.get("per90_xG") or 0) > (first.get("per90_xG") or 0) + 0.05 else (
                "falling" if (last.get("per90_xG") or 0) < (first.get("per90_xG") or 0) - 0.05 else "steady"
            )
            out.append({"name": name, "trend": trend, "seasons": series})
    return out


def get_stadium_weather(team: str, match_date_str: str) -> dict | None:
    """Open-Meteo forecast for the home team's stadium city. Fail soft."""
    try:
        coords = STADIUM_COORDS.get(team)
        if not coords:
            return None
        match_date = datetime.datetime.strptime(match_date_str, "%Y-%m-%d").date()
        today = datetime.datetime.now(datetime.timezone.utc).date()
        if not (today <= match_date <= today + datetime.timedelta(days=16)):
            return None
        cache_key = (team, match_date_str)
        cached = _weather_cache.get(cache_key)
        now = datetime.datetime.now().timestamp()
        if cached and now - cached["fetched_at"] < WEATHER_CACHE_TTL_SECONDS:
            return cached["data"]
        lat, lon = coords
        resp = requests.get(OPEN_METEO_URL, params={
            "latitude": lat, "longitude": lon,
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weathercode",
            "timezone": "auto", "start_date": match_date_str, "end_date": match_date_str,
        }, timeout=8)
        resp.raise_for_status()
        daily = resp.json().get("daily", {})
        if not daily.get("time"):
            return None
        rain = daily["precipitation_probability_max"][0]
        data = {
            "temp_max_c": daily["temperature_2m_max"][0],
            "temp_min_c": daily["temperature_2m_min"][0],
            "precipitation_probability_pct": rain,
            "date": match_date_str,
            "note": (
                "Wet-weather game — expect slower tempo and fewer clear chances."
                if rain is not None and rain >= 60
                else "Conditions look playable for an open match."
            ),
        }
        _weather_cache[cache_key] = {"data": data, "fetched_at": now}
        return data
    except Exception:
        return None


def _map_player_team(team: str, squad: list, form: dict) -> str | None:
    if not squad and not form:
        return None
    top = sorted(squad, key=lambda p: p.get("contrib") or 0, reverse=True)[:3]
    names = ", ".join(p["player_name"] for p in top) if top else "limited squad data"
    gf = form.get("GF_avg5")
    ga = form.get("GA_avg5")
    fin = sum((p.get("finishing") or 0) for p in squad[:15])
    bits = [f"{team}'s attacking load sits mainly with {names}"]
    if gf is not None and ga is not None:
        bits.append(f"while recent team form averages {float(gf):.1f} scored and {float(ga):.1f} conceded per game")
    if abs(fin) >= 1.5:
        bits.append(
            "finishing has been clinical (goals above xG)" if fin > 0
            else "finishing has underperformed xG"
        )
    return " — ".join(bits) + "."


def _verdict(home, away, sections: dict, match: dict | None) -> str:
    bits = []
    ha = sections.get("home_away") or {}
    if ha.get("home_home") and ha["home_home"].get("played"):
        hh = ha["home_home"]
        bits.append(
            f"{home} at home: {hh['wins']}W-{hh['draws']}D-{hh['losses']}L in last {hh['played']}"
        )
    if ha.get("away_away") and ha["away_away"].get("played"):
        aa = ha["away_away"]
        bits.append(
            f"{away} away: {aa['wins']}W-{aa['draws']}D-{aa['losses']}L in last {aa['played']}"
        )
    und = sections.get("underdog")
    if und:
        bits.append(
            f"similar Elo gaps historically see the underdog win outright ~{und['underdog_win_rate']*100:.0f}% "
            f"of the time (n={und['sample']})"
        )
    churn = sections.get("transfers") or {}
    for side, label in (("home", home), ("away", away)):
        c = churn.get(side) or {}
        if c.get("left"):
            left_names = ", ".join(p["name"] for p in c["left"][:2])
            bits.append(f"{label} missing departed contributors ({left_names})")
        if c.get("arrived"):
            arr = ", ".join(p["name"] for p in c["arrived"][:2])
            bits.append(f"{label} adding {arr}")
    atk = sections.get("attack") or {}
    for side, label in (("home", home), ("away", away)):
        prof = atk.get(side)
        if prof and prof.get("style") == "counter":
            bits.append(f"{label} profile leans counter-attacking")
        elif prof and prof.get("style") == "possession_attack":
            bits.append(f"{label} profile leans territorial attack")
    pred = ""
    if match and match.get("Predicted_Result"):
        pred = f" Model lean (unchanged by this scout note): {match['Predicted_Result']}."
    if not bits:
        return (
            f"Scout read on {home} vs {away}: limited supporting detail available; "
            f"rely on the model probabilities below for the quantitative lean.{pred}"
        )
    return "Scout synthesis: " + "; ".join(bits) + "." + pred


def build_match_analysis(
    league_key: str,
    league_label: str,
    home: str,
    away: str,
    match_date: str,
    match: dict | None = None,
    lineup_info: dict | None = None,
) -> dict:
    """Full scout-style report. Never raises."""
    empty = {
        "sections": {},
        "verdict": None,
        "available": False,
    }
    try:
        seasons = _available_player_seasons(league_key)
        curr_season = seasons[-1] if seasons else None
        prev_season = seasons[-2] if len(seasons) >= 2 else None
        curr_players = _load_players_season(league_key, curr_season) if curr_season else tuple()
        prev_players = _load_players_season(league_key, prev_season) if prev_season else tuple()

        home_squad = _team_players(curr_players, home)
        away_squad = _team_players(curr_players, away)
        home_roles = _top_by_role(home_squad)
        away_roles = _top_by_role(away_squad)

        form_df = _load_team_form(league_key)
        home_form = _team_form_row(form_df, home)
        away_form = _team_form_row(form_df, away)

        history = _load_match_history(league_key)
        before = pd.to_datetime(match_date)

        home_home = _venue_record(history, home, "H", before)
        away_away = _venue_record(history, away, "A", before)

        sections: dict = {}

        # 1. Squad by role
        squad_block = {
            "home": home_roles,
            "away": away_roles,
            "home_sentence": _squad_sentence(home, home_roles),
            "away_sentence": _squad_sentence(away, away_roles),
        }
        if lineup_info:
            squad_block["lineup_note"] = (
                "Confirmed FotMob XI / injuries are listed separately below when available; "
                "role leaders above are season-to-date contributors."
            )
        if squad_block["home_sentence"] or squad_block["away_sentence"]:
            sections["squad"] = squad_block

        # 2. Player ↔ team
        map_home = _map_player_team(home, home_squad, home_form)
        map_away = _map_player_team(away, away_squad, away_form)
        if map_home or map_away:
            sections["player_team"] = {
                "home_sentence": map_home,
                "away_sentence": map_away,
            }

        # 3. Home / away
        ha_sentences = []
        if home_home.get("played"):
            ha_sentences.append(
                f"{home} at home recently: {home_home['wins']}W {home_home['draws']}D "
                f"{home_home['losses']}L ({home_home['ppg']} pts/game), "
                f"scoring {home_home['gf']:.0f} and conceding {home_home['ga']:.0f}."
            )
        if away_away.get("played"):
            ha_sentences.append(
                f"{away} on the road recently: {away_away['wins']}W {away_away['draws']}D "
                f"{away_away['losses']}L ({away_away['ppg']} pts/game)."
            )
        home_adv = home_form.get("TeamHomeAdvantage")
        if home_adv is not None and pd.notna(home_adv):
            ha_sentences.append(
                f"Measured home advantage for {home}: {float(home_adv):+.2f} points vs neutral baseline."
            )
        if ha_sentences:
            sections["home_away"] = {
                "home_home": home_home,
                "away_away": away_away,
                "sentences": ha_sentences,
            }

        # 4. Multi-year key players
        key_names = []
        for roles in (home_roles, away_roles):
            for role in ("ATT", "WING", "MID", "GK"):
                for p in roles.get(role) or []:
                    key_names.append(p["name"])
        my_home = _multi_year_key_players(league_key, home, seasons, [p["name"] for p in (home_roles.get("ATT") or []) + (home_roles.get("WING") or []) + (home_roles.get("MID") or [])[:1]])
        my_away = _multi_year_key_players(league_key, away, seasons, [p["name"] for p in (away_roles.get("ATT") or []) + (away_roles.get("WING") or []) + (away_roles.get("MID") or [])[:1]])
        if my_home or my_away:
            sentences = []
            for side in my_home + my_away:
                sentences.append(
                    f"{side['name']} looks {side['trend']} on xG/90 across {len(side['seasons'])} seasons on file."
                )
            sections["multi_year"] = {
                "home": my_home,
                "away": my_away,
                "sentences": sentences,
            }

        # 5. Fair play
        fp_home = _fair_play(history, home, before)
        fp_away = _fair_play(history, away, before)
        if fp_home or fp_away:
            sentences = []
            if fp_home:
                sentences.append(
                    f"{home} fair-play pulse: {fp_home['yellow_per_game']} yellows/game"
                    + (f", {fp_home['fouls_per_game']} fouls/game" if fp_home.get("fouls_per_game") is not None else "")
                    + f" over last {fp_home['games']}."
                )
            if fp_away:
                sentences.append(
                    f"{away}: {fp_away['yellow_per_game']} yellows/game over last {fp_away['games']}."
                )
            sections["fair_play"] = {
                "home": fp_home,
                "away": fp_away,
                "sentences": sentences,
            }

        # 6. Underdog
        home_elo = float(home_form.get("Elo") or 1500)
        away_elo = float(away_form.get("Elo") or 1500)
        elo_diff = home_elo - away_elo
        und = _underdog_from_train_rows(league_key, elo_diff)
        if und:
            underdog = away if elo_diff > 0 else home
            favorite = home if elo_diff > 0 else away
            sections["underdog"] = {
                **und,
                "underdog": underdog,
                "favorite": favorite,
                "sentence": (
                    f"Elo gap ~{und['elo_gap']:.0f} favors {favorite}. In similar historical gaps, "
                    f"the underdog ({underdog}) won outright {und['underdog_win_rate']*100:.0f}% "
                    f"of the time (sample {und['sample']}) — underdog edge is real but uncommon."
                ),
            }

        # 7. Attack / counter
        atk_home = _attack_profile(home_form)
        atk_away = _attack_profile(away_form)
        if atk_home or atk_away:
            sentences = []
            style_words = {
                "counter": "counter-attacking (absorb pressure, punish transitions)",
                "possession_attack": "territorial / shot-volume attacking",
                "under_pressure": "under defensive pressure",
                "balanced": "balanced attack/defence",
            }
            if atk_home:
                sentences.append(
                    f"{home} recent profile: {style_words.get(atk_home['style'], atk_home['style'])} "
                    f"({atk_home['gf_avg5']} GF / {atk_home['ga_avg5']} GA per game"
                    + (f", shot quality ~{atk_home['shot_quality_avg5']}" if atk_home.get("shot_quality_avg5") else "")
                    + ")."
                )
            if atk_away:
                sentences.append(
                    f"{away}: {style_words.get(atk_away['style'], atk_away['style'])} "
                    f"({atk_away['gf_avg5']} GF / {atk_away['ga_avg5']} GA)."
                )
            sections["attack"] = {
                "home": atk_home,
                "away": atk_away,
                "shot_weight_note": "Shot quality treats off-target shots as half an on-target shot.",
                "sentences": sentences,
            }

        # 8. Transfers / churn
        home_prev = _team_players(prev_players, home, min_minutes=1) if prev_players else []
        away_prev = _team_players(prev_players, away, min_minutes=1) if prev_players else []
        home_churn = _transfer_churn(home_squad or _team_players(curr_players, home, 1), home_prev) if prev_season else None
        away_churn = _transfer_churn(away_squad or _team_players(curr_players, away, 1), away_prev) if prev_season else None
        if home_churn or away_churn:
            sentences = []
            for label, churn in ((home, home_churn), (away, away_churn)):
                if not churn:
                    continue
                if churn["left"]:
                    sentences.append(
                        f"{label} lost {churn['n_left']} players vs last season on file; "
                        f"biggest departures by impact: "
                        + ", ".join(
                            f"{p['name']} (contrib {p['contrib']})" for p in churn["left"][:3]
                        )
                        + "."
                    )
                if churn["arrived"]:
                    sentences.append(
                        f"{label} new faces include "
                        + ", ".join(p["name"] for p in churn["arrived"][:3])
                        + "."
                    )
            if sentences:
                sections["transfers"] = {
                    "home": home_churn,
                    "away": away_churn,
                    "from_season": prev_season,
                    "to_season": curr_season,
                    "sentences": sentences,
                }

        # 9. Weather
        weather = get_stadium_weather(home, match_date)
        if weather:
            sections["weather"] = {
                **weather,
                "sentence": (
                    f"Forecast at {home}'s ground on {weather['date']}: "
                    f"{weather['temp_min_c']:.0f}–{weather['temp_max_c']:.0f}°C, "
                    f"{weather['precipitation_probability_pct']}% rain chance. {weather['note']}"
                ),
            }

        verdict = _verdict(home, away, sections, match)
        return {
            "sections": sections,
            "verdict": verdict,
            "available": bool(sections),
            "league_label": league_label,
            "disclaimer": (
                "Scout-style context from squad stats, match history, and weather. "
                "It does not change the machine-learning probabilities shown below."
            ),
        }
    except Exception:
        return empty
