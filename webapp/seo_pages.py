"""SEO helpers: today hubs, match intros, thin-page gates, sitemap shards."""
from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
from typing import Any
from urllib.parse import quote

SITE_ORIGIN = "https://degenpredictions.com"
IST = "Asia/Kolkata"


def soccer_league_keys() -> list[str]:
    from leagues import LEAGUES
    return list(LEAGUES.keys())


def file_lastmod(path: str | Path | None) -> str:
    """ISO date from file mtime, else today (UTC)."""
    try:
        p = Path(path) if path else None
        if p and p.is_file():
            return dt.datetime.utcfromtimestamp(p.stat().st_mtime).date().isoformat()
    except OSError:
        pass
    return dt.date.today().isoformat()


def match_has_forecast(match: dict[str, Any] | None) -> bool:
    """True when the row carries real model probabilities (not an empty stub)."""
    if not match:
        return False
    try:
        p_home = float(match.get("P_Home_Win"))
        p_draw = float(match.get("P_Draw"))
        p_away = float(match.get("P_Away_Win"))
    except (TypeError, ValueError):
        return False
    total = p_home + p_draw + p_away
    return total > 0.5 and max(p_home, p_draw, p_away) > 0.01


def match_has_written_context(context: dict[str, Any] | None) -> bool:
    if not context:
        return False
    if context.get("home_form_sentence") or context.get("away_form_sentence"):
        return True
    if context.get("h2h_sentence"):
        return True
    analysis = context.get("analysis") or {}
    if isinstance(analysis, dict) and analysis.get("available"):
        return True
    return False


def is_match_indexable(match: dict[str, Any] | None, context: dict[str, Any] | None = None) -> bool:
    """Quality gate: index only pages with a forecast + some written context."""
    if not match_has_forecast(match):
        return False
    # Prefer written context; still index if tips exist with a high-conf lean.
    if match_has_written_context(context):
        return True
    # Fallback: Predicted_Result / BTTS fields present
    if match and (match.get("Predicted_Result") or match.get("BTTS_Prediction")):
        return True
    return False


def match_seo_intro(
    match: dict[str, Any],
    tips: list[dict[str, Any]] | None,
    league_label: str,
    context: dict[str, Any] | None = None,
) -> str:
    """Unique 2–3 sentence lead for match pages (and meta description boost)."""
    home = str(match.get("HomeTeam") or "Home")
    away = str(match.get("AwayTeam") or "Away")
    when = str(match.get("MatchDate") or match.get("DateUtc") or "")[:16]
    fav = str(match.get("Predicted_Result") or "").strip()
    try:
        p_home = float(match.get("P_Home_Win") or 0)
        p_draw = float(match.get("P_Draw") or 0)
        p_away = float(match.get("P_Away_Win") or 0)
    except (TypeError, ValueError):
        p_home = p_draw = p_away = 0.0

    tip_bits = []
    for tip in (tips or [])[:3]:
        pick = tip.get("pick")
        market = tip.get("market")
        prob = tip.get("probability")
        if not pick or pick == "No pick" or prob is None:
            continue
        tip_bits.append(f"{market}: {pick} ({float(prob) * 100:.0f}%)")

    parts = [
        f"{home} vs {away} {league_label} prediction"
        + (f" for {when}" if when else "")
        + "."
    ]
    if fav:
        parts.append(
            f"Underlying 1X2 lean is {fav} "
            f"({p_home * 100:.0f}% home / {p_draw * 100:.0f}% draw / {p_away * 100:.0f}% away)."
        )
    if tip_bits:
        parts.append("Model markets — " + "; ".join(tip_bits) + ".")
    else:
        parts.append(
            "Safer Result, BTTS, goals, and corners are shown when they clear calibrated bars."
        )
    if context and context.get("h2h_sentence"):
        # Keep short: first clause only
        h2h = str(context["h2h_sentence"]).split(" -- ")[0].rstrip(".")
        if len(h2h) < 160:
            parts.append(h2h + ".")
    parts.append("Educational ML estimate — not betting advice.")
    return " ".join(parts)


