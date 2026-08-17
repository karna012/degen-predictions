"""Combo results snapshot + settlement."""
import base64

import app as app_module
import combo_results
import daily_digest
import today_picks


def _basic_auth_header(user, password):
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def test_cricket_rebrand_and_accent_name_match():
    assert combo_results._cricket_names_match("Jamaica Kingsmen", "Jamaica Tallawahs")
    assert combo_results._cricket_names_match("Barbados Tridents", "Barbados Royals won by 6 wickets")
    assert combo_results._cricket_names_match("Manchester Super Giants", "Manchester Originals")
    assert combo_results._names_match("Daniel Mérida", "Daniel Merida")
    assert combo_results._names_match("Saitama Seibu Lions", "Seibu Lions")


def test_hundred_lookup_uses_completed_fixtures(monkeypatch):
    monkeypatch.setattr(
        "hundred_fixtures.get_hundred_fixtures",
        lambda: [{
            "team1": "Trent Rockets",
            "team2": "Southern Brave",
            "team1_elo_name": "Trent Rockets",
            "team2_elo_name": "Southern Brave",
            "completed": True,
            "result": "Trent Rockets won by 12 runs",
            "score1": "150/6",
            "score2": "138/8",
        }],
    )
    score = combo_results._lookup_cricket({
        "league_id": "hundred",
        "home": "Trent Rockets",
        "away": "Southern Brave",
        "matchup": "Trent Rockets vs Southern Brave",
        "sport_key": "cricket",
    })
    assert score and score["completed"]
    st, _ = combo_results.settle_leg_against_score(
        "Match winner", "Trent Rockets", score,
    )
    assert st == combo_results.WON


def test_cpl_lookup_matches_rebrand_display_names(monkeypatch):
    monkeypatch.setattr(
        "cpl_fixtures.get_cpl_fixtures",
        lambda: [{
            "team1": "Jamaica Tallawahs",
            "team2": "Barbados Royals",
            "team1_elo_name": "Jamaica Tallawahs",
            "team2_elo_name": "Barbados Royals",
            "completed": True,
            "result": "Barbados Royals won by 5 wickets",
            "score1": "140/8",
            "score2": "141/5",
        }],
    )
    score = combo_results._lookup_cricket({
        "league_id": "cpl",
        "home": "Jamaica Kingsmen",
        "away": "Barbados Tridents",
        "matchup": "Jamaica Kingsmen vs Barbados Tridents",
        "sport_key": "cricket",
    })
    assert score and score["completed"]
    st, _ = combo_results.settle_leg_against_score(
        "Match winner", "Barbados Tridents", score,
    )
    assert st == combo_results.WON


def test_tennis_lookup_skips_old_completed_when_today_still_pending(monkeypatch):
    monkeypatch.setattr(
        combo_results,
        "_tennis_fixture_list",
        lambda days_back=2: [
            {
                "player1": "Titouan Droguet",
                "player2": "Luca Van Assche",
                "completed": True,
                "winner": "Titouan Droguet",
                "score": "6-4 2-6 7-6",
                "date": "2026-08-03",
            },
            {
                "player1": "L Van Assche",
                "player2": "T Droguet",
                "completed": False,
                "date": "2026-08-11",
            },
        ],
    )
    score = combo_results._lookup_tennis({
        "league_id": "atp",
        "home": "L Van Assche",
        "away": "T Droguet",
        "matchup": "L Van Assche vs T Droguet",
        "kickoff_iso": "2026-08-11",
        "sport_key": "tennis",
    })
    assert score and not score["completed"]


def test_tennis_lookup_uses_completed_near_kickoff(monkeypatch):
    monkeypatch.setattr(
        combo_results,
        "_tennis_fixture_list",
        lambda days_back=2: [{
            "player1": "Learner Tien",
            "player2": "Daniel Merida",
            "completed": True,
            "winner": "Learner Tien",
            "score": "6-3 6-2",
            "date": "2026-08-11",
        }],
    )
    score = combo_results._lookup_tennis({
        "league_id": "atp",
        "home": "Daniel Mérida",
        "away": "Learner Tien",
        "matchup": "Daniel Mérida vs Learner Tien",
        "kickoff_iso": "2026-08-11",
        "sport_key": "tennis",
    })
    assert score and score["completed"]
    assert combo_results._names_match(score["winner_name"], "Learner Tien")


