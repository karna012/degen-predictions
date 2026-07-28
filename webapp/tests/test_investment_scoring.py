"""New landing-page hierarchy (Sports/Crypto/Stocks) and the crypto/stocks
ranking pages backed by the separate investment_scoring project's output."""


def test_category_landing_links_to_sports_crypto_stocks(client):
    resp = client.get("/")
    body = resp.data.decode()
    assert 'href="/sports"' in body
    assert 'href="/crypto"' in body
    assert 'href="/stocks"' in body


def test_sports_landing_links_to_soccer_and_baseball(client):
    resp = client.get("/sports")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert 'href="/sports/soccer"' in body
    assert 'href="/sports/baseball"' in body


def test_soccer_leagues_page_loads(client):
    resp = client.get("/sports/soccer")
    assert resp.status_code == 200


def test_baseball_leagues_page_loads(client):
    resp = client.get("/sports/baseball")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert 'href="/mlb/fixtures"' in body
    assert 'href="/npb/fixtures"' in body


def test_crypto_rankings_page_loads(client):
    resp = client.get("/crypto")
    assert resp.status_code == 200


def test_stocks_landing_links_to_us_and_india(client):
    resp = client.get("/stocks")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert 'href="/stocks/us"' in body
    assert 'href="/stocks/india"' in body


def test_stocks_us_rankings_page_loads(client):
    resp = client.get("/stocks/us")
    assert resp.status_code == 200


def test_stocks_india_rankings_page_loads(client):
    resp = client.get("/stocks/india")
    assert resp.status_code == 200


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
    ticker = crypto_rows.iloc[0]["ticker"]
    resp = client.get(f"/crypto/asset/{ticker}")
    assert resp.status_code == 200
    assert ticker in resp.data.decode()
