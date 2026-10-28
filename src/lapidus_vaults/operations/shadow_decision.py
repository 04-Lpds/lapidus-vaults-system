"""Read-only allocation decision controller; NEVER signs, builds campaigns or broadcasts.

Economic model is explicitly assumption-driven, NOT a vault yield forecast.
Even an attractive shadow decision remains REVIEW_ONLY until separately authorized.
"""
from __future__ import annotations
import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from ..allocator import address_ok
from ..client import MorphoClient
from ..rpc_verify import ReadOnlyRPC
from ..monitor_once import observation_report, drift_summary, exclusive_monitor_lock

WEI=Decimal(10)**18
USDC=Decimal(10)**6
GAS_LIMIT_PER_ACTION=600_000
L1_CONTINGENCY_PER_ACTION_WEI=50_000_000_000_000

def evaluate(report:dict,*,gas_price_wei:int,eth_usd:Decimal,
             incremental_apy:Decimal, horizon_days:int=30,
             threshold_bps:int=500)->dict:
    if (type(gas_price_wei) is not int or gas_price_wei<=0 or
        type(horizon_days) is not int or not 1<=horizon_days<=365 or
        not eth_usd.is_finite() or not Decimal(100)<=eth_usd<=Decimal(100000) or
        not incremental_apy.is_finite() or not Decimal(0)<=incremental_apy<=Decimal(1)):
        raise ValueError("Invalid bounded shadow economics assumptions")
    drift=drift_summary(report,threshold_bps=threshold_bps)
    actions=report["plan"]["actions"]
    amount=sum(a["amount_units"] for a in actions)
    count=sum(1+(a["kind"]=="deposit_intent") for a in actions)
    # Assume every deposit may require an approval; intentionally pessimistic.
    fee_wei=count*(GAS_LIMIT_PER_ACTION*gas_price_wei*2+L1_CONTINGENCY_PER_ACTION_WEI)
    fee_usd=(Decimal(fee_wei)/WEI)*eth_usd
    displaced=Decimal(amount)/USDC
    # Deliberately generous upper bound: all gross moved capital is assumed to earn
    # the full *incremental* APY for the whole horizon.
    projected_incremental_yield_usd=displaced*incremental_apy*Decimal(horizon_days)/Decimal(365)
    if drift["status"]!="REBALANCE_REVIEW":
        decision="HOLD_DRIFT_BELOW_THRESHOLD"
    elif projected_incremental_yield_usd<=fee_usd:
        decision="HOLD_ESTIMATED_FEES_EXCEED_BENEFIT"
    else:
        decision="REBALANCE_REVIEW_ONLY"
    return {"decision":decision,"submitted":False,"campaign_created":False,
        "drift":drift,"assumptions":{"eth_usd":str(eth_usd),
            "incremental_apy":str(incremental_apy),"horizon_days":horizon_days,
            "per_step_gas_units":GAS_LIMIT_PER_ACTION,
            "l1_fee_contingency_wei":L1_CONTINGENCY_PER_ACTION_WEI,
            "fee_multiplier":2,"approval_assumed_for_each_deposit":True},
        "candidate_steps_upper_bound":count,"estimated_fee_wei":fee_wei,
        "estimated_fee_usd":str(fee_usd),
        "optimistic_incremental_yield_usd":str(projected_incremental_yield_usd),
        "limitations":"No live APY forecast, L1 fee quote, slippage, liquidity or exact transaction gas; REBALANCE_REVIEW_ONLY never authorizes execution."}

def record(db_path:str, wallet:str, report:dict, result:dict)->int:
    with sqlite3.connect(db_path,timeout=10) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS shadow_decisions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            observed_at TEXT NOT NULL, wallet TEXT NOT NULL,
            rpc_block INTEGER NOT NULL, decision TEXT NOT NULL,
            details_json TEXT NOT NULL)""")
        cur=db.execute("""INSERT INTO shadow_decisions
            (observed_at,wallet,rpc_block,decision,details_json)
            VALUES (?,?,?,?,?)""",(
            datetime.now(timezone.utc).isoformat(),wallet.lower(),
            report["plan"]["observed_block"],result["decision"],json.dumps(result,sort_keys=True)))
        return int(cur.lastrowid)

def main(argv=None):
    p=argparse.ArgumentParser(description="One-shot live shadow decision; never signs or creates campaigns")
    p.add_argument("--wallet",required=True)
    p.add_argument("--allowlist",required=True)
    p.add_argument("--db",required=True)
    p.add_argument("--live-economics",action="store_true",help="Use realized per-vault Morpho APYs and best-effort RPC gas estimates")
    p.add_argument("--eth-usd",required=True,help="Explicit assumed ETH/USD for cost comparison, not a live quote")
    p.add_argument("--incremental-apy",default="0.10",help="Assumed annual incremental yield, decimal (0.10=10%%)")
    p.add_argument("--horizon-days",type=int,default=30)
    p.add_argument("--drift-bps",type=int,default=500)
    args=p.parse_args(argv)
    try:
        if not address_ok(args.wallet) or not os.path.isfile(args.db):
            raise ValueError("Valid wallet and existing DB required")
        if not os.environ.get("BASE_RPC_URL","").startswith("https://"):
            raise ValueError("HTTPS BASE_RPC_URL required")
        price=Decimal(args.eth_usd)
        incremental=Decimal(args.incremental_apy)
        with exclusive_monitor_lock(args.db):
            with ReadOnlyRPC(os.environ["BASE_RPC_URL"]) as rpc, MorphoClient() as morpho:
                report=observation_report(args.wallet,args.allowlist,rpc,morpho)
                if args.live_economics:
                    from .live_shadow_economics import evaluate_live
                    result=evaluate_live(report,rpc=rpc,morpho=morpho,eth_usd=price,
                        horizon_days=args.horizon_days,threshold_bps=args.drift_bps)
                else:
                    gas_price=rpc.gas_price_wei()
                    result=evaluate(report,gas_price_wei=gas_price,eth_usd=price,
                        incremental_apy=incremental,horizon_days=args.horizon_days,
                        threshold_bps=args.drift_bps)
            result["shadow_decision_id"]=record(args.db,args.wallet,report,result)
            result["rpc_block"]=report["plan"]["observed_block"]
            print(json.dumps(result,indent=2))
        return 0
    except (ValueError,InvalidOperation,OSError,sqlite3.Error,RuntimeError) as exc:
        print("SHADOW DECISION BLOCKED: "+type(exc).__name__+": "+str(exc),file=sys.stderr)
        return 1

if __name__=="__main__":
    raise SystemExit(main())