def test_npb_lookup_prefers_completed_same_date(monkeypatch):
    monkeypatch.setattr(
        "npb_fixtures.get_fixtures",
        lambda days_back=7, resolve_pitchers=False: [
            {
                "date": "2026-08-11T14:00:00",
                "home_team": "Chunichi Dragons",
                "away_team": "Yokohama DeNA BayStars",
                "completed": True,
                "postponed": False,
                "home_score": 5,
                "away_score": 2,
            },
            {
                "date": "2026-08-12T18:00:00",
                "home_team": "Chunichi Dragons",
                "away_team": "Yokohama DeNA BayStars",
                "completed": False,
                "postponed": False,
                "home_score": None,
                "away_score": None,
            },
        ],
    )
    score = combo_results._lookup_npb({
        "league_id": "npb",
        "home": "Chunichi Dragons",
        "away": "Yokohama DeNA BayStars",
        "kickoff_iso": "2026-08-11T14:00:00",
        "sport_key": "baseball",
    })
    assert score and score["completed"]
    assert score["home_score"] == 5 and score["away_score"] == 2


def test_mlb_lookup_prefers_completed_same_date(monkeypatch):
    monkeypatch.setattr(
        "mlb_fixtures.get_fixtures",
        lambda days_back=2: [
            {
                "date": "2026-08-11T23:00:00Z",
                "home_team": "New York Yankees",
                "away_team": "Boston Red Sox",
                "completed": True,
                "home_score": 6,
                "away_score": 2,
            },
            {
                "date": "2026-08-12T23:00:00Z",
                "home_team": "New York Yankees",
                "away_team": "Boston Red Sox",
                "completed": False,
                "home_score": None,
                "away_score": None,
            },
        ],
    )
    score = combo_results._lookup_mlb({
        "league_id": "mlb",
        "home": "New York Yankees",
        "away": "Boston Red Sox",
        "kickoff_iso": "2026-08-11T23:00:00Z",
        "sport_key": "baseball",
    })
    assert score and score["completed"]
    assert score["home_score"] == 6 and score["away_score"] == 2


def test_mlb_lookup_picks_closest_kickoff_not_earlier_series_game(monkeypatch):
    """Tue night and Wed afternoon share UTC date 2026-08-12."""
    monkeypatch.setattr(
        "mlb_fixtures.get_fixtures",
        lambda days_back=2: [
            {
                "date": "2026-08-12T01:45Z",
                "home_team": "San Francisco Giants",
                "away_team": "Houston Astros",
                "completed": True,
                "home_score": "4",
                "away_score": "1",
            },
            {
                "date": "2026-08-12T19:45Z",
                "home_team": "San Francisco Giants",
                "away_team": "Houston Astros",
                "completed": True,
                "home_score": "1",
                "away_score": "2",
            },
        ],
    )
    score = combo_results._lookup_mlb({
        "league_id": "mlb",
        "home": "San Francisco Giants",
        "away": "Houston Astros",
        "kickoff_iso": "2026-08-12T19:45Z",
        "sport_key": "baseball",
    })
    assert score and score["completed"]
    assert score["home_score"] == "1" and score["away_score"] == "2"
    st, _ = combo_results.settle_leg_against_score(
        "Handicap", "Houston Astros +1.5", score,
    )
    assert st == combo_results.WON


def test_soccer_team_match_rejects_united_last_token():
    assert combo_results._soccer_team_match("Manchester United", "Manchester United")
    assert not combo_results._soccer_team_match("Manchester United", "Newcastle United")
    assert combo_results._soccer_team_match("Atlanta United", "Atlanta")
    assert combo_results._soccer_team_match("FC Dallas", "Dallas")
    assert combo_results._soccer_team_match("Austin FC", "Austin")
    assert not combo_results._soccer_team_match("United", "Minnesota United")
    assert not combo_results._soccer_team_match("Diego", "San Diego FC")


def test_soccer_lookup_picks_closest_kickoff_not_earlier_h2h(monkeypatch):
    monkeypatch.setattr(
        "soccer_fixtures.get_feed",
        lambda league_id: [
            {
                "date": "2026-08-01T00:30:00Z",
                "DateUtc": "2026-08-01T00:30:00Z",
                "home": "Inter Miami",
                "away": "LA Galaxy",
                "completed": True,
                "home_score": 5,
                "away_score": 0,
            },
            {
                "date": "2026-08-16T02:30:00Z",
                "DateUtc": "2026-08-16T02:30:00Z",
                "home": "Inter Miami",
                "away": "LA Galaxy",
                "completed": True,
                "home_score": 1,
                "away_score": 2,
            },
        ],
    )
    score = combo_results._lookup_soccer({
        "league_id": "mls",
        "home": "Inter Miami",
        "away": "LA Galaxy",
        "kickoff_iso": "2026-08-16T02:30:00Z",
        "sport_key": "soccer",
    })
    assert score and score["completed"]
    assert score["home_score"] == 1 and score["away_score"] == 2


