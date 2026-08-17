"""Last-known-good feeds: never cache a failed or empty wipe as success."""
from __future__ import annotations

import analytics
import feed_store


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self):
        return self._payload


def _reset(tmp_path, monkeypatch):
    monkeypatch.setattr(feed_store, "STORE_DIR", str(tmp_path))
    monkeypatch.setattr(feed_store.time, "sleep", lambda s: None)
    feed_store._memory.clear()
    feed_store._status.clear()


def test_keeps_last_good_when_next_fetch_is_empty(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)
    good = [{"MatchNumber": 1}, {"MatchNumber": 2}]
    monkeypatch.setattr(feed_store.requests, "get", lambda *a, **k: _Resp(good))
    first = feed_store.get_json("soccer:demo", "http://example.test", ttl=0)
    assert first == good

    monkeypatch.setattr(feed_store.requests, "get", lambda *a, **k: _Resp([]))
    second = feed_store.get_json("soccer:demo", "http://example.test", ttl=0)
    assert second == good
    snap = feed_store.snapshot()["soccer:demo"]
    assert snap["degraded"] is True
    assert snap["hard_fail"] is False
    assert snap["items"] == 2


def test_keeps_last_good_when_fetch_raises(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)
    good = {"events": [{"id": "1"}]}
    monkeypatch.setattr(feed_store.requests, "get", lambda *a, **k: _Resp(good))
    first = feed_store.get_json("mlb:range", "http://example.test", ttl=0)
    assert first == good

    def boom(*a, **k):
        raise ConnectionError("upstream down")

    monkeypatch.setattr(feed_store.requests, "get", boom)
    second = feed_store.get_json("mlb:range", "http://example.test", ttl=0)
    assert second == good
    snap = feed_store.snapshot()["mlb:range"]
    assert snap["degraded"] is True
    assert snap["ok"] is False


def test_survives_process_restart_via_disk(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)
    good = [{"id": 9}]
    monkeypatch.setattr(feed_store.requests, "get", lambda *a, **k: _Resp(good))
    feed_store.get_json("npb:season", "http://example.test", ttl=0)
    feed_store._memory.clear()

    def boom(*a, **k):
        raise TimeoutError("gone")

    monkeypatch.setattr(feed_store.requests, "get", boom)
    restored = feed_store.get_json("npb:season", "http://example.test", ttl=0)
    assert restored == good


def test_hard_fail_when_no_snapshot(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)

    def boom(*a, **k):
        raise TimeoutError("gone")

    monkeypatch.setattr(feed_store.requests, "get", boom)
    payload = feed_store.get_json("tennis:atp", "http://example.test", ttl=0)
    assert payload is None
    snap = feed_store.snapshot()["tennis:atp"]
    assert snap["hard_fail"] is True
    assert snap["items"] == 0


def test_healthz_is_json_and_unlogged(client):
    feed_store._status.clear()
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.get_json()
    assert "ok" in body
    assert isinstance(body.get("feeds"), dict)
    assert analytics.should_log("/healthz") is False
    feed_store._status["probe"] = {
        "hard_fail": True, "degraded": True, "ok": False, "items": 0, "reason": "test",
    }
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is False
    feed_store._status.pop("probe", None)
