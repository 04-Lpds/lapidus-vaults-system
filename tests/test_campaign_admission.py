"""Admission is review-only and rejects unsafe economics or transaction bytes."""
from copy import deepcopy
from lapidus_vaults.operations.campaign_admission import assess_admission
from lapidus_vaults.allocator import BASE_USDC
from lapidus_vaults.rpc_verify import _address_word,_word
from lapidus_vaults.unsigned_candidates import APPROVE,DEPOSIT
W="0x"+"1"*40
V="0x"+"2"*40

def sample():
    economics={"decision":"REBALANCE_REVIEW_ONLY","submitted":False,"capital_units":5_000_000,
               "estimated_fee_usd":"0.001","projected_historical_rate_yield_delta_usd":"0.05",
               "eth_usd_assumption":"3000"}
    candidates=[
       {"sequence":1,"method":"approve_intent","chain_id":8453,"from_address":W,
        "to_address":BASE_USDC,"data":APPROVE+_address_word(V)+_word(800000),
        "requested_assets_units":800000,"value_wei":0,"depends_on":[]},
       {"sequence":2,"method":"deposit_intent","chain_id":8453,"from_address":W,
        "to_address":V,"data":DEPOSIT+_word(800000)+_address_word(W),
        "requested_assets_units":800000,"value_wei":0,"depends_on":[1]}
    ]
    return economics,candidates

def test_review_only_even_for_economically_positive_case():
    e,c=sample()
    r=assess_admission(economics=e,candidates=c,wallet=W,approved_vaults=(V,))
    assert r["status"]=="ADMISSION_REVIEW_ONLY"
    assert r["execution_enabled"] is False and r["campaign_created"] is False

def test_reject_hold_and_small_portfolio_fee_burden():
    e,c=sample()
    e["decision"]="HOLD_ESTIMATED_FEES_EXCEED_BENEFIT"
    assert assess_admission(economics=e,candidates=c,wallet=W,approved_vaults=(V,))["status"]=="ADMISSION_BLOCKED"
    e["decision"]="REBALANCE_REVIEW_ONLY"
    e["estimated_fee_usd"]="0.10"
    assert assess_admission(economics=e,candidates=c,wallet=W,approved_vaults=(V,))["status"]=="ADMISSION_BLOCKED"

def test_reject_tampered_approval_and_oversized_action():
    e,c=sample()
    c[0]["data"]=APPROVE+_address_word(V)+_word(900000)
    assert assess_admission(economics=e,candidates=c,wallet=W,approved_vaults=(V,))["status"]=="ADMISSION_BLOCKED"
    e,c=sample()
    c[1]["requested_assets_units"]=1_000_001
    assert assess_admission(economics=e,candidates=c,wallet=W,approved_vaults=(V,))["status"]=="ADMISSION_BLOCKED"

def test_reject_nan_apy_and_bad_dependencies():
    e,c=sample()
    e["estimated_fee_usd"]="NaN"
    assert assess_admission(economics=e,candidates=c,wallet=W,approved_vaults=(V,))["status"]=="ADMISSION_BLOCKED"
    e,c=sample()
    c[1]["depends_on"]=[2]
    assert assess_admission(economics=e,candidates=c,wallet=W,approved_vaults=(V,))["status"]=="ADMISSION_BLOCKED"