def test_soccer_lookup_refuses_unfinished_dallas_austin(monkeypatch):
    """Austin FC vs FC Dallas not final → never invent a result from history."""
    monkeypatch.setattr(
        "soccer_fixtures.get_feed",
        lambda league_id: [{
            "date": "2026-08-17 00:30:00Z",
            "DateUtc": "2026-08-17 00:30:00Z",
            "home": "Austin FC",
            "away": "FC Dallas",
            "completed": False,
            "home_score": None,
            "away_score": None,
        }],
    )
    import pandas as pd
    monkeypatch.setattr(
        "match_context._load_league_history",
        lambda league_id: pd.DataFrame([{
            "Date": "2025-08-17",
            "HomeTeam": "Austin FC",
            "AwayTeam": "FC Dallas",
            "FTHG": 4,
            "FTAG": 0,
        }]),
    )
    import sys, types
    leagues_mod = types.ModuleType("leagues")
    leagues_mod.LEAGUES = {"mls": {"label": "MLS"}}
    monkeypatch.setitem(sys.modules, "leagues", leagues_mod)

    # Leg home/away swapped vs feed orientation (common OCR / pick order).
    score = combo_results._lookup_soccer({
        "league_id": "mls",
        "home": "FC Dallas",
        "away": "Austin FC",
        "kickoff_iso": "2026-08-17T00:30:00Z",
        "sport_key": "soccer",
    })
    assert score is None


def test_soccer_lookup_refuses_stale_history_without_near_date(monkeypatch):
    import pandas as pd

    monkeypatch.setattr("soccer_fixtures.get_feed", lambda league_id: [])
    monkeypatch.setattr(
        "match_context._load_league_history",
        lambda league_id: pd.DataFrame([{
            "Date": "2025-03-01",
            "HomeTeam": "Inter Miami",
            "AwayTeam": "LA Galaxy",
            "FTHG": 9,
            "FTAG": 0,
        }]),
    )
    import sys
    import types
    leagues_mod = types.ModuleType("leagues")
    leagues_mod.LEAGUES = {"mls": {"label": "MLS"}}
    monkeypatch.setitem(sys.modules, "leagues", leagues_mod)

    score = combo_results._lookup_soccer({
        "league_id": "mls",
        "home": "Inter Miami",
        "away": "LA Galaxy",
        "kickoff_iso": "2026-08-16T02:30:00Z",
        "sport_key": "soccer",
    })
    assert score is None


def test_list_degen_team_items_requires_telegram_message_ids(tmp_path, monkeypatch):
    db = tmp_path / "combo_results.db"
    monkeypatch.setattr(combo_results, "DB_PATH", str(db))
    combo_results.init_db()
    with_ids = combo_results.record_degen_team_slip(
        [{
            "matchup": "A vs B", "home": "A", "away": "B", "pick": "A",
            "market": "1X2", "sport_key": "soccer", "league_id": "mls",
            "kickoff_iso": "2026-08-16T00:00:00Z",
        }],
        title="Sent",
        telegram_message_ids=[101],
        slate_date="2026-08-16",
    )
    no_ids = combo_results.record_degen_team_slip(
        [{
            "matchup": "C vs D", "home": "C", "away": "D", "pick": "C",
            "market": "1X2", "sport_key": "soccer", "league_id": "mls",
            "kickoff_iso": "2026-08-16T00:00:00Z",
        }],
        title="Not sent",
        telegram_message_ids=[],
        slate_date="2026-08-16",
    )
    assert with_ids["ok"] and no_ids["ok"]
    items = combo_results.list_degen_team_items(days=60, limit=80)
    titles = {s.get("title") for s in items["singles"]}
    assert "Sent" in titles
    assert "Not sent" not in titles


def test_format_kickoff_ist_never_labels_utc():
    text = today_picks.format_kickoff_ist("2026-08-15 17:30:00Z")
    assert "IST" in text
    assert "UTC" not in text
    assert "23:00" in text  # 17:30 UTC → 23:00 IST


def test_settle_leg_moneyline_and_totals():
    score = {
        "home": "Yankees", "away": "Red Sox",
        "home_score": 5, "away_score": 3, "completed": True,
    }
    st, note = combo_results.settle_leg_against_score("Moneyline", "Yankees", score)
    assert st == combo_results.WON
    assert "5" in note

    st, _ = combo_results.settle_leg_against_score("Total runs", "Over 7.5", score)
    assert st == combo_results.WON
    st, _ = combo_results.settle_leg_against_score("Total runs", "Under 7.5", score)
    assert st == combo_results.LOST
    st, _ = combo_results.settle_leg_against_score("Total runs", "Over 8", score)
    assert st == combo_results.PUSH


