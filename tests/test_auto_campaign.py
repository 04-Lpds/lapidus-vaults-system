"""Behavior tests for the bounded trial campaign runner, with no network or keys."""
import json
import sqlite3
from types import SimpleNamespace
import pytest
from lapidus_vaults.operations import auto_campaign

W="0x"+"1"*40
P="0x"+"3"*40
V="0x"+"2"*40
U="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"

def setup_campaign(tmp_path):
    db=tmp_path/"campaign.sqlite3"
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE rebalance_campaigns(campaign_id TEXT,wallet TEXT,plan_json TEXT,state TEXT)")
        steps=[{"method":"approve_intent","requested_assets_units":800000,"to_address":U},
               {"method":"deposit_intent","requested_assets_units":800000,"to_address":V}]
        c.execute("INSERT INTO rebalance_campaigns VALUES (?,?,?,?)",
                  ("trial",W,json.dumps(steps),"ACTIVE"))
    return str(db)

def test_recovery_and_deposit_complete(tmp_path,monkeypatch):
    db=setup_campaign(tmp_path)
    monkeypatch.setattr(auto_campaign,"read_policy",lambda _:SimpleNamespace(vault_addresses=(V,)))
    calls=[]
    def step(**kwargs):
        calls.append(True)
        if len(calls)==1:
            return {"status":"RECEIPT_AND_BLOCK_MATCH","resubmitted":False}
        if len(calls)==2:
            return {"state":"SUBMITTED","sequence":2}
        with sqlite3.connect(db) as c:
            c.execute("UPDATE rebalance_campaigns SET state='COMPLETED'")
        return {"status":"RECEIPT_AND_BLOCK_MATCH"}
    result=auto_campaign.run_campaign(db_path=db,campaign_id="trial",
        wallet=W,protected_wallet=P,allowlist_path="x",rpc_url="https://example.invalid",
        private_key="fake",execute_fn=step,sleep_fn=lambda _:None)
    assert result["status"]=="CAMPAIGN_COMPLETED"
    assert len(calls)==3

def test_pending_does_not_advance_to_new_send(tmp_path,monkeypatch):
    db=setup_campaign(tmp_path)
    monkeypatch.setattr(auto_campaign,"read_policy",lambda _:SimpleNamespace(vault_addresses=(V,)))
    result=auto_campaign.run_campaign(db_path=db,campaign_id="trial",
        wallet=W,protected_wallet=P,allowlist_path="x",rpc_url="https://example.invalid",
        private_key="fake",execute_fn=lambda **_:{"status":"PENDING_NOT_FOUND"},sleep_fn=lambda _:None)
    assert result["status"]=="PAUSED_INVOCATION_LIMIT"
    assert len(result["events"])==8

def test_refuse_non_trial_without_sending(tmp_path,monkeypatch):
    db=setup_campaign(tmp_path)
    with sqlite3.connect(db) as c:
        c.execute("UPDATE rebalance_campaigns SET plan_json=?",
                  (json.dumps([{"method":"withdraw_intent","requested_assets_units":800000,"to_address":V}]),))
    with pytest.raises(ValueError,match="exact 0.8"):
        auto_campaign.run_campaign(db_path=db,campaign_id="trial",
            wallet=W,protected_wallet=P,allowlist_path="x",rpc_url="https://example.invalid",
            private_key="fake",execute_fn=lambda **_:pytest.fail("must never send"))
