"""Tests for the cricket (The Hundred + MLC) section."""
import markupsafe
import pandas as pd
import pytest

import cricket_predict


# ---- routes: landing + separate leagues -----------------------------------

def test_cricket_landing_page_loads(client):
    resp = client.get("/sports/cricket")
    assert resp.status_code == 200


def test_sports_landing_links_to_cricket(client):
    resp = client.get("/sports")
    body = resp.data.decode()
    assert 'href="/sports/cricket"' in body


def test_hundred_home_redirects_to_fixtures(client):
    resp = client.get("/hundred/")
    assert resp.status_code in (301, 302)
    assert resp.headers["Location"].endswith("/hundred/fixtures")


def test_mlc_home_redirects_to_fixtures(client):
    resp = client.get("/mlc/")
    assert resp.status_code in (301, 302)
    assert resp.headers["Location"].endswith("/mlc/fixtures")


def test_hundred_fixtures_page_loads(client):
    resp = client.get("/hundred/fixtures")
    assert resp.status_code == 200


def test_mlc_fixtures_page_loads(client):
    resp = client.get("/mlc/fixtures")
    assert resp.status_code == 200


def test_hundred_fixtures_page_reflects_real_upcoming_state(client):
    """The Hundred's fixtures now come from a real source (Wikipedia's
    season article, see hundred_fixtures.py) -- whichever state is
    genuinely true right now (in-season with real fixtures, or between
    seasons) must render honestly, never a stale historical match
    mislabeled as upcoming."""
    fixtures = cricket_predict.upcoming_fixtures("hnd_male")
    body = client.get("/hundred/fixtures").data.decode()
    if fixtures:
        assert any(f["team1"] in body and f["team2"] in body for f in fixtures)
    else:
        assert "No upcoming" in body
    assert "results" in body.lower()


def test_mlc_fixtures_page_shows_honest_empty_state(client):
    """MLC's 2026 season already concluded and no next-season schedule
    source was found -- this stays an honest empty state until code
    changes (see cricket_predict.upcoming_fixtures docstring)."""
    resp = client.get("/mlc/fixtures")
    body = resp.data.decode()
    assert "No upcoming" in body


def test_hundred_fixtures_cards_have_get_prediction_button(client):
    resp = client.get("/hundred/fixtures")
    body = resp.data.decode()
    if "fixture-card" in body:
        assert "Unlock prediction" in body
        assert "/hundred/fixture/" in body
        assert 'data-lock="1"' in body
        assert "window.location.href" not in body


def test_hundred_fixture_detail_page_loads_for_real_fixture(client):
    fixtures = cricket_predict.upcoming_fixtures("hnd_male")
    if not fixtures:
        pytest.skip("no upcoming Hundred fixtures right now")
    slug = fixtures[0]["slug"]
    resp = client.get(f"/hundred/fixture/{slug}")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert fixtures[0]["team1"] in body and fixtures[0]["team2"] in body
    assert "Match terminal" in body


def test_hundred_fixture_detail_unknown_slug_is_404(client):
    resp = client.get("/hundred/fixture/not-a-real-slug-vs-nobody-2099-01-01")
    assert resp.status_code == 404


def test_hundred_fixture_detail_includes_pitch_and_weather_when_available(client):
    """This is exactly the case the weather feature was built for -- a
    real fixture with a real date -- so it should actually show up here,
    unlike the historical /hundred/match/<id> pages."""
    fixtures = cricket_predict.upcoming_fixtures("hnd_male")
    if not fixtures:
        pytest.skip("no upcoming Hundred fixtures right now")
    fixture = fixtures[0]
    detail = cricket_predict.build_fixture_detail(fixture)
    resp = client.get(f"/hundred/fixture/{fixture['slug']}")
    body = resp.data.decode()
    if detail["pitch_report"]:
        assert "Pitch Report" in body
    if detail["weather"]:
        assert "Weather Forecast" in body


def test_build_fixture_detail_shape():
    fixtures = cricket_predict.upcoming_fixtures("hnd_male")
    if not fixtures:
        pytest.skip("no upcoming Hundred fixtures right now")
    detail = cricket_predict.build_fixture_detail(fixtures[0])
    assert detail["team1"] and detail["team2"]
    assert "prediction" in detail and "pitch_report" in detail and "weather" in detail


