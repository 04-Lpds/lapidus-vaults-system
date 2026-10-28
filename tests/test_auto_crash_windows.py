"""Crash-window guards for autonomous campaign authorizations."""
import sqlite3
import pytest
from lapidus_vaults.operations.auto_once import assert_no_orphaned_authorizations
from lapidus_vaults.operations.auto_journal import reserve, mark_submitted, mark_verified
from lapidus_vaults.operations.campaigns import create_campaign, record_receipt_status
from lapidus_vaults.operations.receipt_bridge import bind_campaign_quote
from lapidus_vaults.unsigned_candidates import UnsignedCandidate, APPROVE
from lapidus_vaults.rpc_verify import _word, _address_word
from lapidus_vaults.allocator import BASE_USDC

WALLET="0x"+"1"*40
VAULT="0x"+"2"*40
AMOUNT=800000
DATA=APPROVE+_address_word(VAULT)+_word(AMOUNT)
HASH="0x"+"a"*64

def setup_bound(tmp_path):
    db=str(tmp_path/"crash.sqlite3")
    create_campaign(db,"crash-1",(UnsignedCandidate(
        1,(),"approve_intent",8453,WALLET,BASE_USDC,0,DATA,AMOUNT),))
    with sqlite3.connect(db) as conn:
        conn.execute("""CREATE TABLE live_deposit_authorizations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prepared_at TEXT NOT NULL, quote_id TEXT UNIQUE NOT NULL,
            wallet TEXT NOT NULL, vault TEXT NOT NULL, stage TEXT NOT NULL,
            amount_units INTEGER NOT NULL, transaction_json TEXT NOT NULL)""")
        import json
        consumed=dict(wallet=WALLET,vault=VAULT,stage="approve",
                      amount_units=AMOUNT,to=BASE_USDC,data=DATA,chain_id=8453)
        conn.execute("""INSERT INTO live_deposit_authorizations
            (prepared_at,quote_id,wallet,vault,stage,amount_units,transaction_json)
            VALUES (?,?,?,?,?,?,?)""",
            ("now","quote-1",WALLET,VAULT,"approve",AMOUNT,json.dumps(consumed)))
    bind_campaign_quote(db,"crash-1",1,"quote-1")
    return db

def test_crash_after_binding_before_reservation_is_blocked(tmp_path):
    db=setup_bound(tmp_path)
    with pytest.raises(RuntimeError, match="no complete recovery journal"):
        assert_no_orphaned_authorizations(db)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM autonomous_submissions").fetchone()[0] == 0 if (
            conn.execute("SELECT name FROM sqlite_master WHERE name='autonomous_submissions'").fetchone()
        ) else True

def test_crash_after_confirmed_journal_before_campaign_bridge_is_blocked(tmp_path):
    db=setup_bound(tmp_path)
    reserve(db,campaign_id="crash-1",sequence=1,quote_id="quote-1",
            wallet=WALLET,to_address=BASE_USDC,calldata=DATA,amount_units=AMOUNT)
    mark_submitted(db,campaign_id="crash-1",sequence=1,quote_id="quote-1",tx_hash=HASH)
    mark_verified(db,campaign_id="crash-1",sequence=1,tx_hash=HASH,verified={
        "status":"RECEIPT_AND_BLOCK_MATCH","matched":True,
        "balance_match":True,"tx_hash":HASH,
        "transaction_event_reconciliation":{"matched":True}})
    with pytest.raises(RuntimeError, match="bridge incomplete"):
        assert_no_orphaned_authorizations(db)
    record_receipt_status(db,"crash-1",1,"RECEIPT_AND_BLOCK_MATCH",HASH)
    assert_no_orphaned_authorizations(db)

def test_matching_submitted_journal_is_not_orphaned(tmp_path):
    db=setup_bound(tmp_path)
    reserve(db,campaign_id="crash-1",sequence=1,quote_id="quote-1",
            wallet=WALLET,to_address=BASE_USDC,calldata=DATA,amount_units=AMOUNT)
    mark_submitted(db,campaign_id="crash-1",sequence=1,quote_id="quote-1",tx_hash=HASH)
    assert_no_orphaned_authorizations(db)
