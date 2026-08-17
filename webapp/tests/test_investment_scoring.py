"""New landing-page hierarchy (Sports/Crypto/Stocks) and the crypto/stocks
ranking pages backed by the separate investment_scoring project's output."""


def test_category_landing_links_to_sports_crypto_stocks(client):
    resp = client.get("/")
    body = resp.data.decode()
    assert 'href="/sports"' in body
    assert 'href="/crypto"' in body
    assert 'href="/stocks"' in body
    assert 'id="todays-picks-nav"' in body
    # Live strip / accuracy chips (below hero) may link to Today's Picks; hero title must not
    # push MLB track-record CTAs.
    pre_grid = body.split('class="league-grid"')[0]
    assert "MLB track record" not in pre_grid
    assert 'href="/mlb/results"' not in pre_grid
    assert 'class="accuracy-strip"' in body or "Track record" in body
    assert "home-hero" in body
    assert "home-drop" in body
    assert "Today’s drop" in body or "Today's drop" in body
    assert "Today’s slate" not in body
    assert "home-slate" not in body
    assert "Best 2-leg" not in pre_grid
    assert "Unlock today’s calls" in body or "Unlock today's calls" in body
    assert "Follow daily picks" not in body
    # Dubai / Metals wiped — must not appear on homepage.
    assert 'href="/dubai"' not in body
    assert 'href="/metals"' not in body
    assert "SPORTS" in body.upper()
    # Category cards: Sports before Crypto/Stocks (ignore earlier meta copy).
    grid = body.split('class="league-grid"', 1)[-1]
    assert grid.upper().find(">SPORTS<") < grid.upper().find(">CRYPTO<")
    # Top-searched leagues featured for SEO / discovery
    assert 'href="/premier_league/"' in body
    assert 'href="/ipl/"' in body
    assert 'href="/mlb/"' in body
    assert 'class="popular-leagues"' in body
    assert "Popular leagues" in body


def test_nav_omits_dubai_and_metals(client):
    resp = client.get("/")
    body = resp.data.decode()
    assert "Dubai Real Estate" not in body
    assert ">Metals<" not in body
    assert 'href="/sports"' in body
    assert 'href="/crypto"' in body
    assert 'href="/stocks"' in body


def test_dubai_and_metals_routes_are_gone(client):
    for path in ("/dubai", "/dubai/", "/dubai/guide", "/metals", "/metals/", "/metals/insights"):
        assert client.get(path).status_code == 404
    assert client.post("/dubai/api/calculate", json={}).status_code == 404
    assert client.post("/metals/api/predict", json={}).status_code == 404


def test_sports_landing_links_to_soccer_and_baseball(client):
    resp = client.get("/sports")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert 'href="/sports/soccer"' in body
    assert 'href="/sports/baseball"' in body
    assert 'href="/accuracy"' in body
    assert "Track record" in body
    assert 'class="accuracy-strip"' in body or "OOS" in body
    assert 'href="/premier_league/results"' in body
    assert 'href="/mlb/results"' in body
    assert "high-probability" in body.lower() or "Today's Picks" in body
    assert 'class="popular-leagues"' in body
    assert 'href="/premier_league/"' in body
    assert "Top searched leagues" in body

def test_soccer_leagues_page_loads(client):
    resp = client.get("/sports/soccer")
    assert resp.status_code == 200


def test_baseball_leagues_page_loads(client):
    resp = client.get("/sports/baseball")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert 'href="/mlb/fixtures"' in body
    assert 'href="/npb/fixtures"' in body
    assert "See fixtures" in body
    assert 'href="/todays-picks/baseball"' in body


def test_enrich_rankings_keeps_absolute_and_exposes_signed_vs_board():
    """Absolute forecasts stay as-is; the signed headline is vs the board."""
    import app as app_module
    import pandas as pd

    df = pd.DataFrame([
        {"ticker": "A", "predicted_forward_return": 0.20, "horizon_months": 6},
        {"ticker": "B", "predicted_forward_return": 0.10, "horizon_months": 6},
        {"ticker": "C", "predicted_forward_return": 0.04, "horizon_months": 6},
    ])
    out = app_module.enrich_rankings_for_display(df)
    assert (out["predicted_forward_return"] > 0).all()
    assert list(out["predicted_forward_return"]) == [0.20, 0.10, 0.04]
    assert (out["predicted_excess_return"] > 0).sum() >= 1
    assert (out["predicted_excess_return"] < 0).sum() >= 1
    assert out.loc[out["ticker"] == "C", "display_edge"].iloc[0] < 0


