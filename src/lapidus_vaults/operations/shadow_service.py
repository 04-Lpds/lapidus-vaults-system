"""Read-only Base monitoring service with bounded restart policy.

Never loads an execution private key, creates campaigns or submits transactions.
Use a dedicated shadow SQLite file, separate from the live executor journal.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import sys
import time

from ..allocator import address_ok
from ..wallet_plan import read_policy
from .shadow_watch import run_cycle
from ..monitor_once import exclusive_monitor_lock

def service(*,db_path:str,wallet:str,allowlist:str,rpc_url:str,
            eth_usd:Decimal,interval_seconds:int=900,max_cycles:int=96,
            failure_limit:int=3,cycle_fn=run_cycle,sleep_fn=time.sleep)->dict:
    if not address_ok(wallet) or not rpc_url.startswith("https://"):
        raise ValueError("Invalid wallet or HTTPS Base RPC")
    if not 60<=interval_seconds<=86400 or not 1<=max_cycles<=672:
        raise ValueError("Invalid monitoring schedule")
    if not 1<=failure_limit<=10 or not eth_usd.is_finite() or not Decimal(100)<=eth_usd<=Decimal(100000):
        raise ValueError("Invalid monitoring policy")
    read_policy(allowlist)
    target=Path(db_path)
    if target.exists() and not target.is_file():
        raise ValueError("Invalid monitoring journal")
    target.parent.mkdir(parents=True,exist_ok=True)
    completed=0
    failures=0
    consecutive=0
    with exclusive_monitor_lock(db_path):
        for index in range(max_cycles):
            if Path("data/STOP_AUTOMATION").exists() or Path("data/STOP_SHADOW_SERVICE").exists():
                return {"status":"STOPPED_BY_OPERATOR","completed":completed,"failed":failures,"submitted":False}
            try:
                result=cycle_fn(db_path=db_path,wallet=wallet,allowlist=allowlist,
                    rpc_url=rpc_url,eth_usd=eth_usd,horizon_days=30,drift_bps=500)
                completed+=1
                consecutive=0
                print(json.dumps({"cycle":index+1,"decision":result["decision"],
                    "rpc_block":result["rpc_block"],"at_utc":datetime.now(timezone.utc).isoformat(),
                    "submitted":False}),flush=True)
            except Exception as exc:
                failures+=1
                consecutive+=1
                print(json.dumps({"cycle":index+1,"status":"MONITOR_FAILED",
                    "error_type":type(exc).__name__,"submitted":False}),flush=True)
                if consecutive>=failure_limit:
                    return {"status":"STOPPED_AFTER_REPEATED_FAILURES",
                        "completed":completed,"failed":failures,"submitted":False}
            if index+1<max_cycles:
                sleep_fn(interval_seconds)
    return {"status":"MONITORING_FINISHED","completed":completed,"failed":failures,"submitted":False}

def main(argv=None):
    p=argparse.ArgumentParser(description="Read-only, bounded Base monitor service")
    p.add_argument("--wallet",required=True)
    p.add_argument("--allowlist",required=True)
    p.add_argument("--db",default="data/lvs_shadow_service.sqlite3")
    p.add_argument("--eth-usd",required=True)
    p.add_argument("--interval-seconds",type=int,default=900)
    p.add_argument("--max-cycles",type=int,default=96)
    a=p.parse_args(argv)
    try:
        result=service(db_path=a.db,wallet=a.wallet,allowlist=a.allowlist,
            rpc_url=os.environ.get("BASE_RPC_URL",""),eth_usd=Decimal(a.eth_usd),
            interval_seconds=a.interval_seconds,max_cycles=a.max_cycles)
        print(json.dumps(result,indent=2))
        return 0 if result["status"] in {"MONITORING_FINISHED","STOPPED_BY_OPERATOR"} else 2
    except KeyboardInterrupt:
        print("READ-ONLY MONITOR INTERRUPTED. No transactions sent.")
        return 0
    except Exception as exc:
        print("SHADOW SERVICE BLOCKED: "+type(exc).__name__,file=sys.stderr)
        return 1

if __name__=="__main__":
    raise SystemExit(main())
