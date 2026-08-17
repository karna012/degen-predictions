"""A/B / consistency tests.

This product does not run split experiments (no experiment IDs, no variant
cookies, no multivariate CTAs). These cases lock in a deterministic public
experience so an accidental A/B framework cannot silently fork copy.
"""
import pytest


pytestmark = pytest.mark.ab


def test_no_experiment_cookie_on_anonymous_home(client):
    resp = client.get("/")
    set_cookie = ";".join(resp.headers.getlist("Set-Cookie")).lower()
    for needle in ("variant", "experiment", "ab_test", "optimizely", "gaexp"):
        assert needle not in set_cookie


def test_homepage_landmarks_are_stable_across_repeats(client):
    first = client.get("/").data.decode()
    second = client.get("/").data.decode()
    for needle in ("Sports", "Today's Picks", "Degen Predictions"):
        assert needle in first and needle in second
    assert ("See fixtures" in first) == ("See fixtures" in second)


def test_sports_hub_cta_copy_is_not_randomized(client):
    bodies = [client.get("/sports").data.decode() for _ in range(3)]
    for body in bodies:
        assert "AMERICAN FOOTBALL" in body
        assert "NFL predictions" in body
    assert bodies[0].count("AMERICAN FOOTBALL") == bodies[1].count("AMERICAN FOOTBALL")
    assert bodies[0].count("Get Predictions") == bodies[2].count("Get Predictions")


def test_nfl_unlock_label_is_canonical(client):
    body = client.get("/nfl/fixtures").data.decode()
    # Product rule: one unlock verb. Variants like "Get Prediction" must not appear.
    assert "Get Prediction" not in body
    if "predict-btn" in body:
        assert "Unlock prediction" in body
