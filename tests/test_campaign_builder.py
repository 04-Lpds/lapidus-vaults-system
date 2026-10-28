"""Campaign creation gates, without real RPC or money movement."""
from unittest.mock import Mock,patch
import pytest
from lapidus_vaults.operations.campaign_builder import build_campaign_from_live_plan
from lapidus_vaults.fork_roundtrip import ACTOR,VAULTS
from lapidus_vaults.wallet_plan import Policy
from decimal import Decimal

def report():
    a=VAULTS[0].lower()
    return {"status":"VERIFIED_READ_ONLY_PLAN_NOT_EXECUTABLE","plan":{
        "status":"NON_EXECUTABLE_REQUIRES_FRESH_PREFLIGHT","chain_id":8453,
        "observed_block":100,"wallet":ACTOR.lower(),
        "capital_units":5_000_000,"starting_cash_units":0,
        "intended_idle_units":5_000_000,"projected_cash_units":5_000_000,
        "actions":[{"sequence":1,"kind":"withdraw_intent","vault":a,
                    "amount_units":5_000_000,"depends_on":[],
                    "precondition":"fresh exit check"}],
        "assumptions":["non-executable"]}}

def test_creates_campaign_after_fresh_simulated_withdrawal(tmp_path):
    rpc=Mock()
    rpc.chain_id.return_value=8453
    rpc.block_number.return_value=101
    policy=Policy((VAULTS[0].lower(),),Decimal(".2"),Decimal(".4"))
    with patch("lapidus_vaults.operations.campaign_builder.read_policy",return_value=policy),\
         patch("lapidus_vaults.operations.campaign_builder.WithdrawalQuotes") as quote:
        result=build_campaign_from_live_plan(str(tmp_path/"db.sqlite"),"run1",ACTOR,
                                               "unused",report(),rpc)
        quote.return_value.quote.assert_called_once()
        assert result["execution_enabled"] is False
        assert result["steps"][0]["status"]=="READY_FOR_MANUAL_PREFLIGHT"

def test_rejects_stale_plan_before_quote_or_persistence(tmp_path):
    rpc=Mock()
    rpc.chain_id.return_value=8453
    rpc.block_number.return_value=111
    with pytest.raises(ValueError,match="stale"):
        build_campaign_from_live_plan(str(tmp_path/"db.sqlite"),"run1",ACTOR,
                                      "unused",report(),rpc)

def test_simulation_failure_blocks_campaign(tmp_path):
    rpc=Mock()
    rpc.chain_id.return_value=8453
    rpc.block_number.return_value=101
    policy=Policy((VAULTS[0].lower(),),Decimal(".2"),Decimal(".4"))
    with patch("lapidus_vaults.operations.campaign_builder.read_policy",return_value=policy),\
         patch("lapidus_vaults.operations.campaign_builder.WithdrawalQuotes") as quote:
        quote.return_value.quote.side_effect=ValueError("insufficient instant liquidity")
        with pytest.raises(ValueError,match="liquidity"):
            build_campaign_from_live_plan(str(tmp_path/"db.sqlite"),"run1",ACTOR,
                                          "unused",report(),rpc)
