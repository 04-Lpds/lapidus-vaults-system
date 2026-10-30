"""Read-only data-backed economics for prospective rebalance.

Historical realized Morpho APY is an observation, not a forecast. Actual
estimateGas on a hypothetical next step may fail because its prerequisite
withdraw/approval hasn't happened; fall back to bounded policy assumptions.
Never constructs a campaign, signs, or broadcasts.
"""
from __future__ import annotations
from decimal import Decimal
from ..allocator import BASE_USDC
from ..rpc_verify import _address_word,_word
from ..unsigned_candidates import APPROVE, DEPOSIT,WITHDRAW
from .shadow_decision import WEI, USDC, GAS_LIMIT_PER_ACTION, L1_CONTINGENCY_PER_ACTION_WEI
from ..live_readonly_plan import ALLOWANCE

def parse_apy(response:dict, vault:str)->Decimal:
    if not isinstance(response,dict) or str(response.get("vault_address","")).lower()!=vault.lower():
        raise ValueError("Morpho APY vault identity mismatch")
    if response.get("lookback")!="seven_days":
        raise ValueError("Unexpected Morpho APY lookback")
    if response.get("apy") is None or isinstance(response["apy"],bool):
        raise ValueError("Missing historical APY; economic decision unavailable")
    apy=Decimal(str(response["apy"]))
    if not apy.is_finite() or not Decimal("-1")<=apy<=Decimal("5"):
        raise ValueError("APY outside bounded analysis range")
    return apy

def evaluate_live(report:dict,*,rpc,morpho,eth_usd:Decimal,
                  horizon_days:int=30,threshold_bps:int=500)->dict:
    from ..monitor_once import drift_summary
    if not eth_usd.is_finite() or not Decimal(100)<=eth_usd<=Decimal(100000):
        raise ValueError("Invalid ETH USD price assumption")
    if type(horizon_days) is not int or not 1<=horizon_days<=365:
        raise ValueError("Invalid evaluation horizon")
    plan=report["plan"]
    if plan["chain_id"]!=8453:
        raise ValueError("Wrong chain")
    vaults=[c["vault_address"] for c in plan["changes"]]
    if len(set(vaults))!=len(vaults):
        raise ValueError("Duplicate vault")
    wallet=plan.get("wallet")
    if not wallet:
        raise ValueError("Snapshot wallet identity missing")
    apys={v:parse_apy(morpho.v2_apy(8453,v,lookback="seven_days"),v) for v in vaults}
    gas_price=rpc.gas_price_wei()
    if type(gas_price) is not int or not 0<gas_price<=100_000_000_000:
        raise ValueError("Unsafe gas quote")
    actions=[]
    block=plan["observed_block"]
    for c in plan["changes"]:
        delta=c["delta_units"]
        if not delta:
            continue
        v=c["vault_address"]
        a=abs(delta)
        if delta>0:
            allowance=rpc.call_uint(BASE_USDC,ALLOWANCE,block,_address_word(wallet),_address_word(v))
            if allowance<a:
                if allowance!=0:
                    return {"decision":"BLOCKED_NONZERO_INSUFFICIENT_ALLOWANCE","submitted":False,
                            "campaign_created":False,"reason":"Approval state requires separate review"}
                actions.append(("approve",BASE_USDC,APPROVE+_address_word(v)+_word(a)))
            actions.append(("deposit",v,DEPOSIT+_word(a)+_address_word(wallet)))
        else:
            actions.append(("withdraw",v,WITHDRAW+_word(a)+_address_word(wallet)+_address_word(wallet)))
    step_costs=[]
    for method,to,data in actions:
        # A dependent deposit may fail estimateGas until its preceding
        # approval is on-chain. Do not treat this as executable validation.
        try:
            estimated=rpc.estimate_gas(wallet,to,data)
            if not 21_000<=estimated<=700_000:
                raise ValueError("Gas out of supported range")
            gas=min(700_000,estimated*13//10+10_000)
            source="rpc_estimate"
        except Exception:
            gas=GAS_LIMIT_PER_ACTION
            source="policy_fallback_unexecutable"
        step_costs.append({"method":method,"gas_units":gas,"source":source})
    total_fee_wei=sum(s["gas_units"]*gas_price*2+L1_CONTINGENCY_PER_ACTION_WEI for s in step_costs)
    fee_usd=Decimal(total_fee_wei)/WEI*eth_usd
    # Difference of target and current holdings. Withdrawn capital loses its
    # source APY, while deposited capital gains the destination APY; idle=0.
    yield_delta=sum((Decimal(c["delta_units"])/USDC)*apys[c["vault_address"]]
                    for c in plan["changes"])
    benefit=yield_delta*Decimal(horizon_days)/Decimal(365)
    drift=drift_summary(report,threshold_bps=threshold_bps)
    if drift["status"]!="REBALANCE_REVIEW":
        decision="HOLD_DRIFT_BELOW_THRESHOLD"
    elif benefit<=0:
        decision="HOLD_NO_POSITIVE_HISTORICAL_YIELD_DELTA"
    elif benefit<=fee_usd:
        decision="HOLD_ESTIMATED_FEES_EXCEED_BENEFIT"
    else:
        decision="REBALANCE_REVIEW_ONLY"
    return {"decision":decision,"submitted":False,"campaign_created":False,
        "capital_units":plan["capital_units"],"drift":drift,
        "historical_apy_7d":{v:str(a) for v,a in apys.items()},
        "estimated_annual_yield_delta_usd":str(yield_delta),
        "horizon_days":horizon_days,"projected_historical_rate_yield_delta_usd":str(benefit),
        "estimated_fee_usd":str(fee_usd),"estimated_fee_wei":total_fee_wei,
        "gas_price_wei":gas_price,"gas_steps":step_costs,
        "eth_usd_assumption":str(eth_usd),
        "limitations":"APY is backward-looking and excludes rewards; ETH/USD input is an assumption. Gas simulations can fall back to conservative policy ceilings; fixed L1 contingency is NOT a live L1 fee quote. No slippage, vault gating, liquidity, yield projection certainty or execution authorization."}