def test_hundred_results_page_loads(client):
    resp = client.get("/hundred/results")
    assert resp.status_code == 200


def test_mlc_results_page_loads(client):
    resp = client.get("/mlc/results")
    assert resp.status_code == 200


def test_hundred_and_mlc_results_are_genuinely_separate_pages(client):
    """Hundred results must not list MLC teams and vice versa -- the two
    competitions are served as separate leagues, not a merged table."""
    df = cricket_predict.load_dataset()
    if df.empty:
        pytest.skip("no cricket training dataset available")
    hnd_teams = set(df[df["Competition"] == "hnd_male"]["Team1"]) | set(df[df["Competition"] == "hnd_male"]["Team2"])
    mlc_teams = set(df[df["Competition"] == "mlc_male"]["Team1"]) | set(df[df["Competition"] == "mlc_male"]["Team2"])
    only_mlc = mlc_teams - hnd_teams
    if not only_mlc:
        pytest.skip("no MLC-only team name to distinguish with")
    body = client.get("/hundred/results").data.decode()
    assert not any(team in body for team in only_mlc)


def test_hundred_results_cards_link_to_match_detail(client):
    resp = client.get("/hundred/results")
    body = resp.data.decode()
    if "fixture-card" in body:
        assert "/hundred/match/" in body or "/hundred/fixture/" in body
        assert "Unlock prediction" in body
        assert "js-unlock-slate" in body
        assert 'data-lock="1"' in body
        assert "onclick=\"window.location.href" not in body
        assert "fixture-final-score" in body


def test_hundred_results_include_live_completed_not_in_archive(client):
    """Wikipedia finals must appear even when Cricsheet still lags."""
    import hundred_fixtures
    live = [f for f in hundred_fixtures.get_hundred_fixtures() if f.get("completed")]
    if not live:
        pytest.skip("no completed Hundred fixtures from Wikipedia")
    archive = cricket_predict.matches_for_competition("hnd_male", limit=80)
    archive_keys = set()
    if archive is not None and not archive.empty:
        for _, row in archive.iterrows():
            day = row["Date"].strftime("%Y-%m-%d") if hasattr(row["Date"], "strftime") else str(row["Date"])[:10]
            archive_keys.add(cricket_predict.result_pair_key(day, row["Team1"], row["Team2"]))
    missing = [
        f for f in live
        if cricket_predict.result_pair_key(f.get("date"), f.get("team1"), f.get("team2")) not in archive_keys
    ]
    if not missing:
        pytest.skip("archive already has every Wikipedia final")
    body = client.get("/hundred/results").data.decode()
    sample = missing[0]
    assert sample["team1"] in body
    assert sample["team2"] in body
    assert "/hundred/fixture/" in body


def test_upcoming_fixtures_mlc_is_empty_list():
    """MLC has no real upcoming-fixture source (2026 season already
    concluded) -- see cricket_predict module docstring."""
    assert cricket_predict.upcoming_fixtures("mlc_male") == []


def test_upcoming_fixtures_cpl_never_raises():
    """Whether or not CPL is in-season, this must never raise."""
    fixtures = cricket_predict.upcoming_fixtures("cpl_male")
    assert isinstance(fixtures, list)


def test_cpl_routes_load(client):
    for path in ("/cpl/", "/cpl/fixtures", "/cpl/results"):
        resp = client.get(path)
        assert resp.status_code in (200, 301, 302), path


def test_cricket_landing_lists_cpl(client):
    body = client.get("/sports/cricket").data.decode()
    assert "CPL" in body


def test_cpl_fixtures_page_reflects_upcoming_state(client):
    fixtures = cricket_predict.upcoming_fixtures("cpl_male")
    body = client.get("/cpl/fixtures").data.decode()
    if fixtures:
        assert "/cpl/fixture/" in body or fixtures[0]["team1"] in body
    else:
        assert "No upcoming" in body or "no upcoming" in body.lower() or "check results" in body.lower()


