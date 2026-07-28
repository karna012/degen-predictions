"""
Live lineup / injury lookup via FotMob's public JSON API.

This is deliberately kept separate from the trained model: confirmed
starting XIs don't exist until ~1 hour before kickoff, so there's no way to
have this as a standing feature for fixtures months out (the whole season's
worth of upcoming-fixture predictions). Instead it's surfaced on the match
page as live supplementary context -- injuries update immediately, and the
confirmed lineup appears automatically once FotMob has it, without needing a
new model or a retrain.
"""
import requests

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"}

# fixturedownload.com names -> FotMob's team names (used for matching)
FOTMOB_NAME_MAP = {
    "Spurs": "Tottenham", "Man Utd": "Manchester United", "Man United": "Manchester United",
    "Man City": "Manchester City", "Nott'm Forest": "Nottm Forest", "Newcastle": "Newcastle",
    "West Ham": "West Ham", "Leeds": "Leeds", "Wolves": "Wolves",
}


def _normalize(name):
    return FOTMOB_NAME_MAP.get(name, name).lower().replace(".", "").replace("'", "")


def find_fotmob_match_id(home, away, date_str):
    """date_str: 'YYYY-MM-DD'. Scans that day's matches across all
    competitions FotMob returns and matches by team name."""
    date_compact = date_str.replace("-", "")
    resp = requests.get(f"https://www.fotmob.com/api/data/matches?date={date_compact}",
                         headers=HEADERS, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    home_n, away_n = _normalize(home), _normalize(away)
    for league in data.get("leagues", []):
        for m in league.get("matches", []):
            m_home = _normalize(m["home"]["name"])
            m_away = _normalize(m["away"]["name"])
            if home_n in m_home or m_home in home_n:
                if away_n in m_away or m_away in away_n:
                    return m["id"]
    return None


def get_lineup_and_injuries(match_id):
    resp = requests.get(f"https://www.fotmob.com/api/data/matchDetails?matchId={match_id}",
                         headers=HEADERS, timeout=10)
    resp.raise_for_status()
    lineup = resp.json().get("content", {}).get("lineup", {})

    def team_info(side):
        team = lineup.get(side, {})
        starters = team.get("players")  # populated once lineup is confirmed (~1hr pre-kickoff)
        return {
            "name": team.get("name"),
            "confirmed": bool(starters),
            "starters": starters,
            "unavailable": [
                {"name": p["name"], "type": p.get("unavailability", {}).get("type", "unknown"),
                 "expected_return": p.get("unavailability", {}).get("expectedReturn")}
                for p in team.get("unavailable", [])
            ],
        }

    return {"home": team_info("homeTeam"), "away": team_info("awayTeam")}


def get_live_matchday_info(home, away, date_str):
    """Best-effort: returns None if the match can't be found on FotMob
    (e.g. too far in the future for FotMob's own schedule, or a name
    mismatch) rather than raising -- this is supplementary, not required."""
    try:
        match_id = find_fotmob_match_id(home, away, date_str)
        if not match_id:
            return None
        return get_lineup_and_injuries(match_id)
    except Exception:
        return None
