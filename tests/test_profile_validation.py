from __future__ import annotations

from tr_bot.strategy.profile import build_profile_document, compute_config_hash, validate_candidate_profile


def test_config_hash_stable():
    params = {"RISK_PER_TRADE": 0.01, "MAX_TRADES_PER_DAY": 2}
    h1 = compute_config_hash(params)
    h2 = compute_config_hash({"MAX_TRADES_PER_DAY": 2, "RISK_PER_TRADE": 0.01})
    assert h1 == h2
    assert len(h1) == 64


def test_validate_candidate_profile_ok():
    doc = build_profile_document("test_profile", {"RISK_PER_TRADE": 0.01})
    result = validate_candidate_profile(doc, expected_profile_id="test_profile")
    assert result["ok"] is True
    assert result["config_hash"]


def test_validate_rejects_unknown_param():
    doc = build_profile_document("test_profile", {"RISK_PER_TRADE": 0.01})
    doc["params"]["NOT_A_REAL_PARAM"] = 1
    result = validate_candidate_profile(doc)
    assert result["ok"] is False
