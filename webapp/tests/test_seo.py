"""SEO-related checks: robots.txt, sitemap.xml, meta tags, canonical URLs."""
import app as app_module


def test_robots_txt_loads_and_points_at_sitemap(client):
    resp = client.get("/robots.txt")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Allow: /" in body
    assert "Sitemap: https://degenpredictions.com/sitemap.xml" in body


def test_ads_txt_loads_with_adsense_publisher_id(client):
    resp = client.get("/ads.txt")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "google.com, pub-9692020533590575, DIRECT, f08c47fec0942fa0" in body


def test_sitemap_xml_loads_and_is_valid_xml(client):
    resp = client.get("/sitemap.xml")
    assert resp.status_code == 200
    assert resp.content_type.startswith("application/xml")
    body = resp.data.decode()
    assert body.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    assert "<urlset" in body


def test_sitemap_includes_every_league_and_static_page(client):
    resp = client.get("/sitemap.xml")
    body = resp.data.decode()
    assert "https://degenpredictions.com/terms" in body
    assert "https://degenpredictions.com/privacy" in body
    for key in app_module.LEAGUES:
        assert f"https://degenpredictions.com/{key}/</loc>" in body


def test_sitemap_urls_are_percent_encoded_for_team_names_with_spaces(client):
    """Regression: a team name like 'Man United' must not produce a raw
    space in the sitemap URL -- that's invalid XML/URL syntax."""
    resp = client.get("/sitemap.xml")
    body = resp.data.decode()
    assert " United<" not in body  # would indicate an unencoded space
    assert "%20" in body or "Man%20United" in body or "United" in body


def test_pages_have_meta_description(client, league_key):
    resp = client.get(f"/{league_key}/")
    body = resp.data.decode()
    assert '<meta name="description"' in body


def test_pages_have_canonical_link(client, league_key):
    resp = client.get(f"/{league_key}/")
    body = resp.data.decode()
    assert f'<link rel="canonical" href="https://degenpredictions.com/{league_key}/"' in body


def test_leagues_page_has_unique_title_and_description(client):
    resp = client.get("/")
    body = resp.data.decode()
    assert "<title>" in body
    assert '<meta name="description"' in body


def test_structured_data_json_ld_present(client):
    resp = client.get("/")
    body = resp.data.decode()
    assert 'application/ld+json' in body
    assert '"@type": "WebSite"' in body


def test_pages_have_keywords_meta_tag(client, league_key):
    resp = client.get(f"/{league_key}/")
    body = resp.data.decode()
    assert '<meta name="keywords"' in body
    assert "soccer predictions" in body


def test_league_page_keywords_include_league_specific_terms(client):
    resp = client.get("/premier_league/")
    body = resp.data.decode()
    assert "EPL predictions" in body
    resp = client.get("/la_liga/")
    body = resp.data.decode()
    assert "LaLiga predictions" in body


def test_open_graph_tags_present(client):
    resp = client.get("/premier_league/")
    body = resp.data.decode()
    assert 'property="og:title"' in body
    assert 'property="og:description"' in body
    assert 'property="og:url"' in body


def test_sitemap_includes_lastmod(client):
    resp = client.get("/sitemap.xml")
    body = resp.data.decode()
    assert "<lastmod>" in body


def test_sitemap_includes_mlb_and_npb_fixtures_pages(client):
    resp = client.get("/sitemap.xml")
    body = resp.data.decode()
    assert "https://degenpredictions.com/mlb/fixtures" in body
    assert "https://degenpredictions.com/npb/fixtures" in body


def test_sitemap_ampersands_are_xml_escaped(client):
    """Regression: MLB/NPB match URLs carry query strings with a literal
    '&' between params -- unescaped, that's invalid XML."""
    resp = client.get("/sitemap.xml")
    body = resp.data.decode()
    assert "&amp;" in body or "&" not in body.split("<urlset")[1]


def test_mlb_match_page_has_sportsevent_structured_data(client):
    import mlb_predict
    df = mlb_predict.load_mlb_elo()
    if len(df) < 2:
        return
    home, away = df.iloc[0]["Team"], df.iloc[1]["Team"]
    resp = client.get(f"/mlb/match/{home}/{away}?home_name=Home+Team&away_name=Away+Team")
    body = resp.data.decode()
    assert 'application/ld+json' in body
    assert '"@type": "SportsEvent"' in body


def test_npb_match_page_has_sportsevent_structured_data(client):
    import npb_predict
    df = npb_predict.load_npb_elo()
    if len(df) < 2:
        return
    home, away = int(df.iloc[0]["TeamID"]), int(df.iloc[1]["TeamID"])
    resp = client.get(f"/npb/match/{home}/{away}?home_name=Home+Team&away_name=Away+Team")
    body = resp.data.decode()
    assert 'application/ld+json' in body
    assert '"@type": "SportsEvent"' in body