def test_settle_safer_result_and_dnb():
    score = {
        "home": "Arsenal", "away": "Chelsea",
        "home_score": 1, "away_score": 1, "completed": True,
    }
    st, _ = combo_results.settle_leg_against_score(
        "Safer Result", "Arsenal or Draw (1X)", score,
    )
    assert st == combo_results.WON
    st, _ = combo_results.settle_leg_against_score(
        "Draw No Bet", "Arsenal (DNB)", score,
    )
    assert st == combo_results.PUSH
    st, _ = combo_results.settle_leg_against_score(
        "Safer Result", "Arsenal win", score,
    )
    assert st == combo_results.LOST


def test_settle_handicap_dog():
    score = {
        "home": "Dodgers", "away": "Giants",
        "home_score": 3, "away_score": 4, "completed": True,
    }
    # Dodgers dog +1.5: lost by 1 → covers
    st, _ = combo_results.settle_leg_against_score("Handicap", "Dodgers +1.5", score)
    assert st == combo_results.WON


def test_snapshot_and_settle_rollup(tmp_path, monkeypatch):
    db = tmp_path / "combo_results.db"
    monkeypatch.setattr(combo_results, "DB_PATH", str(db))
    combo_results.init_db()

    legs = [
        today_picks.annotate_leg(
            today_picks._pick(
                away="Red Sox", home="Yankees", pick="Yankees",
                confidence=0.72, market="Moneyline",
                matchup="Red Sox @ Yankees",
            ),
            sport_key="baseball", sport_label="Baseball",
            league_id="mlb", league_label="MLB",
        ),
        today_picks.annotate_leg(
            today_picks._pick(
                away="Chelsea", home="Arsenal", pick="Under 2.5",
                confidence=0.80, market="Total Goals",
                matchup="Arsenal vs Chelsea",
            ),
            sport_key="soccer", sport_label="Soccer",
            league_id="premier_league", league_label="Premier League",
        ),
    ]
    combos = today_picks.build_best_combos(legs, sizes=(2,), top_k=1, min_k=1)
    assert combos.get(2)
    n = combo_results.snapshot_published_combos(combos, slate_date="2026-08-10")
    assert n >= 1
    # Idempotent
    assert combo_results.snapshot_published_combos(combos, slate_date="2026-08-10") == 0

    # Force-settle by stubbing lookups
    def fake_lookup(leg):
        if leg.get("league_id") == "mlb":
            return {
                "home": "Yankees", "away": "Red Sox",
                "home_score": 5, "away_score": 2, "completed": True,
            }
        return {
            "home": "Arsenal", "away": "Chelsea",
            "home_score": 0, "away_score": 1, "completed": True,
        }

    monkeypatch.setattr(combo_results, "lookup_score_for_leg", fake_lookup)
    stats = combo_results.settle_pending()
    assert stats["slips_settled"] >= 1
    slips = combo_results.list_slips(days=60)
    assert slips
    assert slips[0]["status"] in (
        combo_results.WON, combo_results.LOST, combo_results.VOID,
    )
    summary = combo_results.summary(days=60)
    assert summary["decided"] + summary["pending"] + summary["void"] >= 1


def test_list_overdue_pending_flags_old_kickoffs(tmp_path, monkeypatch):
    db = tmp_path / "combo_results.db"
    monkeypatch.setattr(combo_results, "DB_PATH", str(db))
    combo_results.init_db()
    combo_results.snapshot_published_picks(
        [today_picks.annotate_leg(
            today_picks._pick(
                away="Trent Rockets", home="Southern Brave",
                pick="Southern Brave", confidence=0.72, market="Match winner",
                matchup="Trent Rockets vs Southern Brave",
                kickoff_iso="2026-08-01",
            ),
            sport_key="cricket", sport_label="Cricket",
            league_id="hundred", league_label="The Hundred",
        )],
        slate_date="2026-08-01",
    )
    overdue = combo_results.list_overdue_pending(grace_hours=6.0)
    assert overdue
    assert overdue[0]["league_id"] == "hundred"
    assert overdue[0]["kind"] == "pick"


