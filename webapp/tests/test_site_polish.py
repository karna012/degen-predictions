"""Public site polish helpers."""
from pathlib import Path

import site_polish


def test_render_og_card_png():
    png = site_polish.render_og_card(title="MLB Today's Picks", subtitle="3 high-probability picks")
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(png) > 1000


def test_watch_cookie_roundtrip():
    items = [{"kind": "crypto", "key": "bitcoin", "label": "bitcoin", "href": "/crypto/asset/bitcoin"}]
    raw = site_polish.dump_watch_cookie(items)
    assert site_polish.parse_watch_cookie(raw) == items
    assert site_polish.parse_watch_cookie("not-json") == []


def test_home_now_shape(client):
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "What's on now" in body or "home-now" in body or "Today's drop" in body


def test_og_share_route(client):
    resp = client.get("/og/share.png", query_string={"title": "Test", "sub": "Hello", "kind": "pick"})
    assert resp.status_code == 200
    assert resp.mimetype == "image/png"


def test_watchlist_api_toggle(client):
    resp = client.post(
        "/api/watchlist",
        json={"action": "toggle", "kind": "league", "key": "mlb", "label": "MLB", "href": "/mlb/fixtures"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["count"] >= 1
