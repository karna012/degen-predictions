"""Parse Sackmann / TennisMyLife ATP score strings into set/game totals."""
from __future__ import annotations

import re

SET_RE = re.compile(
    r"(?P<a>\d+)-(?P<b>\d+)(?:\((?P<tb>\d+)\))?",
)


def parse_tennis_score(score: str | None) -> dict | None:
    """Return winner/loser set & game counts from a completed score.

    Score is always written winner-first per set (Sackmann convention), e.g.
    ``6-4 3-6 7-6(5)``. Retirements / walkovers return None.
    """
    raw = (score or "").strip()
    if not raw:
        return None
    upper = raw.upper()
    if any(tok in upper for tok in ("W/O", "WALKOVER", "DEF", "DEFAULT", "ABD")):
        return None
    retired = "RET" in upper
    cleaned = re.sub(r"\bRET\b", "", raw, flags=re.I).strip()
    sets = []
    for m in SET_RE.finditer(cleaned):
        a, b = int(m.group("a")), int(m.group("b"))
        # Ignore unfinished 0-0 style noise
        if a == 0 and b == 0:
            continue
        sets.append((a, b))
    if not sets:
        return None
    # Incomplete retirement with <1 finished set → skip for markets
    if retired and len(sets) < 1:
        return None
    w_sets = sum(1 for a, b in sets if a > b)
    l_sets = sum(1 for a, b in sets if b > a)
    w_games = sum(a for a, _ in sets)
    l_games = sum(b for _, b in sets)
    return {
        "sets": sets,
        "total_sets": len(sets),
        "winner_sets": w_sets,
        "loser_sets": l_sets,
        "winner_games": w_games,
        "loser_games": l_games,
        "total_games": w_games + l_games,
        "games_margin": w_games - l_games,
        "loser_won_set": l_sets >= 1,
        "retired": retired,
    }
