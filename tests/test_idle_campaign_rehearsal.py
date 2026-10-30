import pytest
from lapidus_vaults.operations.idle_campaign_rehearsal import rehearse

W="0x"+"1"*40
A="0x"+"2"*40
B="0x"+"3"*40

def test_idle_snapshot_generates_approve_then_deposit_without_signing():
    result=rehearse(W,(A,B))
    assert result["status"]=="OFFLINE_IDLE_CAMPAIGN_ADMITTED"
    assert result["steps"]==["approve_intent","deposit_intent"]
    assert result["first_step_status"]=="READY_FOR_MANUAL_PREFLIGHT"
    assert result["submitted"] is False
    assert result["live_signer_enabled"] is False

@pytest.mark.parametrize("kwargs",[
    {"capital_units":5_000_001},
    {"idle_units":5_000_001},
    {"deposit_units":1_000_001},
    {"idle_units":100_000,"deposit_units":500_000},
])
def test_hard_caps_reject_unsafe_idle_campaign(kwargs):
    with pytest.raises(ValueError):
        rehearse(W,(A,B),**kwargs)

def test_duplicate_vaults_rejected():
    with pytest.raises(ValueError):
        rehearse(W,(A,A))
