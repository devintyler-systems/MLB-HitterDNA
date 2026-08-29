from hitterdna.discovery_queue import market_ready, validate_discovery_record


def base_record():
    return {
        "analysis_date": "2026-08-29",
        "game_pk": 999999,
        "player_mlbam_id": 999999,
        "candidate_name": "Example Hitter",
        "team": "EX",
        "opponent": "OP",
        "prop_family": "home_run",
        "source_type": "savant",
        "trigger_type": "arsenal_collision",
        "raw_claim": "Hitter owns above-baseline expected damage versus the projected pitch lane.",
        "source_url": "https://baseballsavant.mlb.com/",
        "retrieved_at_utc": "2026-08-29T19:15:00Z",
        "sample_type": "pitches",
        "sample_n": 245,
        "stabilization_status": "pass",
        "feature_mapping": ["arsenal_collision"],
        "expected_direction": "positive",
        "duplicate_risk": "low",
        "evidence_status": "verified",
        "lineup_status": "confirmed",
        "market_status": "unpriced",
        "disposition": "investigate",
    }


def test_valid_arsenal_claim_advances():
    decision = validate_discovery_record(base_record())
    assert decision.valid is True
    assert decision.disposition == "advance"


def test_bvp_under_50_pa_drops():
    record = base_record()
    record.update(
        source_type="bvp_display",
        trigger_type="bvp",
        raw_claim="Hitter is 3-for-5 with one home run against pitcher.",
        sample_type="ab",
        sample_n=5,
    )
    decision = validate_discovery_record(record)
    assert decision.valid is False
    assert decision.disposition == "drop"
    assert "BvP under 50 PA: descriptive only, not predictive" in decision.reasons


def test_streak_claim_drops():
    record = base_record()
    record["raw_claim"] = "Hitter has an active 12-game hit streak."
    decision = validate_discovery_record(record)
    assert decision.valid is False
    assert "prohibited recency, streak, or due-logic evidence" in decision.reasons


def test_market_gate_holds_missing_price():
    record = base_record()
    record["disposition"] = "advance"
    decision = market_ready(record)
    assert decision.valid is False
    assert decision.disposition == "hold"


def test_market_gate_allows_price():
    record = base_record()
    record.update(
        disposition="advance",
        market_book="ExampleBook",
        market_line=0.5,
        market_price=175,
    )
    decision = market_ready(record)
    assert decision.valid is True
    assert decision.disposition == "priced"