def test_admin_combo_results_section(client, monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "ADMIN_USER", "admin")
    monkeypatch.setattr(app_module, "ADMIN_PASSWORD", "secret123")
    db = tmp_path / "combo_results.db"
    monkeypatch.setattr(combo_results, "DB_PATH", str(db))
    combo_results.init_db()
    auth = _basic_auth_header("admin", "secret123")
    assert client.get("/admin?section=combo_results").status_code == 302
    resp = client.get("/admin?section=combo_results&settle=0", headers=auth)
    assert resp.status_code == 200
    body = resp.data.decode()
    assert 'data-admin-section="combo_results"' in body
    assert "Combo results" in body
    assert "Hit rate" in body


def test_public_combo_results_page_is_hidden(client, monkeypatch, tmp_path):
    db = tmp_path / "combo_results.db"
    monkeypatch.setattr(combo_results, "DB_PATH", str(db))
    combo_results.init_db()
    resp = client.get("/todays-picks/combos/results")
    assert resp.status_code == 404


def test_admin_can_open_combo_results_page(client, monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "ADMIN_USER", "admin")
    monkeypatch.setattr(app_module, "ADMIN_PASSWORD", "secret123")
    db = tmp_path / "combo_results.db"
    monkeypatch.setattr(combo_results, "DB_PATH", str(db))
    combo_results.init_db()
    auth = _basic_auth_header("admin", "secret123")
    resp = client.get("/todays-picks/combos/results", headers=auth)
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Best Combos results" in body
    assert "Hit rate" in body


def test_cricket_rebrand_and_accent_name_match():
    assert combo_results._cricket_names_match("Jamaica Kingsmen", "Jamaica Tallawahs")
    assert combo_results._cricket_names_match(
        "Barbados Tridents", "Barbados Royals won by 6 wickets",
    )
    assert combo_results._cricket_names_match(
        "Manchester Super Giants", "Manchester Originals",
    )
    assert combo_results._names_match("Daniel Mérida", "Daniel Merida")
    assert combo_results._names_match("Saitama Seibu Lions", "Seibu Lions")


def test_hundred_lookup_uses_completed_fixtures(monkeypatch):
    monkeypatch.setattr(
        "hundred_fixtures.get_hundred_fixtures",
        lambda: [{
            "team1": "Trent Rockets",
            "team2": "Southern Brave",
            "team1_elo_name": "Trent Rockets",
            "team2_elo_name": "Southern Brave",
            "completed": True,
            "result": "Trent Rockets won by 12 runs",
            "score1": "150/6",
            "score2": "138/8",
        }],
    )
    score = combo_results._lookup_cricket({
        "league_id": "hundred",
        "home": "Trent Rockets",
        "away": "Southern Brave",
        "matchup": "Trent Rockets vs Southern Brave",
        "sport_key": "cricket",
    })
    assert score and score["completed"]
    st, _ = combo_results.settle_leg_against_score(
        "Match winner", "Trent Rockets", score,
    )
    assert st == combo_results.WON


def test_cpl_lookup_matches_rebrand_display_names(monkeypatch):
    monkeypatch.setattr(
        "cpl_fixtures.get_cpl_fixtures",
        lambda: [{
            "team1": "Jamaica Tallawahs",
            "team2": "Barbados Royals",
            "team1_elo_name": "Jamaica Tallawahs",
            "team2_elo_name": "Barbados Royals",
            "completed": True,
            "result": "Barbados Royals won by 5 wickets",
            "score1": "140/8",
            "score2": "141/5",
        }],
    )
    score = combo_results._lookup_cricket({
        "league_id": "cpl",
        "home": "Jamaica Kingsmen",
        "away": "Barbados Tridents",
        "matchup": "Jamaica Kingsmen vs Barbados Tridents",
        "sport_key": "cricket",
    })
    assert score and score["completed"]
    st, _ = combo_results.settle_leg_against_score(
        "Match winner", "Barbados Tridents", score,
    )
    assert st == combo_results.WON

