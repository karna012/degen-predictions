"""Daily digest: Top-3 selection over existing Today's Picks legs."""
import base64
import json
import re
import datetime as dt
from pathlib import Path

import app as app_module
import daily_digest


def _basic_auth_header(user, password):
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _leg(**kwargs):
    base = {
        "matchup": "A vs B",
        "pick": "B",
        "market": "Moneyline",
        "confidence": 0.72,
        "sport_key": "soccer",
        "sport_label": "Soccer",
        "league_id": "mls",
        "league_label": "MLS",
        "status": "upcoming",
        "away": "A",
        "home": "B",
    }
    base.update(kwargs)
    return base


def test_select_top3_prefers_high_conf_and_diversifies_leagues():
    legs = [
        _leg(league_id="mls", league_label="MLS", matchup="A @ B", away="A", home="B",
             pick="B", confidence=0.80),
        _leg(league_id="mls", league_label="MLS", matchup="C @ D", away="C", home="D",
             pick="D", confidence=0.79),
        _leg(league_id="mlb", league_label="MLB", sport_key="baseball", sport_label="Baseball",
             matchup="NYY @ BOS", away="NYY", home="BOS", pick="Under 8.5",
             market="Total runs", confidence=0.74),
        _leg(league_id="atp", league_label="ATP", sport_key="tennis", sport_label="Tennis",
             matchup="P1 vs P2", away="P1", home="P2", pick="P1",
             market="Match winner", confidence=0.71),
        _leg(league_id="ipl", league_label="IPL", sport_key="cricket",
             matchup="MI vs CSK", away="MI", home="CSK", pick="Over",
             market="Total runs", confidence=0.55, status="final"),
    ]
    top = daily_digest.select_top3(legs, n=3)
    assert len(top) == 3
    leagues = {p["league_id"] for p in top}
    assert "mls" in leagues
    assert len(leagues) == 3
    assert all(p.get("confidence_pct") is not None for p in top)


def test_build_digest_payload_promotes_combos_and_leagues():
    picks = daily_digest.select_top3([
        _leg(league_id="mlb", league_label="MLB", sport_key="baseball",
             matchup="NYY @ BOS", away="NYY", home="BOS", pick="Under 8.5",
             market="Total runs", confidence=0.74),
        _leg(league_id="mls", confidence=0.70),
    ], n=3)

    def _combo(size, joint, mix_kind, legs):
        return {
            "size": size,
            "joint_prob": joint,
            "joint_formula": f"{joint * 100:.1f}%",
            "mix_label": " + ".join(leg["league_label"] for leg in legs),
            "mix_kind": mix_kind,
            "score": 1.0,
            "legs": legs,
        }

    # Best 3-leg pool (kept disjoint from the Best 2-leg pool below so the
    # cross-bucket anti-reshuffle fixture dedupe in build_digest_payload
    # doesn't starve one bucket of legs the other bucket already claimed).
    leg_a = _leg(league_id="mlb", league_label="MLB", sport_key="baseball",
                 matchup="NYY @ BOS", away="NYY", home="BOS", pick="Under 8.5",
                 market="Total runs", confidence=0.74)
    leg_b = _leg(league_id="mls", league_label="MLS", sport_key="soccer",
                 matchup="A vs B", away="A", home="B", pick="B", confidence=0.70)
    leg_c = _leg(league_id="atp", league_label="ATP", sport_key="tennis",
                 matchup="P1 vs P2", away="P1", home="P2", pick="P1",
                 market="Match winner", confidence=0.71)
    # Best 2-leg pool: two disjoint pairs so both can be selected without
    # tripping the fixture-reuse guard.
    leg_e = _leg(league_id="npb", league_label="NPB", sport_key="baseball",
                 matchup="SB @ YG", away="SB", home="YG", pick="Under 7.5",
                 market="Total runs", confidence=0.75)
    leg_f = _leg(league_id="la_liga", league_label="La Liga", sport_key="soccer",
                 matchup="X vs Y", away="X", home="Y", pick="Y", confidence=0.73)
    leg_g = _leg(league_id="ipl", league_label="IPL", sport_key="cricket",
                 matchup="MI vs CSK", away="MI", home="CSK", pick="MI",
                 market="Match winner", confidence=0.71)
    leg_h = _leg(league_id="bundesliga", league_label="Bundesliga", sport_key="soccer",
                 matchup="M vs N", away="M", home="N", pick="N", confidence=0.70)
    # Unique mixed slips (different legs than the Best pools above).
    leg_d = _leg(league_id="epl", league_label="EPL", sport_key="soccer",
                 sport_label="Soccer", matchup="ARS vs CHE", away="ARS", home="CHE",
                 pick="Over 2.5", market="Total goals", confidence=0.68)
    leg_i = _leg(league_id="nfl", league_label="NFL", sport_key="american_football",
                 matchup="NE @ KC", away="NE", home="KC", pick="KC -3.5",
                 market="Handicap", confidence=0.66)
    leg_j = _leg(league_id="bbl", league_label="BBL", sport_key="cricket",
                 matchup="ST vs SIX", away="ST", home="SIX", pick="Over 150.5",
                 market="Total runs", confidence=0.65)
    leg_k = _leg(league_id="mlb", league_label="MLB", sport_key="baseball",
                 matchup="LAD @ SF", away="LAD", home="SF", pick="Over 8.5",
                 market="Total runs", confidence=0.64)
    payload = daily_digest.build_digest_payload(
        picks,
        day="2099-01-01",
        legs=[
            _leg(league_id="mlb", league_label="MLB", sport_key="baseball"),
            _leg(league_id="mls", league_label="MLS", sport_key="soccer"),
        ],
        combos={
            2: [
                _combo(2, 0.52, "Same-league", [leg_e, leg_f]),
                _combo(2, 0.50, "Same-league", [leg_g, leg_h]),
                # Overlaps leg_e/leg_g — must be skipped (fixture already used).
                _combo(2, 0.40, "Same-league", [leg_e, leg_g]),
            ],
            3: [
                _combo(3, 0.36, "Cross-sport", [leg_a, leg_b, leg_c]),
            ],
            "mixed_2": [
                # Duplicate of Best 2-leg #2 — must be skipped
                _combo(2, 0.50, "Cross-sport", [leg_g, leg_h]),
                # Unique mixed
                _combo(2, 0.49, "Cross-league", [leg_d, leg_i]),
                _combo(2, 0.45, "Cross-sport", [leg_j, leg_k]),
            ],
            "mixed_3": [
                # Exact duplicate of Best 3-leg — hide / skip
                _combo(3, 0.36, "Cross-sport", [leg_a, leg_b, leg_c]),
            ],
        },
    )
    assert payload["has_combos"] is True
    assert len(payload["combos_2"]) == 2
    assert len(payload["combos_3"]) == 1
    # Mixed 2 skips the Best duplicate and takes next unique slips.
    assert len(payload["mixed_2"]) == 2
    assert payload["mixed_2"][0]["mix_kind"] == "Cross-league" or payload["mixed_2"][0]["mix_kind"] == "Cross-sport"
    fps_best = {c["fingerprint"] for c in payload["combos_2"] + payload["combos_3"]}
    fps_mixed = {c["fingerprint"] for c in payload["mixed_2"] + payload["mixed_3"]}
    assert fps_best.isdisjoint(fps_mixed)
    # Mixed 3 had only a Best duplicate → section empty.
    assert payload["mixed_3"] == []
    assert payload["featured_combo_count"] == 5
    assert payload["combos_2"][0]["joint_pct"] == 52.0
    assert "Best 2-leg" in payload["share_text"]
    assert "Mixed 2-leg" in payload["telegram_text"]
    assert "Mixed 3-leg" not in payload["telegram_text"]
    assert "Baseball · MLB" in payload["telegram_text"] or "MLB" in payload["telegram_text"]
    assert "Soccer · MLS" in payload["share_text"] or "MLS" in payload["share_text"]
    # Clean leg lines: sport/event/selection — no nested "(matchup, %)" dumps.
    for msg in payload["telegram_messages"][1:]:
        assert "@ T" not in msg
        assert "vs Da" not in msg
        assert "Cross-sport" not in msg
        assert "mix_label" not in msg.lower()
    tg_msgs = payload["telegram_messages"]
    assert tg_msgs[0].startswith("Degen Predictions")
    assert "2099-01-01" in tg_msgs[0]
    assert tg_msgs[1].startswith("Today's Picks ·")
    assert len(tg_msgs) == 1 + 1 + payload["featured_combo_count"]
    assert "MLB" in tg_msgs[1]
    assert "MLS" in tg_msgs[1] or "Soccer" in tg_msgs[1]
    assert any(m.startswith("Best 2-leg") for m in tg_msgs)
    assert any(m.startswith("Best 3-leg") for m in tg_msgs)
    assert any(m.startswith("Mixed 2-leg") for m in tg_msgs)
    assert not any(m.startswith("Mixed 3-leg") for m in tg_msgs)
    assert all("%" not in m for m in tg_msgs)
    assert any("Baseball · MLB" in m or "Soccer · MLS" in m for m in tg_msgs[1:])
    assert "/todays-picks/combos" in payload["share_text"]
    assert "Telegram:" in payload["share_text"]
    assert "/todays-picks/digest" not in payload["share_text"]
    assert any(r["league_id"] == "mlb" for r in payload["league_urls"])
    assert "not betting advice" in payload["share_text"].lower() or "Educational" in payload["disclaimer"]