def match_seo_description(match: dict[str, Any], league_label: str, intro: str) -> str:
    """Meta description ≤ ~155 chars, unique per fixture."""
    home = str(match.get("HomeTeam") or "")
    away = str(match.get("AwayTeam") or "")
    when = str(match.get("MatchDate") or "")[:12]
    base = f"{home} vs {away} {league_label} prediction"
    if when:
        base += f" ({when})"
    fav = str(match.get("Predicted_Result") or "").strip()
    if fav:
        base += f" — model lean {fav}"
    base += ". Free forecast, not betting advice."
    if len(base) <= 155:
        return base
    # Fall back to truncated intro
    text = intro.replace("Educational ML estimate — not betting advice.", "").strip()
    if len(text) > 152:
        text = text[:149].rstrip() + "..."
    return text


def build_predictions_today_board(league_key: str, load_predictions, split_soccer_rows) -> dict[str, Any]:
    """Today's upcoming soccer fixtures + tip teasers for the SEO hub."""
    import today_picks

    from leagues import LEAGUES

    cfg = LEAGUES.get(league_key) or {}
    label = cfg.get("label") or league_key.replace("_", " ").title()
    tz_name = (
        today_picks.TODAY_SPORTS.get("soccer", {})
        .get("leagues", {})
        .get(league_key, {})
        .get("tz")
        or IST
    )
    today = today_picks.today_in_tz(tz_name)
    df = load_predictions(league_key)
    rows = df.to_dict(orient="records") if df is not None and not df.empty else []
    upcoming, _finished = split_soccer_rows(league_key, rows)

    fixtures: list[dict[str, Any]] = []
    for m in upcoming:
        kickoff = m.get("DateUtc")
        if today_picks.local_date(kickoff, tz_name) != today:
            continue
        home = m.get("HomeTeam")
        away = m.get("AwayTeam")
        href = f"/{league_key}/match/{quote(str(home))}/{quote(str(away))}"
        indexable = is_match_indexable(m, None)
        # Light tips for the hub cards (reuse generate path lazily in route)
        fixtures.append({
            "home": home,
            "away": away,
            "matchup": f"{home} vs {away}",
            "when": m.get("MatchDate") or str(kickoff or "")[:16],
            "kickoff_iso": str(kickoff or ""),
            "href": href,
            "predicted": m.get("Predicted_Result"),
            "p_home": m.get("P_Home_Win"),
            "p_draw": m.get("P_Draw"),
            "p_away": m.get("P_Away_Win"),
            "btts": m.get("BTTS_Prediction"),
            "goals": m.get("Goals_Prediction"),
            "data_confidence": m.get("Data_Confidence"),
            "indexable": indexable,
            "row": m,
        })

    fixtures.sort(key=lambda x: x.get("kickoff_iso") or "")
    return {
        "league_key": league_key,
        "league_label": label,
        "today": today.isoformat() if hasattr(today, "isoformat") else str(today),
        "tz": tz_name,
        "fixtures": fixtures,
        "count": len(fixtures),
    }


def breadcrumbs(*crumbs: tuple[str, str]) -> list[dict[str, str]]:
    """crumbs: (name, path) — path may be '' for current page without link."""
    out = [{"name": "Home", "item": f"{SITE_ORIGIN}/"}]
    for name, path in crumbs:
        item = f"{SITE_ORIGIN}{path}" if path.startswith("/") else path
        out.append({"name": name, "item": item})
    return out


def breadcrumb_jsonld(crumbs: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": i + 1,
                "name": c["name"],
                "item": c["item"],
            }
            for i, c in enumerate(crumbs)
        ],
    }


def clean_mlb_match_url(home_code: str, away_code: str) -> str:
    return f"{SITE_ORIGIN}/mlb/match/{quote(str(home_code))}/{quote(str(away_code))}"


def clean_nfl_match_url(home_code: str, away_code: str) -> str:
    return f"{SITE_ORIGIN}/nfl/match/{quote(str(home_code))}/{quote(str(away_code))}"


def clean_nba_match_url(home_code: str, away_code: str) -> str:
    return f"{SITE_ORIGIN}/nba/match/{quote(str(home_code))}/{quote(str(away_code))}"


def clean_nhl_match_url(home_code: str, away_code: str) -> str:
    return f"{SITE_ORIGIN}/nhl/match/{quote(str(home_code))}/{quote(str(away_code))}"


def clean_npb_match_url(home_id: int | str, away_id: int | str) -> str:
    return f"{SITE_ORIGIN}/npb/match/{home_id}/{away_id}"


def soccer_match_url(league_key: str, home: str, away: str) -> str:
    return f"{SITE_ORIGIN}/{league_key}/match/{quote(str(home))}/{quote(str(away))}"


def predictions_today_url(league_key: str) -> str:
    return f"{SITE_ORIGIN}/{league_key}/predictions-today"
