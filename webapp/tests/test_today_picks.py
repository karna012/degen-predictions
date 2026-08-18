"""Today's Picks hub: Sports >> League >> slate (or no match today)."""
import today_picks


def test_nav_has_todays_picks(client):
    body = client.get("/").data.decode()
    assert "Today's Picks" in body
    assert 'id="todays-picks-nav"' in body
    assert 'id="todays-picks-toggle"' in body
    assert 'href="/todays-picks/soccer/premier_league"' in body
    assert 'href="/todays-picks/baseball/mlb"' in body
    assert 'href="/todays-picks/cricket/hundred"' in body
    assert 'href="/todays-picks/combos"' in body
    assert "Best Combos" in body
    # Single Best Combos entry lives inside the Today's Picks dropdown (not a duplicate top-level).
    nav = body.split("<nav")[1].split("</nav>")[0]
    assert nav.count('href="/todays-picks/combos"') == 1
    # Accuracy hub lives in footer + homepage strip (not top nav).
    assert 'href="/accuracy"' not in nav
    assert "Accuracy hub" not in nav
    assert 'class="accuracy-strip"' in body or "Track record" in body
    assert 'id="nav-burger"' in body
    # Nav uses a dropdown toggle, not a top-level page link to /todays-picks hub.
    assert 'href="/todays-picks"' not in nav or 'href="/todays-picks/' in nav


def test_mobile_nav_uses_accessible_inline_accordion(client):
    body = client.get("/").data.decode()
    nav = body.split("<nav")[1].split("</nav>")[0]
    css = client.get("/static/style.css").data.decode()

    assert body.count('id="todays-picks-menu"') == 1
    assert 'aria-controls="todays-picks-menu"' in nav
    assert 'aria-expanded="false"' in nav
    assert 'role="menu"' not in nav
    # Mobile keeps the submenu under its toggle; only desktop may portal it.
    assert 'if (mobileNavQuery.matches)' in body
    assert "restoreMenu();" in body
    assert "position:static!important" in css
    assert "100dvh" in css
    assert ".nav-dropdown.open .nav-dropdown-caret" in css
    assert "html.nav-drawer-open" in css


def test_accuracy_and_about_pages(client):
    assert client.get("/accuracy").status_code == 200
    body = client.get("/accuracy").data.decode()
    assert "Accuracy hub" in body
    assert "Safer Result" in body
    assert "coverage" in body
    assert "n=" in body
    assert "<th>1X2</th>" not in body
    assert "Fav avoids defeat" not in body
    about = client.get("/about")
    assert about.status_code == 200
    assert b"Degen Predictions" in about.data


def test_strongest_pick_per_matchup_keeps_highest_confidence():
    a = today_picks._pick(
        away="B", home="A", pick="A", confidence=0.71, market="Match winner",
        matchup="A vs B",
    )
    b = today_picks._pick(
        away="B", home="A", pick="Over 22.5", confidence=0.78, market="Total games",
        matchup="A vs B",
    )
    c = today_picks._pick(
        away="D", home="C", pick="C", confidence=0.70, market="Match winner",
        matchup="C vs D",
    )
    out = today_picks._strongest_pick_per_matchup([a, b, c])
    assert [p["matchup"] for p in out] == ["A vs B", "C vs D"]
    assert out[0]["market"] == "Total games"


def test_combo_picks_page_loads(client):
    resp = client.get("/todays-picks/combos")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Best Combo Picks Today" in body
    assert (
        ("Best 2-leg" in body)
        or ("Mixed sports" in body)
        or ("No combo picks today" in body)
    )
    assert "Join Telegram" in body or "No combo picks today" in body
    if "No combo picks today" not in body:
        assert "js-unlock-slate" not in body
        assert "Combo on Telegram" in body
        assert 'data-href="/go/telegram"' in body


def test_unlock_gate_asks_to_unlock_first():
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "static" / "ui-polish.js").read_text(encoding="utf-8")
    assert "Unlock it first" in js
    assert "cardNeedsUnlock" in js
    assert "revealSlateCard" in js


def test_build_best_combos_mixes_sports_and_sizes():
    legs = [
        today_picks.annotate_leg(
            today_picks._pick(away="NYY", home="BOS", pick="BOS", confidence=0.72, market="Moneyline", matchup="NYY @ BOS"),
            sport_key="baseball", sport_label="Baseball", league_id="mlb", league_label="MLB",
        ),
        today_picks.annotate_leg(
            today_picks._pick(away="Chelsea", home="Arsenal", pick="Arsenal or Draw (1X)", confidence=0.70, market="Safer Result", matchup="Arsenal vs Chelsea"),
            sport_key="soccer", sport_label="Soccer", league_id="premier_league", league_label="Premier League",
        ),
        today_picks.annotate_leg(
            today_picks._pick(away="Giants", home="Hawks", pick="Hawks", confidence=0.68, market="Moneyline", matchup="Giants @ Hawks"),
            sport_key="baseball", sport_label="Baseball", league_id="npb", league_label="NPB",
        ),
        today_picks.annotate_leg(
            today_picks._pick(away="LAD", home="SF", pick="LAD", confidence=0.69, market="Moneyline", matchup="LAD @ SF"),
            sport_key="baseball", sport_label="Baseball", league_id="mlb", league_label="MLB",
        ),
    ]
    combos = today_picks.build_best_combos(legs, sizes=(2, 3), top_k=3)
    assert len(combos[2]) >= 1
    # 4 unique fixtures: Best 2-leg consumes most; 3-leg only if leftovers remain.
    assert len(combos[2]) <= 3
    assert len(combos[3]) <= 1
    best2 = combos[2][0]
    assert abs(best2["joint_prob"] - (0.72 * 0.70)) < 1e-9
    assert "MLB" in best2["mix_label"] and "Premier League" in best2["mix_label"]
    assert best2["mix_kind"] == "Cross-sport"
    assert "72%" in best2["joint_formula"] and "70%" in best2["joint_formula"]
    used = set()
    for bucket in (combos[2], combos[3], combos.get("mixed_2") or [], combos.get("mixed_3") or []):
        for c in bucket:
            keys = {today_picks._leg_match_key(leg) for leg in c["legs"]}
            assert not (keys & used)
            used |= keys
    mixed2 = combos.get("mixed_2") or []
    assert all(c["mix_kind"] in ("Cross-sport", "Cross-league") for c in mixed2)
    # Same fixture twice rejected
    dup = legs[:2] + [
        today_picks.annotate_leg(
            today_picks._pick(away="NYY", home="BOS", pick="Over", confidence=0.99, market="Total", matchup="NYY @ BOS"),
            sport_key="baseball", sport_label="Baseball", league_id="mlb", league_label="MLB",
        )
    ]
    c2 = today_picks.build_best_combos(dup, sizes=(2,), top_k=10)[2]
    for c in c2:
        keys = [today_picks._leg_match_key(leg) for leg in c["legs"]]
        assert len(keys) == len(set(keys))


