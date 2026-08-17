"""NFL / American football sport wiring."""
import analytics
import combo_results
import nfl_fixtures
import nfl_predict
import today_picks


SAMPLE_EVENT = {
    "id": "401772001",
    "date": "2026-08-15T00:00:00Z",
    "competitions": [{
        "competitors": [
            {
                "homeAway": "home",
                "score": "24",
                "team": {"displayName": "Kansas City Chiefs", "abbreviation": "KC"},
                "records": [{"type": "total", "summary": "1-0"}],
            },
            {
                "homeAway": "away",
                "score": "17",
                "team": {"displayName": "Buffalo Bills", "abbreviation": "BUF"},
                "records": [{"type": "total", "summary": "0-1"}],
            },
        ],
        "status": {"type": {"state": "post", "completed": True, "shortDetail": "Final"}},
    }],
    "status": {"type": {"state": "post", "completed": True, "shortDetail": "Final"}},
}


def _upcoming_fixture(**kwargs):
    row = {
        "id": "401772099",
        "date": "2099-09-07T17:00:00Z",
        "start_time_utc": "2099-09-07T17:00:00Z",
        "home_team": "Kansas City Chiefs",
        "home_abbr": "KC",
        "away_team": "Buffalo Bills",
        "away_abbr": "BUF",
        "home_record": "0-0",
        "away_record": "0-0",
        "status": "pre",
        "status_detail": "Sun 1:00 PM ET",
        "completed": False,
        "home_score": None,
        "away_score": None,
    }
    row.update(kwargs)
    return row


