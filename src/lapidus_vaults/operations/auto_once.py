"""One-action-per-invocation dedicated-wallet runner (experimental).

Explicit opt-in required. Restart handling checks a journaled hash BEFORE
considering any new action. Never blindly re-sends an unknown transaction.
A separate low-value dedicated wallet is mandatory. No secrets are logged.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys
import httpx

from ..allocator import address_ok
from ..client import MorphoClient
from ..live_readonly_plan import build_live_readonly_plan, DEDICATED_EXECUTOR_ETH_RESERVE
from ..rpc_verify import ReadOnlyRPC
from ..wallet_plan import read_policy
from ..live_deposit import DepositQuotes
from ..live_withdraw import WithdrawalQuotes
from .autopilot_policy import AutoPolicy, next_auto_action
from .campaign_quote import quote_next_campaign_action
from .receipt_bridge import bind_campaign_quote, advance_bound_campaign
from .auto_journal import current, mark_verified
from .dedicated_signer import reserve_and_broadcast


@contextmanager
def execution_lock(db_path: str):
    path = Path(str(db_path) + ".auto.lock")
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd,"w") as out:
            out.write("Locked for autonomous execution; investigate before removing stale lock.\n")
        yield
    finally:
        path.unlink(missing_ok=True)


def read_nonce(rpc_url: str, wallet: str, tag: str) -> int:
    if tag not in {"latest", "pending"}:
        raise ValueError("Invalid nonce block tag")
    with httpx.Client(timeout=15, follow_redirects=False, trust_env=False) as client:
        response = client.post(rpc_url, json={
            "jsonrpc": "2.0", "id": 1, "method": "eth_getTransactionCount",
            "params": [wallet, tag],
        })
        response.raise_for_status()
        data=response.json()
    if data.get("error") or not isinstance(data.get("result"), str):
        raise RuntimeError("Cannot establish dedicated wallet nonce")
    return int(data["result"],16)


def _load_consumed(db_path: str, quote_id: str) -> dict:
    with sqlite3.connect(db_path, timeout=10) as db:
        row=db.execute(
            "SELECT transaction_json FROM live_deposit_authorizations WHERE quote_id=?",
            (quote_id,),
        ).fetchone()
    if row is None:
        raise RuntimeError("Missing persisted signed-intent authorization")
    return json.loads(row[0])


def _recover(db_path: str, campaign_id: str, entries: list[dict], rpc: ReadOnlyRPC) -> dict | None:
    # Stop on ANY unknown or submitted transaction, even from a prior campaign.
    outstanding = [e for e in entries if e["state"] != "CONFIRMED"]
    if not outstanding:
        return None
    if len(outstanding) != 1:
        raise RuntimeError("Multiple unresolved submissions; manual halt required")
    entry=outstanding[0]
    if entry["state"] != "SUBMITTED" or not entry.get("tx_hash"):
        raise RuntimeError("Submission outcome unknown; cannot automatically retry")
    quote_id=entry["quote_id"]
    saved=_load_consumed(db_path,quote_id)
    engine=WithdrawalQuotes() if saved["stage"]=="withdraw" else DepositQuotes()
    engine.consumed[quote_id]=saved
    verified=engine.check_receipt(rpc,quote_id,entry["tx_hash"])
    # Preserve independent receipt evidence through the actual bridge.
    if verified["status"]=="RECEIPT_AND_BLOCK_MATCH":
        mark_verified(db_path,campaign_id=campaign_id,
                      sequence=entry["sequence"],tx_hash=entry["tx_hash"],
                      verified=verified)
    result=advance_bound_campaign(db_path,quote_id,verified)
    return {"status":verified["status"],"tx_hash":entry["tx_hash"],
            "campaign_state":result["state"] if result else None,
            "resubmitted":False}



def assert_no_orphaned_authorizations(db_path: str) -> None:
    """Fail closed on crash windows not represented by a pending submission.

    A persisted binding is an irreversible local authorization boundary: a
    restart must never generate another quote for that campaign step merely
    because no signed transaction made it to autonomous_submissions.
    Likewise, a confirmed journal is not sufficient if the campaign bridge
    was interrupted before recording the receipt.
    """
    with sqlite3.connect(db_path, timeout=10) as db:
        tables = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        if "rebalance_campaign_quote_bindings" not in tables:
            return
        if not {"autonomous_submissions", "rebalance_campaign_steps"} <= tables:
            if db.execute("SELECT 1 FROM rebalance_campaign_quote_bindings LIMIT 1").fetchone():
                raise RuntimeError("Authorized campaign step has no complete recovery journal; manual halt")
            return
        rows = db.execute("""
            SELECT b.campaign_id,b.sequence,b.quote_id,
                   j.state,j.quote_id,s.status
            FROM rebalance_campaign_quote_bindings AS b
            LEFT JOIN autonomous_submissions AS j
              ON j.campaign_id=b.campaign_id AND j.sequence=b.sequence
            LEFT JOIN rebalance_campaign_steps AS s
              ON s.campaign_id=b.campaign_id AND s.sequence=b.sequence
        """).fetchall()
    for campaign_id, sequence, quote_id, journal_state, journal_quote, step_status in rows:
        if journal_state is None or journal_quote != quote_id:
            raise RuntimeError(
                "Orphaned bound quote: authorization exists without a matching "
                "submission reservation; manual review required, no resubmission"
            )
        if journal_state == "CONFIRMED" and step_status != "RECEIPT_AND_BLOCK_MATCH":
            raise RuntimeError(
                "Confirmed journal but campaign receipt bridge incomplete; "
                "manual review required, no resubmission"
            )


def execute_once(*, db_path: str, wallet: str, protected_wallet: str,
                 approved_vaults: tuple[str,...], allowlist_path: str, rpc_url: str,
                 private_key: str, max_capital_units: int) -> dict:
    # No new action if an emergency stop file is present.
    if Path("data/STOP_AUTOMATION").exists():
        raise RuntimeError("Emergency stop file exists; execution disabled")
    if not address_ok(protected_wallet) or wallet.lower()==protected_wallet.lower():
        raise ValueError("Dedicated wallet must be distinct from protected wallet")
    if not rpc_url.startswith("https://"):
        raise ValueError("Live RPC must be a trusted HTTPS endpoint")
    policy=AutoPolicy(wallet=wallet.lower(),vault_allowlist=approved_vaults,
                      max_action_units=1_000_000,max_capital_units=max_capital_units)
    # Check private-key identity before network writes and never echo the key.
    from eth_account import Account
    if Account.from_key(private_key).address.lower() != policy.wallet:
        raise ValueError("Dedicated signer does not match configured wallet")
    with execution_lock(db_path):
        with ReadOnlyRPC(rpc_url) as rpc:
            if rpc.chain_id()!=8453:
                raise RuntimeError("Wrong RPC network")
            with sqlite3.connect(db_path, timeout=10) as db:
                tables={row[0] for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )}
                if "autonomous_submissions" in tables:
                    existing=db.execute(
                        "SELECT DISTINCT campaign_id FROM autonomous_submissions "
                        "WHERE state!='CONFIRMED'"
                    ).fetchall()
                else:
                    existing=[]
            # Recover an unresolved transaction for ANY campaign first.
            if existing:
                if len(existing)!=1:
                    raise RuntimeError("Multiple unresolved campaigns")
                entries=current(db_path,existing[0][0])
                recovery=_recover(db_path,existing[0][0],entries,rpc)
                if recovery is not None:
                    return recovery
            # Also cover crashes after authorization/binding but before reserve.
            assert_no_orphaned_authorizations(db_path)
            with MorphoClient() as morpho:
                report=build_live_readonly_plan(
                    wallet, allowlist_path,rpc,morpho,
                    eth_reserve_wei=DEDICATED_EXECUTOR_ETH_RESERVE)
            observed_units=report["plan"]["capital_units"]
            action=next_auto_action(db_path,policy=policy,
                                    observed_capital_units=observed_units,
                                    current_chain_id=rpc.chain_id())
            if action["status"]!="AUTO_POLICY_PASSED_AWAITING_SECURE_SIGNER":
                return {"status":action["status"],"submitted":False}
            # Nonce gap/pending wallet transaction blocks sending.
            nonce_latest=read_nonce(rpc_url,policy.wallet,"latest")
            nonce_pending=read_nonce(rpc_url,policy.wallet,"pending")
            if nonce_latest!=nonce_pending:
                raise RuntimeError("Dedicated wallet has other pending transactions")
            deposits, withdrawals = DepositQuotes(), WithdrawalQuotes()
            quote=quote_next_campaign_action(
                db_path,wallet=policy.wallet,approved_vaults=approved_vaults,
                rpc=rpc,deposits=deposits,withdrawals=withdrawals)
            engine=withdrawals if quote["stage"]=="withdraw" else deposits
            consumed=engine.consume(rpc,quote["quote_id"])
            with sqlite3.connect(db_path,timeout=10) as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("""CREATE TABLE IF NOT EXISTS live_deposit_authorizations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    prepared_at TEXT NOT NULL,quote_id TEXT UNIQUE NOT NULL,
                    wallet TEXT NOT NULL,vault TEXT NOT NULL,stage TEXT NOT NULL,
                    amount_units INTEGER NOT NULL,transaction_json TEXT NOT NULL)""")
                db.execute("""INSERT INTO live_deposit_authorizations
                    (prepared_at,quote_id,wallet,vault,stage,amount_units,transaction_json)
                    VALUES (?,?,?,?,?,?,?)""",(
                        datetime.now(timezone.utc).isoformat(),quote["quote_id"],
                        consumed["wallet"],consumed["vault"],consumed["stage"],
                        consumed["amount_units"],json.dumps(consumed,sort_keys=True)
                    ))
            bind_campaign_quote(db_path,action["campaign_id"],
                                action["sequence"],quote["quote_id"])
            with sqlite3.connect(db_path,timeout=10) as db:
                row=db.execute("SELECT plan_json FROM rebalance_campaigns WHERE campaign_id=?",
                    (action["campaign_id"],)).fetchone()
            candidate=json.loads(row[0])[action["sequence"]-1]
            return reserve_and_broadcast(
                db_path=db_path,campaign_id=action["campaign_id"],
                sequence=action["sequence"],quote_id=quote["quote_id"],
                consumed=consumed,candidate=candidate,
                policy_wallet=policy.wallet,protected_wallet=protected_wallet,
                private_key=private_key,rpc_url=rpc_url,nonce=nonce_pending,
                gas_price_wei=rpc.gas_price_wei(),approved_vaults=approved_vaults,
            )


def main(argv=None):
    p=argparse.ArgumentParser(description="Dedicated-wallet one-step unattended executor (experimental)")
    p.add_argument("--wallet",required=True)
    p.add_argument("--protected-wallet",required=True,
                   help="Personal wallet that may NEVER be used as the signer")
    p.add_argument("--allowlist",required=True)
    p.add_argument("--db",default="data/lapidus.sqlite3")
    p.add_argument("--max-capital-usdc",type=int,default=10)
    p.add_argument("--execute-live",action="store_true")
    args=p.parse_args(argv)
    if not args.execute_live:
        print("DISABLED: requires explicit --execute-live. No transaction submitted.")
        return 0
    secret=os.environ.get("LVS_AUTO_PRIVATE_KEY")
    rpc_url=os.environ.get("BASE_RPC_URL")
    if not secret or not rpc_url:
        print("BLOCKED: dedicated signing key and HTTPS BASE_RPC_URL required",file=sys.stderr)
        return 1
    try:
        approved=read_policy(args.allowlist).vault_addresses
        result=execute_once(
            db_path=args.db,wallet=args.wallet,protected_wallet=args.protected_wallet,
            approved_vaults=approved,allowlist_path=args.allowlist,rpc_url=rpc_url,private_key=secret,
            max_capital_units=args.max_capital_usdc*1_000_000)
        # Never log RPC URLs, raw transactions, or secrets.
        print(json.dumps(result,indent=2))
        return 0
    except Exception:
        print("AUTO EXECUTOR HALTED: inspect SQLite journal and chain receipt; never retry an uncertain send.",
              file=sys.stderr)
        return 1


if __name__=="__main__":
    raise SystemExit(main())