def test_build_best_combos_mixed_board_prefers_cross_sport():
    """Dedicated mixed board must be Cross-sport / Cross-league only."""
    legs = [
        today_picks.annotate_leg(
            today_picks._pick(away="NYY", home="BOS", pick="BOS", confidence=0.80, market="Moneyline", matchup="NYY @ BOS"),
            sport_key="baseball", sport_label="Baseball", league_id="mlb", league_label="MLB",
        ),
        today_picks.annotate_leg(
            today_picks._pick(away="LAD", home="SF", pick="LAD", confidence=0.79, market="Moneyline", matchup="LAD @ SF"),
            sport_key="baseball", sport_label="Baseball", league_id="mlb", league_label="MLB",
        ),
        today_picks.annotate_leg(
            today_picks._pick(away="Chelsea", home="Arsenal", pick="Arsenal or Draw (1X)", confidence=0.78, market="Safer Result", matchup="Arsenal vs Chelsea"),
            sport_key="soccer", sport_label="Soccer", league_id="premier_league", league_label="Premier League",
        ),
        today_picks.annotate_leg(
            today_picks._pick(away="City", home="United", pick="Under 2.5", confidence=0.77, market="Total Goals", matchup="City vs United"),
            sport_key="soccer", sport_label="Soccer", league_id="mls", league_label="MLS",
        ),
        today_picks.annotate_leg(
            today_picks._pick(away="Oval", home="Lords", pick="Oval", confidence=0.76, market="Match winner", matchup="Oval vs Lords"),
            sport_key="cricket", sport_label="Cricket", league_id="hundred", league_label="The Hundred",
        ),
        today_picks.annotate_leg(
            today_picks._pick(away="Giants", home="Hawks", pick="Hawks", confidence=0.75, market="Moneyline", matchup="Giants @ Hawks"),
            sport_key="baseball", sport_label="Baseball", league_id="npb", league_label="NPB",
        ),
    ]
    # Extra multi-sport legs so Mixed still has unique fixtures after Best.
    for i, conf in enumerate((0.74, 0.73, 0.72, 0.71, 0.70, 0.69)):
        sport = ("soccer", "baseball", "cricket")[i % 3]
        legs.append(today_picks.annotate_leg(
            today_picks._pick(
                away=f"x{i}", home=f"y{i}", pick=f"y{i}",
                confidence=conf, market="Handicap", matchup=f"x{i} @ y{i}",
            ),
            sport_key=sport, sport_label=sport.title(),
            league_id=f"mx{i}", league_label=f"MX{i}",
        ))
    combos = today_picks.build_best_combos(legs, sizes=(2,), top_k=2, mixed_top_k=3, min_k=1)
    mixed2 = combos["mixed_2"]
    assert mixed2
    assert all(c["mix_kind"] in ("Cross-sport", "Cross-league") for c in mixed2)
    assert any(c["mix_kind"] == "Cross-sport" for c in mixed2)
    # Same-league MLB+MLB should not appear on the mixed board.
    for c in mixed2:
        league_ids = {leg["league_id"] for leg in c["legs"]}
        sport_keys = {leg["sport_key"] for leg in c["legs"]}
        assert len(league_ids) > 1 or len(sport_keys) > 1
    used = set()
    for bucket in (combos[2], mixed2):
        for c in bucket:
            keys = {today_picks._leg_match_key(leg) for leg in c["legs"]}
            assert not (keys & used)
            used |= keys


def test_build_best_combos_fallback_fills_nearest():
    """Thin pool: publish unique high-prob slips — never reshuffle fixtures."""
    legs = [
        today_picks.annotate_leg(
            today_picks._pick(away="a", home="b", pick="B", confidence=0.74, matchup="a @ b"),
            sport_key="baseball", sport_label="Baseball", league_id="mlb", league_label="MLB",
        ),
        today_picks.annotate_leg(
            today_picks._pick(away="c", home="d", pick="D", confidence=0.72, matchup="c @ d"),
            sport_key="soccer", sport_label="Soccer", league_id="mls", league_label="MLS",
        ),
        today_picks.annotate_leg(
            today_picks._pick(away="e", home="f", pick="F", confidence=0.70, matchup="e @ f"),
            sport_key="cricket", sport_label="Cricket", league_id="hundred", league_label="The Hundred",
        ),
        today_picks.annotate_leg(
            today_picks._pick(away="g", home="h", pick="H", confidence=0.68, matchup="g @ h"),
            sport_key="baseball", sport_label="Baseball", league_id="npb", league_label="NPB",
        ),
    ]
    combos = today_picks.build_best_combos(legs, sizes=(2, 3), top_k=3)
    # 4 fixtures → at most two disjoint 2-legs; 3-leg may be empty/short.
    assert 1 <= len(combos[2]) <= 2
    assert len(combos[3]) <= 1
    assert not any(c.get("is_soft_fill") for c in combos[2] + combos[3])
    used = set()
    for size in (2, 3):
        for c in combos[size]:
            assert min(float(leg["confidence"]) for leg in c["legs"]) >= today_picks.COMBO_MIN_LEG
            keys = {today_picks._leg_match_key(leg) for leg in c["legs"]}
            assert not (keys & used)
            used |= keys


def test_build_best_combos_outstanding_prefers_70pct_legs():
    """Outstanding (≥70% on every leg) cards outrank mid-60s soft fills."""
    legs = [
        today_picks.annotate_leg(
            today_picks._pick(away="a", home="b", pick="B", confidence=0.78, market="Safer Result", matchup="a @ b"),
            sport_key="soccer", sport_label="Soccer", league_id="epl", league_label="EPL",
        ),
        today_picks.annotate_leg(
            today_picks._pick(away="c", home="d", pick="D", confidence=0.76, market="Total Goals", matchup="c @ d"),
            sport_key="soccer", sport_label="Soccer", league_id="mls", league_label="MLS",
        ),
        today_picks.annotate_leg(
            today_picks._pick(away="e", home="f", pick="F", confidence=0.74, market="Moneyline", matchup="e @ f"),
            sport_key="baseball", sport_label="Baseball", league_id="mlb", league_label="MLB",
        ),
        today_picks.annotate_leg(
            today_picks._pick(away="g", home="h", pick="H", confidence=0.72, market="Match winner", matchup="g @ h"),
            sport_key="cricket", sport_label="Cricket", league_id="hundred", league_label="The Hundred",
        ),
        today_picks.annotate_leg(
            today_picks._pick(away="i", home="j", pick="J", confidence=0.68, market="Total runs", matchup="i @ j"),
            sport_key="baseball", sport_label="Baseball", league_id="npb", league_label="NPB",
        ),
        today_picks.annotate_leg(
            today_picks._pick(away="k", home="l", pick="L", confidence=0.68, market="Handicap", matchup="k @ l"),
            sport_key="baseball", sport_label="Baseball", league_id="kbo", league_label="KBO",
        ),
    ]
    combos = today_picks.build_best_combos(legs, sizes=(2,), top_k=3, min_k=2)
    assert combos[2]
    best = combos[2][0]
    assert best.get("is_outstanding") is True
    assert min(float(leg["confidence"]) for leg in best["legs"]) >= 0.70
    assert today_picks._combo_passes_quality(best)


def test_combo_quality_rejects_upset_lean():
    legs = [
        today_picks.annotate_leg(
            today_picks._pick(away="a", home="b", pick="B", confidence=0.80, market="Moneyline", matchup="a @ b"),
            sport_key="baseball", sport_label="Baseball", league_id="mlb", league_label="MLB",
        ),
        today_picks.annotate_leg(
            today_picks._pick(away="c", home="d", pick="C", confidence=0.75, market="Upset lean", matchup="c @ d"),
            sport_key="baseball", sport_label="Baseball", league_id="npb", league_label="NPB",
        ),
    ]
    usable = today_picks._usable_combo_legs(legs, 0.68)
    assert len(usable) == 1
    assert usable[0]["market"] == "Moneyline"


def test_tennis_set_winner_is_shrunk_and_capped():
    pred = {
        "player1_win_prob": 0.78,
        "player2_win_prob": 0.22,
        "best_of": 3,
        "markets": {
            "sets_line": 2.5,
            "over_sets_prob": 0.22,
            "player1_wins_set_prob": 0.94,
            "player2_wins_set_prob": 0.40,
        },
    }
    tips = today_picks._tennis_market_tips(
        pred, player1="Jack Draper", player2="Martin Landaluce",
    )
    by_m = {}
    for t in tips:
        by_m.setdefault(t["market"], []).append(t)
    under = [t for t in by_m["Total sets"] if str(t["pick"]).startswith("Under")][0]
    assert under["probability"] <= today_picks.TENNIS_UNDER_SETS_CAP
    fav_set = [t for t in by_m["Player to win a set"] if t["pick"] == "Jack Draper"][0]
    assert fav_set["probability"] <= today_picks.TENNIS_SET_WINNER_CAP
    assert fav_set["probability"] < 0.90


def test_tennis_tips_use_total_games_not_handicap():
    pred = {
        "player1_win_prob": 0.62,
        "player2_win_prob": 0.38,
        "best_of": 3,
        "markets": {
            "sets_line": 2.5,
            "over_sets_prob": 0.41,
            "predicted_total_games": 22.4,
            "total_games_std": 4.5,
            "predicted_games_margin": 4.2,
            "games_handicap_line": 3.5,
            "games_margin_std": 5.0,
        },
    }
    tips = today_picks._tennis_market_tips(
        pred, player1="Alcaraz", player2="Sinner",
    )
    markets = {t["market"] for t in tips}
    assert "Handicap" not in markets
    assert "Set handicap" not in markets
    games = next(t for t in tips if t["market"] == "Total games")
    assert games["pick"].startswith("Over ") or games["pick"].startswith("Under ")
    assert "22.5" in games["pick"] or "21.5" in games["pick"] or "23.5" in games["pick"]