def test_degen_team_legacy_rows_excluded_from_board_feedback(tmp_path, monkeypatch):
    """Legacy Telegram ledger rows must not affect public/board feedback."""
    db = tmp_path / "combo_results.db"
    monkeypatch.setattr(combo_results, "DB_PATH", str(db))
    combo_results.init_db()

    legs = [
        {
            "sport_key": "baseball", "league_id": "mlb", "league_label": "MLB",
            "matchup": "Red Sox @ Yankees", "home": "Yankees", "away": "Red Sox",
            "market": "Moneyline", "pick": "Yankees", "confidence": 0.72,
        },
        {
            "sport_key": "soccer", "league_id": "premier_league",
            "league_label": "Premier League",
            "matchup": "Arsenal vs Chelsea", "home": "Arsenal", "away": "Chelsea",
            "market": "Total Goals", "pick": "Under 2.5", "confidence": 0.70,
        },
    ]
    rec = combo_results.record_degen_team_slip(
        legs, title="2-leg slip", telegram_message_ids=[101, 102],
        slate_date="2026-08-11",
    )
    assert rec["ok"] and rec["kind"] == "combo"
    combo_results.manual_settle_combo_slip(rec["id"], "won")

    single = combo_results.record_degen_team_slip(
        [{
            "sport_key": "cricket", "league_id": "cpl", "league_label": "CPL",
            "matchup": "A vs B", "home": "B", "away": "A",
            "market": "Match winner", "pick": "B", "confidence": 0.71,
        }],
        title="Single",
        slate_date="2026-08-11",
    )
    combo_results.manual_settle_pick(single["id"], "lost")

    assert combo_results.list_slips(days=60) == []
    assert combo_results.summary(days=60)["total"] == 0
    only_dt = combo_results.list_slips(
        days=60, source=combo_results.SOURCE_DEGEN_TEAM, exclude_source=None,
    )
    assert len(only_dt) == 1

    report = combo_results.feedback_report(days=60)
    assert report["overall"]["n"] == 0
    assert "degen_team" not in report


def test_select_telegram_results_keeps_all_wins_caps_losses():
    items = [
        {"id": 1, "status": "won", "title": "Best 2-leg", "legs": [{}, {}]},
        {"id": 2, "status": "won", "title": "Mixed 2-leg", "legs": [{}, {}]},
        {"id": 3, "status": "lost", "title": "Today's Picks", "legs": [{}, {}, {}]},
        {"id": 4, "status": "lost", "title": "Best 3-leg", "legs": [{}, {}, {}]},
        {"id": 5, "status": "lost", "title": "Best 2-leg", "legs": [{}, {}]},
        {"id": 6, "status": "void", "title": "Void slip", "legs": [{}]},
    ]
    post, skip = combo_results.select_telegram_results_for_channel(
        items, max_losses=2, losses_already_posted=0,
    )
    assert [i["id"] for i in post if i["status"] == "won"] == [1, 2]
    loss_ids = [i["id"] for i in post if i["status"] == "lost"]
    assert len(loss_ids) == 2
    assert 3 in loss_ids  # Today's Picks preferred
    assert 6 in [i["id"] for i in skip]
    assert len(skip) == 2  # one extra loss + void

    post2, skip2 = combo_results.select_telegram_results_for_channel(
        items, max_losses=2, losses_already_posted=2,
    )
    assert all(i["status"] == "won" for i in post2)
    assert len([i for i in skip2 if i["status"] == "lost"]) == 3


def test_telegram_result_reply_due_after_settle(tmp_path, monkeypatch):
    """Settled channel slips with message ids become due for a RESULT reply."""
    db = tmp_path / "combo_results.db"
    monkeypatch.setattr(combo_results, "DB_PATH", str(db))
    combo_results.init_db()

    rec = combo_results.record_degen_team_slip(
        [{
            "sport_key": "soccer", "league_id": "mls", "league_label": "MLS",
            "matchup": "A vs B", "home": "A", "away": "B",
            "market": "1X2", "pick": "A", "confidence": 0.71,
        }],
        title="Single",
        telegram_message_ids=[555],
        slate_date="2026-08-12",
    )
    assert rec["ok"] and rec["kind"] == "single"
    assert combo_results.list_telegram_results_due() == []

    combo_results.manual_settle_pick(rec["id"], "won", note="2-1")
    due = combo_results.list_telegram_results_due()
    assert len(due) == 1
    assert due[0]["id"] == rec["id"]
    assert due[0]["status"] == "won"
    text = combo_results.format_telegram_result_text(due[0])
    assert "RESULT: WON" in text
    assert "A vs B" in text
    assert "2 - 1" in text

    combo_results.mark_telegram_result_posted("single", rec["id"])
    assert combo_results.list_telegram_results_due() == []


def test_event_name_and_score_line_home_away_format():
    leg = {
        "home": "Tampa Bay Rays",
        "away": "Boston Red Sox",
        "matchup": "Boston Red Sox @ Tampa Bay Rays",
        "actual_note": "5–3",
    }
    assert daily_digest._event_name(leg) == "Tampa Bay Rays vs Boston Red Sox"
    assert daily_digest._score_line(leg) == "5 - 3"
    leg2 = {"home": "A", "away": "B", "home_score": 2, "away_score": 1}
    assert daily_digest._score_line(leg2) == "2 - 1"


