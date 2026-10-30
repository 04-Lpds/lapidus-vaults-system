import pytest
from lapidus_vaults.operations.pilot_config_preview import validate

CONFIG={"managed_capital_usdc":"20","max_action_usdc":"1",
        "cycle_seconds":300,"idle_deployment_horizon_days":180,
        "minimum_idle_deposit_usdc":"0.5","rebalance_drift_bps":500}

def test_preview_does_not_enable_live_signing():
    result=validate(CONFIG)
    assert result["status"]=="VALID_PREVIEW_NOT_ACTIVE"
    assert result["live_signing_enabled"] is False

@pytest.mark.parametrize("field,value",[
    ("managed_capital_usdc","21"),
    ("max_action_usdc","25"),
    ("minimum_idle_deposit_usdc","1.5"),
    ("cycle_seconds",30),
    ("rebalance_drift_bps",20),
    ("managed_capital_usdc","NaN"),
    ("max_action_usdc","0.1234567"),
])
def test_invalid_preview_rejected(field,value):
    with pytest.raises(ValueError):
        validate({**CONFIG,field:value})

def test_unknown_settings_fail_closed():
    with pytest.raises(ValueError):
        validate({**CONFIG,"private_key":"do-not-store"})
