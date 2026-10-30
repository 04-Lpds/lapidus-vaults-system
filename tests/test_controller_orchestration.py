"""Composition contract: HOLD never executes, admitted fork invokes bounded driver."""
from decimal import Decimal
import pytest
from lapidus_vaults.operations.controller_orchestration import run_fork_controller_cycle
BASE=dict(db_path="unused",wallet="0x"+"1"*40,
          protected_wallet="0x"+"2"*40,allowlist="unused.json",
          live_rpc_url="https://mainnet.base.org",
          fork_rpc_url="http://127.0.0.1:8545",eth_usd=Decimal("3000"),
          campaign_id="test-case",fork_private_key="disposable-test-only")

def test_hold_never_calls_executor():
    def decision(**kwargs):return {"status":"HOLD"}
    def forbidden(**kwargs):raise AssertionError("executed on HOLD")
    out=run_fork_controller_cycle(**BASE,controller_fn=decision,driver_fn=forbidden)
    assert out["status"]=="CONTROLLER_NO_EXECUTION"
    assert out["submitted"] is False

def test_admitted_decision_passes_evidence_to_fork_driver():
    economics={"decision":"REBALANCE_REVIEW_ONLY","capital_units":1000000}
    def decision(**kwargs):
        return {"status":"CAMPAIGN_CREATED_REVIEW_ONLY",
                "campaign_id":"test-case","economic_evidence":economics}
    observed=[]
    def driver(**kwargs):
        observed.append(kwargs)
        return {"status":"CAMPAIGN_COMPLETED"}
    out=run_fork_controller_cycle(**BASE,controller_fn=decision,driver_fn=driver)
    assert out["execution"]["status"]=="CAMPAIGN_COMPLETED"
    assert observed[0]["admitted_economics"] is economics
    assert observed[0]["rpc_url"]=="http://127.0.0.1:8545"

def test_remote_fork_endpoint_rejected_before_decision():
    with pytest.raises(ValueError):
        run_fork_controller_cycle(**{**BASE,"fork_rpc_url":"https://mainnet.base.org"},
            controller_fn=lambda **kwargs: (_ for _ in ()).throw(AssertionError("called")))
