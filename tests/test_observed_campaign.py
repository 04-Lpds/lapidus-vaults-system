from dataclasses import asdict
import pytest
from lapidus_vaults.operations.observed_campaign import propose_candidates
from lapidus_vaults.operations.campaign_admission import assess_admission
W="0x"+"1"*40
V1="0x"+"2"*40
V2="0x"+"3"*40

def report(d1=-500_000,d2=500_000, cash=0):
    return {"plan":{"chain_id":8453,"wallet":W,"observed_block":123,
        "wallet_usdc_units":cash,"capital_units":5_000_000,
        "changes":[{"vault_address":V1,"delta_units":d1},
                   {"vault_address":V2,"delta_units":d2}]}}

def test_observation_proposal_orders_withdraw_before_approval_deposit():
    out=propose_candidates(report(),wallet=W,approved_vaults=(V1,V2))
    assert [x.method for x in out]==["withdraw_intent","approve_intent","deposit_intent"]
    assert [x.depends_on for x in out]==[(),(1,),(2,)]
    e={"decision":"REBALANCE_REVIEW_ONLY","submitted":False,"capital_units":5_000_000,
       "estimated_fee_usd":"0.001","projected_historical_rate_yield_delta_usd":"0.10",
       "eth_usd_assumption":"3000"}
    assert assess_admission(economics=e,candidates=[asdict(c) for c in out],
                           wallet=W,approved_vaults=(V1,V2))["status"]=="ADMISSION_REVIEW_ONLY"

def test_observation_rejects_out_of_cap_and_bad_wallet():
    with pytest.raises(ValueError):
        propose_candidates(report(d1=0,d2=2_000_000,cash=2_000_000),
                            wallet=W,approved_vaults=(V1,V2))
    with pytest.raises(ValueError):
        propose_candidates(report(),wallet="0x"+"4"*40,approved_vaults=(V1,V2))

def test_observation_cash_dependency_and_coverage():
    with pytest.raises(ValueError):
        propose_candidates(report(d1=0,d2=500_000,cash=0),
                           wallet=W,approved_vaults=(V1,V2))
    with pytest.raises(ValueError):
        propose_candidates(report(),wallet=W,approved_vaults=(V1,))
