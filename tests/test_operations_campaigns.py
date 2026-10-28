"""Durable supervised campaigns; tests do not require RPC or live funds."""
import pytest
from lapidus_vaults.unsigned_candidates import UnsignedCandidate
from lapidus_vaults.operations.campaigns import create_campaign,read_campaign,record_receipt_status,halt_campaign

def setup(tmp_path):
    path=str(tmp_path/"campaign.sqlite")
    actor="0x"+"1"*40
    vault="0x"+"2"*40
    steps=tuple(UnsignedCandidate(i,deps,kind,8453,actor,vault,0,"0x",1000000)
                for i,deps,kind in [(1,(),"withdraw_intent"),(2,(1,),"deposit_intent")])
    return path,steps

def test_campaign_persists_and_enforces_dependencies(tmp_path):
    db,steps=setup(tmp_path)
    created=create_campaign(db,"c1",steps)
    assert [s["status"] for s in created["steps"]]==["READY_FOR_MANUAL_PREFLIGHT","BLOCKED"]
    with pytest.raises(ValueError,match="active campaign"):
        create_campaign(db,"c2",steps)
    tx1="0x"+"a"*64
    with pytest.raises(ValueError,match="Earlier"):
        record_receipt_status(db,"c1",2,"SUBMITTED","0x"+"b"*64)
    pending=record_receipt_status(db,"c1",1,"SUBMITTED",tx1)
    assert pending["steps"][0]["status"]=="AWAIT_CONFIRMATION"
    with pytest.raises(ValueError,match="bound transaction"):
        record_receipt_status(db,"c1",1,"SUBMITTED","0x"+"c"*64)
    ready=record_receipt_status(db,"c1",1,"RECEIPT_AND_BLOCK_MATCH",tx1)
    assert ready["steps"][1]["status"]=="READY_FOR_MANUAL_PREFLIGHT"
    done=record_receipt_status(db,"c1",2,"RECEIPT_AND_BLOCK_MATCH","0x"+"b"*64)
    assert done["state"]=="COMPLETED"
    assert read_campaign(db,"c1")["state"]=="COMPLETED"
    with pytest.raises(ValueError,match="not active"):
        record_receipt_status(db,"c1",2,"SUBMITTED","0x"+"b"*64)

def test_halt_preserves_journal_and_blocks_updates(tmp_path):
    db,steps=setup(tmp_path)
    create_campaign(db,"c1",steps)
    halt_campaign(db,"c1")
    assert read_campaign(db,"c1")["state"]=="HALTED"
    with pytest.raises(ValueError):
        record_receipt_status(db,"c1",1,"SUBMITTED","0x"+"a"*64)
    assert create_campaign(db,"c2",steps)["state"]=="ACTIVE"

def test_bad_status_and_hash_rejected(tmp_path):
    db,steps=setup(tmp_path)
    create_campaign(db,"c1",steps)
    with pytest.raises(ValueError):
        record_receipt_status(db,"c1",1,"BOGUS","0x"+"a"*64)
    with pytest.raises(ValueError):
        record_receipt_status(db,"c1",1,"SUBMITTED","not-a-hash")
