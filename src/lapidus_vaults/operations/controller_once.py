"""Keyless portfolio observation -> economics -> admission -> durable campaign.

No synthetic inputs allowed in the CLI. This command NEVER signs, broadcasts,
or invokes the autonomous live executor. New campaigns are review-only.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict
from decimal import Decimal
import json
import os
from pathlib import Path
import sys

from ..allocator import address_ok
from ..client import MorphoClient
from ..monitor_once import observation_report, exclusive_monitor_lock
from ..rpc_verify import ReadOnlyRPC
from ..wallet_plan import read_policy
from .live_shadow_economics import evaluate_live
from .observed_campaign import propose_candidates
from .campaign_admission import assess_admission
from .campaigns import create_campaign
from .shadow_decision import record

def controller_once(*,db_path:str,wallet:str,allowlist:str,rpc_url:str,
                    eth_usd:Decimal,campaign_id:str,horizon_days:int=30,
                    drift_bps:int=500,managed_capital_cap_units:int|None=None)->dict:
    if not address_ok(wallet) or not rpc_url.startswith("https://"):
        raise ValueError("Valid wallet and HTTPS Base RPC required")
    if not Path(db_path).is_file():
        raise ValueError("Existing executor database required")
    if Path("data/STOP_AUTOMATION").exists():
        raise RuntimeError("Emergency stop is active")
    policy=read_policy(allowlist)
    # Reuse the monitor lock to prevent overlapping observations and journals.
    with exclusive_monitor_lock(db_path):
        with ReadOnlyRPC(rpc_url) as rpc, MorphoClient() as morpho:
            report=(observation_report(wallet,allowlist,rpc,morpho,managed_capital_cap_units=managed_capital_cap_units)
                    if managed_capital_cap_units is not None else observation_report(wallet,allowlist,rpc,morpho))
            economics=evaluate_live(report,rpc=rpc,morpho=morpho,
                 eth_usd=eth_usd,horizon_days=horizon_days,
                 threshold_bps=drift_bps)
            # Persist the decision before any campaign mutation.
            decision_id=record(db_path,wallet,report,economics)
            result={"status":"HOLD","decision":economics["decision"],
                    "shadow_decision_id":decision_id,
                    "rpc_block":report["plan"]["observed_block"],
                    "submitted":False,"execution_enabled":False,
                    "campaign_created":False}
            if economics["decision"]!="REBALANCE_REVIEW_ONLY":
                return result
            try:
                proposed=propose_candidates(report,wallet=wallet,
                      approved_vaults=policy.vault_addresses)
            except ValueError:
                # An economically appealing but oversized movement is not
                # authority to relax a hard per-transaction cap.
                result["status"]="CANDIDATE_POLICY_BLOCKED"
                return result
            admission=assess_admission(economics=economics,
                candidates=[asdict(c) for c in proposed],wallet=wallet,
                approved_vaults=policy.vault_addresses)
            result["admission"]=admission
            if admission["status"]!="ADMISSION_REVIEW_ONLY":
                result["status"]="ADMISSION_BLOCKED"
                return result
            # Observe freshness and availability again immediately before
            # persisting; any drift invalidates the original economic decision.
            fresh=(observation_report(wallet,allowlist,rpc,morpho,managed_capital_cap_units=managed_capital_cap_units)
                   if managed_capital_cap_units is not None else observation_report(wallet,allowlist,rpc,morpho))
            if (fresh["plan"]["observed_block"]!=report["plan"]["observed_block"]
                or fresh["plan"]["wallet_usdc_units"]!=report["plan"]["wallet_usdc_units"]
                or fresh["plan"]["changes"]!=report["plan"]["changes"]):
                result["status"]="STALE_OBSERVATION"
                return result
            created=create_campaign(db_path,campaign_id,proposed)
            result.update({"status":"CAMPAIGN_CREATED_REVIEW_ONLY",
                "campaign_created":True,"campaign_id":created["campaign_id"],
                "campaign_state":created["state"],"steps":len(created["steps"]),
                "economic_evidence":economics})
            return result

def main(argv=None):
    p=argparse.ArgumentParser(description="One keyless live decision-to-campaign cycle; NEVER signs")
    p.add_argument("--wallet",required=True)
    p.add_argument("--allowlist",required=True)
    p.add_argument("--db",required=True)
    p.add_argument("--campaign-id",required=True)
    p.add_argument("--eth-usd",required=True)
    p.add_argument("--horizon-days",type=int,default=30)
    p.add_argument("--drift-bps",type=int,default=500)
    a=p.parse_args(argv)
    try:
        result=controller_once(db_path=a.db,wallet=a.wallet,
            allowlist=a.allowlist,rpc_url=os.environ.get("BASE_RPC_URL",""),
            eth_usd=Decimal(a.eth_usd),campaign_id=a.campaign_id,
            horizon_days=a.horizon_days,drift_bps=a.drift_bps)
        print(json.dumps(result,indent=2))
        return 0
    except Exception as exc:
        # Never expose credential-bearing RPC URLs or provider response bodies.
        print("CONTROLLER BLOCKED: "+type(exc).__name__,file=sys.stderr)
        return 1

if __name__=="__main__":
    raise SystemExit(main())