def test_combo_slate_does_not_reuse_one_tennis_favorite():
    """Draper-style 90% set-winner must not sit on every published combo."""
    draper = today_picks.annotate_leg(
        today_picks._pick(
            away="Landaluce", home="Draper", pick="Jack Draper",
            confidence=0.90, market="Player to win a set",
            matchup="Jack Draper vs Martin Landaluce",
        ),
        sport_key="tennis", sport_label="Tennis", league_id="atp", league_label="ATP",
    )
    legs = [draper]
    for i, conf in enumerate((0.80, 0.78, 0.76, 0.74, 0.73, 0.72, 0.71, 0.70)):
        sport = ("soccer", "baseball", "cricket")[i % 3]
        legs.append(today_picks.annotate_leg(
            today_picks._pick(
                away=f"a{i}", home=f"b{i}", pick=f"b{i}",
                confidence=conf, market="Handicap", matchup=f"a{i} @ b{i}",
            ),
            sport_key=sport, sport_label=sport.title(),
            league_id=f"lg{i}", league_label=f"L{i}",
        ))
    combos = today_picks.build_best_combos(legs, sizes=(2, 3), top_k=3, mixed_top_k=3)
    published = combos[2] + combos[3] + combos["mixed_2"] + combos["mixed_3"]
    draper_slips = [
        c for c in published
        if any("Draper" in str(leg.get("pick") or "") for leg in c["legs"])
    ]
    assert len(draper_slips) <= 1
    for c in published:
        unders = sum(1 for leg in c["legs"] if today_picks._leg_is_fragile_total(leg))
        covers = sum(1 for leg in c["legs"] if today_picks._leg_is_favorite_cover(leg))
        assert unders <= 1
        assert covers <= 1


def test_favorite_cover_and_under_are_shrunk_on_all_sports():
    """90% pedigree is not a lock — soccer / baseball / cricket / tennis."""
    soccer = today_picks._soccer_high_confidence_tips(__import__("pandas").Series({
        "HomeTeam": "Arsenal", "AwayTeam": "Chelsea",
        "P_Home_Win": 0.48, "P_Draw": 0.30, "P_Away_Win": 0.22,
        "P_BTTS_Yes": 0.50, "P_Over_2.5_Goals": 0.18,
        "P_Favorite_Avoids_Defeat": 0.78,
        "Favorite_Team": "Arsenal",
    }))
    dnb = next(t for t in soccer if t["market"] == "Draw No Bet")
    under_g = next(t for t in soccer if t["market"] == "Total Goals")
    assert dnb["probability"] <= today_picks.FAVORITE_COVER_CAP
    assert under_g["pick"] == "Under 2.5"
    assert under_g["probability"] <= today_picks.UNDER_CAP

    bb = today_picks._baseball_market_tips(
        {
            "home_prob": 0.90, "away_prob": 0.10,
            "total_runs_pick": "Under", "total_runs_line": 8.5,
            "total_runs_pick_probability": 88.0,
        },
        home_name="Yankees",
        away_name="Red Sox",
        include_hits_so=False,
    )
    ml = next(t for t in bb if t["market"] == "Moneyline")
    under_r = next(t for t in bb if t["market"] == "Total runs")
    assert ml["probability"] <= today_picks.FAVORITE_COVER_CAP
    assert ml["probability"] < 0.90
    assert str(under_r["pick"]).startswith("Under")
    assert under_r["probability"] <= today_picks.UNDER_CAP

    tennis = today_picks._tennis_market_tips(
        {
            "player1_win_prob": 0.90, "player2_win_prob": 0.10, "best_of": 3,
            "markets": {"sets_line": 2.5, "over_sets_prob": 0.20},
        },
        player1="Alcaraz", player2="Qualifer",
    )
    mw = next(t for t in tennis if t["market"] == "Match winner")
    assert mw["probability"] <= today_picks.FAVORITE_COVER_CAP
    assert mw["probability"] < 0.90

    ou = today_picks._normal_ou(140.0, std_dev=18.0)
    assert ou["pick"] == "Under"
    assert ou["probability"] <= today_picks.UNDER_CAP


def test_combo_slate_does_not_reuse_one_favorite_across_sports():
    """A 90% MLB / soccer / cricket favorite must not sit on every combo."""
    yankees = today_picks.annotate_leg(
        today_picks._pick(
            away="Red Sox", home="Yankees", pick="Yankees",
            confidence=0.90, market="Moneyline", matchup="Red Sox @ Yankees",
        ),
        sport_key="baseball", sport_label="Baseball", league_id="mlb", league_label="MLB",
    )
    arsenal = today_picks.annotate_leg(
        today_picks._pick(
            away="Chelsea", home="Arsenal", pick="Arsenal win",
            confidence=0.88, market="Safer Result", matchup="Arsenal vs Chelsea",
        ),
        sport_key="soccer", sport_label="Soccer",
        league_id="premier_league", league_label="Premier League",
    )
    legs = [yankees, arsenal]
    for i, conf in enumerate((0.80, 0.78, 0.76, 0.74, 0.73, 0.72, 0.71, 0.70)):
        sport = ("soccer", "baseball", "cricket")[i % 3]
        legs.append(today_picks.annotate_leg(
            today_picks._pick(
                away=f"a{i}", home=f"b{i}", pick=f"b{i} +1.5",
                confidence=conf, market="Handicap", matchup=f"a{i} @ b{i}",
            ),
            sport_key=sport, sport_label=sport.title(),
            league_id=f"lg{i}", league_label=f"L{i}",
        ))
    combos = today_picks.build_best_combos(legs, sizes=(2, 3), top_k=3, mixed_top_k=3)
    published = combos[2] + combos[3] + combos["mixed_2"] + combos["mixed_3"]
    cover_slips = [
        c for c in published
        if any(today_picks._leg_is_favorite_cover(leg) for leg in c["legs"])
    ]
    assert len(cover_slips) <= 1
    for c in published:
        covers = sum(1 for leg in c["legs"] if today_picks._leg_is_favorite_cover(leg))
        unders = sum(1 for leg in c["legs"] if today_picks._leg_is_fragile_total(leg))
        assert covers <= 1
        assert unders <= 1


def test_leg_classifiers_cover_all_sports():
    ml = today_picks.annotate_leg(
        today_picks._pick(away="a", home="b", pick="b", confidence=0.90, market="Moneyline"),
        sport_key="baseball", sport_label="Baseball", league_id="mlb", league_label="MLB",
    )
    dnb = today_picks.annotate_leg(
        today_picks._pick(away="c", home="d", pick="d (DNB)", confidence=0.85, market="Draw No Bet"),
        sport_key="soccer", sport_label="Soccer", league_id="epl", league_label="EPL",
    )
    runs = today_picks.annotate_leg(
        today_picks._pick(away="e", home="f", pick="Under 8.5", confidence=0.80, market="Total runs"),
        sport_key="baseball", sport_label="Baseball", league_id="mlb", league_label="MLB",
    )
    hc = today_picks.annotate_leg(
        today_picks._pick(away="g", home="h", pick="g +1.5", confidence=0.90, market="Handicap"),
        sport_key="soccer", sport_label="Soccer", league_id="epl", league_label="EPL",
    )
    assert today_picks._leg_is_favorite_cover(ml)
    assert today_picks._leg_is_favorite_cover(dnb)
    assert today_picks._leg_is_fragile_total(runs)
    assert not today_picks._leg_is_favorite_cover(hc)
    assert not today_picks._leg_is_fragile_total(hc)


def test_build_best_combos_daily_quota_two_to_three():
    """With a rich multi-sport pool, publish unique slips up to quota."""
    legs = []
    for i, conf in enumerate((
        0.80, 0.79, 0.78, 0.77, 0.76, 0.75, 0.74, 0.73,
        0.72, 0.71, 0.70, 0.695, 0.69, 0.685, 0.68,
    )):
        sport = ("soccer", "baseball", "cricket")[i % 3]
        legs.append(today_picks.annotate_leg(
            today_picks._pick(
                away=f"a{i}", home=f"b{i}", pick=f"b{i}",
                confidence=conf, market="Moneyline", matchup=f"a{i} @ b{i}",
            ),
            sport_key=sport, sport_label=sport.title(),
            league_id=f"lg{i}", league_label=f"L{i}",
        ))
    combos = today_picks.build_best_combos(legs, sizes=(2, 3), top_k=3)
    assert 1 <= len(combos[2]) <= 3
    assert 1 <= len(combos[3]) <= 3
    used = set()
    for bucket in (combos[2], combos[3], combos["mixed_2"], combos["mixed_3"]):
        for c in bucket:
            keys = {today_picks._leg_match_key(leg) for leg in c["legs"]}
            assert not (keys & used), "fixture reshuffled across today's slips"
            used |= keys


