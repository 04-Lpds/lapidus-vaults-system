"""Unattended policy must fail closed for mainnet and unsafe capital."""
import pytest
from lapidus_vaults.operations.autopilot_policy import AutoPolicy, next_auto_action
from lapidus_vaults.operations.campaigns import create_campaign, record_receipt_status
from lapidus_vaults.unsigned_candidates import UnsignedCandidate

W = "0x" + "1"*40
V = "0x" + "2"*40

def policy(**kwargs):
    settings = dict(wallet=W, vault_allowlist=(V,), max_action_units=1_000_000,
                    max_capital_units=10_000_000)
    settings.update(kwargs)
    return AutoPolicy(**settings)

def test_autonomous_mainnet_live_mode_rejected():
    with pytest.raises(ValueError, match="not implemented"):
        policy(live_enabled=True)

@pytest.mark.parametrize("overrides", [
    {"max_action_units": 1_000_001},
    {"max_capital_units": 10_000_001},
    {"max_action_units": 0},
    {"vault_allowlist": ()},
    {"chain_id": 1},
])
def test_unsafe_policy_rejected(overrides):
    with pytest.raises(ValueError):
        policy(**overrides)

def test_auto_policy_blocks_oversized_campaign_step(tmp_path):
    db = str(tmp_path / "auto.db")
    candidate = UnsignedCandidate(1, (), "deposit_intent", 8453, W, V, 0, "0x", 2_000_000)
    create_campaign(db, "auto-test", (candidate,))
    with pytest.raises(ValueError, match="cap"):
        next_auto_action(db, policy=policy(), observed_capital_units=5_000_000,
                         current_chain_id=8453)

def test_auto_policy_rejects_wrong_chain_and_capital(tmp_path):
    db = str(tmp_path / "auto.db")
    with pytest.raises(ValueError, match="Wrong chain"):
        next_auto_action(db, policy=policy(), observed_capital_units=1_000_000,
                         current_chain_id=1)
    with pytest.raises(ValueError, match="outside unattended policy"):
        next_auto_action(db, policy=policy(), observed_capital_units=11_000_000,
                         current_chain_id=8453)

def test_auto_policy_never_enables_broadcast(tmp_path):
    db = str(tmp_path / "auto.db")
    candidate = UnsignedCandidate(1, (), "deposit_intent", 8453, W, V, 0, "0x", 1_000_000)
    create_campaign(db, "auto-test", (candidate,))
    decision = next_auto_action(db, policy=policy(), observed_capital_units=5_000_000,
                                current_chain_id=8453)
    assert decision["status"] == "AUTO_POLICY_PASSED_AWAITING_SECURE_SIGNER"
    assert decision["automatic_submission_enabled"] is False
