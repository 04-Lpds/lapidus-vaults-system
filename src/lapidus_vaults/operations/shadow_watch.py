"""Read-only periodic live shadow monitor; never accesses a signing key.

One process, one decision each interval, bounded run duration. If a check
fails, record sanitized failure and continue; no campaign/signer access.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

from ..monitor_once import observation_report, exclusive_monitor_lock
from ..rpc_verify import ReadOnlyRPC
from ..client import MorphoClient
from .live_shadow_economics import evaluate_live
from .shadow_decision import record

DEFAULT_INTERVAL_SECONDS=900
DEFAULT_MAX_CYCLES=96

def run_cycle(*,db_path:str,wallet:str,allowlist:str,rpc_url:str,
              eth_usd:Decimal,horizon_days:int,drift_bps:int)->dict:
    with ReadOnlyRPC(rpc_url) as rpc, MorphoClient() as morpho:
        report=observation_report(wallet,allowlist,rpc,morpho)
        result=evaluate_live(report,rpc=rpc,morpho=morpho,eth_usd=eth_usd,
                             horizon_days=horizon_days,threshold_bps=drift_bps)
    result["shadow_decision_id"]=record(db_path,wallet,report,result)
    result["rpc_block"]=report["plan"]["observed_block"]
    return result

def monitor_loop(*,db_path:str,wallet:str,allowlist:str,rpc_url:str,eth_usd:Decimal,
                 horizon_days:int=30,drift_bps:int=500,
                 interval_seconds:int=DEFAULT_INTERVAL_SECONDS,
                 max_cycles:int=DEFAULT_MAX_CYCLES,
                 cycle_fn=run_cycle,sleep_fn=time.sleep)->dict:
    if not 60<=interval_seconds<=86400 or not 1<=max_cycles<=672:
        raise ValueError("Monitoring interval or cycle count out of bounds")
    if not rpc_url.startswith("https://") or not Path(db_path).is_file():
        raise ValueError("HTTPS Base RPC and existing database required")
    from ..allocator import address_ok
    if not address_ok(wallet):
        raise ValueError("Invalid monitored wallet")
    if not eth_usd.is_finite() or not Decimal(100)<=eth_usd<=Decimal(100000):
        raise ValueError("Invalid ETH/USD assumption")
    counts={"completed":0,"failed":0}
    # Lock is exclusive across the entire session: another LVS monitor cannot overlap.
    with exclusive_monitor_lock(db_path):
        for i in range(max_cycles):
            try:
                result=cycle_fn(db_path=db_path,wallet=wallet,allowlist=allowlist,
                    rpc_url=rpc_url,eth_usd=eth_usd,horizon_days=horizon_days,
                    drift_bps=drift_bps)
                counts["completed"]+=1
                print(json.dumps({"cycle":i+1,"at_utc":datetime.now(timezone.utc).isoformat(),
                    "decision":result["decision"],"shadow_decision_id":result["shadow_decision_id"],
                    "rpc_block":result["rpc_block"],"submitted":False}),flush=True)
            except Exception as exc:
                counts["failed"]+=1
                # No provider URLs, tokens, RPC error bodies or wallet secrets in log.
                print(json.dumps({"cycle":i+1,"at_utc":datetime.now(timezone.utc).isoformat(),
                    "status":"MONITOR_FAILED","error_type":type(exc).__name__,
                    "submitted":False}),flush=True)
            if i<max_cycles-1:
                sleep_fn(interval_seconds)
    return {"status":"MONITORING_FINISHED","cycles":max_cycles,**counts,
            "submitted":False}

def main(argv=None)->int:
    p=argparse.ArgumentParser(description="Read-only periodic shadow observation loop")
    p.add_argument("--wallet",required=True)
    p.add_argument("--allowlist",required=True)
    p.add_argument("--db",required=True)
    p.add_argument("--eth-usd",required=True)
    p.add_argument("--horizon-days",type=int,default=30)
    p.add_argument("--drift-bps",type=int,default=500)
    p.add_argument("--interval-seconds",type=int,default=DEFAULT_INTERVAL_SECONDS)
    p.add_argument("--max-cycles",type=int,default=DEFAULT_MAX_CYCLES)
    args=p.parse_args(argv)
    try:
        rpc_url=os.environ.get("BASE_RPC_URL","")
        result=monitor_loop(db_path=args.db,wallet=args.wallet,allowlist=args.allowlist,
            rpc_url=rpc_url,eth_usd=Decimal(args.eth_usd),
            horizon_days=args.horizon_days,drift_bps=args.drift_bps,
            interval_seconds=args.interval_seconds,max_cycles=args.max_cycles)
        print(json.dumps(result),flush=True)
        return 0 if result["failed"]==0 else 2
    except KeyboardInterrupt:
        print("MONITOR STOPPED by operator; no transactions sent.",flush=True)
        return 0
    except Exception as exc:
        print("MONITOR BLOCKED: "+type(exc).__name__,file=sys.stderr)
        return 1

if __name__=="__main__":
    raise SystemExit(main())