def test_cpl_fixture_detail_page_loads_for_real_fixture(client):
    fixtures = cricket_predict.upcoming_fixtures("cpl_male")
    if not fixtures:
        pytest.skip("no upcoming CPL fixtures")
    slug = fixtures[0]["slug"]
    resp = client.get(f"/cpl/fixture/{slug}")
    assert resp.status_code == 200
    body = resp.data.decode()
    # Jinja HTML-escapes rendered text, so team names with special
    # characters (e.g. "St Kitts & Nevis Patriots") won't appear verbatim.
    team1 = str(markupsafe.escape(fixtures[0]["team1"]))
    team2 = str(markupsafe.escape(fixtures[0]["team2"]))
    assert team1 in body and team2 in body


def test_upcoming_fixtures_unknown_competition_is_empty_list():
    assert cricket_predict.upcoming_fixtures("not_a_real_competition") == []


def test_upcoming_fixtures_hundred_never_raises():
    """Whether or not the Hundred is genuinely in-season right now, this
    must never raise -- Wikipedia being briefly unreachable or the article
    structure changing must degrade to an empty list, not a 500."""
    fixtures = cricket_predict.upcoming_fixtures("hnd_male")
    assert isinstance(fixtures, list)


def test_season_window_is_data_driven():
    window = cricket_predict.season_window("hnd_male")
    if window is None:
        pytest.skip("no cricket training dataset available")
    assert isinstance(window, str) and len(window) > 0


def test_hundred_match_detail_page_loads(client):
    df = cricket_predict.matches_for_competition("hnd_male", limit=1)
    if df.empty:
        pytest.skip("no Hundred matches available")
    match_id = int(df.iloc[0]["match_id"])
    resp = client.get(f"/hundred/match/{match_id}")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Match terminal" in body
    assert "Match total" in body


def test_mlc_match_detail_page_loads(client):
    df = cricket_predict.matches_for_competition("mlc_male", limit=1)
    if df.empty:
        pytest.skip("no MLC matches available")
    match_id = int(df.iloc[0]["match_id"])
    resp = client.get(f"/mlc/match/{match_id}")
    assert resp.status_code == 200


def test_hundred_match_wrong_competition_is_404(client):
    df = cricket_predict.matches_for_competition("mlc_male", limit=1)
    if df.empty:
        pytest.skip("no MLC matches available")
    match_id = int(df.iloc[0]["match_id"])
    resp = client.get(f"/hundred/match/{match_id}")
    assert resp.status_code == 404


def test_cricket_match_unknown_id_is_404(client):
    resp = client.get("/hundred/match/999999999")
    assert resp.status_code == 404


# ---- unit: data loading -----------------------------------------------------

def test_load_dataset_has_both_competitions():
    df = cricket_predict.load_dataset()
    if df.empty:
        pytest.skip("no cricket training dataset available")
    comps = set(df["Competition"].unique())
    assert comps <= {"hnd_male", "mlc_male", "ipl_male", "bbl_male", "cpl_male"}
    assert "ipl_male" in comps and "bbl_male" in comps


def test_ipl_and_bbl_routes_load(client):
    for path in ("/ipl/", "/ipl/fixtures", "/ipl/results", "/bbl/", "/bbl/fixtures", "/bbl/results"):
        resp = client.get(path)
        assert resp.status_code in (200, 301, 302), path


def test_cricket_landing_lists_ipl_bbl(client):
    body = client.get("/sports/cricket").data.decode()
    assert "IPL" in body and "BBL" in body


def test_ipl_results_match_detail_has_analysis(client):
    df = cricket_predict.matches_for_competition("ipl_male", limit=1)
    if df.empty:
        pytest.skip("no IPL matches")
    match_id = int(df.iloc[0]["match_id"])
    resp = client.get(f"/ipl/match/{match_id}")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Team analysis" in body or "Player analysis" in body


def test_provisional_xi_cache_exists():
    xi = cricket_predict.load_provisional_xi()
    assert "ipl_male" in xi or "bbl_male" in xi


def test_upcoming_fixtures_ipl_bbl_never_raise():
    assert isinstance(cricket_predict.upcoming_fixtures("ipl_male"), list)
    assert isinstance(cricket_predict.upcoming_fixtures("bbl_male"), list)


def test_fd_fixtures_module_parses_or_fails_soft():
    import cricket_fd_fixtures
    fixtures = cricket_fd_fixtures.get_competition_fixtures("ipl")
    assert isinstance(fixtures, list)
    if fixtures:
        f = fixtures[0]
        assert f["team1"] and f["team2"] and f["date"]