def test_mixed_hides_when_only_duplicates_of_best():
    leg_a = _leg(league_id="mlb", league_label="MLB", sport_key="baseball",
                 sport_label="Baseball", matchup="NYY @ BOS", pick="Under 8.5",
                 market="Total runs", confidence=0.74)
    leg_b = _leg(league_id="mls", league_label="MLS", sport_key="soccer",
                 sport_label="Soccer", matchup="A vs B", pick="B", confidence=0.70)
    same = {
        "size": 2,
        "joint_prob": 0.52,
        "mix_label": "MLB + MLS",
        "mix_kind": "Cross-sport",
        "score": 2.0,
        "legs": [leg_a, leg_b],
    }
    payload = daily_digest.build_digest_payload(
        [],
        day="2099-01-02",
        combos={2: [same], 3: [], "mixed_2": [same], "mixed_3": []},
    )
    assert len(payload["combos_2"]) == 1
    assert payload["mixed_2"] == []
    assert "Mixed 2-leg" not in payload["telegram_text"]


def test_telegram_messages_use_simple_leg_lines():
    picks = [
        daily_digest._leg_public(_leg(
            league_id="mlb", league_label="MLB", sport_key="baseball",
            sport_label="Baseball",
            matchup="Boston Red Sox @ Tampa Bay Rays",
            away="Boston Red Sox",
            home="Tampa Bay Rays",
            pick="Boston Red Sox +1.5",
            market="Run line",
            confidence=0.78,
        )),
    ]
    tennis = daily_digest._leg_public(_leg(
        league_id="atp", league_label="ATP (Men)", sport_key="tennis",
        sport_label="Tennis",
        matchup="Luciano Darderi vs Brandon Nakashima",
        away="Luciano Darderi",
        home="Brandon Nakashima",
        pick="Brandon Nakashima",
        market="Player to win a set",
        confidence=0.83,
    ))
    combo = {
        "size": 2,
        "joint_pct": 67.3,
        "mix_label": "ATP + MLB",
        "mix_kind": "Cross-sport",
        "legs": [tennis, picks[0]],
    }
    channel = daily_digest.DEFAULT_TELEGRAM_CHANNEL_URL
    msgs = daily_digest.build_telegram_messages({
        "date": "2099-01-01",
        "picks": picks,
        "combos_2": [combo],
        "combos_3": [],
        "mixed_2": [],
        "mixed_3": [],
        "digest_url": "https://degenpredictions.com/todays-picks/digest",
        "telegram_channel_url": channel,
    })
    assert msgs[0].startswith("Degen Predictions\n2099-01-01\n")
    assert "degenpredictions.com/todays-picks" in msgs[0]
    assert "utm_source=telegram" in msgs[0]
    assert "/todays-picks/digest" not in msgs[0]
    assert channel not in msgs[0]
    assert msgs[1].startswith("Today's Picks · 2099-01-01")
    assert "Tampa Bay Rays vs Boston Red Sox" in msgs[1]
    assert "Boston Red Sox +1.5 · Run line" in msgs[1]
    assert "78%" not in msgs[1]
    assert "utm_source=telegram" in msgs[1]
    assert msgs[2].startswith("Best 2-leg")
    assert "todays-picks/combos" in msgs[2]
    assert "67%" not in msgs[2]
    assert "Nakashima · Win a set" in msgs[2]
    assert "83%" not in msgs[2]
    assert "Brandon Nakashima vs Luciano Darderi" in msgs[2]
    # Sparse: no mix_label / Cross-sport / nested dumps / percentages
    assert "ATP + MLB" not in msgs[2]
    assert "Cross-sport" not in msgs[2]
    assert "Joint" not in msgs[2]
    assert "%" not in "\n".join(msgs)
    assert "Boston Red Sox +1.5 (Boston Red Sox @ T" not in "\n".join(msgs)
    assert len(msgs) == 3  # header + picks + 1 slip


def test_post_telegram_sends_one_photo_per_slip(monkeypatch, tmp_path):
    """Header text + one sendPhoto per slip image (picks + each combo)."""
    calls = []

    class _Resp:
        status_code = 200
        text = "ok"

    def fake_post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return _Resp()

    import requests
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100123")
    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(daily_digest.time, "sleep", lambda *_: None)

    picks = [
        daily_digest._leg_public(_leg(
            league_id="mlb", league_label="MLB", sport_key="baseball",
            sport_label="Baseball", matchup="NYY @ BOS", pick="Under 8.5",
            market="Total runs", confidence=0.74,
        )),
        daily_digest._leg_public(_leg(confidence=0.70)),
    ]
    combo = {
        "size": 2,
        "joint_pct": 52.0,
        "mix_label": "MLB + MLS",
        "mix_kind": "Cross-sport",
        "legs": picks,
    }
    # Unique mixed so Mixed is not dropped as Best duplicate.
    mixed = {
        "size": 2,
        "joint_pct": 48.0,
        "mix_label": "ATP + MLS",
        "mix_kind": "Cross-sport",
        "legs": [
            daily_digest._leg_public(_leg(
                league_id="atp", league_label="ATP", sport_key="tennis",
                sport_label="Tennis", matchup="P1 vs P2", pick="P1",
                market="Match winner", confidence=0.71,
            )),
            daily_digest._leg_public(_leg(confidence=0.69)),
        ],
    }
    digest = {
        "date": "2099-01-01",
        "picks": picks,
        "combos_2": [combo],
        "combos_3": [],
        "mixed_2": [mixed],
        "mixed_3": [],
        "featured_combo_count": 2,
        "digest_url": "https://degenpredictions.com/todays-picks/digest",
        "combos_url": "https://degenpredictions.com/todays-picks/combos",
        "picks_hub_url": "https://degenpredictions.com/todays-picks",
    }
    # Pre-bake tiny real PNGs so post_telegram takes the photo path.
    p1 = tmp_path / "picks.png"
    p2 = tmp_path / "c1.png"
    p3 = tmp_path / "c2.png"
    for p in (p1, p2, p3):
        p.write_bytes(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
            b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00"
            b"\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18"
            b"\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
        )
    slip_images = [
        {"caption": "Today's Picks · 2099-01-01", "path": p1, "kind": "picks"},
        {"caption": "Best 2-leg", "path": p2, "kind": "combo"},
        {"caption": "Mixed 2-leg", "path": p3, "kind": "combo"},
    ]
    ok, msg = daily_digest.post_telegram(
        digest, image_path=None, slip_images=slip_images,
    )
    assert ok is True
    assert "3 photos" in msg
    assert "1 picks + 2 combos" in msg
    # 1 sendMessage header + 3 sendPhoto
    assert len(calls) == 4
    assert calls[0]["url"].endswith("/sendMessage")
    assert calls[0]["json"]["text"].startswith("Degen Predictions")
    photo_calls = calls[1:]
    assert all(c["url"].endswith("/sendPhoto") for c in photo_calls)
    captions = [c["data"]["caption"] for c in photo_calls]
    assert captions[0].startswith("Today's Picks ·")
    assert "degenpredictions.com/todays-picks" in captions[0]
    assert captions[1].startswith("Best 2-leg")
    assert captions[2].startswith("Mixed 2-leg")
    assert "todays-picks/combos" in captions[1]
    assert all("%" not in c for c in captions)
    for c in photo_calls:
        photo = c["files"]["photo"]
        assert photo[0].endswith(".png")
        assert photo[2] == "image/png"
        assert isinstance(photo[1], (bytes, bytearray)) and photo[1]


_TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00"
    b"\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18"
    b"\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)


def test_post_telegram_sends_photo_from_in_memory_bytes(monkeypatch):
    """Admin slips must still sendPhoto when the PNG never hit disk."""
    calls = []

    class _Resp:
        status_code = 200
        text = "ok"

        def json(self):
            return {"ok": True, "result": {"message_id": 9}}

    def fake_post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return _Resp()

    import requests
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100123")
    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(daily_digest.time, "sleep", lambda *_: None)

    ok, msg = daily_digest.post_telegram(
        {"date": "2099-01-01"},
        slip_images=[{
            "caption": "Single",
            "kind": "picks",
            "png_bytes": _TINY_PNG,
        }],
    )
    assert ok is True
    assert "1 photos" in msg
    assert calls[1]["url"].endswith("/sendPhoto")
    photo = calls[1]["files"]["photo"]
    assert photo[0] == "slip.png"
    assert photo[1] == _TINY_PNG
    assert photo[2] == "image/png"


def test_post_telegram_sendphoto_falls_back_to_document(monkeypatch):
    calls = []

    class _Resp:
        def __init__(self, code):
            self.status_code = code
            self.text = "fail" if code >= 400 else "ok"

        def json(self):
            return {"ok": self.status_code < 400, "result": {"message_id": 1}}

    def fake_post(url, **kwargs):
        calls.append(url)
        if url.endswith("/sendPhoto"):
            return _Resp(400)
        return _Resp(200)

    import requests
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100123")
    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(daily_digest.time, "sleep", lambda *_: None)

    ok, msg = daily_digest.post_telegram(
        {"date": "2099-01-01"},
        slip_images=[{
            "caption": "Single",
            "kind": "picks",
            "png_bytes": _TINY_PNG,
        }],
    )
    assert ok is True
    assert any(u.endswith("/sendPhoto") for u in calls)
    assert any(u.endswith("/sendDocument") for u in calls)


def test_build_telegram_slip_images_one_per_slip(tmp_path, monkeypatch):
    monkeypatch.setattr(daily_digest, "CARDS_DIR", tmp_path)
    picks = [
        daily_digest._leg_public(_leg(
            league_id="mlb", league_label="MLB", sport_key="baseball",
            sport_label="Baseball", matchup="NYY @ BOS", pick="Under 8.5",
            market="Total runs", confidence=0.74,
        )),
    ]
    combo_a = {
        "size": 2, "joint_pct": 60.0, "legs": picks + [
            daily_digest._leg_public(_leg(confidence=0.70)),
        ],
    }
    combo_b = {
        "size": 2, "joint_pct": 55.0, "legs": [
            daily_digest._leg_public(_leg(
                league_id="atp", league_label="ATP", sport_key="tennis",
                sport_label="Tennis", matchup="P1 vs P2", pick="P1",
                confidence=0.71,
            )),
            daily_digest._leg_public(_leg(
                league_id="epl", league_label="EPL", matchup="ARS vs CHE",
                pick="Over 2.5", market="Total goals", confidence=0.68,
            )),
        ],
    }
    digest = {
        "date": "2099-01-01",
        "picks": picks,
        "combos_2": [combo_a],
        "combos_3": [],
        "mixed_2": [combo_b],
        "mixed_3": [],
    }
    n = {"v": 0}
    monkeypatch.setattr(
        daily_digest, "_next_telegram_serial",
        lambda: n.__setitem__("v", n["v"] + 1) or n["v"],
    )
    slips = daily_digest.build_telegram_slip_images(digest)
    assert len(slips) == 3  # 1 picks + 2 combos
    assert slips[0]["kind"] == "picks"
    assert all(Path(s["path"]).is_file() for s in slips)
    assert "Best 2-leg" in slips[1]["caption"]
    assert "Mixed 2-leg" in slips[2]["caption"]
    assert slips[0]["telegram_serial"] == 1
    assert slips[1]["caption"].startswith("#T-00001") or slips[1]["caption"].startswith("#T-00002")
    assert "utm_source=telegram" in slips[1]["caption"]
    assert all("%" not in str(s.get("caption") or "") for s in slips)
    # Telegram slip PNG selection lines omit %; admin may still request it.
    assert "%" not in daily_digest._selection_line(picks[0], include_pct=False)
    assert "74%" in daily_digest._selection_line(picks[0], include_pct=True)


def test_build_telegram_slip_images_skips_empty_picks_card(tmp_path, monkeypatch):
    """Do not post a blank Today's Picks PNG when Top-3 is empty."""
    monkeypatch.setattr(daily_digest, "CARDS_DIR", tmp_path)
    monkeypatch.setattr(daily_digest, "_next_telegram_serial", lambda: 7)
    combo = {
        "size": 2,
        "legs": [
            daily_digest._leg_public(_leg(confidence=0.71)),
            daily_digest._leg_public(_leg(
                league_id="atp", league_label="ATP", sport_key="tennis",
                sport_label="Tennis", matchup="P1 vs P2", pick="P1",
                confidence=0.70,
            )),
        ],
    }
    slips = daily_digest.build_telegram_slip_images({
        "date": "2099-01-02",
        "picks": [],
        "combos_2": [combo],
        "combos_3": [],
        "mixed_2": [],
        "mixed_3": [],
    })
    assert len(slips) == 1
    assert slips[0]["kind"] == "combo"
    assert all(s.get("kind") != "picks" for s in slips)

    empty = daily_digest.build_telegram_slip_images({
        "date": "2099-01-02",
        "picks": [],
        "combos_2": [],
        "combos_3": [],
        "mixed_2": [],
        "mixed_3": [],
    })
    assert empty == []


def test_slip_card_png_has_trademark_lockup(tmp_path):
    out = tmp_path / "branded.png"
    path = daily_digest.render_slip_card_png(
        title="Single",
        legs=[_leg(
            league_id="npb", league_label="NPB", sport_key="baseball",
            sport_label="Baseball", matchup="Tigers @ Giants",
            pick="Tigers", market="Moneyline",
        )],
        out_path=out,
        day="2026-08-12",
    )
    assert path is not None and path.is_file()
    assert "™" in daily_digest.BRAND_LOCKUP
    assert "Degen Predictions" in daily_digest.BRAND_LOCKUP
    assert daily_digest._slip_logo_path() is not None
    # Footer + header brand mark should leave non-trivial PNG payload.
    assert path.stat().st_size > 20_000


def test_slip_payload_survives_disk_permission_error(tmp_path, monkeypatch):
    """Admin Telegram must still get PNG bytes when cards/ is not writable."""
    out = tmp_path / "blocked" / "slip.png"

    def boom(self, *_a, **_k):
        raise PermissionError(13, "Permission denied", str(self))

    monkeypatch.setattr(daily_digest, "_next_telegram_serial", lambda: 3)
    monkeypatch.setattr(Path, "write_bytes", boom)
    payload = daily_digest.render_slip_card_payload(
        title="Single",
        legs=[_leg(matchup="A vs B", pick="B", market="BTTS")],
        out_path=out,
        day="2026-08-12",
    )
    assert payload is not None
    assert payload["path"] is None
    assert payload["png_bytes"].startswith(b"\x89PNG")
    slips = daily_digest.build_manual_slip_images(
        picks=[_leg(matchup="A vs B", pick="Yes", market="BTTS")],
        picks_title="Single",
    )
    assert len(slips) == 1
    assert slips[0]["png_bytes"].startswith(b"\x89PNG")
    assert "path" not in slips[0] or slips[0].get("path") is None