def test_post_due_telegram_results_replies_once(tmp_path, monkeypatch):
    import daily_digest

    db = tmp_path / "combo_results.db"
    monkeypatch.setattr(combo_results, "DB_PATH", str(db))
    combo_results.init_db()
    rec = combo_results.record_degen_team_from_telegram_slips([{
        "title": "2-leg slip",
        "telegram_message_id": 901,
        "legs": [
            {
                "sport_key": "baseball", "league_id": "mlb", "league_label": "MLB",
                "matchup": "Sox @ Yanks", "home": "Yankees", "away": "Red Sox",
                "market": "Moneyline", "pick": "Yankees",
            },
            {
                "sport_key": "soccer", "league_id": "mls", "league_label": "MLS",
                "matchup": "A vs B", "home": "A", "away": "B",
                "market": "BTTS", "pick": "Yes",
            },
        ],
    }], slate_date="2026-08-12")
    assert rec and rec[0]["ok"]
    combo_results.manual_settle_combo_slip(rec[0]["id"], "lost")

    calls = []
    x_calls = []

    def fake_reply(text, *, reply_to_message_id=None, png_bytes=None, caption=None):
        calls.append({
            "text": text,
            "reply_to": reply_to_message_id,
            "has_png": bool(png_bytes),
            "caption": caption,
        })
        return True, "ok"

    def fake_x(item, *, png_bytes=None, prefix=""):
        x_calls.append({
            "title": item.get("title"),
            "status": item.get("status"),
            "has_png": bool(png_bytes),
            "prefix": prefix,
        })
        return True, "posted X/Twitter"

    monkeypatch.setattr(daily_digest, "post_telegram_result_reply", fake_reply)
    monkeypatch.setattr(daily_digest, "post_twitter_result", fake_x)
    monkeypatch.setattr(
        daily_digest,
        "build_telegram_result_image",
        lambda item: {"png_bytes": b"PNG", "caption": "RESULT · LOST"},
    )
    out = daily_digest.post_due_telegram_results()
    assert out["posted"] == 1
    assert out["twitter_posted"] == 1
    assert out["checked"] == 1
    assert calls[0]["reply_to"] == 901
    assert calls[0]["has_png"] is True
    assert "RESULT: LOST" in calls[0]["text"]
    assert x_calls[0]["has_png"] is True
    assert x_calls[0]["status"] == "lost"

    out2 = daily_digest.post_due_telegram_results()
    assert out2["posted"] == 0
    assert out2["twitter_posted"] == 0
    assert out2["checked"] == 0
    assert len(calls) == 1
    assert len(x_calls) == 1


def test_post_due_results_retries_x_if_telegram_already_sent(tmp_path, monkeypatch):
    import daily_digest

    db = tmp_path / "combo_results.db"
    monkeypatch.setattr(combo_results, "DB_PATH", str(db))
    combo_results.init_db()
    rec = combo_results.record_degen_team_from_telegram_slips([{
        "title": "Today's Picks",
        "telegram_message_id": 77,
        "legs": [{
            "sport_key": "baseball", "league_id": "mlb", "league_label": "MLB",
            "matchup": "A vs B", "home": "A", "away": "B",
            "market": "Moneyline", "pick": "A",
        }],
    }], slate_date="2026-08-12")
    assert rec and rec[0]["ok"]
    kind = rec[0]["kind"]
    if kind == "combo":
        combo_results.manual_settle_combo_slip(rec[0]["id"], "won")
    else:
        combo_results.manual_settle_pick(rec[0]["id"], "won")
    combo_results.mark_telegram_result_posted(kind, rec[0]["id"])

    x_calls = []
    monkeypatch.setattr(
        daily_digest,
        "post_telegram_result_reply",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("telegram should not resend")),
    )
    monkeypatch.setattr(
        daily_digest,
        "post_twitter_result",
        lambda item, **_k: x_calls.append(item.get("title")) or (True, "ok"),
    )
    monkeypatch.setattr(
        daily_digest,
        "build_telegram_result_image",
        lambda item: {"png_bytes": b"PNG", "caption": "RESULT · WON"},
    )
    out = daily_digest.post_due_telegram_results()
    assert out["posted"] == 0
    assert out["twitter_posted"] == 1
    assert x_calls == ["Today's Picks"]
    out2 = daily_digest.post_due_telegram_results()
    assert out2["twitter_posted"] == 0


def test_build_telegram_result_image_includes_status(tmp_path, monkeypatch):
    import daily_digest

    monkeypatch.setattr(daily_digest, "CARDS_DIR", tmp_path / "cards")
    item = {
        "title": "Today's Picks",
        "slate_date": "2026-08-11",
        "status": "won",
        "kind": "combo",
        "legs": [{
            "matchup": "A vs B", "pick": "A", "market": "1X2",
            "sport_label": "Soccer", "league_label": "MLS",
            "status": "won", "actual_note": "2-1",
        }],
    }
    image = daily_digest.build_telegram_result_image(item)
    assert image and image["png_bytes"][:8] == b"\x89PNG\r\n\x1a\n"
    assert "RESULT" in image["caption"]
    assert "WON" in image["caption"]


