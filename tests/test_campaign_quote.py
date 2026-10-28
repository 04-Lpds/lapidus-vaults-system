"""Offline tests for campaign step inspection and quote matching."""
import sqlite3
from unittest.mock import Mock
import pytest

from lapidus_vaults.allocator import BASE_USDC
from lapidus_vaults.operations.campaigns import create_campaign
from lapidus_vaults.operations.next_action import next_campaign_action
from lapidus_vaults.operations.campaign_quote import quote_next_campaign_action
from lapidus_vaults.unsigned_candidates import UnsignedCandidate
from lapidus_vaults.fork_roundtrip import ACTOR, VAULTS

WALLET=ACTOR.lower()
VAULT=VAULTS[0].lower()

def candidate(stage="withdraw"):
    if stage=="approve":
        from lapidus_vaults.rpc_verify import _address_word, _word
        from lapidus_vaults.unsigned_candidates import APPROVE
        to=BASE_USDC
        data=APPROVE+_address_word(VAULT)+_word(1_000_000)
    else:
        from lapidus_vaults.rpc_verify import _address_word, _word
        from lapidus_vaults.unsigned_candidates import WITHDRAW, DEPOSIT
        to=VAULT
        data=(WITHDRAW+_word(1_000_000)+_address_word(WALLET)+_address_word(WALLET)
              if stage=="withdraw" else DEPOSIT+_word(1_000_000)+_address_word(WALLET))
    return UnsignedCandidate(1,(),stage+"_intent",8453,WALLET,to,0,data,1_000_000)

def campaign_db(tmp_path,stage="withdraw"):
    path=str(tmp_path/"campaign.sqlite")
    create_campaign(path,"c1",(candidate(stage),))
    return path

def test_next_action_uses_saved_vault_and_amount(tmp_path):
    db=campaign_db(tmp_path)
    result=next_campaign_action(db,wallet=WALLET,approved_vaults=(VAULT,))
    assert result["method"]=="withdraw_intent"
    assert result["vault"]==VAULT
    assert result["amount_units"]==1_000_000
    assert result["execution_enabled"] is False

def test_campaign_approval_vault_comes_from_calldata(tmp_path):
    db=campaign_db(tmp_path,"approve")
    action=next_campaign_action(db,wallet=WALLET,approved_vaults=(VAULT,))
    assert action["vault"]==VAULT
    assert action["method"]=="approve_intent"

def test_quote_refuses_stage_or_calldata_mismatch(tmp_path):
    db=campaign_db(tmp_path)
    rpc=Mock()
    withdraw=Mock()
    deposits=Mock()
    intended=candidate()
    q={"quote_id":"q","stage":"withdraw","wallet":WALLET,"to":VAULT,
       "data":intended.data,"amount_units":1_000_000,"chain_id":8453}
    withdraw.quote.return_value=q.copy()
    result=quote_next_campaign_action(db,wallet=WALLET,approved_vaults=(VAULT,),
                                     rpc=rpc,deposits=deposits,withdrawals=withdraw)
    assert result["campaign_id"]=="c1"
    assert result["manual_authorization_required"] is True
    assert withdraw.quote.call_args.kwargs["vault"]==VAULT
    withdraw.quote.return_value={**q,"data":"0xdead"}
    with pytest.raises(ValueError,match="differs from campaign"):
        quote_next_campaign_action(db,wallet=WALLET,approved_vaults=(VAULT,),
                                   rpc=rpc,deposits=deposits,withdrawals=withdraw)

def test_bound_step_cannot_be_quoted_again(tmp_path):
    db=campaign_db(tmp_path)
    with sqlite3.connect(db) as sql:
        sql.execute("""CREATE TABLE rebalance_campaign_quote_bindings
          (quote_id TEXT PRIMARY KEY,campaign_id TEXT,sequence INTEGER)""")
        sql.execute("INSERT INTO rebalance_campaign_quote_bindings VALUES ('q','c1',1)")
    action=next_campaign_action(db,wallet=WALLET,approved_vaults=(VAULT,))
    assert action["status"]=="BOUND_AWAITING_RECEIPT"
    with pytest.raises(ValueError,match="no action eligible"):
        quote_next_campaign_action(db,wallet=WALLET,approved_vaults=(VAULT,),
                                   rpc=Mock(),deposits=Mock(),withdrawals=Mock())

def test_campaign_next_refuses_wrong_wallet(tmp_path):
    db=campaign_db(tmp_path)
    with pytest.raises(ValueError,match="different wallet"):
        next_campaign_action(db,wallet="0x"+"3"*40,approved_vaults=(VAULT,))