def test_sport_league_label_formats_clearly():
    assert daily_digest._sport_league_label({
        "sport_label": "Soccer", "league_label": "MLS",
    }) == "Soccer · MLS"
    assert daily_digest._sport_league_label({
        "sport_label": "Cricket", "league_label": "IPL",
    }) == "Cricket · IPL"
    assert daily_digest._sport_league_label({
        "sport_label": "MLB", "league_label": "MLB",
    }) == "MLB"

def test_select_top_combos_takes_head_of_ranked_bucket():
    legs_a = [
        _leg(league_id="mlb", league_label="MLB", pick="Under", confidence=0.8),
        _leg(league_id="mls", league_label="MLS", pick="Home", confidence=0.7),
    ]
    legs_b = [
        _leg(league_id="mlb", league_label="MLB", pick="Over",
             matchup="C @ D", away="C", home="D", confidence=0.75),
        _leg(league_id="atp", league_label="ATP", sport_key="tennis",
             sport_label="Tennis", pick="P1", matchup="P1 vs P2",
             away="P1", home="P2", confidence=0.71),
    ]
    combos = {
        "mixed_2": [
            {
                "size": 2,
                "joint_prob": 0.56,
                "mix_label": "MLB + MLS",
                "mix_kind": "Cross-sport",
                "score": 2.0,
                "legs": legs_a,
            },
            {
                "size": 2,
                "joint_prob": 0.50,
                "mix_label": "MLB + ATP",
                "mix_kind": "Cross-league",
                "score": 1.0,
                "legs": legs_b,
            },
            {
                "size": 2,
                "joint_prob": 0.40,
                "mix_label": "extra",
                "mix_kind": "Cross-sport",
                "score": 0.5,
                "legs": legs_a,  # same fingerprint as first — skipped
            },
        ],
    }
    top = daily_digest.select_top_combos(combos, "mixed_2", n=2)
    assert len(top) == 2
    assert top[0]["joint_pct"] == 56.0
    assert top[1]["joint_pct"] == 50.0
    # Within-bucket fingerprint dedupe
    assert top[0]["fingerprint"] != top[1]["fingerprint"]


def test_digest_route_requires_admin(client, monkeypatch):
    monkeypatch.setattr(app_module, "ADMIN_USER", "admin")
    monkeypatch.setattr(app_module, "ADMIN_PASSWORD", "secret123")
    assert client.get("/todays-picks/digest").status_code == 302
    assert client.get("/todays-picks/top3").status_code == 302
    assert client.get("/todays-picks/digest.json").status_code == 302

    auth = _basic_auth_header("admin", "secret123")
    resp = client.get("/todays-picks/digest", headers=auth)
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Today's 3 best picks" in body or "digest" in body.lower()
    assert "Admin → Send to Telegram" in body or "/admin?section=telegram" in body
    assert client.post("/admin/telegram-send").status_code == 401
    assert client.post("/todays-picks/digest/telegram-send").status_code == 401