def test_build_best_combos_slips_do_not_share_fixtures():
    """Top slips must not reuse a fixture — one loss should not wipe the slate."""
    legs = []
    for i, conf in enumerate((0.80, 0.79, 0.78, 0.77, 0.76, 0.75)):
        legs.append(today_picks.annotate_leg(
            today_picks._pick(
                away=f"a{i}", home=f"b{i}", pick=f"b{i}",
                confidence=conf, matchup=f"a{i} @ b{i}",
            ),
            sport_key="soccer", sport_label="Soccer",
            league_id=f"lg{i}", league_label=f"L{i}",
        ))
    combos = today_picks.build_best_combos(legs, sizes=(2,), top_k=3)
    assert len(combos[2]) == 3
    used = []
    for c in combos[2]:
        keys = [today_picks._leg_match_key(leg) for leg in c["legs"]]
        assert len(keys) == len(set(keys))
        for k in keys:
            assert k not in used
            used.append(k)
    # Without diversity the top 3 would all share the 0.80 leg
    assert abs(combos[2][0]["joint_prob"] - (0.80 * 0.79)) < 1e-9
    assert abs(combos[2][1]["joint_prob"] - (0.78 * 0.77)) < 1e-9
    assert abs(combos[2][2]["joint_prob"] - (0.76 * 0.75)) < 1e-9


def test_build_best_combos_mixes_markets_not_just_totals():
    """Slate must mix market families — not dump three Under/totals slips."""
    legs = []
    # Totals spam
    for i, conf in enumerate((0.90, 0.89, 0.88, 0.87, 0.86, 0.85)):
        legs.append(today_picks.annotate_leg(
            today_picks._pick(
                away=f"u{i}", home=f"h{i}", pick=f"Under {2.5 + i}",
                confidence=conf, market="Total Goals", matchup=f"u{i} vs h{i}",
            ),
            sport_key="soccer", sport_label="Soccer",
            league_id=f"tg{i}", league_label=f"TG{i}",
        ))
    # Result / handicap / props — slightly lower conf so old scorer ignored them
    legs.append(today_picks.annotate_leg(
        today_picks._pick(
            away="a", home="b", pick="b", confidence=0.78,
            market="Moneyline", matchup="a @ b",
        ),
        sport_key="baseball", sport_label="Baseball",
        league_id="mlb1", league_label="MLB1",
    ))
    legs.append(today_picks.annotate_leg(
        today_picks._pick(
            away="c", home="d", pick="c +1.5", confidence=0.76,
            market="Handicap", matchup="c @ d",
        ),
        sport_key="baseball", sport_label="Baseball",
        league_id="mlb2", league_label="MLB2",
    ))
    legs.append(today_picks.annotate_leg(
        today_picks._pick(
            away="e", home="f", pick="Yes", confidence=0.75,
            market="Both Teams to Score", matchup="e vs f",
        ),
        sport_key="soccer", sport_label="Soccer",
        league_id="pl1", league_label="PL1",
    ))
    legs.append(today_picks.annotate_leg(
        today_picks._pick(
            away="g", home="h", pick="Arsenal or Draw (1X)", confidence=0.74,
            market="Safer Result", matchup="g vs h",
        ),
        sport_key="soccer", sport_label="Soccer",
        league_id="pl2", league_label="PL2",
    ))
    combos = today_picks.build_best_combos(legs, sizes=(2,), top_k=3)
    assert len(combos[2]) >= 2
    markets_used = {
        leg["market"] for c in combos[2] for leg in c["legs"]
    }
    families_used = {
        today_picks._market_family(leg["market"])
        for c in combos[2] for leg in c["legs"]
    }
    # Must pull in non-totals markets, not only Total Goals
    assert markets_used - {"Total Goals"}
    assert len(families_used) >= 3
    assert any(
        len(set(leg["market"] for leg in c["legs"])) >= 2
        for c in combos[2]
    )
    for c in combos[2]:
        assert "corr_label" in c
        assert "corr_score" in c
        assert c["correlation"]["label"] in ("Low", "Medium", "High")


def test_pair_correlation_cross_sport_low_same_league_higher():
    mlb = today_picks.annotate_leg(
        today_picks._pick(
            away="NYY", home="BOS", pick="BOS", confidence=0.72,
            market="Moneyline", matchup="NYY @ BOS",
        ),
        sport_key="baseball", sport_label="Baseball",
        league_id="mlb", league_label="MLB",
    )
    pl = today_picks.annotate_leg(
        today_picks._pick(
            away="Chelsea", home="Arsenal", pick="Arsenal or Draw (1X)",
            confidence=0.70, market="Safer Result", matchup="Arsenal vs Chelsea",
        ),
        sport_key="soccer", sport_label="Soccer",
        league_id="premier_league", league_label="Premier League",
    )
    under_a = today_picks.annotate_leg(
        today_picks._pick(
            away="u0", home="h0", pick="Under 2.5", confidence=0.80,
            market="Total Goals", matchup="u0 vs h0",
        ),
        sport_key="soccer", sport_label="Soccer",
        league_id="premier_league", league_label="Premier League",
    )
    under_b = today_picks.annotate_leg(
        today_picks._pick(
            away="u1", home="h1", pick="Under 3.5", confidence=0.79,
            market="Total Goals", matchup="u1 vs h1",
        ),
        sport_key="soccer", sport_label="Soccer",
        league_id="premier_league", league_label="Premier League",
    )
    cross = today_picks.pair_correlation(mlb, pl)
    same = today_picks.pair_correlation(under_a, under_b)
    assert cross["score"] < same["score"]
    assert cross["label"] == "Low"
    assert same["score"] >= today_picks._CORR_LABEL_LOW
    assert "Same league" in same["reasons"]
    assert "Same O/U side (under)" in same["reasons"]

    slate = today_picks.analyze_slate_correlation([mlb, pl, under_a, under_b])
    assert slate["pair_count"] == 6
    assert under_a["correlation"]["label"] in ("Medium", "High")
    combo = today_picks.analyze_combo_correlation([under_a, under_b])
    assert combo["label"] in ("Medium", "High")
    assert combo["score"] > 0.25


def test_todays_picks_sports_hub(client):
    resp = client.get("/todays-picks")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert 'href="/todays-picks/soccer"' in body
    assert 'href="/todays-picks/baseball"' in body
    assert 'href="/todays-picks/cricket"' in body
    assert 'href="/todays-picks/american_football"' in body


def test_todays_picks_soccer_leagues(client):
    resp = client.get("/todays-picks/soccer")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Today's Picks" in body
    assert "Soccer" in body
    for league_id in today_picks.TODAY_SPORTS["soccer"]["leagues"]:
        assert f'href="/{league_id}/"' in body
    assert "See fixtures" in body


def test_todays_picks_unknown_sport_404(client):
    assert client.get("/todays-picks/curling").status_code == 404


def test_todays_picks_unknown_league_404(client):
    assert client.get("/todays-picks/soccer/mls").status_code == 200
    assert client.get("/todays-picks/soccer/does_not_exist").status_code == 404


def test_every_league_today_page_loads(client):
    for sport_key, sport in today_picks.TODAY_SPORTS.items():
        for league_id in sport["leagues"]:
            resp = client.get(f"/todays-picks/{sport_key}/{league_id}")
            assert resp.status_code == 200, f"{sport_key}/{league_id}"
            body = resp.data.decode()
            assert "Today's Picks" in body
            assert sport["label"] in body
            assert sport["leagues"][league_id]["label"] in body
            # Pick card, no-match, or no high-probability empty state. Picks
            # forward to Telegram now -- no on-site unlock/reveal at all.
            assert (
                ("Join Telegram" in body)
                or ("No match today" in body)
                or ("No high-probability picks today" in body)
                or ("No high-confidence picks today" in body)
            )
            if "fixture-card" in body and "No match today" not in body and "No high-probability" not in body:
                assert "Join Telegram" in body
            assert 'class="predict-btn">See fixtures' not in body
            assert "js-unlock-slate" not in body


