"""Read-only operational status for the dedicated Base executor.

No RPC, private key, or action creation. Does not alter the journal.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sqlite3

def inspect(db_path: str) -> dict:
    if not Path(db_path).is_file():
        raise ValueError("Executor database missing")
    with sqlite3.connect(f"file:{Path(db_path).resolve().as_posix()}?mode=ro",uri=True,timeout=5) as db:
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        def query(table,sql):
            return db.execute(sql).fetchall() if table in tables else []
        open_campaigns=query("rebalance_campaigns",
            "SELECT campaign_id,state FROM rebalance_campaigns WHERE state='ACTIVE' ORDER BY created_at")
        pending=query("autonomous_submissions",
            "SELECT campaign_id,sequence,state,tx_hash FROM autonomous_submissions WHERE state!='CONFIRMED' ORDER BY updated_at")
        latest=query("shadow_decisions",
            "SELECT id,observed_at,rpc_block,decision,details_json FROM shadow_decisions ORDER BY id DESC LIMIT 1")
        completed=query("rebalance_campaigns",
            "SELECT campaign_id FROM rebalance_campaigns WHERE state='COMPLETED' ORDER BY created_at DESC LIMIT 3")
        count=db.execute("SELECT COUNT(*) FROM shadow_decisions").fetchone()[0] if "shadow_decisions" in tables else 0
    output={"status":"REVIEW_REQUIRED" if open_campaigns or pending else "NO_UNRESOLVED_JOURNAL_ACTIVITY",
        "read_only":True,"database":str(db_path),"shadow_decision_count":count,
        "active_campaigns":[{"campaign_id":r[0],"state":r[1]} for r in open_campaigns],
        "unresolved_submissions":[{"campaign_id":r[0],"sequence":r[1],"state":r[2],"tx_hash":r[3]} for r in pending],
        "recent_completed_campaigns":[r[0] for r in completed]}
    if latest:
        i,at,block,decision,raw=latest[0]
        detail=json.loads(raw)
        output["latest_economic_decision"]={"id":i,"at_utc":at,"block":block,"decision":decision,
            "annual_yield_delta_usd":detail.get("estimated_annual_yield_delta_usd"),
            "horizon_benefit_usd":detail.get("projected_historical_rate_yield_delta_usd"),
            "fee_estimate_usd":detail.get("estimated_fee_usd"),
            "estimated_gas_steps":detail.get("gas_steps"),
            "historical_apy_7d":detail.get("historical_apy_7d")}
    return output

def main(argv=None):
    p=argparse.ArgumentParser(description="Read-only execution and economics status (no network or signer)")
    p.add_argument("--db",default="data/lvs_executor_test.sqlite3")
    a=p.parse_args(argv)
    try:
        print(json.dumps(inspect(a.db),indent=2))
        return 0
    except (ValueError,sqlite3.Error,OSError):
        print(json.dumps({"status":"STATUS_UNAVAILABLE","read_only":True}))
        return 1

if __name__=="__main__":
    raise SystemExit(main())