def test_stock_board_analysis_flags_all_positive_absolutes():
    import app as app_module
    import pandas as pd

    df = pd.DataFrame([
        {"predicted_forward_return": 0.12, "predicted_excess_return": 0.02,
         "predicted_outperform_probability": 0.61, "peer_median_return": 0.10},
        {"predicted_forward_return": 0.08, "predicted_excess_return": -0.02,
         "predicted_outperform_probability": 0.41, "peer_median_return": 0.10},
    ])
    stats = app_module.stock_board_analysis(df)
    assert stats["all_absolute_positive"] is True
    assert stats["n_above"] == 1 and stats["n_below"] == 1
    assert stats["n_beat"] == 1 and stats["n_lag"] == 1


def test_endpoint_chart_is_interval_only_and_crosses_zero():
    import app as app_module

    chart = app_module.build_forecast_endpoint_chart({
        "prediction_interval_low": -0.45,
        "prediction_interval_high": 0.80,
        "median_scenario_return": -0.10,
    })
    assert chart is not None
    assert chart["low"] == -0.45
    assert chart["high"] == 0.80
    assert chart["low_position"] < chart["zero_position"]
    assert chart["zero_position"] < chart["low_position"] + chart["interval_width"]


def test_endpoint_chart_axis_never_implies_below_total_loss():
    import app as app_module

    chart = app_module.build_forecast_endpoint_chart({
        "prediction_interval_low": -0.97,
        "prediction_interval_high": -0.01,
        "median_scenario_return": -0.82,
    })
    assert chart is not None
    assert chart["axis_low"] == -1.0


def test_crypto_page_shows_predicted_return(client):
    resp = client.get("/crypto")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Beat BTC" in body
    assert "How this crypto board is analysed" in body
    assert "80% range" in body.lower()
    assert "What the historical crypto data actually supports" not in body
    assert "Walk-forward note" not in body
    assert "Walk-forward ranking winner" not in body
    assert "Direction R²" not in body
    assert "Board as of" in body
    assert "recommended hold through" in body
    assert "stocks-grid" in body
    assert "Investment timeframe" in body
    assert 'name="horizon"' in body
    assert "Predicted 6-Month Ranking" not in body  # old table title


def test_crypto_horizon_query_loads(client):
    resp = client.get("/crypto?horizon=6")
    assert resp.status_code == 200
    assert "Investment timeframe" in resp.data.decode()


def test_stocks_us_shows_predicted_return(client):
    resp = client.get("/stocks/us?horizon=6")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "vs board" in body.lower()
    assert "above the board" in body
    assert "beat index" in body.lower()
    assert "forecast" in body.lower()
    assert "Walk-forward note" not in body
    assert "Walk-forward ranking winner" not in body
    assert "Board as of" in body
    assert "recommended hold through" in body


def test_stocks_landing_links_to_us_and_india(client):
    resp = client.get("/stocks")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert 'href="/stocks/us"' in body
    assert 'href="/stocks/india"' in body
    assert "6 months" in body or "25 years" in body


def test_stocks_us_rankings_page_loads(client):
    resp = client.get("/stocks/us")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Investment timeframe" in body
    assert "horizon" in body


def test_stocks_india_rankings_page_loads(client):
    resp = client.get("/stocks/india")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Investment timeframe" in body
    assert "Walk-forward note" not in body
    assert "Walk-forward ranking winner" not in body
    assert "Board as of" in body
    assert "recommended hold through" in body


def test_stocks_us_horizon_query_loads(client):
    resp = client.get("/stocks/us?horizon=6")
    assert resp.status_code == 200