def test_digest_json_route_requires_admin(client, monkeypatch):
    monkeypatch.setattr(app_module, "ADMIN_USER", "admin")
    monkeypatch.setattr(app_module, "ADMIN_PASSWORD", "secret123")
    assert client.get("/todays-picks/top3").status_code == 302
    resp = client.get(
        "/todays-picks/top3",
        headers=_basic_auth_header("admin", "secret123"),
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert "picks" in data
    assert "digest_url" in data
    assert data["digest_url"].endswith("/todays-picks/digest")
    assert "share_text" in data
    assert "combos_2" in data
    assert "mixed_2" in data
    assert "mixed_3" in data
    assert "combos_3" in data


def test_digest_route_404_when_admin_creds_missing(client, monkeypatch):
    monkeypatch.setattr(app_module, "ADMIN_USER", "")
    monkeypatch.setattr(app_module, "ADMIN_PASSWORD", "")
    assert client.get("/todays-picks/digest").status_code == 404
    assert client.get("/todays-picks/top3").status_code == 404


def test_homepage_cta_and_sitemap_no_share_kit(client, monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "SITEMAP_CACHE_PATH", str(tmp_path / "sitemap.xml"))
    # /grow used to be the share-kit page; without that route Flask may
    # slash-redirect into the league catch-all, which 404s for unknown keys.
    grow = client.get("/grow", follow_redirects=True)
    assert grow.status_code == 404
    assert "Share kit" not in grow.get_data(as_text=True)
    share_kit = client.get("/share-kit", follow_redirects=True)
    assert share_kit.status_code == 404
    assert "Share kit" not in share_kit.get_data(as_text=True)

    home = client.get("/").get_data(as_text=True)
    assert 'href="/todays-picks/digest"' not in home
    assert 'href="/todays-picks"' in home
    assert "Share kit" not in home
    assert 'href="/grow"' not in home

    # /sitemap.xml is a sitemap index pointing at sharded leaf sitemaps;
    # static pages (including todays-picks) live in /sitemap-static.xml.
    sitemap_index = client.get("/sitemap.xml").get_data(as_text=True)
    assert "https://degenpredictions.com/sitemap-static.xml" in sitemap_index

    sitemap = client.get("/sitemap-static.xml").get_data(as_text=True)
    assert "https://degenpredictions.com/todays-picks/digest" not in sitemap
    assert "https://degenpredictions.com/todays-picks" in sitemap
    assert "https://degenpredictions.com/grow" not in sitemap
    assert "https://degenpredictions.com/share-kit" not in sitemap
    assert "https://degenpredictions.com/todays-picks/combos/results" not in sitemap

    robots = client.get("/robots.txt").get_data(as_text=True)
    assert "Disallow: /todays-picks/digest" in robots
    assert "Disallow: /todays-picks/top3" in robots
    assert "Disallow: /todays-picks/combos/results" in robots


def test_channel_skip_without_creds(monkeypatch, tmp_path):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("TWITTER_API_KEY", raising=False)
    ok, msg = daily_digest.post_telegram({"share_text": "hi"})
    assert ok is False
    assert "skipped Telegram" in msg
    ok, msg = daily_digest.post_twitter({"share_text": "hi"})
    assert ok is False
    assert "skipped" in msg.lower()


def test_post_telegram_skips_empty_morning_slate_without_sending(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-1001")
    calls = []

    def fake_post(url, **kwargs):
        calls.append(url)
        raise AssertionError("Telegram API should not be called for empty slate")

    import requests
    monkeypatch.setattr(requests, "post", fake_post)
    ok, msg = daily_digest.post_telegram(
        {
            "date": "2026-08-13",
            "picks": [],
            "featured_combo_count": 0,
            "telegram_messages": [
                "header",
                "Today's Picks · 2026-08-13\n\nNo high-confidence picks today.",
            ],
        },
        slip_images=[],
        rebuild_images=False,
    )
    assert ok is False
    assert "empty slate" in msg
    assert calls == []


def test_post_telegram_empty_slip_list_does_not_rebuild_digest(monkeypatch):
    """Admin pass slip_images=[] must not silently post morning digest board."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-1001")
    calls = {"build": 0, "posts": []}

    def fake_build(_digest):
        calls["build"] += 1
        return [{"caption": "should-not-send", "path": "/nope.png", "kind": "picks"}]

    class FakeResp:
        status_code = 200
        text = '{"ok":true}'

        def json(self):
            return {"ok": True, "result": {"message_id": 1}}

    def fake_post(url, **kwargs):
        calls["posts"].append(url)
        return FakeResp()

    monkeypatch.setattr(daily_digest, "build_telegram_slip_images", fake_build)
    import requests
    monkeypatch.setattr(requests, "post", fake_post)

    ok, msg = daily_digest.post_telegram(
        {
            "date": "2026-08-12",
            "picks": [],
            "telegram_messages": [
                "header",
                "Admin selected text slip",
            ],
        },
        slip_images=[],
        rebuild_images=False,
    )
    assert ok is True
    assert calls["build"] == 0
    assert any("sendMessage" in u for u in calls["posts"])
    assert not any("sendPhoto" in u for u in calls["posts"])
    assert "TEXT FALLBACK" in msg


def test_twitter_posts_from_telegram_slips_are_short_and_tagged(tmp_path, monkeypatch):
    monkeypatch.setattr(daily_digest, "LAST_TELEGRAM_SEND_PATH", tmp_path / "last_tg.json")
    monkeypatch.setattr(daily_digest, "CARDS_DIR", tmp_path / "cards")
    monkeypatch.setattr(daily_digest, "DIGEST_DIR", tmp_path / "digest")
    slips = [{
        "title": "2-leg slip",
        "kind": "picks",
        "legs": [
            _leg(league_id="mlb", league_label="MLB", sport_key="baseball",
                 sport_label="Baseball", matchup="NYY @ BOS", away="NYY",
                 home="BOS", pick="Under 8.5", market="Total runs"),
            _leg(league_id="atp", league_label="ATP", sport_key="tennis",
                 sport_label="Tennis", matchup="Alcaraz vs Sinner",
                 away="Alcaraz", home="Sinner", pick="Alcaraz",
                 market="Match winner"),
        ],
    }]
    daily_digest.save_last_telegram_send(slips, day="2026-08-11")
    queue = daily_digest.twitter_queue(digest={"date": "2026-08-11", "picks": []})
    assert queue["source"] == "telegram"
    assert queue["count"] == 1
    post = queue["posts"][0]
    assert post["char_count"] <= 280
    assert "#DegenPredictions" in post["text"]
    assert "#MLB" in post["text"]
    assert "72%" not in post["text"] and "0.72" not in post["text"]
    assert "Not betting advice" in post["text"]
    assert "utm_source=twitter" in post["text"]
    assert post["intent_url"].startswith("https://twitter.com/intent/tweet?")
    assert post["has_image"] is True
    # Image is addressed by a stable content key, not list position -- the
    # queue accumulates across sends and gets filtered as games finish, so
    # a positional filename would drift and serve the wrong slip's image.
    assert re.match(r"^/admin/twitter-image/[0-9a-f]{16}\.png\?day=", post["image_url"])
    png_files = list((tmp_path / "cards" / "2026-08-11" / "x_queue").glob("*.png"))
    assert len(png_files) == 1
    assert "BOS vs NYY" in post["text"]
    assert "Sinner vs Alcaraz" in post["text"]
    assert "more on site" not in post["text"]


def test_save_last_telegram_send_accumulates_across_separate_sends(tmp_path, monkeypatch):
    """Real bug: sending a second thing to Telegram used to overwrite the
    first, so it silently vanished from the X queue even though it was
    still upcoming and still postable. Every distinct slip sent should
    stay available until it ages out (game finishes), not just the most
    recent send."""
    monkeypatch.setattr(daily_digest, "LAST_TELEGRAM_SEND_PATH", tmp_path / "last_tg.json")
    monkeypatch.setattr(daily_digest, "CARDS_DIR", tmp_path / "cards")
    monkeypatch.setattr(daily_digest, "DIGEST_DIR", tmp_path / "digest")

    first = [{
        "title": "Today's Picks", "kind": "picks",
        "legs": [_leg(league_id="mlb", league_label="MLB", sport_key="baseball",
                      sport_label="Baseball", matchup="NYY @ BOS", away="NYY",
                      home="BOS", pick="Under 8.5", market="Total runs")],
    }]
    second = [{
        "title": "Custom slip", "kind": "picks",
        "legs": [_leg(league_id="atp", league_label="ATP", sport_key="tennis",
                      sport_label="Tennis", matchup="Alcaraz vs Sinner",
                      away="Alcaraz", home="Sinner", pick="Alcaraz",
                      market="Match winner")],
    }]

    daily_digest.save_last_telegram_send(first, day="2026-08-11")
    daily_digest.save_last_telegram_send(second, day="2026-08-11")

    saved = daily_digest.load_last_telegram_send()
    assert len(saved["slips"]) == 2
    texts = {s["title"] for s in saved["slips"]}
    assert texts == {"Today's Picks", "Custom slip"}

    queue = daily_digest.twitter_queue(digest={"date": "2026-08-11", "picks": []})
    assert queue["count"] == 2
    combined_text = " ".join(p["text"] for p in queue["posts"])
    assert "BOS vs NYY" in combined_text
    assert "Sinner vs Alcaraz" in combined_text


def test_save_last_telegram_send_resend_refreshes_not_duplicates(tmp_path, monkeypatch):
    monkeypatch.setattr(daily_digest, "LAST_TELEGRAM_SEND_PATH", tmp_path / "last_tg.json")
    monkeypatch.setattr(daily_digest, "CARDS_DIR", tmp_path / "cards")
    monkeypatch.setattr(daily_digest, "DIGEST_DIR", tmp_path / "digest")

    slip = [{
        "title": "Today's Picks", "kind": "picks",
        "legs": [_leg(league_id="mlb", league_label="MLB", sport_key="baseball",
                      sport_label="Baseball", matchup="NYY @ BOS", away="NYY",
                      home="BOS", pick="Under 8.5", market="Total runs")],
    }]
    daily_digest.save_last_telegram_send(slip, day="2026-08-11")
    daily_digest.save_last_telegram_send(slip, day="2026-08-11")
    saved = daily_digest.load_last_telegram_send()
    assert len(saved["slips"]) == 1


def test_twitter_text_shows_all_legs_never_truncated(tmp_path, monkeypatch):
    monkeypatch.setattr(daily_digest, "CARDS_DIR", tmp_path / "cards")
    monkeypatch.setattr(daily_digest, "DIGEST_DIR", tmp_path / "digest")
    slip = {
        "title": "3-leg slip",
        "kind": "picks",
        "legs": [
            _leg(league_id="mlb", league_label="MLB", sport_key="baseball",
                 sport_label="Baseball", matchup="Yankees @ Red Sox",
                 away="Yankees", home="Red Sox",
                 pick="Under 8.5", market="Total runs"),
            _leg(league_id="atp", league_label="ATP", sport_key="tennis",
                 sport_label="Tennis", matchup="Alcaraz vs Sinner",
                 away="Alcaraz", home="Sinner",
                 pick="Alcaraz", market="Match winner"),
            _leg(league_id="npb", league_label="NPB", sport_key="baseball",
                 sport_label="Baseball", matchup="Tigers @ Giants",
                 away="Tigers", home="Giants",
                 pick="Tigers", market="Moneyline"),
        ],
    }
    text = daily_digest._format_twitter_text(slip, day="2026-08-12")
    assert "Red Sox vs Yankees" in text
    assert "Sinner vs Alcaraz" in text
    assert "Giants vs Tigers" in text
    assert "more on site" not in text


def test_twitter_queue_drops_finished_last_send(tmp_path, monkeypatch):
    monkeypatch.setattr(daily_digest, "LAST_TELEGRAM_SEND_PATH", tmp_path / "last_tg.json")
    monkeypatch.setattr(daily_digest, "CARDS_DIR", tmp_path / "cards")
    monkeypatch.setattr(daily_digest, "DIGEST_DIR", tmp_path / "digest")
    now = dt.datetime.now(dt.timezone.utc)
    today_str = now.strftime("%Y-%m-%d")
    finished = _leg(
        league_id="premier_league", league_label="Premier League",
        sport_key="soccer", sport_label="Soccer",
        matchup="Done vs Old", away="Old", home="Done",
        pick="Done", market="Moneyline",
        kickoff_iso=(now - dt.timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        status="upcoming",
    )
    later = _leg(
        league_id="mlb", league_label="MLB", sport_key="baseball",
        sport_label="Baseball", matchup="NYY @ BOS", away="NYY",
        home="BOS", pick="Under 8.5", market="Total runs",
        kickoff_iso=(now + dt.timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    daily_digest.save_last_telegram_send([
        {"title": "Today's Picks", "kind": "picks", "legs": [finished]},
    ], day=today_str)
    digest = {
        "date": today_str,
        "picks": [later],
        "combos_2": [], "combos_3": [], "mixed_2": [], "mixed_3": [],
    }
    queue = daily_digest.twitter_queue(
        digest=digest, prefer_telegram=True, board_legs=[later],
    )
    assert queue["source"] == "digest"
    assert queue["count"] == 1
    assert "NYY" in queue["posts"][0]["text"] or "BOS" in queue["posts"][0]["text"]


def test_twitter_queue_falls_back_to_digest(tmp_path, monkeypatch):
    monkeypatch.setattr(daily_digest, "LAST_TELEGRAM_SEND_PATH", tmp_path / "missing.json")
    monkeypatch.setattr(daily_digest, "CARDS_DIR", tmp_path / "cards")
    monkeypatch.setattr(daily_digest, "DIGEST_DIR", tmp_path / "digest")
    digest = {
        "date": "2026-08-11",
        "picks": [_leg(league_id="npb", league_label="NPB", sport_key="baseball",
                       sport_label="Baseball", matchup="Tigers @ Giants",
                       pick="Tigers", market="Moneyline")],
        "combos_2": [], "combos_3": [], "mixed_2": [], "mixed_3": [],
    }
    queue = daily_digest.twitter_queue(digest=digest)
    assert queue["source"] == "digest"
    assert queue["count"] == 1
    assert "#NPB" in queue["posts"][0]["text"]
    assert queue["posts"][0]["has_image"] is True
    png_files = list((tmp_path / "cards" / "2026-08-11" / "x_queue").glob("*.png"))
    assert len(png_files) == 1


def test_admin_twitter_image_route(client, monkeypatch, tmp_path):
    import app as app_module
    monkeypatch.setattr(app_module, "ADMIN_USER", "admin")
    monkeypatch.setattr(app_module, "ADMIN_PASSWORD", "secret123")
    monkeypatch.setattr(daily_digest, "LAST_TELEGRAM_SEND_PATH", tmp_path / "last_tg.json")
    monkeypatch.setattr(daily_digest, "CARDS_DIR", tmp_path / "cards")
    monkeypatch.setattr(daily_digest, "DIGEST_DIR", tmp_path / "digest")
    daily_digest.save_last_telegram_send([{
        "title": "Single",
        "kind": "picks",
        "legs": [_leg(league_id="npb", league_label="NPB", sport_key="baseball",
                      sport_label="Baseball", matchup="Tigers @ Giants",
                      pick="Tigers", market="Moneyline")],
    }], day="2026-08-11")
    key = daily_digest.load_last_telegram_send()["slips"][0]["slip_key"]
    auth = _basic_auth_header("admin", "secret123")
    assert client.get(f"/admin/twitter-image/{key}.png").status_code == 302
    resp = client.get(f"/admin/twitter-image/{key}.png?day=2026-08-11", headers=auth)
    assert resp.status_code == 200
    assert resp.mimetype == "image/png"
    assert resp.data[:8] == b"\x89PNG\r\n\x1a\n"


def test_recent_digest_days_oldest_first():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    now = datetime(2026, 8, 13, 11, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    assert daily_digest.recent_digest_days(3, now=now) == [
        "2026-08-11", "2026-08-12", "2026-08-13",
    ]


def test_post_recent_digest_covers_lookback_days(monkeypatch):
    seen = []

    def fake_post(day, max_losses=None):
        seen.append(day)
        return {
            "posted": 0, "failed": 0, "skipped": 0, "pending": 0,
            "twitter_posted": 0, "twitter_failed": 0, "twitter_skipped": 0,
            "errors": [], "day": day,
        }

    monkeypatch.setattr(
        daily_digest, "recent_digest_days",
        lambda n=None: ["2026-08-11", "2026-08-12", "2026-08-13"],
    )
    monkeypatch.setattr(daily_digest, "post_digest_day_telegram_results", fake_post)
    out = daily_digest.post_recent_digest_telegram_results()
    assert seen == ["2026-08-11", "2026-08-12", "2026-08-13"]
    assert out["days"][0]["day"] == "2026-08-11"


def test_verify_digest_fixtures_keeps_only_live_board_matches():
    from datetime import datetime, timedelta, timezone
    kick = (datetime.now(timezone.utc) + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    live = [{
        "league_id": "mlb",
        "sport_key": "baseball",
        "home_names": ["Boston Red Sox", "BOS"],
        "away_names": ["New York Yankees", "NYY"],
        "kickoff_iso": kick,
        "completed": False,
        "status": "upcoming",
    }]
    digest = {
        "picks": [
            _leg(league_id="mlb", home="BOS", away="NYY",
                 matchup="NYY @ BOS", pick="Under 8.5",
                 kickoff_iso=kick, sport_key="baseball"),
            _leg(league_id="mlb", home="Ghosts", away="Phantoms",
                 matchup="Phantoms @ Ghosts", pick="Ghosts",
                 kickoff_iso=kick, sport_key="baseball"),
        ],
        "combos_2": [{
            "legs": [
                _leg(league_id="mlb", home="BOS", away="NYY",
                     matchup="NYY @ BOS", pick="Under 8.5",
                     kickoff_iso=kick, sport_key="baseball"),
                _leg(league_id="mlb", home="Ghosts", away="Phantoms",
                     matchup="Phantoms @ Ghosts", pick="Ghosts",
                     kickoff_iso=kick, sport_key="baseball"),
            ],
        }],
        "combos_3": [], "mixed_2": [], "mixed_3": [],
    }
    out = daily_digest.verify_digest_fixtures(digest, fixtures=live)
    assert len(out["picks"]) == 1
    assert out["picks"][0]["home"] == "BOS"
    assert out["combos_2"] == []
    assert out["featured_combo_count"] == 0
    assert out["fixture_verify"]["dropped_n"] >= 1


def test_run_daily_job_skips_unverified_fixtures(tmp_path, monkeypatch):
    monkeypatch.setattr(daily_digest, "DIGEST_DIR", tmp_path / "digest")
    monkeypatch.setattr(daily_digest, "DRAFTS_DIR", tmp_path / "drafts")
    monkeypatch.setattr(daily_digest, "CARDS_DIR", tmp_path / "cards")
    monkeypatch.setattr(daily_digest, "STATE_PATH", tmp_path / "digest" / "state.json")
    monkeypatch.setattr(daily_digest, "LATEST_PATH", tmp_path / "digest" / "latest.json")
    ghost = _leg(
        league_id="mlb", league_label="MLB", sport_key="baseball",
        matchup="Phantoms @ Ghosts", away="Phantoms", home="Ghosts",
        pick="Ghosts", confidence=0.90, kickoff_iso="2099-01-01T23:00:00Z",
    )
    monkeypatch.setattr(
        daily_digest, "gather_board",
        lambda force_rebuild=False: ([ghost], {"combos_2": []}),
    )
    real_verify = daily_digest.verify_digest_fixtures
    monkeypatch.setattr(
        daily_digest, "verify_digest_fixtures",
        lambda d, **k: real_verify(d, fixtures=[]),
    )
    calls = {"telegram": 0}
    monkeypatch.setattr(
        daily_digest, "post_telegram",
        lambda *_a, **_k: calls.__setitem__("telegram", calls["telegram"] + 1) or (True, "no"),
    )
    monkeypatch.setattr(daily_digest, "post_twitter", lambda *_a, **_k: (False, "skipped"))
    monkeypatch.setattr(daily_digest, "post_email", lambda *_a, **_k: (False, "skipped"))
    monkeypatch.setenv("INDEXNOW_ENABLED", "0")
    result = daily_digest.run_daily_job(force_rebuild=False, dry_run=False)
    assert calls["telegram"] == 0
    assert "admin-only" in (result["channels"].get("telegram") or "")
    assert not (daily_digest.load_state().get(result["date"]) or {}).get("posted_telegram")


def test_run_daily_job_skips_empty_slate_without_marking_posted(tmp_path, monkeypatch):
    """Empty 09:00 gather must not post a blank slip or lock the Telegram day."""
    monkeypatch.setattr(daily_digest, "DIGEST_DIR", tmp_path / "digest")
    monkeypatch.setattr(daily_digest, "DRAFTS_DIR", tmp_path / "drafts")
    monkeypatch.setattr(daily_digest, "CARDS_DIR", tmp_path / "cards")
    monkeypatch.setattr(daily_digest, "STATE_PATH", tmp_path / "digest" / "state.json")
    monkeypatch.setattr(daily_digest, "LATEST_PATH", tmp_path / "digest" / "latest.json")
    monkeypatch.setattr(daily_digest, "gather_board", lambda force_rebuild=False: ([], {}))
    monkeypatch.setattr(daily_digest, "verify_digest_fixtures", lambda d, **k: d)
    calls = {"telegram": 0}

    def fake_post(*_a, **_k):
        calls["telegram"] += 1
        return True, "should-not-run"

    monkeypatch.setattr(daily_digest, "post_telegram", fake_post)
    monkeypatch.setattr(daily_digest, "post_twitter", lambda *_a, **_k: (True, "no"))
    monkeypatch.setattr(daily_digest, "post_email", lambda *_a, **_k: (True, "no"))
    monkeypatch.setenv("INDEXNOW_ENABLED", "0")
    result = daily_digest.run_daily_job(
        force_rebuild=False, dry_run=False, skip_posts=False,
    )
    assert calls["telegram"] == 0
    assert "admin-only" in (result["channels"].get("telegram") or "")
    state = daily_digest.load_state()
    assert not (state.get(result["date"]) or {}).get("posted_telegram")


def test_run_daily_job_does_not_auto_post_when_admin_only(tmp_path, monkeypatch):
    monkeypatch.setattr(daily_digest, "DIGEST_DIR", tmp_path / "digest")
    monkeypatch.setattr(daily_digest, "DRAFTS_DIR", tmp_path / "drafts")
    monkeypatch.setattr(daily_digest, "CARDS_DIR", tmp_path / "cards")
    monkeypatch.setattr(daily_digest, "STATE_PATH", tmp_path / "digest" / "state.json")
    monkeypatch.setattr(daily_digest, "LATEST_PATH", tmp_path / "digest" / "latest.json")
    pick = _leg(
        league_id="mlb", league_label="MLB", sport_key="baseball",
        sport_label="Baseball", matchup="A @ B", away="A", home="B",
        pick="B", confidence=0.80,
    )
    monkeypatch.setattr(
        daily_digest, "gather_board",
        lambda force_rebuild=False: ([pick], {"combos_2": []}),
    )
    monkeypatch.setattr(daily_digest, "verify_digest_fixtures", lambda d, **k: d)
    monkeypatch.setattr(
        daily_digest, "build_telegram_slip_images",
        lambda digest: [{"caption": "Today's Picks", "kind": "picks", "png_bytes": b"PNG"}],
    )
    calls = {"telegram": 0}
    monkeypatch.setattr(
        daily_digest, "post_telegram",
        lambda *_a, **_k: calls.__setitem__("telegram", 1) or (True, "no"),
    )
    monkeypatch.setenv("INDEXNOW_ENABLED", "0")
    monkeypatch.delenv("CHANNEL_AUTO_POST", raising=False)
    result = daily_digest.run_daily_job(force_rebuild=False, dry_run=False)
    assert calls["telegram"] == 0
    assert result["pick_count"] == 1
    assert "admin-only" in (result["channels"].get("telegram") or "")
    assert daily_digest.maybe_catch_up_channel_posts()["reason"] == "admin-only channel posts"


def test_run_daily_job_reposts_after_empty_telegram_lock(tmp_path, monkeypatch):
    """posted_telegram with 0 slips must not block a later real slate."""
    monkeypatch.setattr(daily_digest, "DIGEST_DIR", tmp_path / "digest")
    monkeypatch.setattr(daily_digest, "DRAFTS_DIR", tmp_path / "drafts")
    monkeypatch.setattr(daily_digest, "CARDS_DIR", tmp_path / "cards")
    monkeypatch.setattr(daily_digest, "STATE_PATH", tmp_path / "digest" / "state.json")
    monkeypatch.setattr(daily_digest, "LATEST_PATH", tmp_path / "digest" / "latest.json")
    monkeypatch.setattr(
        daily_digest, "LAST_TELEGRAM_SEND_PATH", tmp_path / "last_tg.json",
    )
    day = daily_digest.today_key()
    (tmp_path / "digest").mkdir(parents=True)
    daily_digest.save_state({
        day: {"posted_telegram": True, "telegram_slip_count": 0, "digest_written": True},
    })
    pick = _leg(
        league_id="mlb", league_label="MLB", sport_key="baseball",
        sport_label="Baseball", matchup="A @ B", away="A", home="B",
        pick="B", confidence=0.80,
    )
    monkeypatch.setattr(
        daily_digest, "gather_board",
        lambda force_rebuild=False: ([pick], {"combos_2": []}),
    )
    monkeypatch.setattr(daily_digest, "verify_digest_fixtures", lambda d, **k: d)
    monkeypatch.setattr(
        daily_digest, "build_telegram_slip_images",
        lambda digest: [{"caption": "Today's Picks", "kind": "picks", "png_bytes": b"PNG"}],
    )
    calls = {"telegram": 0}

    def fake_post(digest, image_path=None, slip_images=None, **kwargs):
        calls["telegram"] += 1
        assert slip_images
        return True, "posted Telegram slip images"

    monkeypatch.setattr(daily_digest, "post_telegram", fake_post)
    monkeypatch.setattr(daily_digest, "post_twitter", lambda *_a, **_k: (False, "skipped"))
    monkeypatch.setattr(daily_digest, "post_email", lambda *_a, **_k: (False, "skipped"))
    monkeypatch.setenv("INDEXNOW_ENABLED", "0")
    monkeypatch.setenv("CHANNEL_AUTO_POST", "1")
    result = daily_digest.run_daily_job(force_rebuild=False, dry_run=False)
    assert calls["telegram"] == 1
    assert result["pick_count"] == 1
    state = daily_digest.load_state()
    assert (state.get(day) or {}).get("telegram_slip_count") == 1


def test_digest_channel_flags_failed_row_is_not_done():
    assert daily_digest._digest_channel_flags({
        "at": "2026-08-13T00:00:00+00:00",
        "status": "won",
        "telegram": False,
        "twitter": False,
    }) == (False, False, False)
    assert daily_digest._digest_channel_flags({
        "at": "2026-08-13T00:00:00+00:00",
        "status": "won",
        "title": "Today's Picks",
    }) == (True, False, False)


def test_collect_digest_results_is_todays_picks_plus_featured_only(tmp_path, monkeypatch):
    monkeypatch.setattr(daily_digest, "DIGEST_DIR", tmp_path / "digest")
    (tmp_path / "digest").mkdir()
    day = "2026-08-12"
    won_leg = _leg(
        league_id="mlb", matchup="A vs B", home="A", away="B",
        pick="A", market="Moneyline",
    )
    lost_leg = _leg(
        league_id="mlb", matchup="C vs D", home="C", away="D",
        pick="C", market="Moneyline",
    )
    combo_leg = _leg(matchup="E vs F", home="E", away="F", pick="E")
    (tmp_path / "digest" / f"{day}.json").write_text(json.dumps({
        "date": day,
        "picks": [won_leg, lost_leg],
        "combos_2": [{
            "title": "Best 2-leg",
            "legs": [won_leg, combo_leg],
        }],
        "combos_3": [], "mixed_2": [], "mixed_3": [],
    }), encoding="utf-8")

    def fake_grade(legs):
        out = []
        for leg in legs:
            row = dict(leg)
            if (leg.get("pick") or "") == "C":
                row["status"] = "lost"
            else:
                row["status"] = "won"
            out.append(row)
        status = "lost" if any(r["status"] == "lost" for r in out) else "won"
        return out, status

    monkeypatch.setattr(daily_digest, "_grade_digest_slip_legs", fake_grade)
    monkeypatch.setattr(
        daily_digest,
        "collect_board_won_combo_slips",
        lambda _day: [{
            "kind": "combo",
            "title": "Board-only win",
            "slate_date": day,
            "status": "won",
            "legs": [won_leg, combo_leg],
        }],
    )
    slips = daily_digest.collect_digest_result_slips(day)
    statuses = [(s["title"], s["status"]) for s in slips]
    assert statuses == [
        ("Today's Picks", "lost"),
        ("Best 2-leg", "won"),
    ]
    assert "Board-only win" not in [s[0] for s in statuses]
    assert not any("Today's Picks ·" in (s[0] or "") for s in statuses)


def test_collect_digest_result_slips_one_todays_picks_card_when_all_won(
    tmp_path, monkeypatch,
):
    day = "2026-08-12"
    monkeypatch.setattr(daily_digest, "DIGEST_DIR", tmp_path / "digest")
    (tmp_path / "digest").mkdir()
    a = _leg(matchup="A vs B", home="A", away="B", pick="B", market="Moneyline")
    b = _leg(matchup="C vs D", home="C", away="D", pick="C", market="Moneyline")
    (tmp_path / "digest" / f"{day}.json").write_text(json.dumps({
        "date": day,
        "picks": [a, b],
        "combos_2": [], "combos_3": [], "mixed_2": [], "mixed_3": [],
    }), encoding="utf-8")

    def fake_grade(legs):
        out = [dict(leg, status="won") for leg in legs]
        return out, "won"

    monkeypatch.setattr(daily_digest, "_grade_digest_slip_legs", fake_grade)
    slips = daily_digest.collect_digest_result_slips(day)
    statuses = [(s["title"], s["status"]) for s in slips]
    assert statuses == [("Today's Picks", "won")]


def test_twitter_result_text_is_short_and_tagged():
    item = {
        "title": "Today's Picks",
        "kind": "combo",
        "status": "won",
        "slate_date": "2026-08-12",
        "legs": [
            _leg(league_id="mlb", league_label="MLB", sport_key="baseball",
                 sport_label="Baseball", matchup="NYY @ BOS", away="NYY",
                 home="BOS", pick="Under 8.5", market="Total runs"),
        ],
    }
    text = daily_digest._format_twitter_result_text(item)
    assert "RESULT · WON" in text
    assert "Today's Picks" in text
    assert "#DegenPredictions" in text
    assert "#MLB" in text
    assert "utm_source=twitter" in text
    assert daily_digest._tweet_len(
        text, daily_digest.site_utm(
            "/todays-picks/combos", campaign="x_result", source="twitter",
        ),
    ) <= 280


def test_post_twitter_result_skips_without_creds(monkeypatch):
    monkeypatch.delenv("TWITTER_API_KEY", raising=False)
    monkeypatch.delenv("X_API_KEY", raising=False)
    ok, msg = daily_digest.post_twitter_result({"title": "Slip", "status": "won", "legs": []})
    assert ok is False
    assert "skipped" in msg.lower()


def test_digest_results_post_telegram_and_x(tmp_path, monkeypatch):
    monkeypatch.setattr(daily_digest, "DIGEST_DIR", tmp_path / "digest")
    monkeypatch.setattr(daily_digest, "CARDS_DIR", tmp_path / "cards")
    monkeypatch.setattr(
        daily_digest, "TELEGRAM_DIGEST_RESULTS_PATH", tmp_path / "tg_results.json",
    )
    day = "2026-08-12"
    (tmp_path / "digest").mkdir(parents=True)
    (tmp_path / "digest" / f"{day}.json").write_text(json.dumps({
        "date": day,
        "picks": [_leg(
            league_id="mlb", league_label="MLB", sport_key="baseball",
            sport_label="Baseball", matchup="A vs B", home="A", away="B",
            pick="A", market="Moneyline",
        )],
        "combos_2": [], "combos_3": [], "mixed_2": [], "mixed_3": [],
    }), encoding="utf-8")
    monkeypatch.setattr(
        daily_digest, "_grade_digest_slip_legs",
        lambda legs: ([{**leg, "status": "won", "actual_note": "2-1"} for leg in legs], "won"),
    )
    tg_calls = []
    x_calls = []
    monkeypatch.setattr(
        daily_digest,
        "post_telegram_result_reply",
        lambda *a, **k: tg_calls.append(k.get("caption") or a[0]) or (True, "ok"),
    )
    monkeypatch.setattr(
        daily_digest,
        "post_twitter_result",
        lambda item, **k: x_calls.append(item.get("title")) or (True, "ok"),
    )
    monkeypatch.setattr(
        daily_digest,
        "build_telegram_result_image",
        lambda item: {"png_bytes": b"PNG", "caption": "RESULT · WON"},
    )
    out = daily_digest.post_digest_day_telegram_results(day)
    assert out["posted"] == 1
    assert out["twitter_posted"] == 1
    assert tg_calls
    assert x_calls == ["Today's Picks"]

    out2 = daily_digest.post_digest_day_telegram_results(day)
    assert out2["posted"] == 0
    assert out2["twitter_posted"] == 0


def test_digest_results_backfill_x_after_telegram(tmp_path, monkeypatch):
    monkeypatch.setattr(daily_digest, "DIGEST_DIR", tmp_path / "digest")
    monkeypatch.setattr(daily_digest, "CARDS_DIR", tmp_path / "cards")
    state_path = tmp_path / "tg_results.json"
    monkeypatch.setattr(daily_digest, "TELEGRAM_DIGEST_RESULTS_PATH", state_path)
    day = "2026-08-12"
    (tmp_path / "digest").mkdir(parents=True)
    pick = _leg(
        league_id="mlb", league_label="MLB", sport_key="baseball",
        sport_label="Baseball", matchup="A vs B", home="A", away="B",
        pick="A", market="Moneyline",
    )
    (tmp_path / "digest" / f"{day}.json").write_text(json.dumps({
        "date": day,
        "picks": [pick],
        "combos_2": [], "combos_3": [], "mixed_2": [], "mixed_3": [],
    }), encoding="utf-8")
    monkeypatch.setattr(
        daily_digest, "_grade_digest_slip_legs",
        lambda legs: ([{**leg, "status": "won", "actual_note": "2-1"} for leg in legs], "won"),
    )
    fp = daily_digest.digest_result_fingerprint({
        "title": "Today's Picks",
        "status": "won",
        "legs": [{**pick, "status": "won"}],
    })
    state_path.write_text(json.dumps({
        day: {fp: {"at": "2026-08-13T00:00:00+00:00", "status": "won", "title": "Today's Picks"}},
    }), encoding="utf-8")

    tg_calls = []
    x_calls = []
    monkeypatch.setattr(
        daily_digest,
        "post_telegram_result_reply",
        lambda *a, **k: tg_calls.append(1) or (True, "ok"),
    )
    monkeypatch.setattr(
        daily_digest,
        "post_twitter_result",
        lambda item, **k: x_calls.append(item.get("title")) or (True, "ok"),
    )
    monkeypatch.setattr(
        daily_digest,
        "build_telegram_result_image",
        lambda item: {"png_bytes": b"PNG", "caption": "RESULT · WON"},
    )
    out = daily_digest.post_digest_day_telegram_results(day)
    assert out["posted"] == 0
    assert out["twitter_posted"] == 1
    assert tg_calls == []
    assert x_calls == ["Today's Picks"]