def test_load_elo_ratings():
    df = cricket_predict.load_elo()
    if df.empty:
        pytest.skip("no cricket elo ratings available")
    assert "Elo" in df.columns
    assert "Team" in df.columns


def test_load_venue_stats_has_pitch_type():
    df = cricket_predict.load_venues()
    if df.empty:
        pytest.skip("no cricket venue stats available")
    assert "PitchType" in df.columns


# ---- unit: prediction + data_confidence ------------------------------------

def test_predict_for_row_surfaces_data_confidence():
    df = cricket_predict.load_dataset()
    if df.empty:
        pytest.skip("no cricket training dataset available")
    row = df.iloc[0]
    pred = cricket_predict.predict_for_row(row)
    assert pred["data_confidence"] in ("high", "medium", "low", "unknown")


def test_build_match_view_includes_actual_and_predicted():
    df = cricket_predict.load_dataset()
    if df.empty:
        pytest.skip("no cricket training dataset available")
    row = df.iloc[-1]
    view = cricket_predict.build_match_view(row)
    assert "predicted_total_runs" in view
    assert "actual_total_runs" in view
    assert view["team1"] and view["team2"]
    assert "insights" in view
    assert "crest_rgb1" in view


def test_fixture_insight_chips_coin_flip():
    chips = cricket_predict.fixture_insight_chips(
        "A", "B",
        {"team1_win_prob": 0.52, "team2_win_prob": 0.48, "predicted_total_runs": 320, "basis": "Elo"},
    )
    texts = " ".join(c["text"] for c in chips).lower()
    assert "coin-flip" in texts
    assert "total" in texts


def test_get_venue_pitch_report_flags_thin_history():
    venues = cricket_predict.load_venues()
    if venues.empty:
        pytest.skip("no cricket venue stats available")
    thin_venue = venues[venues["MatchesPlayed"] < 5]
    if thin_venue.empty:
        pytest.skip("no thin-history venue to test with")
    report = cricket_predict.get_venue_pitch_report(thin_venue.iloc[0]["Venue"])
    assert report["thin_history"] is True


def test_get_venue_pitch_report_unknown_venue_returns_none():
    assert cricket_predict.get_venue_pitch_report("Not A Real Ground") is None


# ---- unit: weather (fail-soft) ----------------------------------------------

def test_get_venue_weather_unknown_venue_fails_soft():
    """No coordinates known for this ground -- must return None, never raise."""
    assert cricket_predict.get_venue_weather("Not A Real Ground", "2026-08-01") is None


def test_get_venue_weather_past_date_fails_soft():
    """Every match currently in this dataset is historical -- Open-Meteo's
    free endpoint has no historical data, so this must degrade to None
    rather than erroring, exactly the case every real match hits today."""
    result = cricket_predict.get_venue_weather("Kennington Oval, London", "2021-07-22")
    assert result is None


def test_get_venue_weather_malformed_date_fails_soft():
    assert cricket_predict.get_venue_weather("Kennington Oval, London", "not-a-date") is None


def test_get_venue_weather_far_future_fails_soft():
    far_future = "2030-01-01"
    assert cricket_predict.get_venue_weather("Kennington Oval, London", far_future) is None


# ---- unit: hundred_fixtures (Wikipedia-sourced live/upcoming fixtures) -----

def test_hundred_fixtures_parse_returns_completed_and_upcoming_shapes():
    import hundred_fixtures
    fixtures = hundred_fixtures.get_hundred_fixtures()
    if not fixtures:
        pytest.skip("Wikipedia fixture fetch unavailable in this environment")
    for f in fixtures:
        assert f["team1"] and f["team2"]
        assert f["date"]
        assert isinstance(f["completed"], bool)
        if f["completed"]:
            assert f["result"]
        else:
            assert f["result"] is None


def test_hundred_fixtures_rebrand_alias_applied():
    import hundred_fixtures
    fixtures = hundred_fixtures.get_hundred_fixtures()
    if not fixtures:
        pytest.skip("Wikipedia fixture fetch unavailable in this environment")
    rebranded = [f for f in fixtures if f["team1"] in hundred_fixtures.REBRAND_ALIAS
                 or f["team2"] in hundred_fixtures.REBRAND_ALIAS]
    if not rebranded:
        pytest.skip("no rebranded team appeared in the fetched fixtures")
    f = rebranded[0]
    if f["team1"] in hundred_fixtures.REBRAND_ALIAS:
        assert f["team1_elo_name"] == hundred_fixtures.REBRAND_ALIAS[f["team1"]]
    if f["team2"] in hundred_fixtures.REBRAND_ALIAS:
        assert f["team2_elo_name"] == hundred_fixtures.REBRAND_ALIAS[f["team2"]]


