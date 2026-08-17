"""Pick ledger + EC2 feedback report helpers."""
import combo_results
import today_picks


def test_snapshot_published_picks_and_feedback(tmp_path, monkeypatch):
    db = tmp_path / "combo_results.db"
    monkeypatch.setattr(combo_results, "DB_PATH", str(db))
    combo_results.init_db()

    legs = [
        today_picks.annotate_leg(
            today_picks._pick(
                away="Red Sox", home="Yankees", pick="Yankees",
                confidence=0.74, market="Moneyline",
                matchup="Red Sox @ Yankees",
            ),
            sport_key="baseball", sport_label="Baseball",
            league_id="mlb", league_label="MLB",
        ),
        today_picks.annotate_leg(
            today_picks._pick(
                away="Chelsea", home="Arsenal", pick="Under 2.5",
                confidence=0.71, market="Total Goals",
                matchup="Arsenal vs Chelsea",
            ),
            sport_key="soccer", sport_label="Soccer",
            league_id="premier_league", league_label="Premier League",
        ),
    ]
    n = combo_results.snapshot_published_picks(legs, slate_date="2026-08-10")
    assert n == 2
    assert combo_results.snapshot_published_picks(legs, slate_date="2026-08-10") == 0

    def fake_lookup(leg):
        if leg.get("league_id") == "mlb":
            return {
                "home": "Yankees", "away": "Red Sox",
                "home_score": 5, "away_score": 2, "completed": True,
            }
        return {
            "home": "Arsenal", "away": "Chelsea",
            "home_score": 0, "away_score": 1, "completed": True,
        }

    monkeypatch.setattr(combo_results, "lookup_score_for_leg", fake_lookup)
    stats = combo_results.settle_pending()
    assert stats["picks_settled"] == 2

    report = combo_results.feedback_report(days=30)
    assert report["overall"]["n"] == 2
    assert report["overall"]["hit_rate"] == 1.0
    assert "promote_baseline" in report


def test_effective_high_prob_min_raises_from_live_cal(tmp_path, monkeypatch):
    cal = tmp_path / "live_calibration.json"
    cal.write_text(
        '{"suggested_min_confidence": 0.72, "overall": {"n": 40}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(today_picks, "_LIVE_CAL_PATH", str(cal))
    today_picks._live_cal_cache = (None, None)
    assert today_picks.effective_high_prob_min() == 0.72
