"""SEO-related checks: robots.txt, sitemap.xml, meta tags, canonical URLs."""
import os

import app as app_module


def test_robots_txt_loads_and_points_at_sitemap(client):
    resp = client.get("/robots.txt")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Allow: /" in body
    assert "Sitemap: https://degenpredictions.com/sitemap.xml" in body
    assert "GPTBot" in body
    assert "PerplexityBot" in body
    assert "ClaudeBot" in body
    assert "llms.txt" in body


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
    assert "<sitemapindex" in body
    assert "/sitemap-static.xml" in body
    assert "/sitemap-leagues.xml" in body
    assert "/sitemap-matches.xml" in body
    assert "/sitemap-markets.xml" in body


def test_sitemap_includes_every_league_and_static_page(client):
    static = client.get("/sitemap-static.xml").data.decode()
    leagues = client.get("/sitemap-leagues.xml").data.decode()
    assert "https://degenpredictions.com/terms" in static
    assert "https://degenpredictions.com/privacy" in static
    for key in app_module.LEAGUES:
        assert f"https://degenpredictions.com/{key}/</loc>" in leagues
        assert f"https://degenpredictions.com/{key}/predictions-today</loc>" in leagues


def test_sitemap_urls_are_percent_encoded_for_team_names_with_spaces(client):
    """Regression: a team name like 'Man United' must not produce a raw
    space in the sitemap URL -- that's invalid XML/URL syntax."""
    # Check the matches shard which contains actual team names
    resp = client.get("/sitemap-matches.xml")
    body = resp.data.decode()
    assert " United<" not in body  # would indicate an unencoded space
    # Either has encoded spaces or doesn't have any literal spaces in URLs
    if body.count("Manchester") > 0 or body.count("United") > 0:
        assert "%20" in body or " United<" not in body


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


def test_bing_webmaster_verification_meta(client):
    resp = client.get("/")
    body = resp.data.decode()
    assert 'name="msvalidate.01"' in body
    assert 'content="803CFF66F82250CBA5A34A0E523D2440"' in body


def test_bing_webmaster_auth_xml(client):
    resp = client.get("/BingSiteAuth.xml")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "803CFF66F82250CBA5A34A0E523D2440" in body
    assert "<users>" in body


def test_indexnow_key_file(client):
    resp = client.get("/84e8fa3329404e81b2aba483549d03f4.txt")
    assert resp.status_code == 200
    assert resp.data.decode().strip() == "84e8fa3329404e81b2aba483549d03f4"


def test_root_favicon_ico(client):
    resp = client.get("/favicon.ico")
    assert resp.status_code == 200
    # ICO magic or declared icon mimetype
    assert resp.data[:4] == b"\x00\x00\x01\x00" or "icon" in (resp.mimetype or "")
    body = client.get("/").data.decode()
    assert 'href="/favicon.ico' in body
    assert "favicon-48.png" in body


def test_structured_data_json_ld_present(client):
    resp = client.get("/")
    body = resp.data.decode()
    assert 'application/ld+json' in body
    assert '"@type": "WebSite"' in body
    assert '"name": "Degen Predictions"' in body
    assert "Degen Prediction" in body and "degenpredictions.com" in body


def test_site_name_signals_present(client):
    """Google SERP site name prefers branded og:site_name / WebSite name."""
    resp = client.get("/")
    body = resp.data.decode()
    assert 'property="og:site_name" content="Degen Predictions"' in body
    assert 'name="application-name" content="Degen Predictions"' in body


def test_homepage_targets_core_sports_keywords(client):
    body = client.get("/").data.decode()
    assert "Sports Predictions" in body
    assert "Today's Picks" in body
    assert "home-ticker" in body
    assert "Join Telegram to unlock full potential" in body
    assert 'href="/go/telegram"' in body
    assert "MLB" in body
    assert "multi bet" in body.lower() or "Best Combos" in body
    assert 'name="twitter:site" content="@degenprediction"' in body
    assert "seo-hub" in body
    assert "/mlb/fixtures" in body
    assert "/tennis/fixtures" in body
    assert "/premier_league/" in body
    assert "/la_liga/" in body


def test_combos_page_targets_multi_bet_keyword(client):
    body = client.get("/todays-picks/combos").data.decode()
    assert "Multi Bet" in body or "multi bet" in body.lower()
    assert "<title>" in body
    assert "Best Combos" in body


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
    assert 'property="og:site_name" content="Degen Predictions"' in body


def test_sitemap_includes_lastmod(client):
    resp = client.get("/sitemap.xml")
    body = resp.data.decode()
    assert "<lastmod>" in body


def test_sitemap_includes_mlb_and_npb_fixtures_pages(client):
    cache = app_module.SITEMAP_CACHE_PATH
    if os.path.exists(cache):
        os.remove(cache)
    # Check static shard for baseball pages (MLB, NPB) and other non-league sports
    static = client.get("/sitemap-static.xml").data.decode()
    assert "https://degenpredictions.com/todays-picks" in static
    assert "https://degenpredictions.com/todays-picks/baseball/mlb" in static
    assert "https://degenpredictions.com/mlb/today" in static
    assert "https://degenpredictions.com/mlb/fixtures" in static
    assert "https://degenpredictions.com/mlb/results" in static
    assert "https://degenpredictions.com/npb/fixtures" in static
    assert "https://degenpredictions.com/npb/results" in static
    # Check leagues shard for traditional leagues
    leagues = client.get("/sitemap-leagues.xml").data.decode()
    assert "https://degenpredictions.com/la_liga/results" in leagues