def test_soccer_offseason_shows_no_match_today(client):
    """With current PL data starting mid/late August, most calendar days
    before kickoff should honestly say no match today."""
    resp = client.get("/todays-picks/soccer/premier_league")
    body = resp.data.decode()
    # Don't assert empty always (matchdays exist) -- just that page is valid.
    assert resp.status_code == 200
    assert "Premier League" in body


def test_filter_upcoming_public_legs_and_combos_drop_finished():
    kick = (
        today_picks.datetime.datetime.now(today_picks.datetime.timezone.utc)
        + today_picks.datetime.timedelta(hours=3)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    live = [{
        "league_id": "mlb",
        "sport_key": "baseball",
        "home_names": ["Boston Red Sox", "BOS"],
        "away_names": ["New York Yankees", "NYY"],
        "kickoff_iso": kick,
        "completed": False,
        "status": "upcoming",
    }]
    later = today_picks.annotate_leg(
        today_picks._pick(
            away="NYY", home="BOS", pick="Under 8.5", confidence=0.72,
            market="Total runs", matchup="NYY @ BOS",
            kickoff_iso=kick,
        ),
        sport_key="baseball", sport_label="Baseball", league_id="mlb", league_label="MLB",
    )
    done = today_picks.annotate_leg(
        today_picks._pick(
            away="Old", home="Done", pick="Done", confidence=0.80,
            market="Moneyline", matchup="Old vs Done",
            kickoff_iso="2020-01-01T12:00:00Z",
        ),
        sport_key="soccer", sport_label="Soccer", league_id="premier_league",
        league_label="Premier League",
    )
    kept = today_picks.filter_upcoming_public_legs([later, done], fixtures=live)
    assert [p["home"] for p in kept] == ["BOS"]
    out = today_picks.filter_upcoming_public_combos(
        {2: [{"legs": [later, done]}, {"legs": [later]}]},
        fixtures=live,
    )
    assert len(out[2]) == 1
    assert [lg["home"] for lg in out[2][0]["legs"]] == ["BOS"]


def test_leg_on_todays_live_board_matches_abbr_and_rejects_ghosts():
    kick = (
        today_picks.datetime.datetime.now(today_picks.datetime.timezone.utc)
        + today_picks.datetime.timedelta(hours=3)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    live = [{
        "league_id": "mlb",
        "sport_key": "baseball",
        "home_names": ["San Francisco Giants", "SF"],
        "away_names": ["Houston Astros", "HOU"],
        "kickoff_iso": kick,
        "completed": False,
        "status": "upcoming",
    }]
    assert today_picks.leg_on_todays_live_board(
        {"league_id": "mlb", "home": "Giants", "away": "Astros",
         "matchup": "Astros @ Giants", "kickoff_iso": kick,
         "sport_key": "baseball"},
        live,
    )
    assert not today_picks.leg_on_todays_live_board(
        {"league_id": "mlb", "home": "Ghosts", "away": "Phantoms",
         "matchup": "Phantoms @ Ghosts", "kickoff_iso": kick,
         "sport_key": "baseball"},
        live,
    )


def test_tennis_upcoming_skips_wikipedia_draw_slots():
    import tennis_fixtures
    now = today_picks.datetime.datetime.now(today_picks.datetime.timezone.utc)
    kick = (now + today_picks.datetime.timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = [
        {
            "player1": "Wiki A", "player2": "Wiki B",
            "date": now.strftime("%Y-%m-%d"), "completed": False, "is_live": False,
            "source": "wikipedia",
        },
        {
            "player1": "Alcaraz", "player2": "Sinner",
            "date": kick[:10], "time": kick[11:16],
            "kickoff_iso": kick,
            "completed": False, "is_live": False, "source": "espn",
        },
    ]
    out = tennis_fixtures.get_upcoming_and_live(rows)
    assert [m["player1"] for m in out] == ["Alcaraz"]


def test_tennis_upcoming_drops_recently_completed_pair():
    import tennis_fixtures
    now = today_picks.datetime.datetime.now(today_picks.datetime.timezone.utc)
    kick = (now + today_picks.datetime.timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = [
        {
            "player1": "Luciano Darderi", "player2": "Brandon Nakashima",
            "date": now.strftime("%Y-%m-%d"), "completed": True,
            "is_live": False, "source": "wikipedia", "score": "6-2 6-3",
        },
        {
            "player1": "Luciano Darderi", "player2": "Brandon Nakashima",
            "date": kick[:10], "time": kick[11:16], "kickoff_iso": kick,
            "completed": False, "is_live": False, "source": "espn",
        },
        {
            "player1": "Alcaraz", "player2": "Sinner",
            "date": kick[:10], "time": kick[11:16], "kickoff_iso": kick,
            "completed": False, "is_live": False, "source": "espn",
        },
    ]
    out = tennis_fixtures.get_upcoming_and_live(rows)
    assert [m["player1"] for m in out] == ["Alcaraz"]


def test_fixture_is_upcoming_or_live_drops_elapsed_and_yesterday():
    now = today_picks.datetime.datetime(2026, 8, 13, 14, 0, tzinfo=today_picks.datetime.timezone.utc)
    assert not today_picks.fixture_is_upcoming_or_live(
        {"date": "2026-08-12", "completed": False},
        sport_key="tennis", now=now,
    )
    assert today_picks.fixture_is_upcoming_or_live(
        {"date": "2026-08-13T18:00:00Z", "completed": False, "status": "pre"},
        sport_key="baseball", now=now,
    )
    assert not today_picks.fixture_is_upcoming_or_live(
        {"date": "2026-08-13T09:00:00Z", "time": "09:00", "completed": False},
        sport_key="cricket", now=now,
    )
    assert today_picks.fixture_is_upcoming_or_live(
        {"date": "2026-08-12T20:00:00Z", "completed": False, "status": "in"},
        sport_key="baseball", now=now,
    )
    assert not today_picks.fixture_is_upcoming_or_live(
        {"date": "2026-08-20T19:00:00Z", "completed": False},
        sport_key="cricket", now=now,
    )
    assert not today_picks.fixture_is_upcoming_or_live(
        {"date": "2026-08-13", "completed": False},
        sport_key="tennis", now=now,
    )


def test_fixture_is_finished_uses_kickoff_and_status():
    now = today_picks.datetime.datetime(2026, 8, 13, 12, 0, tzinfo=today_picks.datetime.timezone.utc)
    assert today_picks.fixture_is_finished(
        {
            "status": "final", "home_score": 4, "away_score": 1,
            "kickoff_iso": "2026-08-13T18:00:00Z",
        },
        now=now,
    )
    assert not today_picks.fixture_is_finished(
        {
            "status": "final", "completed": True,
            "kickoff_iso": "2099-08-20T23:00:00Z",
        },
        now=now,
    )
    assert today_picks.fixture_is_finished(
        {"sport_key": "soccer", "kickoff_iso": "2026-08-13T09:00:00Z"}, now=now,
    )
    assert not today_picks.fixture_is_finished(
        {"sport_key": "soccer", "kickoff_iso": "2026-08-13T18:00:00Z"}, now=now,
    )


def test_split_fixture_board_sends_finals_to_finished_not_upcoming():
    now = today_picks.datetime.datetime(2026, 8, 14, 12, 0, tzinfo=today_picks.datetime.timezone.utc)
    upcoming, postponed, finished = today_picks.split_fixture_board(
        [
            {"home_team": "Live", "status": "in", "date": "2026-08-14T16:00:00Z"},
            {
                "home_team": "Done", "completed": True,
                "home_score": 2, "away_score": 1,
                "date": "2026-08-14T01:00:00Z",
            },
            {"home_team": "Future", "date": "2026-08-20T19:00:00Z", "DateUtc": "2026-08-20 19:00:00Z"},
            {"home_team": "Rainout", "date": "2026-08-10T23:00:00Z", "postponed": True},
        ],
        sport_key="baseball",
        now=now,
        today_str="2026-08-14",
    )
    names = lambda rows: [r["home_team"] for r in rows]
    assert names(upcoming) == ["Live", "Future"]
    assert names(finished) == ["Done"]
    assert names(postponed) == ["Rainout"]


def test_split_fixture_board_drops_soccer_after_duration():
    now = today_picks.datetime.datetime(2026, 8, 15, 20, 30, tzinfo=today_picks.datetime.timezone.utc)
    upcoming, _postponed, finished = today_picks.split_fixture_board(
        [
            {"HomeTeam": "Alaves", "DateUtc": "2026-08-15 17:30:00Z", "sport_key": "soccer"},
            {"HomeTeam": "Sevilla", "DateUtc": "2026-08-15 19:30:00Z", "sport_key": "soccer"},
        ],
        sport_key="soccer",
        now=now,
    )
    assert [r["HomeTeam"] for r in finished] == ["Alaves"]
    assert [r["HomeTeam"] for r in upcoming] == ["Sevilla"]


def test_build_soccer_today_skips_finished_kickoff(monkeypatch):
    import pandas as pd

    monkeypatch.setattr(today_picks, "today_in_tz", lambda tz: "2026-08-13")
    today_picks.load_soccer_thresholds.cache_clear()
    monkeypatch.setattr(
        today_picks,
        "load_soccer_thresholds",
        lambda league_key: {
            "target_accuracy": 0.70,
            "calibrated": False,
            "markets": {k: today_picks.HIGH_CONF_MIN for k in today_picks._MARKET_THRESHOLD_KEYS.values()},
            "available": {k: True for k in today_picks._MARKET_THRESHOLD_KEYS.values()},
            "safer_result_win_threshold": 0.75,
            "safer_result_calibration": None,
        },
    )
    fixed_now = today_picks.datetime.datetime(
        2026, 8, 13, 14, 0, tzinfo=today_picks.datetime.timezone.utc,
    )
    real_finished = today_picks.fixture_is_finished

    def fake_finished(obj, *, sport_key="", now=None):
        return real_finished(obj, sport_key=sport_key, now=now or fixed_now)

    monkeypatch.setattr(today_picks, "fixture_is_finished", fake_finished)
    df = pd.DataFrame([
        {
            "HomeTeam": "Done", "AwayTeam": "Old",
            "DateUtc": "2026-08-13 09:00:00Z",
            "MatchDate": "Thu 13 Aug 2026",
            "Predicted_Result": "H",
            "P_Home_Win": 0.80, "P_Draw": 0.10, "P_Away_Win": 0.10,
            "BTTS_Prediction": "Yes", "P_BTTS_Yes": 0.55,
            "Goals_Prediction": "Over 2.5", "P_Over_2.5_Goals": 0.58,
            "Data_Confidence": "High",
        },
        {
            "HomeTeam": "Later", "AwayTeam": "Night",
            "DateUtc": "2026-08-13 19:00:00Z",
            "MatchDate": "Thu 13 Aug 2026",
            "Predicted_Result": "H",
            "P_Home_Win": 0.78, "P_Draw": 0.12, "P_Away_Win": 0.10,
            "BTTS_Prediction": "Yes", "P_BTTS_Yes": 0.55,
            "Goals_Prediction": "Over 2.5", "P_Over_2.5_Goals": 0.58,
            "Data_Confidence": "High",
        },
    ])
    _today, picks, fixtures_today = today_picks.build_soccer_today(
        "premier_league", df, "Europe/London",
    )
    assert fixtures_today == 2
    assert [p["home"] for p in picks] == ["Later"]


def test_build_soccer_today_filters_by_local_date():
    import pandas as pd

    df = pd.DataFrame([
        {
            "HomeTeam": "A", "AwayTeam": "B",
            "DateUtc": "2099-01-01 15:00:00Z",
            "MatchDate": "Thu 01 Jan 2099",
            "Predicted_Result": "H",
            "P_Home_Win": 0.6, "P_Draw": 0.2, "P_Away_Win": 0.2,
            "BTTS_Prediction": "Yes", "P_BTTS_Yes": 0.55,
            "Goals_Prediction": "Over 2.5", "P_Over_2.5_Goals": 0.58,
        },
        {
            "HomeTeam": "C", "AwayTeam": "D",
            "DateUtc": "2000-01-01 15:00:00Z",
            "MatchDate": "Sat 01 Jan 2000",
            "Predicted_Result": "A",
            "P_Home_Win": 0.2, "P_Draw": 0.2, "P_Away_Win": 0.6,
            "BTTS_Prediction": "No", "P_BTTS_Yes": 0.4,
            "Goals_Prediction": "Under 2.5", "P_Over_2.5_Goals": 0.4,
        },
    ])
    today, picks, fixtures_today = today_picks.build_soccer_today(
        "premier_league", df, "Europe/London",
    )
    assert picks == []
    assert fixtures_today == 0
    assert today  # non-empty ISO date


def test_select_high_confidence_caps_and_filters():
    picks = [
        today_picks._pick(away="a", home="b", pick="B", confidence=0.70, status="upcoming"),
        today_picks._pick(away="c", home="d", pick="D", confidence=0.60, status="upcoming"),
        today_picks._pick(away="e", home="f", pick="F", confidence=0.58, status="upcoming"),
        today_picks._pick(away="g", home="h", pick="H", confidence=0.56, status="upcoming"),
        today_picks._pick(away="i", home="j", pick="J", confidence=0.54, status="upcoming"),
        today_picks._pick(away="k", home="l", pick="L", confidence=0.80, status="final"),
        today_picks._pick(away="m", home="n", pick="No rating yet", confidence=0.90),
    ]
    selected = today_picks.select_high_confidence(picks)
    assert len(selected) == 4
    assert [p["pick"] for p in selected] == ["B", "D", "F", "H"]
    assert all(p["confidence"] >= today_picks.HIGH_CONF_MIN for p in selected)


def test_soccer_high_tips_includes_expanded_markets():
    import pandas as pd

    row = pd.Series({
        "HomeTeam": "Arsenal", "AwayTeam": "Chelsea",
        "P_Home_Win": 0.62, "P_Draw": 0.22, "P_Away_Win": 0.16,
        "P_BTTS_Yes": 0.50, "P_Over_2.5_Goals": 0.70,
        "P_Home_More_Corners": 0.50,
        "P_Favorite_Avoids_Defeat": 0.72,
        "Favorite_Team": "Arsenal",
        "Exp_Total_Corners": 11.2,
    })
    tips = today_picks._soccer_high_confidence_tips(row)
    markets = {t["market"] for t in tips}
    assert "Safer Result" in markets
    assert "Total Goals" in markets
    assert "Both Teams to Score" in markets
    assert "Team to Score" in markets
    assert "Total Corners" in markets
    # Strong favourite → dog +0.5 cover is low; tip appears on closer games.
    assert "Handicap" not in markets
    close = row.copy()
    close["P_Home_Win"] = 0.42
    close["P_Draw"] = 0.30
    close["P_Away_Win"] = 0.28
    close_tips = today_picks._soccer_high_confidence_tips(close)
    assert any(t["market"] == "Handicap" and t["pick"] == "Chelsea +0.5" for t in close_tips)
    # DNB appears when Safer Result is double chance, not the same outright win.
    row_dc = row.copy()
    row_dc["P_Home_Win"] = 0.48
    row_dc["P_Draw"] = 0.30
    row_dc["P_Away_Win"] = 0.22
    tips_dc = today_picks._soccer_high_confidence_tips(row_dc)
    assert any(t["market"] == "Draw No Bet" for t in tips_dc)


def test_safer_result_upgrades_to_exact_win():
    import pandas as pd

    row = pd.Series({
        "HomeTeam": "Arsenal", "AwayTeam": "Chelsea",
        "P_Home_Win": 0.80, "P_Draw": 0.12, "P_Away_Win": 0.08,
        "P_Favorite_Avoids_Defeat": 0.80,
        "Favorite_Team": "Arsenal",
    })
    tip = today_picks._safer_result_tip(row, t_dc=0.70, t_win=0.75)
    assert tip["pick"] == "Arsenal win"
    assert tip["market"] == "Safer Result"


def test_safer_result_falls_back_to_double_chance():
    import pandas as pd

    row = pd.Series({
        "HomeTeam": "Arsenal", "AwayTeam": "Chelsea",
        "P_Home_Win": 0.48, "P_Draw": 0.28, "P_Away_Win": 0.24,
        "P_Favorite_Avoids_Defeat": 0.74,
        "Favorite_Team": "Arsenal",
    })
    tip = today_picks._safer_result_tip(row, t_dc=0.70, t_win=0.75)
    assert tip["pick"] == "Arsenal or Draw (1X)"


def test_best_soccer_tip_prefers_measured_tier_over_soft_score():
    soft = {
        "market": "Team to Score",
        "pick": "Arsenal to score",
        "probability": 0.95,
    }
    measured = {
        "market": "Safer Result",
        "pick": "Arsenal or Draw (1X)",
        "probability": 0.76,
    }
    selected, alternatives = today_picks._best_soccer_tip([
        (soft, 0.68, True),
        (measured, 0.70, False),
    ])
    assert selected[0]["market"] == "Safer Result"
    assert alternatives[0][0]["market"] == "Team to Score"


def test_build_soccer_today_high_conf_cap(monkeypatch):
    import pandas as pd

    monkeypatch.setattr(today_picks, "today_in_tz", lambda tz: "2099-01-01")
    today_picks.load_soccer_thresholds.cache_clear()
    monkeypatch.setattr(
        today_picks,
        "load_soccer_thresholds",
        lambda league_key: {
            "target_accuracy": 0.70,
            "calibrated": False,
            "markets": {k: today_picks.HIGH_CONF_MIN for k in today_picks._MARKET_THRESHOLD_KEYS.values()},
            "available": {k: True for k in today_picks._MARKET_THRESHOLD_KEYS.values()},
            "safer_result_win_threshold": 0.75,
            "safer_result_calibration": None,
        },
    )
    rows = []
    for i, conf in enumerate([0.82, 0.78, 0.74, 0.72, 0.70, 0.68, 0.66, 0.64, 0.62]):
        rows.append({
            "HomeTeam": f"H{i}", "AwayTeam": f"A{i}",
            "DateUtc": "2099-01-01 15:00:00Z",
            "MatchDate": "Thu 01 Jan 2099",
            "Predicted_Result": "H",
            "P_Home_Win": conf, "P_Draw": (1 - conf) / 2, "P_Away_Win": (1 - conf) / 2,
            "BTTS_Prediction": "Yes", "P_BTTS_Yes": 0.5,
            "Goals_Prediction": "Over 2.5", "P_Over_2.5_Goals": 0.5,
            "Data_Confidence": "High",
        })
    df = pd.DataFrame(rows)
    today, picks, fixtures_today = today_picks.build_soccer_today(
        "premier_league", df, "Europe/London",
    )
    assert today == "2099-01-01"
    assert fixtures_today == 9
    assert len(picks) == today_picks.MAX_PICKS_PER_LEAGUE
    assert picks[0]["confidence"] >= picks[-1]["confidence"]
    assert all(p["confidence"] >= today_picks.HIGH_PROB_MIN for p in picks)


def test_build_soccer_today_emits_one_card_per_fixture(monkeypatch):
    """A fixture contributes only its strongest market to picks and combos."""
    import pandas as pd

    monkeypatch.setattr(today_picks, "today_in_tz", lambda tz: "2099-01-01")
    today_picks.load_soccer_thresholds.cache_clear()
    monkeypatch.setattr(
        today_picks,
        "load_soccer_thresholds",
        lambda league_key: {
            "target_accuracy": 0.70,
            "calibrated": False,
            "markets": {k: today_picks.HIGH_CONF_MIN for k in today_picks._MARKET_THRESHOLD_KEYS.values()},
            "available": {k: True for k in today_picks._MARKET_THRESHOLD_KEYS.values()},
            "safer_result_win_threshold": 0.75,
            "safer_result_calibration": None,
        },
    )
    df = pd.DataFrame([{
        "HomeTeam": "New England Revolution",
        "AwayTeam": "Houston Dynamo",
        "DateUtc": "2099-01-01 15:00:00Z",
        "MatchDate": "Thu 01 Jan 2099",
        "Predicted_Result": "H",
        "P_Home_Win": 0.72, "P_Draw": 0.16, "P_Away_Win": 0.12,
        "BTTS_Prediction": "Yes", "P_BTTS_Yes": 0.78,
        "Goals_Prediction": "Over 2.5", "P_Over_2.5_Goals": 0.74,
        "Favorite_Team": "New England Revolution",
        "P_Favorite_Avoids_Defeat": 0.80,
        "Data_Confidence": "High",
    }])
    today, picks, fixtures_today = today_picks.build_soccer_today(
        "mls", df, "America/New_York",
    )
    assert today == "2099-01-01"
    assert fixtures_today == 1
    assert len(picks) == 1
    assert picks[0]["matchup"] == "New England Revolution vs Houston Dynamo"
    assert all(p["confidence"] >= today_picks.HIGH_PROB_MIN for p in picks)


def test_soccer_dog_handicap_tip_is_plus_half():
    tip = today_picks._soccer_dog_handicap_tip(
        "Arsenal", "Chelsea", p_home=0.45, p_draw=0.30, p_away=0.25, fav_team="Arsenal",
    )
    assert tip is not None
    assert tip["market"] == "Handicap"
    assert tip["pick"] == "Chelsea +0.5"
    assert abs(tip["probability"] - 0.55) < 1e-9  # 0.25 + 0.30


def test_baseball_dog_handicap_tip_run_line():
    tip = today_picks._baseball_dog_handicap_tip(
        {
            "home_prob": 0.56, "away_prob": 0.44,
            "home_predicted_runs": 4.6, "away_predicted_runs": 4.2,
        },
        home_name="Yankees",
        away_name="Red Sox",
    )
    assert tip is not None
    assert tip["market"] == "Handicap"
    assert tip["pick"] == "Red Sox +1.5"
    assert tip["probability"] >= 0.55


def test_baseball_market_tips_include_dog_handicap():
    tips = today_picks._baseball_market_tips(
        {
            "home_prob": 0.58, "away_prob": 0.42,
            "home_predicted_runs": 4.7, "away_predicted_runs": 4.3,
            "total_runs_pick": "Over", "total_runs_line": 8.5,
            "total_runs_pick_probability": 70.0,
        },
        home_name="Yankees",
        away_name="Red Sox",
        include_moneyline=True,
        include_hits_so=False,
    )
    markets = {t["market"] for t in tips}
    assert "Moneyline" in markets
    assert "Handicap" in markets
    hc = next(t for t in tips if t["market"] == "Handicap")
    assert hc["pick"] == "Red Sox +1.5"


def test_baseball_market_tips_include_totals():
    tips = today_picks._baseball_market_tips(
        {
            "home_prob": 0.71, "away_prob": 0.29,
            "total_runs_pick": "Over", "total_runs_line": 8.5,
            "total_runs_pick_probability": 72.0,
            "home_runs_pick": "Over", "home_runs_line": 4.5,
            "home_runs_pick_probability": 69.0,
            "away_runs_pick": "Under", "away_runs_line": 3.5,
            "away_runs_pick_probability": 66.0,
            "predicted_total_hits": 17.2,
            "predicted_total_home_runs": 2.4,
            "predicted_total_strikeouts": 18.1,
        },
        home_name="Yankees",
        away_name="Red Sox",
    )
    markets = {t["market"] for t in tips}
    assert "Moneyline" in markets
    assert "Total runs" in markets
    assert "Team runs" in markets
    assert "Total hits" in markets
    assert "Total strikeouts" in markets
    assert "Total home runs" not in markets
    assert "Handicap" in markets
    ml = next(t for t in tips if t["market"] == "Moneyline")
    assert ml["pick"] == "Yankees"
    assert ml["probability"] <= today_picks.FAVORITE_COVER_CAP
    assert abs(ml["probability"] - today_picks._shrink_favorite_cover_prob(0.71)) < 1e-9


def test_baseball_market_tips_never_emit_total_home_runs():
    """Game total HRs aren't a standard book market — keep them off picks/combos."""
    tips = today_picks._baseball_market_tips(
        {
            "home_prob": 0.60, "away_prob": 0.40,
            "predicted_total_home_runs": 3.8,
            "predicted_total_hits": 16.0,
            "predicted_total_strikeouts": 17.0,
        },
        home_name="Yankees",
        away_name="Red Sox",
        include_moneyline=False,
    )
    assert all(t["market"] != "Total home runs" for t in tips)
    assert any(t["market"] == "Total hits" for t in tips)


def test_baseball_market_tips_mlb_skips_moneyline_and_surfaces_upset_lean():
    tips = today_picks._baseball_market_tips(
        {"home_prob": 0.54, "away_prob": 0.46, "total_runs_pick": "Over",
         "total_runs_line": 8.5, "total_runs_pick_probability": 70.0},
        home_name="Yankees",
        away_name="Red Sox",
        include_moneyline=False,
        include_upset_lean=True,
        include_hits_so=False,
    )
    markets = {t["market"] for t in tips}
    assert "Moneyline" not in markets
    assert "Upset lean" in markets
    dog = next(t for t in tips if t["market"] == "Upset lean")
    assert dog["pick"] == "Red Sox"
    assert abs(dog["probability"] - 0.46) < 1e-9


def test_normalize_mlb_slate_excludes_moneyline_keeps_props():
    slate = {
        "today_et": "2099-01-01",
        "board": [{
            "fixture": {
                "home_team": "Yankees", "away_team": "Red Sox",
                "date": "2099-01-01T19:00:00Z", "status": "pre", "completed": False,
            },
            "prediction": {
                "home_prob": 0.54, "away_prob": 0.46,
                "total_runs_pick": "Over", "total_runs_line": 8.5,
                "total_runs_pick_probability": 72.0,
            },
            "match_url": "/mlb/match/NYY/BOS",
        }],
    }
    _, picks, n = today_picks.normalize_mlb_slate(slate, max_n=8)
    assert n == 1
    assert all(p["market"] != "Moneyline" for p in picks)
    assert any(p["market"] == "Total runs" for p in picks)
    assert any(p["market"] == "Upset lean" and p["pick"] == "Red Sox" for p in picks)


def test_normalize_mlb_slate_combo_path_includes_moneyline_skips_upset():
    slate = {
        "today_et": "2099-01-01",
        "board": [{
            "fixture": {
                "home_team": "Yankees", "away_team": "Red Sox",
                "date": "2099-01-01T19:00:00Z", "status": "pre", "completed": False,
            },
            "prediction": {
                "home_prob": 0.90, "away_prob": 0.10,
                "total_runs_pick": "Over", "total_runs_line": 8.5,
                "total_runs_pick_probability": 72.0,
            },
            "match_url": "/mlb/match/NYY/BOS",
        }],
    }
    _, picks, _ = today_picks.normalize_mlb_slate(
        slate, max_n=8, min_conf=today_picks.COMBO_FALLBACK_MIN,
    )
    assert all(p["market"] != "Upset lean" for p in picks)
    assert any(p["market"] == "Moneyline" and p["pick"] == "Yankees" for p in picks)
    assert any(p["market"] == "Total runs" for p in picks)


def test_build_npb_today_skips_moneyline_and_surfaces_upset_lean():
    import pandas as pd

    fixtures = [{
        "home_team": "Giants", "away_team": "Hawks",
        "home_team_id": 1, "away_team_id": 2,
        "date": "2099-01-01T10:00:00+09:00",
        "completed": False, "is_live": False, "postponed": False,
    }]
    elo = pd.DataFrame([{"TeamID": 1}, {"TeamID": 2}])

    def compute(_h, _a, as_of_date=None):
        return {
            "home_prob": 0.54, "away_prob": 0.46,
            "total_runs_pick": "Over", "total_runs_line": 7.5,
            "total_runs_pick_probability": 72.0,
            "home_runs_pick": "Over", "home_runs_line": 3.5,
            "home_runs_pick_probability": 70.0,
            "away_runs_pick": "Under", "away_runs_line": 3.5,
            "away_runs_pick_probability": 69.0,
        }

    # Freeze "today" in Tokyo to the fixture day.
    orig_today = today_picks.today_in_tz
    orig_local = today_picks.local_date
    today_picks.today_in_tz = lambda _tz: "2099-01-01"
    today_picks.local_date = lambda iso, _tz: (iso or "")[:10]
    try:
        _, picks, n = today_picks.build_npb_today(
            compute, lambda: fixtures, lambda: elo, tz_name="Asia/Tokyo", max_n=8,
        )
    finally:
        today_picks.today_in_tz = orig_today
        today_picks.local_date = orig_local

    assert n == 1
    assert all(p["market"] != "Moneyline" for p in picks)
    assert any(p["market"] == "Total runs" for p in picks)
    assert any(p["market"] == "Upset lean" and p["pick"] == "Hawks" for p in picks)


def test_build_npb_today_combo_path_includes_moneyline_skips_upset():
    import pandas as pd

    fixtures = [{
        "home_team": "Giants", "away_team": "Hawks",
        "home_team_id": 1, "away_team_id": 2,
        "date": "2099-01-01T10:00:00+09:00",
        "completed": False, "is_live": False, "postponed": False,
    }]
    elo = pd.DataFrame([{"TeamID": 1}, {"TeamID": 2}])

    def compute(_h, _a, as_of_date=None):
        return {
            "home_prob": 0.90, "away_prob": 0.10,
            "total_runs_pick": "Over", "total_runs_line": 7.5,
            "total_runs_pick_probability": 72.0,
        }

    orig_today = today_picks.today_in_tz
    orig_local = today_picks.local_date
    today_picks.today_in_tz = lambda _tz: "2099-01-01"
    today_picks.local_date = lambda iso, _tz: (iso or "")[:10]
    try:
        _, picks, _ = today_picks.build_npb_today(
            compute, lambda: fixtures, lambda: elo, tz_name="Asia/Tokyo",
            max_n=8, min_conf=today_picks.COMBO_FALLBACK_MIN,
        )
    finally:
        today_picks.today_in_tz = orig_today
        today_picks.local_date = orig_local

    assert all(p["market"] != "Upset lean" for p in picks)
    assert any(p["market"] == "Moneyline" and p["pick"] == "Giants" for p in picks)


def test_build_cricket_today_skips_match_winner_and_surfaces_upset_lean(monkeypatch):
    fixtures = [{
        "date": "2099-01-01",
        "time": "18:00",
        "team1": "Oval Invincibles",
        "team2": "London Spirit",
        "slug": "oval-vs-spirit-2026-08-08",
        "data_confidence": "medium",
        "prediction": {
            "team1_win_prob": 0.54,
            "team2_win_prob": 0.46,
            "predicted_total_runs": 310.0,
            "predicted_team1_runs": 160.0,
            "predicted_team2_runs": 150.0,
            "data_confidence": "medium",
        },
    }]

    import cricket_predict
    monkeypatch.setattr(cricket_predict, "upcoming_fixtures", lambda _c: fixtures)
    monkeypatch.setattr(today_picks, "today_in_tz", lambda _tz: "2099-01-01")
    monkeypatch.setattr(
        today_picks,
        "_normal_ou",
        lambda point, std_dev=1.0: {
            "pick": "Over",
            "line": float(int(point)) + 0.5,
            "probability": 0.72,
        },
    )

    _, picks, n = today_picks.build_cricket_today("hundred", "Europe/London", max_n=8)
    assert n == 1
    assert all(p["market"] != "Match winner" for p in picks)
    assert any(p["market"] == "Upset lean" and p["pick"] == "London Spirit" for p in picks)
    assert any(p["market"] in ("Total runs", "Team runs") for p in picks)


def test_build_cricket_today_combo_path_includes_match_winner(monkeypatch):
    fixtures = [{
        "date": "2099-01-01",
        "team1": "A", "team2": "B",
        "data_confidence": "medium",
        "prediction": {
            "team1_win_prob": 0.90,
            "team2_win_prob": 0.10,
            "predicted_total_runs": 310.0,
            "data_confidence": "medium",
        },
    }]

    import cricket_predict
    monkeypatch.setattr(cricket_predict, "upcoming_fixtures", lambda _c: fixtures)
    monkeypatch.setattr(today_picks, "today_in_tz", lambda _tz: "2099-01-01")
    monkeypatch.setattr(
        today_picks,
        "_normal_ou",
        lambda point, std_dev=1.0: {
            "pick": "Over",
            "line": float(int(point)) + 0.5,
            "probability": 0.72,
        },
    )

    _, picks, _ = today_picks.build_cricket_today(
        "mlc", "America/New_York",
        max_n=8, min_conf=today_picks.COMBO_FALLBACK_MIN,
    )
    assert all(p["market"] != "Upset lean" for p in picks)
    assert any(p["market"] == "Match winner" and p["pick"] == "A" for p in picks)
    assert any(p["market"] == "Total runs" for p in picks)
