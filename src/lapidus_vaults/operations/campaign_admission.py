"""Fail-closed admission gate for a future automatic campaign controller.

No network, signing, or database writes. This deliberately does not grant
execution authority: separately tested transaction orchestration is required.
"""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

ALLOWED={"approve_intent","deposit_intent","withdraw_intent"}

@dataclass(frozen=True)
class AdmissionPolicy:
    max_capital_units:int=5_000_000
    max_action_units:int=1_000_000
    max_steps:int=6
    max_fee_fraction:Decimal=Decimal("0.01")
    minimum_net_benefit_usd:Decimal=Decimal("0.01")

def assess_admission(*,economics:dict, candidates:list[dict],
                     wallet:str, approved_vaults:tuple[str,...],
                     policy:AdmissionPolicy=AdmissionPolicy())->dict:
    def reject(reason):
        return {"status":"ADMISSION_BLOCKED","reason":reason,
                "execution_enabled":False,"campaign_created":False}
    if economics.get("decision")!="REBALANCE_REVIEW_ONLY" or economics.get("submitted") is not False:
        return reject("Economic decision is not eligible for campaign review")
    if not candidates or len(candidates)>policy.max_steps:
        return reject("Invalid action count")
    capital=economics.get("capital_units")
    if type(capital) is not int or not 0<capital<=policy.max_capital_units:
        return reject("Capital outside dedicated policy")
    try:
        fee=Decimal(str(economics["estimated_fee_usd"]))
        benefit=Decimal(str(economics["projected_historical_rate_yield_delta_usd"]))
        eth_usd=Decimal(str(economics["eth_usd_assumption"]))
        max_fee=Decimal(capital)/Decimal(1_000_000)*policy.max_fee_fraction
    except (KeyError,ValueError,TypeError,InvalidOperation):
        return reject("Missing or invalid economics")
    if not all(x.is_finite() for x in (fee,benefit,eth_usd)):
        return reject("Nonfinite economics")
    if fee<0 or fee>max_fee or benefit-fee<policy.minimum_net_benefit_usd:
        return reject("Fee percentage or minimum net benefit policy failed")
    permitted=set(approved_vaults)
    if not permitted or len(permitted)!=len(approved_vaults):
        return reject("Invalid allowlist")
    from ..allocator import BASE_USDC,address_ok
    from ..rpc_verify import _address_word,_word
    from ..unsigned_candidates import APPROVE,DEPOSIT,WITHDRAW
    if not address_ok(wallet):
        return reject("Invalid wallet")
    for index,c in enumerate(candidates,1):
        method=c.get("method")
        amount=c.get("requested_assets_units")
        to=c.get("to_address")
        deps=c.get("depends_on")
        if (c.get("sequence")!=index or
            c.get("chain_id")!=8453 or
            c.get("from_address")!=wallet.lower() or
            type(amount) is not int or not 0<amount<=policy.max_action_units or
            method not in ALLOWED or c.get("value_wei")!=0 or
            not isinstance(deps,(list,tuple)) or
            any(type(d) is not int or not 1<=d<index for d in deps)):
            return reject("Candidate policy or dependency mismatch")
        data=c.get("data")
        if not isinstance(data,str):
            return reject("Missing candidate calldata")
        if method=="approve_intent":
            if to!=BASE_USDC or not data.startswith(APPROVE) or len(data)!=138:
                return reject("Unsupported approval")
            vault="0x"+data[34:74]
            expected=APPROVE+_address_word(vault)+_word(amount)
        else:
            vault=to
            if vault not in permitted:
                return reject("Unapproved vault")
            expected=(DEPOSIT+_word(amount)+_address_word(wallet) if method=="deposit_intent"
                      else WITHDRAW+_word(amount)+_address_word(wallet)+_address_word(wallet))
        if vault not in permitted or data.lower()!=expected.lower():
            return reject("Unexpected calldata or vault")
    return {"status":"ADMISSION_REVIEW_ONLY","reason":"All gates passed; separate execution authorization still required",
            "execution_enabled":False,"campaign_created":False,
            "candidate_count":len(candidates),"total_fee_usd":str(fee),
            "net_historical_rate_benefit_usd":str(benefit-fee)}
