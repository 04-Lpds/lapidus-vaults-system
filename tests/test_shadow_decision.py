"""Offline tests: shadow evaluation has no signer, campaign writer or broadcaster."""
import json
import sqlite3
from decimal import Decimal
import pytest
from lapidus_vaults.operations.shadow_decision import evaluate,record

W="0x"+"1"*40
def report(amount=2_000_000):
    return {"plan":{"observed_block":123,"capital_units":5_000_000,
        "actions":[{"kind":"deposit_intent","amount_units":amount,"vault":"0x"+"2"*40}]}}

def test_high_fee_small_portfolio_holds():
    r=evaluate(report(),gas_price_wei=6_000_000,
               eth_usd=Decimal("4000"),incremental_apy=Decimal("0.10"))
    assert r["decision"]=="HOLD_ESTIMATED_FEES_EXCEED_BENEFIT"
    assert r["candidate_steps_upper_bound"]==2
    assert r["submitted"] is False and r["campaign_created"] is False

def test_below_drift_holds_even_if_low_fee():
    r=evaluate(report(5_000),gas_price_wei=6_000_000,
               eth_usd=Decimal("100"),incremental_apy=Decimal("1"))
    assert r["decision"]=="HOLD_DRIFT_BELOW_THRESHOLD"

def test_rebalance_is_review_only():
    r=evaluate(report(),gas_price_wei=1,
               eth_usd=Decimal("100"),incremental_apy=Decimal("1"))
    assert r["decision"]=="REBALANCE_REVIEW_ONLY"
    assert not r["submitted"] and not r["campaign_created"]

def test_invalid_parameters_fail_closed():
    with pytest.raises(ValueError):
        evaluate(report(),gas_price_wei=0,eth_usd=Decimal("2000"),
                 incremental_apy=Decimal("0.1"))
    with pytest.raises(ValueError):
        evaluate(report(),gas_price_wei=6_000_000,eth_usd=Decimal("NaN"),
                 incremental_apy=Decimal("0.1"))

def test_decisions_append_to_same_db(tmp_path):
    path=str(tmp_path/"executor.sqlite3")
    r=evaluate(report(),gas_price_wei=6_000_000,
               eth_usd=Decimal("3000"),incremental_apy=Decimal("0.10"))
    assert record(path,W,report(),r)==1
    assert record(path,W,report(),r)==2
    with sqlite3.connect(path) as db:
        row=db.execute("SELECT decision,details_json FROM shadow_decisions WHERE id=2").fetchone()
    assert row[0]==r["decision"]
    assert json.loads(row[1])["submitted"] is False