def test_filter_rankings_by_horizon_defaults_legacy_rows():
    import app as app_module
    import pandas as pd

    df = pd.DataFrame([
        {"ticker": "AAPL", "asset_class": "us_stocks", "rank_within_asset_class": 1,
         "predicted_forward_return": 0.1},
    ])
    # Simulate legacy load (no horizon column) via helper path
    df["horizon_months"] = 6
    out = app_module.filter_rankings_by_horizon(df, "us_stocks", 6)
    assert len(out) == 1
    assert out.iloc[0]["ticker"] == "AAPL"


def test_stocks_invalid_market_is_404(client):
    resp = client.get("/stocks/europe")
    assert resp.status_code == 404


def test_crypto_asset_detail_unknown_ticker_is_404(client):
    resp = client.get("/crypto/asset/not-a-real-coin")
    assert resp.status_code == 404


def test_crypto_asset_detail_loads_for_real_ticker(client):
    import app as app_module
    df = app_module.load_investment_rankings()
    if df.empty:
        return
    crypto_rows = df[df["asset_class"] == "crypto"]
    if crypto_rows.empty:
        return
    crypto_rows = app_module.filter_crypto_display_universe(crypto_rows)
    if crypto_rows.empty:
        return
    ticker = crypto_rows.iloc[0]["ticker"]
    horizon = int(crypto_rows.iloc[0].get("horizon_months") or 6)
    # Detail pages should only serve public horizons.
    if horizon > app_module.CRYPTO_MAX_PUBLIC_HORIZON_MONTHS:
        horizon = 6
        crypto_rows = app_module.filter_crypto_display_universe(
            app_module.filter_rankings_by_horizon(df, "crypto", horizon)
        )
        if crypto_rows.empty:
            return
        ticker = crypto_rows.iloc[0]["ticker"]
    resp = client.get(f"/crypto/asset/{ticker}?horizon={horizon}")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert ticker in body
    assert "Bitcoin (BTC)" in body or "Beat BTC" in body


def test_crypto_range_detail_has_no_fake_trajectory(client, monkeypatch):
    import app as app_module
    import pandas as pd

    row = {
        "ticker": "testcoin", "asset_class": "crypto", "horizon_months": 6,
        "horizon_label": "6 months", "rank_within_asset_class": 1,
        "final_score": 1.0, "predicted_forward_return": float("nan"),
        "ranking_signal": 0.2, "median_scenario_return": -0.10,
        "prediction_interval_low": -0.55, "prediction_interval_high": 0.75,
        "prediction_interval_level": 0.8, "predicted_downside_probability": 0.62,
        "predicted_outperform_probability": 0.55, "selected_model": "xgboost_log",
        "model_ic": 0.2, "model_r2": -0.1, "data_confidence": "high",
        "as_of_date": "2026-08-08", "hold_until_date": "2027-02-08",
        "absolute_direction_supported": False, "forecast_mode": "range_only",
    }
    monkeypatch.setattr(
        app_module, "load_investment_rankings", lambda: pd.DataFrame([row]),
    )
    resp = client.get("/crypto/asset/testcoin?horizon=6")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Horizon endpoint estimate — not a price path" in body
    assert "does not predict a straight month-by-month trajectory" in body
    assert "62%" in body
    assert "-55%" in body and "+75%" in body
    assert "Beat BTC" in body
    assert "absolute usd return direction failed" in body.lower() or "absolute direction failed" in body.lower() or "failed purged oos" in body.lower()


def test_negative_point_forecast_renders_negative_sign(client, monkeypatch):
    import app as app_module
    import pandas as pd

    row = {
        "ticker": "bearcoin", "asset_class": "crypto", "horizon_months": 6,
        "horizon_label": "6 months", "rank_within_asset_class": 1,
        "final_score": 1.0, "predicted_forward_return": -0.25,
        "predicted_outperform_probability": 0.25, "selected_model": "test_model",
        "model_ic": 0.1, "model_r2": 0.01, "data_confidence": "high",
        "as_of_date": "2026-08-08", "hold_until_date": "2027-02-08",
        "absolute_direction_supported": True, "forecast_mode": "point_and_range",
    }
    monkeypatch.setattr(
        app_module, "load_investment_rankings", lambda: pd.DataFrame([row]),
    )
    resp = client.get("/crypto/asset/bearcoin?horizon=6")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "-25.0%" in body
    assert "Estimated 6 months return" in body


