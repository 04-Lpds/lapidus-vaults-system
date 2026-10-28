"""Only exact receipt-checked transaction intentions advance campaigns."""
import json
import sqlite3
import pytest
from lapidus_vaults.operations.campaigns import create_campaign
from lapidus_vaults.operations.receipt_bridge import apply_verified_receipt
from lapidus_vaults.unsigned_candidates import UnsignedCandidate

def setup(tmp_path):
    db=str(tmp_path/"test.sqlite")
    wallet="0x"+"1"*40
    vault="0x"+"2"*40
    data="0xb460af94"+"00"*96
    c=UnsignedCandidate(1,(),"withdraw_intent",8453,wallet,vault,0,data,1000000)
    create_campaign(db,"x",(c,))
    with sqlite3.connect(db) as cx:
        cx.execute("""CREATE TABLE live_deposit_authorizations
          (quote_id TEXT,wallet TEXT,vault TEXT,stage TEXT,amount_units INTEGER,transaction_json TEXT)""")
        tx={"to":vault,"data":data,"chain_id":8453}
        cx.execute("INSERT INTO live_deposit_authorizations VALUES (?,?,?,?,?,?)",
                   ("q",wallet,vault,"withdraw",1000000,json.dumps(tx)))
    return db

def valid_receipt():
    return {"status":"RECEIPT_AND_BLOCK_MATCH","matched":True,
            "tx_hash":"0x"+"a"*64,"balance_match":True,
            "transaction_event_reconciliation":{"matched":True}}

def test_valid_checked_receipt_completes_campaign(tmp_path):
    db=setup(tmp_path)
    assert apply_verified_receipt(db,"x",1,"q",valid_receipt())["state"]=="COMPLETED"

def test_rejects_unverified_or_mismatched_evidence(tmp_path):
    db=setup(tmp_path)
    r=valid_receipt()
    r["transaction_event_reconciliation"]["matched"]=False
    with pytest.raises(ValueError,match="Missing independent"):
        apply_verified_receipt(db,"x",1,"q",r)
    r=valid_receipt()
    r["status"]="FAKE"
    with pytest.raises(ValueError):
        apply_verified_receipt(db,"x",1,"q",r)

def test_wrong_campaign_calldata_cannot_advance(tmp_path):
    db=setup(tmp_path)
    with sqlite3.connect(db) as cx:
        cx.execute("UPDATE live_deposit_authorizations SET transaction_json=? WHERE quote_id='q'",
                   (json.dumps({"to":"0x"+"2"*40,"data":"0xdead","chain_id":8453}),))
    with pytest.raises(ValueError,match="differs"):
        apply_verified_receipt(db,"x",1,"q",valid_receipt())

def test_explicit_binding_advances_only_matching_receipt(tmp_path):
    from lapidus_vaults.operations.receipt_bridge import (
        bind_campaign_quote, advance_bound_campaign)
    db=setup(tmp_path)
    assert advance_bound_campaign(db,"q",valid_receipt()) is None
    bind_campaign_quote(db,"x",1,"q")
    completed=advance_bound_campaign(db,"q",valid_receipt())
    assert completed["state"]=="COMPLETED"
    assert advance_bound_campaign(db,"q",valid_receipt())["state"]=="COMPLETED"

def test_invalid_binding_rejected_before_receipt(tmp_path):
    from lapidus_vaults.operations.receipt_bridge import bind_campaign_quote
    db=setup(tmp_path)
    with pytest.raises(ValueError,match="No active campaign"):
        bind_campaign_quote(db,"x",1,"nonexistent")
    with pytest.raises(ValueError,match="Invalid campaign step"):
        bind_campaign_quote(db,"x",2,"q")