def _seed_ratings(monkeypatch, tmp_path):
    monkeypatch.setattr(nfl_predict, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(nfl_predict, "ELO_PATH", str(tmp_path / "elo_ratings.csv"))
    monkeypatch.setattr(nfl_predict, "HISTORY_PATH", str(tmp_path / "game_history.csv"))
    games = [
        {
            "date": "2025-09-07T17:00:00Z",
            "home_team": "Kansas City Chiefs", "home_abbr": "KC",
            "away_team": "Buffalo Bills", "away_abbr": "BUF",
            "completed": True, "home_score": 31, "away_score": 17,
        },
        {
            "date": "2025-09-14T17:00:00Z",
            "home_team": "Buffalo Bills", "home_abbr": "BUF",
            "away_team": "Kansas City Chiefs", "away_abbr": "KC",
            "completed": True, "home_score": 20, "away_score": 24,
        },
        {
            "date": "2025-09-21T17:00:00Z",
            "home_team": "Kansas City Chiefs", "home_abbr": "KC",
            "away_team": "Philadelphia Eagles", "away_abbr": "PHI",
            "completed": True, "home_score": 27, "away_score": 20,
        },
        {
            "date": "2025-09-21T20:00:00Z",
            "home_team": "Philadelphia Eagles", "home_abbr": "PHI",
            "away_team": "Buffalo Bills", "away_abbr": "BUF",
            "completed": True, "home_score": 22, "away_score": 19,
        },
    ]
    elo_df, history_df = nfl_predict.rebuild_ratings(games)
    nfl_predict._save_ratings(elo_df, history_df)
    return elo_df


def test_parse_espn_event():
    fx = nfl_fixtures.parse_event(SAMPLE_EVENT)
    assert fx["home_abbr"] == "KC"
    assert fx["away_abbr"] == "BUF"
    assert fx["completed"] is True
    assert fx["home_score"] == "24"
    assert fx["away_team"] == "Buffalo Bills"


def test_history_fixtures_uses_chunks(monkeypatch):
    ranges = []

    def fake_fetch(date_range, timeout=8):
        ranges.append(date_range)
        return {"events": [SAMPLE_EVENT]}

    monkeypatch.setattr(nfl_fixtures, "_fetch_scoreboard", fake_fetch)
    out = nfl_fixtures.get_history_fixtures(days_back=200, chunk_days=90)
    assert len(ranges) >= 2
    assert out
    assert out[0]["home_abbr"] == "KC"


def test_rebuild_ratings_favours_winner(tmp_path, monkeypatch):
    elo_df = _seed_ratings(monkeypatch, tmp_path)
    kc = float(elo_df[elo_df["Team"] == "KC"].iloc[0]["Elo"])
    buf = float(elo_df[elo_df["Team"] == "BUF"].iloc[0]["Elo"])
    assert kc > buf
    assert kc > nfl_predict.START_ELO


def test_compute_prediction_has_spread_and_totals(tmp_path, monkeypatch):
    _seed_ratings(monkeypatch, tmp_path)
    pred = nfl_predict.compute_nfl_prediction("KC", "BUF")
    assert pred is not None
    assert abs((pred["home_prob"] + pred["away_prob"]) - 1.0) < 1e-9
    assert pred["predicted_total_points"] > 0
    assert pred["spread_pick"]
    assert pred["total_points_pick"] in ("Over", "Under")


def test_today_sports_includes_nfl():
    sport = today_picks.TODAY_SPORTS["american_football"]
    assert sport["leagues"]["nfl"]["kind"] == "nfl"
    assert sport["leagues"]["nfl"]["label"] == "NFL"


def test_nfl_home_redirects_to_todays_picks(client):
    resp = client.get("/nfl/")
    assert resp.status_code in (301, 302)
    assert resp.headers["Location"].endswith("/todays-picks/american_football/nfl")


def test_nfl_hub_and_todays_picks(client, monkeypatch, tmp_path):
    _seed_ratings(monkeypatch, tmp_path)
    monkeypatch.setattr("app.ensure_nfl_ratings", lambda **k: nfl_predict.load_nfl_elo())
    monkeypatch.setattr("app.get_nfl_fixtures", lambda *a, **k: [_upcoming_fixture()])
    monkeypatch.setattr("app.load_nfl_elo", nfl_predict.load_nfl_elo)
    monkeypatch.setattr(combo_results, "refresh_for_finished_events", lambda **k: None)

    hub = client.get("/sports/american-football")
    assert hub.status_code == 200
    body = hub.data.decode()
    assert "NFL" in body
    assert "See fixtures" in body
    assert 'href="/nfl/fixtures"' in body

    picks = client.get("/todays-picks/american_football")
    assert picks.status_code == 200
    assert "NFL" in picks.data.decode()
    assert 'href="/nfl/fixtures"' in picks.data.decode()

    league = client.get("/todays-picks/american_football/nfl")
    assert league.status_code == 200
    assert "Today's Picks" in league.data.decode()


def test_nfl_fixtures_unlock_prediction(client, monkeypatch, tmp_path):
    _seed_ratings(monkeypatch, tmp_path)
    monkeypatch.setattr("app.ensure_nfl_ratings", lambda **k: nfl_predict.load_nfl_elo())
    monkeypatch.setattr("app.load_nfl_elo", nfl_predict.load_nfl_elo)
    monkeypatch.setattr("app.get_nfl_fixtures", lambda *a, **k: [_upcoming_fixture()])
    monkeypatch.setattr(combo_results, "refresh_for_finished_events", lambda **k: None)

    body = client.get("/nfl/fixtures").data.decode()
    assert "Unlock prediction" in body
    assert "Buffalo Bills vs Kansas City Chiefs" in body
    assert "/nfl/api/predict" in body


def test_nfl_fixtures_finished_keep_unlock(client, monkeypatch, tmp_path):
    _seed_ratings(monkeypatch, tmp_path)
    monkeypatch.setattr("app.ensure_nfl_ratings", lambda **k: nfl_predict.load_nfl_elo())
    monkeypatch.setattr("app.load_nfl_elo", nfl_predict.load_nfl_elo)
    finished = _upcoming_fixture(
        date="2026-08-11T00:00:00Z",
        completed=True,
        status="post",
        home_score="27",
        away_score="20",
    )
    monkeypatch.setattr("app.get_nfl_fixtures", lambda *a, **k: [finished])
    monkeypatch.setattr(combo_results, "refresh_for_finished_events", lambda **k: None)
    fixtures_body = client.get("/nfl/fixtures").data.decode()
    assert "Recent results" not in fixtures_body
    assert "Final: 20-27" not in fixtures_body
    results_body = client.get("/nfl/results?refresh=1").data.decode()
    assert "Latest scores" in results_body
    assert "Final: 20-27" in results_body


def test_nfl_api_predict(client, monkeypatch, tmp_path):
    _seed_ratings(monkeypatch, tmp_path)
    monkeypatch.setattr(nfl_predict, "ensure_nfl_ratings", lambda **k: nfl_predict.load_nfl_elo())
    resp = client.get("/nfl/api/predict?home=KC&away=BUF")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "home_prob" in data
    assert "spread_pick" in data
    assert data["home"] == "KC"


def test_nfl_match_page_schema(client, monkeypatch, tmp_path):
    _seed_ratings(monkeypatch, tmp_path)
    monkeypatch.setattr(nfl_predict, "ensure_nfl_ratings", lambda **k: nfl_predict.load_nfl_elo())
    resp = client.get(
        "/nfl/match/KC/BUF?home_name=Kansas+City+Chiefs&away_name=Buffalo+Bills"
        "&date=2026-09-07T17:00:00Z"
    )
    assert resp.status_code == 200
    body = resp.data.decode()
    assert '"@type": "SportsEvent"' in body
    assert "American Football" in body
    assert "Win Probability" in body
    assert "Spread" in body


def test_nfl_results_page_public(client, monkeypatch, tmp_path):
    _seed_ratings(monkeypatch, tmp_path)
    monkeypatch.setattr("app.nfl_track_record", nfl_predict.nfl_track_record)
    monkeypatch.setattr(combo_results, "refresh_for_finished_events", lambda **k: None)
    resp = client.get("/nfl/results")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Track Record" in body or "track record" in body.lower()


def test_nfl_market_tips_skip_ml_on_public_board():
    pred = {
        "home": "KC", "away": "BUF",
        "home_prob": 0.62, "away_prob": 0.38,
        "spread_home": -3.5,
        "spread_pick": "KC -3.5",
        "spread_pick_probability": 40.0,
        "total_points_pick": "Over",
        "total_points_line": 44.5,
        "total_points_pick_probability": 56.0,
        "home_points_pick": "Over",
        "home_points_line": 24.5,
        "home_points_pick_probability": 55.0,
        "away_points_pick": "Under",
        "away_points_line": 20.5,
        "away_points_pick_probability": 54.0,
    }
    public = today_picks._nfl_market_tips(
        pred, home_name="Kansas City Chiefs", away_name="Buffalo Bills",
        include_moneyline=False, include_upset_lean=True, include_favorite_spread=False,
    )
    markets = {t["market"] for t in public}
    assert "Moneyline" not in markets
    assert "Total points" in markets
    assert "Spread" in markets
    combo = today_picks._nfl_market_tips(
        pred, home_name="Kansas City Chiefs", away_name="Buffalo Bills",
        include_moneyline=True, include_upset_lean=False, include_favorite_spread=True,
    )
    assert any(t["market"] == "Moneyline" for t in combo)
    fav_spread = next(t for t in combo if t["market"] == "Spread")
    assert "Chiefs" in fav_spread["pick"] and "-" in fav_spread["pick"]


def test_favorite_cover_spread_only_minus_line():
    fav = {"market": "Spread", "pick": "Kansas City Chiefs -3.5", "confidence": 0.86}
    dog = {"market": "Spread", "pick": "Buffalo Bills +3.5", "confidence": 0.86}
    assert today_picks._leg_is_favorite_cover(fav) is True
    assert today_picks._leg_is_favorite_cover(dog) is False


def test_settle_nfl_spread_and_total_points():
    score = {
        "home": "Kansas City Chiefs", "away": "Buffalo Bills",
        "home_score": 27, "away_score": 20, "completed": True,
    }
    st, _ = combo_results.settle_leg_against_score("Spread", "Kansas City Chiefs -3.5", score)
    assert st == combo_results.WON
    st, _ = combo_results.settle_leg_against_score("Spread", "Buffalo Bills +3.5", score)
    assert st == combo_results.LOST
    st, _ = combo_results.settle_leg_against_score("Total points", "Over 44.5", score)
    assert st == combo_results.WON
    st, _ = combo_results.settle_leg_against_score("Team points", "Kansas City Chiefs Over 24.5", score)
    assert st == combo_results.WON


def test_lookup_nfl_uses_fixtures(monkeypatch):
    monkeypatch.setattr(
        "nfl_fixtures.get_fixtures",
        lambda days_back=5: [{
            "home_team": "Kansas City Chiefs", "away_team": "Buffalo Bills",
            "home_abbr": "KC", "away_abbr": "BUF",
            "home_score": 24, "away_score": 17,
            "completed": True, "date": "2026-08-11T00:00:00Z",
        }],
    )
    score = combo_results.lookup_score_for_leg({
        "sport_key": "american_football",
        "league_id": "nfl",
        "home": "Kansas City Chiefs",
        "away": "Buffalo Bills",
        "kickoff_iso": "2026-08-11T00:00:00Z",
    })
    assert score is not None
    assert score["completed"] is True
    assert float(score["home_score"]) == 24


def test_sports_landing_has_nfl_card(client):
    body = client.get("/sports").data.decode()
    assert 'href="/sports/american-football"' in body
    assert "AMERICAN FOOTBALL" in body


def test_gp_path_includes_nfl():
    assert analytics.is_get_prediction_path("/nfl/api/predict?home=KC&away=BUF")
    assert analytics.is_get_prediction_api_path("/nfl/api/predict")
    meta = analytics.get_prediction_event_meta("/nfl/api/predict?home=KC&away=BUF")
    assert meta["league_label"] == "NFL"
    assert "KC" in meta["label"] and "BUF" in meta["label"]
