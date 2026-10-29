"""Bounded dedicated-wallet controller. No signer is loaded unless --execute-live.

Operator opt-in allows eligible campaigns *within* a pinned vault allowlist,
subject to fresh economics and independently re-quoted per-step transactions.
Journal recovery always takes precedence over generating any new campaign.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json
import hashlib
import os
from pathlib import Path
import sqlite3
import sys
import time
from uuid import uuid4

from ..wallet_plan import read_policy
from ..allocator import address_ok
from .auto_once import execute_once, assert_no_orphaned_authorizations
from .controller_once import controller_once

CAPITAL_CAP = 5_000_000
# A bounded 1,000-unit accounting tolerance is only used for the existing
# 5.000202 USDC balance. It does NOT authorize investment of the excess.
CAPITAL_ACCOUNTING_TOLERANCE = 1_000
MAX_ACTION = 1_000_000


def _state(db_path):
    if not Path(db_path).is_file():
        raise ValueError("Dedicated executor journal must exist")
    with sqlite3.connect(db_path, timeout=10) as db:
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "rebalance_campaigns" not in names:
            return None
        row = db.execute("SELECT campaign_id,state FROM rebalance_campaigns WHERE state='ACTIVE' ORDER BY created_at LIMIT 1").fetchone()
        return row


def _outstanding(db_path):
    with sqlite3.connect(db_path, timeout=10) as db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "autonomous_submissions" not in tables:
            return False
        return bool(db.execute("SELECT 1 FROM autonomous_submissions WHERE state!='CONFIRMED' LIMIT 1").fetchone())


def _digest(db_path, campaign_id, wallet, vaults):
    with sqlite3.connect(db_path,timeout=10) as db:
        row=db.execute("SELECT wallet,plan_json FROM rebalance_campaigns WHERE campaign_id=?",
                       (campaign_id,)).fetchone()
    if row is None or row[0]!=wallet.lower():
        raise RuntimeError("Pilot campaign wallet mismatch")
    raw=json.dumps({"wallet":wallet.lower(),"vaults":vaults,
                    "candidate_json":json.loads(row[1])},sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def _authorized(db_path, campaign_id, wallet, vaults):
    with sqlite3.connect(db_path,timeout=10) as db:
        table=db.execute("SELECT name FROM sqlite_master WHERE name='pilot_authorizations'").fetchone()
        if table is None:
            return False
        row=db.execute("SELECT digest FROM pilot_authorizations WHERE campaign_id=?",
                       (campaign_id,)).fetchone()
    return row is not None and row[0]==_digest(db_path,campaign_id,wallet,vaults)


def _authorize(db_path,campaign_id,wallet,vaults):
    digest=_digest(db_path,campaign_id,wallet,vaults)
    with sqlite3.connect(db_path,timeout=10) as db:
        db.execute("CREATE TABLE IF NOT EXISTS pilot_authorizations (campaign_id TEXT PRIMARY KEY, digest TEXT NOT NULL)")
        db.execute("INSERT INTO pilot_authorizations VALUES (?,?)",(campaign_id,digest))
        db.commit()


def cycle(*,db_path,wallet,protected_wallet,allowlist,rpc_url,eth_usd,
          execute_live=False,private_key=None,controller_fn=controller_once,
          execute_fn=execute_once):
    if Path("data/STOP_AUTOMATION").exists():
        return {"status":"STOPPED_BY_OPERATOR","submitted":False}
    if not address_ok(wallet) or not address_ok(protected_wallet) or wallet.lower()==protected_wallet.lower():
        raise ValueError("Dedicated wallet and protected wallet must differ")
    if not rpc_url.startswith("https://") or not Path(db_path).is_file():
        raise ValueError("HTTPS RPC and existing executor journal required")
    policy=read_policy(allowlist)
    if not policy.vault_addresses or len(policy.vault_addresses)>2:
        raise ValueError("Pilot must contain one or two explicitly allowed vaults")
    if execute_live:
        if not private_key:
            raise ValueError("Explicit live mode requires dedicated signer key")
        from eth_account import Account
        if Account.from_key(private_key).address.lower()!=wallet.lower():
            raise ValueError("Dedicated signer wallet mismatch")
    # Unresolved transaction hashes MUST be recovered before any new campaign.
    outstanding=_outstanding(db_path)
    active=_state(db_path)
    if outstanding or active:
        if not execute_live:
            return {"status":"ACTIVE_OR_PENDING_REVIEW_ONLY","submitted":False}
        if not active or not _authorized(db_path,active[0],wallet,policy.vault_addresses):
            return {"status":"UNAUTHORIZED_EXISTING_CAMPAIGN","submitted":False}
        event=execute_fn(db_path=db_path,wallet=wallet.lower(),
            protected_wallet=protected_wallet,approved_vaults=policy.vault_addresses,
            allowlist_path=allowlist,rpc_url=rpc_url,private_key=private_key,
            max_capital_units=CAPITAL_CAP+CAPITAL_ACCOUNTING_TOLERANCE)
        return {"status":"EXISTING_CAMPAIGN_PROGRESS","event":event,
                "submitted":event.get("state")=="SUBMITTED"}
    assert_no_orphaned_authorizations(db_path)
    campaign_id="pilot-"+datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")+"-"+uuid4().hex[:8]
    decision=controller_fn(db_path=db_path,wallet=wallet.lower(),
        allowlist=allowlist,rpc_url=rpc_url,eth_usd=eth_usd,campaign_id=campaign_id,
        managed_capital_cap_units=CAPITAL_CAP)
    if decision.get("status")!="CAMPAIGN_CREATED_REVIEW_ONLY":
        return {"status":"NO_ACTION","decision":decision,"submitted":False}
    # Review-only admission is NOT enough by itself: check the persisted
    # economic evidence and exact bytes again before the live signer is called.
    economics=decision.get("economic_evidence")
    if not isinstance(economics,dict) or decision.get("campaign_id")!=campaign_id:
        raise RuntimeError("Campaign lacks authenticated controller evidence")
    from .campaign_admission import assess_admission
    with sqlite3.connect(db_path,timeout=10) as db:
        row=db.execute("SELECT wallet,plan_json,state FROM rebalance_campaigns WHERE campaign_id=?",
                       (campaign_id,)).fetchone()
    if row is None or row[0]!=wallet.lower() or row[2]!="ACTIVE":
        raise RuntimeError("Campaign journal identity changed")
    candidates=json.loads(row[1])
    capital=economics.get("capital_units")
    if type(capital) is not int or not 0<capital<=CAPITAL_CAP:
        return {"status":"CAPITAL_CAP_BLOCKED","submitted":False,"campaign_id":campaign_id}
    verdict=assess_admission(economics=economics,candidates=candidates,
                             wallet=wallet.lower(),approved_vaults=policy.vault_addresses)
    if verdict["status"]!="ADMISSION_REVIEW_ONLY":
        return {"status":"ADMISSION_BLOCKED","admission":verdict,"submitted":False}
    if not execute_live:
        return {"status":"CAMPAIGN_CREATED_NOT_AUTHORIZED","campaign_id":campaign_id,
                "submitted":False,"admission":verdict}
    _authorize(db_path,campaign_id,wallet,policy.vault_addresses)
    # One transaction at most per cycle; recovery/confirmation unlocks later
    # steps on subsequent cycles, never within an unchecked signing loop.
    event=execute_fn(db_path=db_path,wallet=wallet.lower(),
        protected_wallet=protected_wallet,approved_vaults=policy.vault_addresses,
        allowlist_path=allowlist,rpc_url=rpc_url,private_key=private_key,
        max_capital_units=CAPITAL_CAP+CAPITAL_ACCOUNTING_TOLERANCE)
    return {"status":"PILOT_CAMPAIGN_PROGRESS","campaign_id":campaign_id,
            "event":event,"submitted":event.get("state")=="SUBMITTED"}


def main(argv=None):
    p=argparse.ArgumentParser(description="Bounded Base pilot; defaults to keyless review")
    p.add_argument("--wallet",required=True)
    p.add_argument("--protected-wallet",required=True)
    p.add_argument("--allowlist",required=True)
    p.add_argument("--db",required=True)
    p.add_argument("--eth-usd",required=True)
    p.add_argument("--interval-seconds",type=int,default=300)
    p.add_argument("--max-cycles",type=int,default=288)
    p.add_argument("--execute-live",action="store_true")
    a=p.parse_args(argv)
    if not 60<=a.interval_seconds<=86400 or not 1<=a.max_cycles<=672:
        p.error("Monitoring interval or bounded cycle count invalid")
    try:
        usd=Decimal(a.eth_usd)
        if not usd.is_finite() or not 100<=usd<=100000:
            raise ValueError("Invalid ETH USD assumption")
        rpc_url=os.environ.get("BASE_RPC_URL","")
        private_key=os.environ.get("LVS_AUTO_PRIVATE_KEY") if a.execute_live else None
        for index in range(a.max_cycles):
            result=cycle(db_path=a.db,wallet=a.wallet,protected_wallet=a.protected_wallet,
                 allowlist=a.allowlist,rpc_url=rpc_url,eth_usd=usd,
                 execute_live=a.execute_live,private_key=private_key)
            print(json.dumps({"cycle":index+1,**result},default=str),flush=True)
            if result.get("status")=="STOPPED_BY_OPERATOR":
                return 0
            # An unexpected state or a review-required execution must halt.
            event=result.get("event",{})
            permitted={"SUBMITTED","RECEIPT_AND_BLOCK_MATCH","NO_ACTIVE_CAMPAIGN",
                "AUTO_POLICY_PASSED_AWAITING_SECURE_SIGNER","PENDING_NOT_FOUND","PENDING_MATCHED_INPUT"}
            if event and event.get("status",event.get("state")) not in permitted:
                return 2
            if result["status"] in {"CAPITAL_CAP_BLOCKED","ADMISSION_BLOCKED","UNAUTHORIZED_EXISTING_CAMPAIGN"}:
                return 2
            if index+1<a.max_cycles:
                time.sleep(a.interval_seconds)
        return 0
    except KeyboardInterrupt:
        print("PILOT INTERRUPTED. Confirm pending transaction status before restart.")
        return 0
    except Exception as exc:
        print("PILOT HALTED: "+type(exc).__name__+
              ". Inspect executor journal and on-chain receipts before restart.",file=sys.stderr)
        return 1

if __name__=="__main__":
    raise SystemExit(main())
