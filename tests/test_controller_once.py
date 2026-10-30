"""Offline controller tests: never sign or submit."""
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from lapidus_vaults.operations.controller_once import controller_once
from lapidus_vaults.wallet_plan import Policy

W="0x"+"1"*40
V="0x"+"2"*40

class StubClient:
    def __init__(self,*args): pass
    def __enter__(self): return self
    def __exit__(self,*args): pass

def observed():
    return {"plan":{"chain_id":8453,"wallet":W,"observed_block":100,
      "capital_units":5_000_000,"wallet_usdc_units":5_000_000,
      "changes":[{"vault_address":V,"delta_units":800_000}],
      "actions":[{"kind":"deposit_intent","amount_units":800_000,"vault":V}]}}

def exercise(path,decision):
    path.touch()
    economics={"decision":decision,"submitted":False,"capital_units":5_000_000,
      "estimated_fee_usd":"0.001",
      "projected_historical_rate_yield_delta_usd":"0.10",
      "eth_usd_assumption":"3000"}
    root="lapidus_vaults.operations.controller_once."
    with patch(root+"ReadOnlyRPC",StubClient), patch(root+"MorphoClient",StubClient), patch(
         root+"read_policy",return_value=Policy((V,),Decimal("0.2"),Decimal("0.4"))), patch(
         root+"observation_report",return_value=observed()), patch(
         root+"evaluate_live",return_value=economics):
        return controller_once(db_path=str(path),wallet=W,allowlist="unused",
           rpc_url="https://mainnet.base.org",eth_usd=Decimal("3000"),
           campaign_id="test-campaign")

def test_hold_has_no_campaign(tmp_path):
    import sqlite3
    path=tmp_path/"hold.sqlite3"
    out=exercise(path,"HOLD_ESTIMATED_FEES_EXCEED_BENEFIT")
    assert out["status"]=="HOLD"
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM shadow_decisions").fetchone()[0]==1

def test_admitted_persists_campaign(tmp_path):
    from lapidus_vaults.operations.campaigns import read_campaign
    path=tmp_path/"eligible.sqlite3"
    out=exercise(path,"REBALANCE_REVIEW_ONLY")
    assert out["status"]=="CAMPAIGN_CREATED_REVIEW_ONLY"
    assert out["steps"]==2
    assert out["submitted"] is False and out["execution_enabled"] is False
    assert read_campaign(str(path),"test-campaign")["state"]=="ACTIVE"
