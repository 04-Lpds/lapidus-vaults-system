"""Offline lifecycle regression: real SQLite campaign and receipt bridge.

Run the production auto_once state machine five times with simulated RPC,
quote engine and transaction transport. No keys are broadcast, and an unresolved
receipt must never trigger another submission.
"""
import json
from types import SimpleNamespace
from eth_account import Account
import pytest

from lapidus_vaults.operations import auto_once
from lapidus_vaults.operations.auto_journal import reserve, mark_submitted, current
from lapidus_vaults.operations.campaigns import create_campaign, read_campaign
from lapidus_vaults.unsigned_candidates import UnsignedCandidate, APPROVE, DEPOSIT
from lapidus_vaults.rpc_verify import _address_word, _word
from lapidus_vaults.allocator import BASE_USDC

VAULT = "0x" + "2" * 40
PROTECTED = "0x" + "3" * 40
AMOUNT = 800000

class FakeRPC:
    def __init__(self, *args): pass
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def chain_id(self): return 8453
    def gas_price_wei(self): return 6000000

class FakeClient:
    def __enter__(self): return self
    def __exit__(self, *args): pass

class FakeQuotes:
    quote_seq = 0
    receipt_ready = False
    def __init__(self):
        self.pending={}
        self.consumed={}
    def quote(self, rpc, *, wallet, vault, approved_vaults, amount_units):
        from lapidus_vaults.operations.next_action import next_campaign_action
        stage = ["approve", "deposit"][FakeQuotes.quote_seq]
        FakeQuotes.quote_seq += 1
        to = BASE_USDC if stage=="approve" else vault
        data = (APPROVE+_address_word(vault)+_word(amount_units)
                if stage=="approve" else
                DEPOSIT+_word(amount_units)+_address_word(wallet))
        quote = dict(quote_id="test-quote-"+str(FakeQuotes.quote_seq),stage=stage,
                     wallet=wallet,vault=vault,to=to,data=data,
                     amount_units=amount_units,chain_id=8453,value="0x0",
                     gas="0x30d40")
        self.pending[quote["quote_id"]]=quote
        return quote
    def consume(self, rpc, quote_id):
        quote=self.pending.pop(quote_id)
        self.consumed[quote_id]=quote
        return quote
    def check_receipt(self, rpc, quote_id, hash):
        if not FakeQuotes.receipt_ready:
            return dict(status="PENDING_NOT_FOUND", tx_hash=hash, matched=False)
        return dict(status="RECEIPT_AND_BLOCK_MATCH",tx_hash=hash,
                    matched=True,balance_match=True,
                    transaction_event_reconciliation=dict(matched=True))

def test_auto_runner_full_restart_and_unknown_receipt(tmp_path, monkeypatch):
    account=Account.create()
    key="0x"+bytes(account.key).hex()
    wallet=account.address.lower()
    db=str(tmp_path/"auto-test.sqlite")
    data1=APPROVE+_address_word(VAULT)+_word(AMOUNT)
    data2=DEPOSIT+_word(AMOUNT)+_address_word(wallet)
    steps=(
        UnsignedCandidate(1,(),"approve_intent",8453,wallet,BASE_USDC,0,data1,AMOUNT),
        UnsignedCandidate(2,(1,),"deposit_intent",8453,wallet,VAULT,0,data2,AMOUNT),
    )
    create_campaign(db,"full-auto-test",steps)

    FakeQuotes.quote_seq=0
    FakeQuotes.receipt_ready=False
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(auto_once,"ReadOnlyRPC",FakeRPC)
    monkeypatch.setattr(auto_once,"MorphoClient",FakeClient)
    monkeypatch.setattr(auto_once,"DepositQuotes",FakeQuotes)
    monkeypatch.setattr(auto_once,"WithdrawalQuotes",FakeQuotes)
    monkeypatch.setattr(auto_once,"build_live_readonly_plan",
        lambda *args:{"plan":{"capital_units":2_000_000}})
    monkeypatch.setattr(auto_once,"read_nonce",lambda *args:0)

    sent=[]
    def fake_send(**kwargs):
        seq=kwargs["sequence"]
        tx_hash="0x"+format(seq,"064x")
        sent.append(tx_hash)
        reserve(db,campaign_id=kwargs["campaign_id"],sequence=seq,
                quote_id=kwargs["quote_id"],wallet=wallet,
                to_address=kwargs["consumed"]["to"],
                calldata=kwargs["consumed"]["data"],amount_units=AMOUNT)
        mark_submitted(db,campaign_id=kwargs["campaign_id"],sequence=seq,
                       quote_id=kwargs["quote_id"],tx_hash=tx_hash)
        return {"state":"SUBMITTED","tx_hash":tx_hash}
    monkeypatch.setattr(auto_once,"reserve_and_broadcast",fake_send)

    def run():
        return auto_once.execute_once(
            db_path=db,wallet=wallet,protected_wallet=PROTECTED,
            approved_vaults=(VAULT,),allowlist_path="fake.json",
            rpc_url="https://example.invalid",private_key=key,
            max_capital_units=10_000_000)

    assert run()["state"]=="SUBMITTED"
    assert len(sent)==1
    assert run()["status"]=="PENDING_NOT_FOUND"
    assert len(sent)==1  # restart never rebroadcasts pending hash
    FakeQuotes.receipt_ready=True
    assert run()["status"]=="RECEIPT_AND_BLOCK_MATCH"
    assert len(sent)==1
    assert run()["state"]=="SUBMITTED"
    assert len(sent)==2
    assert run()["status"]=="RECEIPT_AND_BLOCK_MATCH"
    assert read_campaign(db,"full-auto-test")["state"]=="COMPLETED"
    assert [x["state"] for x in current(db,"full-auto-test")] == ["CONFIRMED","CONFIRMED"]
    assert run()["status"]=="NO_ACTIVE_CAMPAIGN"
    assert len(sent)==2