def test_admin_telegram_and_result_serials(tmp_path, monkeypatch):
    db = tmp_path / "combo_results.db"
    monkeypatch.setattr(combo_results, "DB_PATH", str(db))
    combo_results.init_db()

    rec1 = combo_results.record_degen_team_slip(
        [{
            "sport_key": "soccer", "league_id": "mls", "league_label": "MLS",
            "matchup": "A vs B", "home": "A", "away": "B",
            "market": "1X2", "pick": "A", "confidence": 0.71,
        }],
        title="Single",
        telegram_message_ids=[11],
        slate_date="2026-08-13",
    )
    rec2 = combo_results.record_degen_team_slip(
        [{
            "sport_key": "soccer", "league_id": "mls", "league_label": "MLS",
            "matchup": "C vs D", "home": "C", "away": "D",
            "market": "1X2", "pick": "C", "confidence": 0.72,
        }],
        title="Single 2",
        telegram_message_ids=[12],
        telegram_serial=9,
        slate_date="2026-08-13",
    )
    assert rec1["telegram_serial"] == 1
    assert rec2["telegram_serial"] == 9

    rec3 = combo_results.record_degen_team_slip(
        [{
            "sport_key": "soccer", "league_id": "mls", "league_label": "MLS",
            "matchup": "E vs F", "home": "E", "away": "F",
            "market": "1X2", "pick": "E", "confidence": 0.73,
        }],
        title="Single 3",
        telegram_message_ids=[13],
        slate_date="2026-08-13",
    )
    assert rec3["telegram_serial"] == 2
    assert combo_results.format_telegram_serial(1) == "#T-00001"
    assert combo_results.format_result_serial(9) == "#R-00009"

    combo_results.manual_settle_pick(rec1["id"], "won")
    due = combo_results.list_telegram_results_due()
    assert due[0]["telegram_serial"] == 1
    stamped = combo_results.stamp_result_serial(due[0])
    assert stamped["result_serial"] == 1
    again = combo_results.stamp_result_serial(due[0])
    assert again["result_serial"] == 1
    text = combo_results.format_telegram_result_text(stamped)
    assert "#R-00001" in text
    assert "#T-00001" in text
    assert "RESULT: WON" in text


def test_live_cache_bust_invalidates_fresh_check(tmp_path, monkeypatch):
    import time
    import live_cache_bust
    monkeypatch.setattr(live_cache_bust, "BUST_PATH", str(tmp_path / "live_cache_bust"))
    now = time.time()
    assert live_cache_bust.is_fresh(now, ttl_seconds=3600) is True
    # A real sleep, not just a later wall-clock read: on Windows the mtime a
    # filesystem assigns to a just-written file can register microseconds
    # *behind* a time.time() value captured immediately beforehand (clock
    # source skew between the two), which flakes the "written after now"
    # assumption below without a real gap between the two timestamps.
    time.sleep(0.05)
    live_cache_bust.mark_stale()
    assert live_cache_bust.is_fresh(now, ttl_seconds=3600) is False


def test_refresh_for_finished_events_settles_without_busting_when_idle(monkeypatch):
    calls = []
    monkeypatch.setattr(combo_results, "bust_live_score_caches", lambda: calls.append("bust") or ["combo_lookup"])
    monkeypatch.setattr(combo_results, "drop_html_result_caches", lambda: calls.append("html") or [])
    monkeypatch.setattr(combo_results, "maybe_settle_pending", lambda min_interval=60: calls.append("settle") or {"legs_settled": 0})
    out = combo_results.refresh_for_finished_events(min_interval=60)
    assert calls == ["settle"]
    assert out["legs_settled"] == 0


def test_refresh_for_finished_events_busts_after_real_settles(monkeypatch):
    calls = []
    monkeypatch.setattr(combo_results, "bust_live_score_caches", lambda: calls.append("bust") or ["combo_lookup"])
    monkeypatch.setattr(combo_results, "drop_html_result_caches", lambda: calls.append("html") or [])
    monkeypatch.setattr(combo_results, "maybe_settle_pending", lambda min_interval=60: calls.append("settle") or {"legs_settled": 2})
    out = combo_results.refresh_for_finished_events(min_interval=60)
    assert calls == ["settle", "bust", "html"]
    assert out["legs_settled"] == 2
