"""Regression tests for specific bugs found and fixed earlier: these lock in
behavior so future changes can't silently reintroduce them."""
import os

import pandas as pd

import app as app_module


def test_load_backtest_handles_mixed_date_formats_without_crashing(league_key):
    """Regression: Ligue1's backtest CSV mixes date formats across concatenated
    seasons, which crashed pd.to_datetime without format='mixed'. This must not
    raise for any configured league."""
    app_module.load_backtest(league_key)  # raises on regression, no assert needed


def test_low_confidence_fixtures_get_alert_badge_not_dimmed(client):
    """Regression: low-confidence fixtures used to just be faded (opacity),
    which read as broken/disabled rather than a deliberate warning. They must
    render an explicit low-confidence chip/note instead."""
    df = app_module.load_predictions("premier_league")
    low_conf = df[df["Data_Confidence"].str.contains("Low", na=False)]
    if low_conf.empty:
        return
    resp = client.get("/premier_league/")
    body = resp.data.decode()
    assert "confidence-chip is-low" in body or "confidence-alert" in body
    assert "Low conf" in body or "Low confidence" in body


def test_nav_shows_login_signup_when_logged_out(client):
    """Accounts were reintroduced (email/password + Google) for real user
    counting -- logged-out visitors should see Log In/Sign Up in the nav,
    but never a stale Profile link (that page doesn't exist)."""
    resp = client.get("/")
    body = resp.data.decode()
    assert 'href="/login"' in body
    assert 'href="/signup"' in body
    assert 'href="/profile"' not in body


def test_no_credits_or_deposit_markup_anywhere(client, league_key):
    """Regression: the play-money credits/crypto-deposit system was removed
    entirely -- no leftover markup should render on the fixtures page."""
    resp = client.get(f"/{league_key}/")
    body = resp.data.decode()
    for dead_id in ("credits-bar", "deposit-panel", "credits-amount"):
        assert dead_id not in body


def test_no_gambling_promotion_language_on_any_page(client, league_key):
    """Regression / compliance: pages should never contain language that
    reads as gambling promotion (as opposed to the disclaimers explicitly
    saying the site does NOT do this, which are fine and expected)."""
    resp = client.get(f"/{league_key}/")
    body = resp.data.decode().lower()
    for banned in ("place your bet", "bet now", "wager now", "join now and win"):
        assert banned not in body


def test_round_unlock_is_sequential_not_just_date_based_per_round(league_key):
    """Regression: an earlier version of this check only looked at whether a
    single round's dates had passed, which could let round 5 unlock while
    round 3 was still pending (e.g. postponed fixtures). The loop must break
    on the first incomplete round rather than continuing past it."""
    df = app_module.load_predictions(league_key)
    if df.empty:
        return
    unlocked = app_module.compute_unlocked_round(df)
    all_rounds = sorted(df["RoundNumber"].unique().tolist())
    # every round before `unlocked` must actually be in the past
    now = pd.Timestamp.now("UTC").tz_localize(None)
    dates = pd.to_datetime(df["DateUtc"])
    if dates.dt.tz is not None:
        dates = dates.dt.tz_localize(None)
    for r in all_rounds:
        if r >= unlocked:
            break
        round_max_date = dates[df["RoundNumber"] == r].max()
        assert round_max_date < now, f"Round {r} should be complete but isn't"


def test_fixture_card_predict_button_says_get_predictions(client):
    """Regression: button copy churned through several states this session
    ('Predict (free)', 'Unlock $X.XX', ...) -- confirm it's settled."""
    resp = client.get("/premier_league/")
    body = resp.data.decode()
    assert "Unlock prediction" in body
    assert "Predict (free)" not in body
    if "Unlock prediction" in body:
        assert 'data-lock="1"' in body


def test_select_elements_have_dark_color_scheme_applied():
    """Regression: native <select> dropdown popups rendered as jarring white
    boxes until color-scheme: dark was added globally."""
    css_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "static", "style.css",
    )
    with open(css_path, encoding="utf-8") as f:
        css = f.read()
    assert "color-scheme:dark" in css or "color-scheme: dark" in css


def test_no_forced_ad_gate_prediction_lock():
    """Regression: an earlier ad-gate modal forced users to watch a timed ad
    before predictions unlocked, then was explicitly removed. AdSense itself
    (banner ads via adsbygoogle.js) was later explicitly re-added and is
    expected -- but the forced watch-before-you-can-predict gate must not
    silently reappear."""
    webapp_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    banned_terms = ("ad-gate", "ADSENSE_CLIENT_ID")
    for root, _dirs, files in os.walk(os.path.join(webapp_dir, "templates")):
        for name in files:
            path = os.path.join(root, name)
            with open(path, encoding="utf-8") as f:
                content = f.read()
            for term in banned_terms:
                assert term not in content, f"{term} found in {path}"


def test_adsense_verification_script_present(client):
    """AdSense site verification requires the publisher script literally in
    <head> (Google's crawler often fails if it is only injected deferred)."""
    resp = client.get("/")
    body = resp.data.decode()
    assert "pagead2.googlesyndication.com/pagead/js/adsbygoogle.js" in body
    assert "ca-pub-9692020533590575" in body
    head = body.split("</head>", 1)[0]
    assert "adsbygoogle.js?client=ca-pub-9692020533590575" in head
    assert 'crossorigin="anonymous"' in head


def test_about_page_has_no_metrics_table(client, league_key):
    """Regression: the About page used to show a model-info/metrics table
    (first with model-architecture columns, then with accuracy/log loss/MAE
    columns) -- removed entirely at the user's request. About should now be
    just author info + general, non-technical content, no <table> at all."""
    resp = client.get(f"/{league_key}/about")
    body = resp.data.decode()
    # Scope to the page's own content, not the site-wide footer (which links
    # to the separate /accuracy hub page on every page).
    main = body.split('<main', 1)[1].split("</main>", 1)[0]
    assert "<table>" not in main
    assert "<th>" not in main
    assert "Accuracy" not in main
    assert "Log Loss" not in main
    assert "Winner used" not in main


def test_no_model_architecture_names_leaked_anywhere(client, league_key):
    """Regression: internal model architecture names (Random Forest, XGBoost,
    CatBoost) and implementation-detail terms (stacked meta-learner, avg
    ensemble) must not appear on any public-facing page -- these are
    implementation details, not something a reader needs."""
    for path in (f"/{league_key}/about", f"/{league_key}/", "/"):
        resp = client.get(path)
        body = resp.data.decode()
        for banned in ("Random Forest", "XGBoost", "CatBoost", "stacked meta-learner"):
            assert banned not in body, f"{banned!r} leaked on {path}"


def test_about_page_shows_valuable_content(client):
    """The About page should be genuinely useful, non-technical content --
    not empty, not a stub, and without author/hosting/methodology UI blocks."""
    resp = client.get("/premier_league/about")
    body = resp.data.decode()
    assert "About the Author" not in body
    assert "Deployment & Hosting" not in body
    assert "How we score (full slate vs 70% tier)" not in body
    assert "What this site does" in body
    assert "Honest limitations" in body
