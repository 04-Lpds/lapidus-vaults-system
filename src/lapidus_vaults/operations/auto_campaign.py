"""Bounded whole-campaign runner for an existing, explicitly created campaign.

Each iteration uses the hardened one-action engine. A submitted transaction
must be recovered and independently reconciled before any successor action.
Never auto-retry uncertain sends, or generate a new campaign.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

from ..wallet_plan import read_policy
from .auto_once import execute_once

MAX_INVOCATIONS = 8
POLL_SECONDS = 4
MAX_PENDING_POLLS = 15

def campaign_state(db_path: str, campaign_id: str) -> str:
    with sqlite3.connect(db_path,timeout=10) as db:
        row=db.execute("SELECT state FROM rebalance_campaigns WHERE campaign_id=?",(campaign_id,)).fetchone()
    if row is None:
        raise ValueError("Unknown explicitly authorized campaign")
    return row[0]

def run_campaign(*, db_path: str, campaign_id: str, wallet: str,
                 protected_wallet: str, allowlist_path: str, rpc_url: str,
                 private_key: str, max_capital_units: int = 5_000_000,
                 execute_fn=execute_once, sleep_fn=time.sleep) -> dict:
    if not 0 < max_capital_units <= 5_000_000:
        raise ValueError("Campaign runner limited to 5 USDC")
    if not os.path.isfile(db_path):
        raise ValueError("Campaign DB not found")
    if Path("data/STOP_AUTOMATION").exists():
        raise RuntimeError("Emergency stop is active")
    if campaign_state(db_path,campaign_id)!="ACTIVE":
        raise ValueError("Campaign must be ACTIVE")
    with sqlite3.connect(db_path,timeout=10) as db:
        row=db.execute("SELECT wallet,plan_json FROM rebalance_campaigns WHERE campaign_id=?",(campaign_id,)).fetchone()
    if row[0].lower()!=wallet.lower():
        raise ValueError("Campaign belongs to another wallet")
    steps=json.loads(row[1])
    if len(steps)!=2 or [s["method"] for s in steps]!=["approve_intent","deposit_intent"] or any(
        s["requested_assets_units"]!=800_000 for s in steps):
        raise ValueError("Runner only permits the existing exact 0.8 USDC approve/deposit trial")
    approved=read_policy(allowlist_path).vault_addresses
    if steps[0]["to_address"].lower() not in {"0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"} or steps[1]["to_address"].lower() not in approved:
        raise ValueError("Campaign targets outside USDC/vault allowlist")
    events=[]
    pending_polls=0
    for _ in range(MAX_INVOCATIONS):
        if Path("data/STOP_AUTOMATION").exists():
            raise RuntimeError("Emergency stop activated; stopped without more sends")
        result=execute_fn(db_path=db_path,wallet=wallet,
            protected_wallet=protected_wallet,approved_vaults=approved,
            allowlist_path=allowlist_path,rpc_url=rpc_url,
            private_key=private_key,max_capital_units=max_capital_units)
        events.append(result)
        state=campaign_state(db_path,campaign_id)
        if state=="COMPLETED":
            return {"status":"CAMPAIGN_COMPLETED","campaign_id":campaign_id,"events":events}
        if state!="ACTIVE":
            raise RuntimeError("Campaign halted or changed; stop")
        status=result.get("status", result.get("state"))
        if status=="SUBMITTED":
            # Only receipt recovery may unlock the next campaign step.
            sleep_fn(POLL_SECONDS)
            continue
        if status in {"PENDING_NOT_FOUND","PENDING_MATCHED_INPUT"}:
            pending_polls+=1
            if pending_polls>=MAX_PENDING_POLLS:
                return {"status":"PENDING_MANUAL_REVIEW","campaign_id":campaign_id,"events":events}
            sleep_fn(POLL_SECONDS)
            continue
        if status=="RECEIPT_AND_BLOCK_MATCH":
            # Looping will independently select and freshly quote next step.
            pending_polls=0
            continue
        # No blind retries and no progressing on unrecognized result.
        return {"status":"HALTED_REVIEW_REQUIRED","campaign_id":campaign_id,"events":events}
    return {"status":"PAUSED_INVOCATION_LIMIT","campaign_id":campaign_id,"events":events}

def run_bounded_campaign(*, db_path: str, campaign_id: str, wallet: str,
                         protected_wallet: str, allowlist_path: str, rpc_url: str,
                         private_key: str, admitted_economics: dict,
                         max_capital_units: int = 5_000_000,
                         execute_fn=execute_once, sleep_fn=time.sleep) -> dict:
    """Bounded controller driver, not exposed through a live CLI.

    Admission must be rechecked against the EXACT persisted campaign bytes.
    Caller is responsible for authenticating the fresh economic observation.
    """
    from .campaign_admission import assess_admission
    from .campaigns import read_campaign
    if not 0 < max_capital_units <= 5_000_000:
        raise ValueError("Bounded controller requires at most 5 USDC")
    if not os.path.isfile(db_path) or Path("data/STOP_AUTOMATION").exists():
        raise RuntimeError("Missing journal or emergency stop active")
    policy=read_policy(allowlist_path)
    campaign=read_campaign(db_path,campaign_id)
    if campaign["state"]!="ACTIVE" or campaign["wallet"]!=wallet.lower():
        raise ValueError("Active campaign wallet mismatch")
    with sqlite3.connect(db_path,timeout=10) as db:
        row=db.execute("SELECT plan_json FROM rebalance_campaigns WHERE campaign_id=?",
                       (campaign_id,)).fetchone()
    candidates=json.loads(row[0])
    verdict=assess_admission(economics=admitted_economics,candidates=candidates,
                              wallet=wallet,approved_vaults=policy.vault_addresses)
    if verdict["status"]!="ADMISSION_REVIEW_ONLY":
        return {"status":"ADMISSION_BLOCKED","submitted":False,"admission":verdict}
    # A review decision is NOT live signing consent. Explicit permission must
    # come from a separately configured authority, so this path stays fork-only.
    if not rpc_url.startswith("http://127.0.0.1:"):
        return {"status":"LIVE_EXECUTION_NOT_AUTHORIZED","submitted":False}
    # execute_once intentionally requires a HTTPS provider. The local fork
    # test harness supplies its own strictly-loopback RPC adapter.
    events=[]
    for _ in range(MAX_INVOCATIONS):
        if Path("data/STOP_AUTOMATION").exists():
            raise RuntimeError("Emergency stop active")
        event=execute_fn(db_path=db_path,wallet=wallet,
            protected_wallet=protected_wallet,
            approved_vaults=policy.vault_addresses,
            allowlist_path=allowlist_path,rpc_url="https://local-only.invalid",
            private_key=private_key,max_capital_units=max_capital_units)
        events.append(event)
        state=campaign_state(db_path,campaign_id)
        if state=="COMPLETED":
            return {"status":"CAMPAIGN_COMPLETED","events":events,"submitted":True}
        if state!="ACTIVE":
            return {"status":"HALTED_REVIEW_REQUIRED","events":events}
        status=event.get("status",event.get("state"))
        if status in {"SUBMITTED","PENDING_NOT_FOUND","PENDING_MATCHED_INPUT"}:
            sleep_fn(POLL_SECONDS)
            continue
        if status=="RECEIPT_AND_BLOCK_MATCH":
            continue
        return {"status":"HALTED_REVIEW_REQUIRED","events":events}
    return {"status":"PAUSED_INVOCATION_LIMIT","events":events}

def main(argv=None):
    p=argparse.ArgumentParser(description="Execute one 0.8 USDC campaign end-to-end, with strict recovery gates")
    p.add_argument("--wallet",required=True)
    p.add_argument("--protected-wallet",required=True)
    p.add_argument("--allowlist",required=True)
    p.add_argument("--db",required=True)
    p.add_argument("--campaign-id",required=True)
    p.add_argument("--execute-live",action="store_true")
    args=p.parse_args(argv)
    if not args.execute_live:
        print("DISABLED: explicit --execute-live required. No transaction submitted.")
        return 0
    secret=os.environ.get("LVS_AUTO_PRIVATE_KEY")
    rpc_url=os.environ.get("BASE_RPC_URL")
    if not secret or not rpc_url or not rpc_url.startswith("https://"):
        print("BLOCKED: dedicated key and trusted HTTPS Base RPC required",file=sys.stderr)
        return 1
    try:
        result=run_campaign(db_path=args.db,campaign_id=args.campaign_id,
            wallet=args.wallet,protected_wallet=args.protected_wallet,
            allowlist_path=args.allowlist,rpc_url=rpc_url,private_key=secret)
        print(json.dumps(result,indent=2))
        return 0 if result["status"]=="CAMPAIGN_COMPLETED" else 2
    except Exception:
        print("CAMPAIGN RUNNER HALTED: inspect journal and on-chain receipt before any rerun",file=sys.stderr)
        return 1

if __name__=="__main__":
    raise SystemExit(main())
