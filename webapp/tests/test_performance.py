"""Lightweight performance/efficiency checks. Not a load-testing tool --
just guardrails so a route or data-loading function doesn't silently become
10x slower (e.g. an accidental O(n^2) loop or a CSV re-read per row)."""
import time

import pytest

import app as app_module

# Generous thresholds for a Flask dev server on local CSV files -- these
# exist to catch orders-of-magnitude regressions, not to enforce production
# SLAs on hardware this hasn't been benchmarked against.
PAGE_LOAD_BUDGET_S = 2.0
API_PREDICT_BUDGET_S = 8.0  # includes live model inference, not just I/O


def _time_it(fn, *args, **kwargs):
    start = time.perf_counter()
    result = fn(*args, **kwargs)
    return result, time.perf_counter() - start


def test_landing_page_responds_within_budget(client):
    resp, elapsed = _time_it(client.get, "/")
    assert resp.status_code == 200
    assert elapsed < PAGE_LOAD_BUDGET_S, f"/ took {elapsed:.2f}s (budget {PAGE_LOAD_BUDGET_S}s)"


def test_fixture_page_responds_within_budget(client, league_key):
    resp, elapsed = _time_it(client.get, f"/{league_key}/")
    assert resp.status_code == 200
    assert elapsed < PAGE_LOAD_BUDGET_S, f"/{league_key}/ took {elapsed:.2f}s"


def test_load_predictions_is_fast_standalone(league_key):
    """Isolates CSV-loading time from template rendering."""
    _, elapsed = _time_it(app_module.load_predictions, league_key)
    assert elapsed < 1.0, f"load_predictions({league_key}) took {elapsed:.2f}s"


def test_compute_unlocked_round_is_cheap_on_a_full_season(league_key):
    df = app_module.load_predictions(league_key)
    _, elapsed = _time_it(app_module.compute_unlocked_round, df)
    assert elapsed < 0.2, f"compute_unlocked_round took {elapsed:.2f}s for {len(df)} rows"


def test_repeated_requests_dont_regress_response_time(client):
    """Rough check that per-request cost stays flat across repeats (no
    unbounded in-memory growth / no cache that gets slower over time)."""
    times = []
    for _ in range(5):
        _, elapsed = _time_it(client.get, "/premier_league/")
        times.append(elapsed)
    assert max(times) < PAGE_LOAD_BUDGET_S
    # last request shouldn't be dramatically slower than the first
    assert times[-1] < times[0] * 5 + 0.5


@pytest.mark.slow
def test_live_predict_api_responds_within_budget(client):
    df = app_module.load_predictions("premier_league")
    if df.empty:
        pytest.skip("no premier_league predictions available")
    row = df.iloc[0]
    resp, elapsed = _time_it(
        client.get, f"/premier_league/api/predict/{row['HomeTeam']}/{row['AwayTeam']}"
    )
    assert resp.status_code == 200
    assert elapsed < API_PREDICT_BUDGET_S, f"live predict took {elapsed:.2f}s"
