"""Unit tests for bet-slip text parsing (no image persistence)."""

from slip_ocr import parse_slip_image, parse_slip_text


def test_parse_stake_2_leg_multi_text():
    text = """
    2 Leg Multi
    2.28
    Both Teams to Score
    Yes
    1.63
    Bodo Glimt vs Union Saint Gilloise
    Tue, Aug 11 at 6:00 PM
    1x2
    Olympique Lyonnais
    1.40
    Olympique Lyonnais vs AC Sparta Prague
    Tue, Aug 11 at 9:00 PM
    """
    parsed = parse_slip_text(text)
    assert parsed["slip_mode"] == "multi"
    assert len(parsed["legs"]) >= 2
    events = " | ".join(lg.get("event", "") for lg in parsed["legs"]).lower()
    assert "bodo" in events or "glimt" in events
    assert "lyon" in events or "olympique" in events


def test_parse_slip_image_text_only_no_bytes():
    """Pasted text path — image_bytes empty; nothing to store."""
    result = parse_slip_image(
        b"",
        ocr_text="Single\nFootball\nUEFA Champions League\nBTTS\nYes\nTeam A vs Team B\n",
    )
    assert result.get("stored") is None or result.get("ok") in (True, False)
    assert "raw_text" in result or result.get("legs") is not None
    # Must not invent a filesystem path
    assert "path" not in result
    assert "image_path" not in result