def test_hundred_fixtures_get_upcoming_and_live_excludes_past_dates():
    import hundred_fixtures
    import datetime
    fixtures = hundred_fixtures.get_upcoming_and_live()
    today_str = datetime.datetime.now().strftime("%Y-%m-%d")
    assert all(f["date"] >= today_str for f in fixtures)
    assert all(not f["completed"] for f in fixtures)


def test_hundred_fixtures_malformed_wikitext_fails_soft():
    import hundred_fixtures
    assert hundred_fixtures._parse_fixtures("not valid wikitext at all") == []


def test_hundred_fixtures_parse_includes_knockout_final():
    """Knockout Final/Eliminator use the same Single-innings template as
    league matches — parser must accept them so the board is not empty
    after the group stage ends."""
    import hundred_fixtures
    wt = """
{{Single-innings cricket match
| round = {{anchor|match34}} '''Final'''
| date = 16 August 2026
| time = 18:00
| daynight = y
| team1 = {{cr-ENG|tr}}
| team2 = {{cr-ENG|msg}}
| score1 =
| score2 =
| result =
| venue = [[Lord's]], [[London]]
}}
"""
    fx = hundred_fixtures._parse_fixtures(wt)
    assert len(fx) == 1
    assert fx[0]["team1"] == "Trent Rockets"
    assert fx[0]["team2"] == "Manchester Super Giants"
    assert fx[0]["team2_elo_name"] == "Manchester Originals"
    assert fx[0]["date"] == "2026-08-16"
    assert fx[0]["completed"] is False


def test_hundred_fixtures_fetch_includes_knockout_when_live():
    """Live Wikipedia fetch must merge Knockout stages > Men, not only
    Fixtures > Men — otherwise Final day shows an empty board."""
    import hundred_fixtures
    hundred_fixtures._cache.clear()
    fixtures = hundred_fixtures.get_hundred_fixtures()
    if not fixtures:
        pytest.skip("Wikipedia fixture fetch unavailable in this environment")
    knockout = [
        f for f in fixtures
        if (f.get("date") or "")[:10] >= "2026-08-14"
    ]
    if not knockout:
        pytest.skip("season article has no knockout fixtures yet")
    assert any(
        f["team1"] and f["team2"] and f.get("date")
        for f in knockout
    )


def test_predict_live_fixture_returns_none_for_unknown_teams():
    fixture = {"team1": "Not A Real Team", "team2": "Also Not Real",
               "team1_elo_name": "Not A Real Team", "team2_elo_name": "Also Not Real",
               "venue": "Nowhere"}
    assert cricket_predict.predict_live_fixture(fixture) is None


def test_predict_live_fixture_uses_rebrand_alias_for_elo_lookup():
    elo_df = cricket_predict.load_elo()
    if elo_df.empty or "Oval Invincibles" not in set(elo_df["Team"]):
        pytest.skip("no Oval Invincibles Elo rating available")
    fixture = {"team1": "MI London", "team2": "Welsh Fire",
               "team1_elo_name": "Oval Invincibles", "team2_elo_name": "Welsh Fire",
               "venue": "Not A Real Venue"}
    pred = cricket_predict.predict_live_fixture(fixture)
    assert pred is not None
    assert pred["team1_win_prob"] + pred["team2_win_prob"] == pytest.approx(1.0)


def test_cricket_match_page_renders_without_weather_for_historical_match(client):
    """The dormant weather section must not break rendering for the
    historical-only matches this pipeline currently has."""
    df = cricket_predict.matches_for_competition("hnd_male", limit=1)
    if df.empty:
        pytest.skip("no Hundred matches available")
    match_id = int(df.iloc[0]["match_id"])
    resp = client.get(f"/hundred/match/{match_id}")
    assert resp.status_code == 200
    assert "Weather Forecast" not in resp.data.decode()
