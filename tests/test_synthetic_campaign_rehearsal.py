"""Synthetic admission to isolated SQLite campaign, no funds or RPC."""
from lapidus_vaults.operations.synthetic_campaign_rehearsal import rehearse

def test_profitable_persists_isolated_campaign():
    r=rehearse("profitable")
    assert r["admission"]=="ADMISSION_REVIEW_ONLY"
    assert r["isolated_campaign_persisted"] is True
    assert r["campaign_state"]=="ACTIVE"
    assert r["step_count"]==2
    assert r["submitted"] is False

def test_blocked_scenarios_never_create_campaign():
    for name in ("uneconomic","tampered"):
        r=rehearse(name)
        assert r["admission"]=="ADMISSION_BLOCKED"
        assert r["isolated_campaign_persisted"] is False
        assert r["submitted"] is False
