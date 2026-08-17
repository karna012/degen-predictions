"""Performance guardrails — catch order-of-magnitude regressions, not load tests."""
import time

import pytest


pytestmark = pytest.mark.performance

PAGE_BUDGET_S = 8.0
REPEAT_BUDGET_S = 8.0


def _elapsed(fn, *args, **kwargs):
    start = time.perf_counter()
    result = fn(*args, **kwargs)
    return result, time.perf_counter() - start


@pytest.mark.parametrize("path", (
    "/",
    "/sports",
    "/todays-picks",
    "/faq",
    "/robots.txt",
    "/llms.txt",
))
def test_static_ish_pages_within_budget(client, path):
    resp, elapsed = _elapsed(client.get, path)
    assert resp.status_code == 200
    assert elapsed < PAGE_BUDGET_S, f"{path} took {elapsed:.2f}s"


def test_nfl_fixtures_within_budget(client):
    resp, elapsed = _elapsed(client.get, "/nfl/fixtures")
    assert resp.status_code == 200
    assert elapsed < PAGE_BUDGET_S, f"/nfl/fixtures took {elapsed:.2f}s"


def test_unlock_api_is_cheap(client):
    _, elapsed = _elapsed(client.post, "/api/unlock/pick")
    assert elapsed < 0.5


def test_repeated_homepage_stays_flat(client):
    times = []
    for _ in range(4):
        resp, elapsed = _elapsed(client.get, "/")
        assert resp.status_code == 200
        times.append(elapsed)
    assert max(times) < REPEAT_BUDGET_S
    assert times[-1] < times[0] * 6 + 0.5