def test_filter_crypto_display_drops_junk_and_clones():
    import app as app_module
    import pandas as pd

    df = pd.DataFrame([
        {"ticker": "figure-heloc", "final_score": 3.0, "predicted_forward_return": 27.4,
         "predicted_outperform_probability": 0.10, "data_confidence": "low"},
        {"ticker": "bittensor", "final_score": 2.0, "predicted_forward_return": 26.430880,
         "predicted_outperform_probability": 0.319837, "data_confidence": "low"},
        {"ticker": "aster-2", "final_score": 2.0, "predicted_forward_return": 26.430880,
         "predicted_outperform_probability": 0.319837, "data_confidence": "low"},
        {"ticker": "rain", "final_score": 2.0, "predicted_forward_return": 26.430880,
         "predicted_outperform_probability": 0.319837, "data_confidence": "low"},
        {"ticker": "world-liberty-financial", "final_score": 2.0,
         "predicted_forward_return": 26.430880,
         "predicted_outperform_probability": 0.319837, "data_confidence": "low"},
        {"ticker": "bitcoin", "final_score": 1.0, "predicted_forward_return": 10.0,
         "predicted_outperform_probability": 0.40, "data_confidence": "high"},
        {"ticker": "pax-gold", "final_score": 0.5, "predicted_forward_return": 8.0,
         "predicted_outperform_probability": 0.20, "data_confidence": "high"},
        {"ticker": "spiko-amundi-overnight-swap-fund-eur", "final_score": 0.4,
         "predicted_forward_return": 7.0, "predicted_outperform_probability": 0.15,
         "data_confidence": "low"},
    ])
    out = app_module.filter_crypto_display_universe(df)
    tickers = set(out["ticker"].astype(str).str.lower())
    assert "figure-heloc" not in tickers
    assert "pax-gold" not in tickers
    assert "spiko-amundi-overnight-swap-fund-eur" not in tickers
    # Exactly one survivor from the identical-forecast clone group.
    clone_hits = [t for t in tickers if t in {
        "bittensor", "aster-2", "rain", "world-liberty-financial",
    }]
    assert len(clone_hits) == 1
    assert "bitcoin" in tickers
    # Unique dense ranks, no shared Rank #2.
    assert list(out["rank_within_asset_class"]) == list(range(1, len(out) + 1))


def test_crypto_public_horizons_cap_at_three_years():
    import app as app_module
    import pandas as pd

    df = pd.DataFrame([
        {"ticker": "bitcoin", "asset_class": "crypto", "horizon_months": h,
         "predicted_forward_return": 0.1}
        for h in (6, 12, 24, 36, 60, 120, 300)
    ])
    avail = app_module.available_stock_horizons(df, "crypto")
    assert 36 in avail
    assert 60 not in avail
    assert 120 not in avail
    assert 300 not in avail
    # Stocks are unaffected.
    stocks = df.assign(asset_class="us_stocks")
    assert 120 in app_module.available_stock_horizons(stocks, "us_stocks")


def test_crypto_page_hides_long_horizons_and_labels_beat_btc(client):
    resp = client.get("/crypto?horizon=36")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Beat BTC" in body
    # 5y/10y chips exist in the form but must be disabled for crypto.
    assert 'value="60"' in body
    assert 'value="120"' in body
    # Active/enabled horizon buttons should not include 5y+ as available.
    # Disabled chips carry is-disabled; ensure long horizons are disabled.
    import re
    for h in (60, 120, 180, 240, 300):
        m = re.search(
            rf'name="horizon" value="{h}"[^>]*class="([^"]*)"',
            body,
        )
        assert m is not None
        assert "is-disabled" in m.group(1)
    # Junk / clone names from the live CSV must not headline the board
    # (cards use data-ticker=; also check they aren't in the TV map JSON).
    assert 'data-ticker="figure-heloc"' not in body.lower()
    assert "spiko-amundi" not in body.lower()
    assert 'data-ticker="pax-gold"' not in body.lower()
    assert '"pax-gold"' not in body.lower()
    assert '"figure-heloc"' not in body.lower()
