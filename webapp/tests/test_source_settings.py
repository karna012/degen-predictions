"""Admin source_settings store + Sources panel."""
import base64
from pathlib import Path

import app as app_module
import source_settings


def _basic_auth_header(user, password):
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def test_source_settings_save_and_resolve(tmp_path, monkeypatch):
    monkeypatch.setattr(source_settings, "SETTINGS_PATH", Path(tmp_path) / "source_settings.json")
    source_settings.clear_cache()
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setenv("TELEGRAM_CHANNEL_URL", "https://t.me/fromenv")

    source_settings.save_settings({
        "telegram": {
            "channel_url": "https://t.me/fromadmin",
            "chat_id": "-10099",
            "bot_token": "123:abc",
            "auto_post": True,
        },
        "feeds": {
            "mlb": {
                "enabled": True,
                "fixtures_primary": "espn_cdn",
                "fixtures_fallback": "mlb_statsapi",
                "results_primary": "mlb_statsapi",
                "results_fallback": "espn_cdn",
            }
        },
        "apis": {"api_sports_key": "sports-key-1"},
    })
    creds = source_settings.telegram_credentials()
    assert creds["channel_url"] == "https://t.me/fromadmin"
    assert creds["bot_token"] == "123:abc"
    assert creds["chat_id"] == "-10099"
    assert creds["auto_post"] is True
    pref = source_settings.feed_preference("mlb")
    assert pref["fixtures_primary"] == "espn_cdn"
    assert source_settings.api_key("api_sports_key") == "sports-key-1"

    view = source_settings.admin_view_model()
    assert view["telegram"]["bot_token_source"] == "admin"
    assert view["telegram"]["bot_token"] == "123:abc"
    assert view["secrets_vault"]["count_total"] >= 10


def test_source_settings_form_keeps_secret(tmp_path, monkeypatch):
    monkeypatch.setattr(source_settings, "SETTINGS_PATH", Path(tmp_path) / "source_settings.json")
    source_settings.clear_cache()
    source_settings.save_settings({
        "telegram": {"bot_token": "keep-me", "chat_id": "-1", "channel_url": "", "auto_post": False},
        "apis": {"api_sports_key": "old-key"},
    })

    class Form(dict):
        def get(self, key, default=None):
            return super().get(key, default)

    form = Form({
        "tg_channel_url": "https://t.me/x",
        "tg_chat_id": "-1",
        "tg_bot_token": "keep-me",
        "api_api_sports_key": "old-key",
        "notes": "hello",
    })
    for sport, meta in source_settings.FEED_CATALOG.items():
        d = meta["defaults"]
        form[f"feed_{sport}_enabled"] = "1"
        form[f"feed_{sport}_fixtures_primary"] = d["fixtures_primary"]
        form[f"feed_{sport}_fixtures_fallback"] = d["fixtures_fallback"]
        form[f"feed_{sport}_results_primary"] = d["results_primary"]
        form[f"feed_{sport}_results_fallback"] = d["results_fallback"]

    saved = source_settings.apply_form(form)
    assert saved["telegram"]["bot_token"] == "keep-me"
    assert saved["apis"]["api_sports_key"] == "old-key"
    assert saved["notes"] == "hello"


def test_admin_sources_section_loads(client, monkeypatch, tmp_path):
    monkeypatch.setattr(source_settings, "SETTINGS_PATH", Path(tmp_path) / "source_settings.json")
    source_settings.clear_cache()
    monkeypatch.setattr(app_module, "ADMIN_USER", "admin")
    monkeypatch.setattr(app_module, "ADMIN_PASSWORD", "secret123")
    auth = _basic_auth_header("admin", "secret123")
    resp = client.get("/admin?section=sources", headers=auth)
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Telegram (quick)" in body or 'name="tg_bot_token"' in body
    assert "MLB" in body
    assert "Stocks" in body
    assert "Crypto" in body
    assert 'name="tg_bot_token"' in body
    assert "Sources &amp; API keys" in body or "sources" in body.lower()


def test_admin_sources_save_post(client, monkeypatch, tmp_path):
    monkeypatch.setattr(source_settings, "SETTINGS_PATH", Path(tmp_path) / "source_settings.json")
    source_settings.clear_cache()
    monkeypatch.setattr(app_module, "ADMIN_USER", "admin")
    monkeypatch.setattr(app_module, "ADMIN_PASSWORD", "secret123")
    auth = _basic_auth_header("admin", "secret123")
    # Prime CSRF via a GET under auth
    client.get("/admin?section=sources", headers=auth)
    with client.session_transaction() as sess:
        csrf = sess.get("_csrf_token") or "test-csrf"
        sess["_csrf_token"] = csrf

    data = {
        "csrf_token": csrf,
        "tg_channel_url": "https://t.me/testchannel",
        "tg_chat_id": "-100555",
        "tg_bot_token": "999:token",
        "notes": "from test",
    }
    for sport, meta in source_settings.FEED_CATALOG.items():
        d = meta["defaults"]
        data[f"feed_{sport}_enabled"] = "1"
        data[f"feed_{sport}_fixtures_primary"] = d["fixtures_primary"]
        data[f"feed_{sport}_fixtures_fallback"] = d["fixtures_fallback"]
        data[f"feed_{sport}_results_primary"] = d["results_primary"]
        data[f"feed_{sport}_results_fallback"] = d["results_fallback"]

    resp = client.post(
        "/admin/sources/save",
        data=data,
        headers=auth,
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    loc = resp.headers.get("Location") or ""
    assert "section=sources" in loc
    assert "src=ok" in loc
    loaded = source_settings.load_settings(force=True)
    assert loaded["telegram"]["channel_url"] == "https://t.me/testchannel"
    assert loaded["telegram"]["bot_token"] == "999:token"
