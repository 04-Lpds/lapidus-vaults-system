"""Explicit one-time 0.5 USDC real Base cross-vault canary.

Not an investment decision: operator specifically authorizes a known test.
No fabricated market/economic data, no private key in the journal.
Reuses the production quote, signer, receipt verification, and crash journal.
Do not operate concurrently with live-pilot on the same database.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

from ..allocator import BASE_USDC, address_ok
from ..rpc_verify import ReadOnlyRPC, ASSET, BALANCE_OF, _address_word, _word
from ..unsigned_candidates import UnsignedCandidate, WITHDRAW, APPROVE, DEPOSIT
from ..wallet_plan import read_policy
from .campaigns import create_campaign, read_campaign
from .auto_once import execute_once, assert_no_orphaned_authorizations
from .live_pilot import _state, _outstanding

AMOUNT=500_000
MAX_TOTAL_CAPITAL=5_001_000


def prepare(*,db_path,wallet,protected_wallet,allowlist,rpc_url,source,destination,
            campaign_id="live-canary-500k"):
    wallet=wallet.lower()
    source=source.lower()
    destination=destination.lower()
    if not all(address_ok(x) for x in (wallet,protected_wallet,source,destination)):
        raise ValueError("Invalid canary wallet or vault")
    if wallet==protected_wallet.lower() or source==destination:
        raise ValueError("Protected wallet or same-vault canary not permitted")
    if not rpc_url.startswith("https://") or not Path(db_path).is_file():
        raise ValueError("Missing verified HTTPS RPC or executor journal")
    if Path(str(db_path)+".pilot.lock").exists() or Path(str(db_path)+".auto.lock").exists():
        raise RuntimeError("Stop live-pilot before running a canary")
    if Path("data/STOP_AUTOMATION").exists():
        raise RuntimeError("Emergency stop is active")
    allowed=read_policy(allowlist).vault_addresses
    if len(allowed)!=2 or source not in allowed or destination not in allowed:
        raise ValueError("Canary requires two previously allowlisted Base vaults")
    if _state(db_path) is not None or _outstanding(db_path):
        raise RuntimeError("Another active or unresolved campaign must be reviewed first")
    assert_no_orphaned_authorizations(db_path)
    with ReadOnlyRPC(rpc_url) as rpc:
        if rpc.chain_id()!=8453:
            raise ValueError("Expected Base mainnet")
        block=rpc.block_number()
        for vault in (source,destination):
            if rpc.call_address(vault,ASSET,block)!=BASE_USDC:
                raise ValueError("Vault underlying mismatch")
        shares=rpc.call_uint(source,BALANCE_OF,block,_address_word(wallet))
        if shares<=0:
            raise ValueError("Canary source has no vault shares")
        from ..live_withdraw import PREVIEW_WITHDRAW
        required=rpc.call_uint(source,PREVIEW_WITHDRAW,block,_word(AMOUNT))
        if not 0<required<=shares:
            raise ValueError("Insufficient source vault shares for 0.5 USDC")
        from ..live_withdraw import WithdrawalQuotes
        # Independently simulate withdrawal and check gas before journaling.
        WithdrawalQuotes(min_gas_reserve_wei=200_000_000_000_000).quote(rpc,wallet=wallet,vault=source,
                                 approved_vaults=allowed,amount_units=AMOUNT)
        if rpc.eth_balance(wallet,block)<200_000_000_000_000:
            raise ValueError("Executor ETH reserve insufficient")
    steps=(
        UnsignedCandidate(1,(),"withdraw_intent",8453,wallet,source,0,
                          WITHDRAW+_word(AMOUNT)+_address_word(wallet)+_address_word(wallet),AMOUNT),
        UnsignedCandidate(2,(1,),"approve_intent",8453,wallet,BASE_USDC,0,
                          APPROVE+_address_word(destination)+_word(AMOUNT),AMOUNT),
        UnsignedCandidate(3,(2,),"deposit_intent",8453,wallet,destination,0,
                          DEPOSIT+_word(AMOUNT)+_address_word(wallet),AMOUNT)
    )
    created=create_campaign(db_path,campaign_id,steps)
    return {"status":"CANARY_CREATED_NOT_EXECUTED","campaign_id":campaign_id,
            "source":source,"destination":destination,
            "amount_usdc":"0.5","steps":3,"submitted":False,"rpc_block":block}


def run(*,db_path,wallet,protected_wallet,allowlist,rpc_url,private_key,campaign_id,
        execute_fn=execute_once,sleep_fn=time.sleep):
    if Path(str(db_path)+".pilot.lock").exists():
        raise RuntimeError("Live pilot must be stopped before canary")
    allowed=read_policy(allowlist).vault_addresses
    campaign=read_campaign(db_path,campaign_id)
    if campaign["state"]!="ACTIVE" or campaign["wallet"]!=wallet.lower():
        raise RuntimeError("Canary campaign not active for this wallet")
    # Exact source, destination, amount and three steps must remain unchanged.
    with sqlite3.connect(db_path,timeout=10) as db:
        raw=db.execute("SELECT plan_json FROM rebalance_campaigns WHERE campaign_id=?",
                       (campaign_id,)).fetchone()[0]
    steps=json.loads(raw)
    if len(steps)!=3 or [s["method"] for s in steps]!=[
            "withdraw_intent","approve_intent","deposit_intent"]:
        raise ValueError("Not an exact three-step canary")
    source=steps[0]["to_address"]
    destination=steps[2]["to_address"]
    if source==destination or source not in allowed or destination not in allowed:
        raise ValueError("Canary vault identities changed")
    expected=[
      WITHDRAW+_word(AMOUNT)+_address_word(wallet)+_address_word(wallet),
      APPROVE+_address_word(destination)+_word(AMOUNT),
      DEPOSIT+_word(AMOUNT)+_address_word(wallet)]
    if any(s["requested_assets_units"]!=AMOUNT or s["data"]!=expected[i] or
           s["from_address"]!=wallet.lower() or s["chain_id"]!=8453
           for i,s in enumerate(steps)):
        raise ValueError("Canary payload changed")
    if any(s["to_address"]!=t for s,t in zip(steps,[source,BASE_USDC,destination])):
        raise ValueError("Canary destination changed")
    if not private_key:
        raise ValueError("Dedicated signing key required")
    from eth_account import Account
    if Account.from_key(private_key).address.lower()!=wallet.lower():
        raise ValueError("Dedicated signing key identity mismatch")
    events=[]
    for _ in range(24):
        if Path("data/STOP_AUTOMATION").exists():
            return {"status":"STOPPED_BY_OPERATOR","events":events}
        event=execute_fn(db_path=db_path,wallet=wallet.lower(),
              protected_wallet=protected_wallet,approved_vaults=allowed,
              allowlist_path=allowlist,rpc_url=rpc_url,private_key=private_key,
              max_capital_units=MAX_TOTAL_CAPITAL)
        status=event.get("status",event.get("state"))
        events.append({"status":status,"tx_hash":event.get("tx_hash")})
        print(json.dumps({"canary":campaign_id,**events[-1]}),flush=True)
        if read_campaign(db_path,campaign_id)["state"]=="COMPLETED":
            return {"status":"CANARY_COMPLETED","campaign_id":campaign_id,"events":events}
        if status in {"SUBMITTED","PENDING_NOT_FOUND","PENDING_MATCHED_INPUT"}:
            sleep_fn(5)
        elif status=="RECEIPT_AND_BLOCK_MATCH":
            continue
        else:
            return {"status":"CANARY_HALTED_REVIEW","campaign_id":campaign_id,"events":events}
    return {"status":"CANARY_PAUSED_PENDING_REVIEW","campaign_id":campaign_id,"events":events}


def main(argv=None):
    p=argparse.ArgumentParser(description="Explicit Base 0.5 USDC cross-vault canary")
    p.add_argument("--wallet",required=True)
    p.add_argument("--protected-wallet",required=True)
    p.add_argument("--allowlist",required=True)
    p.add_argument("--db",required=True)
    p.add_argument("--source",required=True)
    p.add_argument("--destination",required=True)
    p.add_argument("--campaign-id",default="live-canary-500k")
    p.add_argument("--execute-live",action="store_true")
    a=p.parse_args(argv)
    try:
        rpc=os.getenv("BASE_RPC_URL","")
        if not a.execute_live:
            report=prepare(db_path=a.db,wallet=a.wallet,
              protected_wallet=a.protected_wallet,allowlist=a.allowlist,
              rpc_url=rpc,source=a.source,destination=a.destination,
              campaign_id=a.campaign_id)
            print(json.dumps(report,indent=2))
            return 0
        # An explicit second invocation is required to submit real funds.
        report=run(db_path=a.db,wallet=a.wallet,protected_wallet=a.protected_wallet,
                   allowlist=a.allowlist,rpc_url=rpc,
                   private_key=os.getenv("LVS_AUTO_PRIVATE_KEY"),
                   campaign_id=a.campaign_id)
        print(json.dumps(report,indent=2))
        return 0 if report["status"]=="CANARY_COMPLETED" else 2
    except KeyboardInterrupt:
        print("CANARY INTERRUPTED; inspect journal and receipts before resuming.")
        return 2
    except Exception as exc:
        print("CANARY BLOCKED: "+type(exc).__name__+
              ". Inspect journal and receipts before restarting.",file=sys.stderr)
        return 1

if __name__=="__main__":
    raise SystemExit(main())