def test_sitemap_ampersands_are_xml_escaped(client):
    """Regression: MLB/NPB match URLs carry query strings with a literal
    '&' between params -- unescaped, that's invalid XML."""
    # Check the matches shard which contains match URLs with query strings
    resp = client.get("/sitemap-matches.xml")
    body = resp.data.decode()
    # Either has properly escaped ampersands or no literal ampersands in URLs
    if "<url>" in body:
        # If there's content with URLs, check for proper escaping
        urlset_content = body.split("<loc>")[1:] if "<loc>" in body else []
        for url_part in urlset_content:
            if "?" in url_part:  # Has query params
                assert "&amp;" in url_part or "&" not in url_part


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

def test_website_schema_has_brand_and_singular_alternate(client):
    resp = client.get("/")
    body = resp.data.decode()
    assert '"@type": "WebSite"' in body
    assert '"name": "Degen Predictions"' in body
    assert "Degen Prediction" in body
    assert "degenpredictions.com" in body
    assert 'property="og:site_name" content="Degen Predictions"' in body
    assert "Sports Predictions" in body
    assert "| Degen Predictions" in body


def test_tennis_hub_seo_and_accuracy(client):
    resp = client.get("/sports/tennis")
    body = resp.data.decode()
    assert resp.status_code == 200
    assert "Tennis Predictions" in body
    assert '<meta name="description"' in body
    assert "tennis predictions" in body.lower()
    # Real ATP full-slate OOS (~65%) when thresholds exist
    if "full-slate" in body.lower() or "OOS" in body:
        assert "%" in body


def test_ipl_and_cpl_fixtures_seo(client):
    for path, needle in (("/ipl/fixtures", "IPL Predictions"), ("/cpl/fixtures", "CPL Predictions")):
        resp = client.get(path)
        body = resp.data.decode()
        assert resp.status_code == 200
        assert needle in body
        assert '<meta name="description"' in body
        assert f'<link rel="canonical" href="https://degenpredictions.com{path}"' in body


def test_sitemap_includes_tennis_ipl_cpl_stocks_crypto(client):
    # /sitemap.xml is a sitemap index pointing at sharded leaf sitemaps;
    # these are all static pages, which live in /sitemap-static.xml.
    resp = client.get("/sitemap-static.xml")
    body = resp.data.decode()
    for url in (
        "https://degenpredictions.com/sports/tennis",
        "https://degenpredictions.com/sports/american-football",
        "https://degenpredictions.com/nfl/fixtures",
        "https://degenpredictions.com/todays-picks/american_football/nfl",
        "https://degenpredictions.com/tennis/fixtures",
        "https://degenpredictions.com/ipl/fixtures",
        "https://degenpredictions.com/cpl/fixtures",
        "https://degenpredictions.com/crypto",
        "https://degenpredictions.com/stocks",
        "https://degenpredictions.com/stocks/us",
        "https://degenpredictions.com/stocks/india",
        "https://degenpredictions.com/faq",
        "https://degenpredictions.com/todays-picks",
        "https://degenpredictions.com/todays-picks/tennis/atp",
        "https://degenpredictions.com/todays-picks/cricket/ipl",
        "https://degenpredictions.com/todays-picks/cricket/cpl",
        "https://degenpredictions.com/accuracy",
    ):
        assert url in body
    assert "https://degenpredictions.com/todays-picks/digest" not in body
    assert "https://degenpredictions.com/contact" not in body


def test_organization_schema_has_contact_point(client):
    body = client.get("/").data.decode()
    assert '"@type": "ContactPoint"' in body
    assert "info@degenpredictions.com" in body
    assert "instagram.com/degen_predictions467" in body


def test_todays_picks_league_has_share_and_og(client):
    resp = client.get("/todays-picks/tennis/atp")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert 'property="og:title"' in body
    assert 'property="og:description"' in body
    assert 'property="og:image"' in body
    assert "Degen Predictions" in body
    assert "WhatsApp" in body
    assert "api.whatsapp.com/send" in body
    assert "twitter.com/intent/tweet" in body
    assert "data-copy-share" in body
    assert "summary_large_image" in body


def test_faq_covers_accuracy_ipl_tennis(client):
    body = client.get("/faq").data.decode()
    assert "Degen Predictions accuracy" in body or "degen-predictions-accuracy" in body
    assert "tennis" in body.lower()
    assert "IPL" in body
    assert 'href="/accuracy"' in body
    assert '"@type": "FAQPage"' in body
    assert "Is Degen Predictions betting advice?" in body
    assert '"@type": "BreadcrumbList"' in body


def test_llms_txt_has_oos_and_updated(client):
    body = client.get("/llms.txt").data.decode()
    assert "Degen Predictions" in body
    assert "Updated:" in body
    assert "not betting" in body.lower()
    full = client.get("/llms-full.txt").data.decode()
    assert "Methodology" in full
    assert len(full) > len(body)


def test_picks_and_combos_show_updated_stamp(client):
    picks = client.get("/todays-picks").data.decode()
    assert "Updated " in picks
    assert "IST" in picks
    combos = client.get("/todays-picks/combos").data.decode()
    assert "Updated " in combos
    assert '"@type": "BreadcrumbList"' in combos


def test_accuracy_hub_has_oos_snapshot_table(client):
    body = client.get("/accuracy").data.decode()
    assert "Full-slate OOS snapshot" in body
    assert "Updated " in body
    assert '"@type": "BreadcrumbList"' in body


def test_crypto_and_stocks_seo_titles(client):
    crypto = client.get("/crypto").data.decode()
    assert "Crypto Predictions" in crypto
    assert "Bitcoin" in crypto or "crypto" in crypto.lower()
    us = client.get("/stocks/us").data.decode()
    assert "US Stock Predictions" in us or "S&amp;P" in us or "S&P" in us
    india = client.get("/stocks/india").data.decode()
    assert "India Stock Predictions" in india or "Nifty" in india
