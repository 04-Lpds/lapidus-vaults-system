"""Local-only Anvil campaign-state and SQLite persistence integration rehearsal.

Exercises real forked withdrawal, approval, and deposit plus the persisted
campaign coordinator. Does NOT exercise production quote journal, browser UI,
or receipt_bridge, and is NOT an unattended signer. Never connects to a
remote RPC or uses real wallet keys.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import tempfile
from pathlib import Path

from .fork_roundtrip import (
    ForkRPC, VAULTS, BASE_USDC, ASSET, APPROVE, WITHDRAW, DEPOSIT,
    addr_word, word,
)
from .unsigned_candidates import UnsignedCandidate
from .operations.campaigns import create_campaign, read_campaign, record_receipt_status
from .operations.next_action import next_campaign_action


def run_campaign_fork(rpc: ForkRPC, wallet: str, source: str,
                      destination: str, amount: int, db_path: str) -> dict:
    # Strict preflight before any Anvil state mutations.
    addr_word(wallet)
    wallet = wallet.lower()
    if source not in VAULTS or destination not in VAULTS or source == destination:
        raise ValueError("Distinct allowlisted vaults required")
    if type(amount) is not int or not 0 < amount <= 10_000_000:
        raise ValueError("Amount must be 1..10 USDC")
    if int(rpc.call("eth_chainId", []), 16) != 8453:
        raise RuntimeError("Only local Base fork is supported")
    for v in (source, destination):
        code = rpc.call("eth_getCode", [v, "latest"])
        if not isinstance(code, str) or len(code) < 10:
            raise RuntimeError("Missing forked vault bytecode")
        if "0x" + format(rpc.uint(v, ASSET), "040x") != BASE_USDC:
            raise RuntimeError("Vault underlying is not Base native USDC")
    if rpc.balance(source, wallet) == 0:
        raise RuntimeError("Source wallet has no vault shares")

    calldata = (
        WITHDRAW + word(amount) + addr_word(wallet) + addr_word(wallet),
        APPROVE + addr_word(destination) + word(amount),
        DEPOSIT + word(amount) + addr_word(wallet),
    )
    operations = (
        ("withdraw_intent", source), ("approve_intent", BASE_USDC),
        ("deposit_intent", destination),
    )
    candidates = tuple(
        UnsignedCandidate(
            sequence=i + 1, depends_on=() if i == 0 else (i,),
            method=method, chain_id=8453, from_address=wallet,
            to_address=target, value_wei=0, data=calldata[i],
            requested_assets_units=amount,
        ) for i, (method, target) in enumerate(operations)
    )
    campaign_id = "fork-campaign-1"
    created = create_campaign(db_path, campaign_id, candidates)
    if created["steps"][0]["status"] != "READY_FOR_MANUAL_PREFLIGHT":
        raise RuntimeError("First campaign step is not ready")

    initial_cash = rpc.balance(BASE_USDC, wallet)
    initial_source = rpc.balance(source, wallet)
    initial_dest = rpc.balance(destination, wallet)
    receipts = []
    rpc.call("anvil_setBalance", [wallet, hex(10**18)])
    rpc.call("anvil_impersonateAccount", [wallet])
    try:
        for i, ((method, target), data) in enumerate(zip(operations, calldata), 1):
            # Reopen database before every action: exercise persisted state.
            projected = next_campaign_action(
                db_path, wallet=wallet, approved_vaults=VAULTS
            )
            if (projected["status"] != "READY_FOR_MANUAL_PREFLIGHT" or
                    projected["sequence"] != i or projected["method"] != method):
                raise RuntimeError(f"Campaign did not unlock step {i}")
            pre_cash = rpc.balance(BASE_USDC, wallet)
            pre_source = rpc.balance(source, wallet)
            pre_dest = rpc.balance(destination, wallet)
            receipt = rpc.transact(target, wallet, data, step=f"campaign {method}")
            tx_hash = receipt["transactionHash"]
            post_cash = rpc.balance(BASE_USDC, wallet)
            post_source = rpc.balance(source, wallet)
            post_dest = rpc.balance(destination, wallet)
            # A successful receipt alone is not sufficient: check expected
            # balance movements for this exact candidate.
            if i == 1:
                valid = (post_cash - pre_cash == amount and
                         post_source < pre_source and post_dest == pre_dest)
            elif i == 2:
                allowance = rpc.uint(
                    BASE_USDC, "0xdd62ed3e" + addr_word(wallet) +
                    addr_word(destination)
                )
                valid = (post_cash == pre_cash and post_source == pre_source
                         and post_dest == pre_dest and allowance >= amount)
            else:
                valid = (pre_cash - post_cash == amount and
                         post_dest > pre_dest and post_source == pre_source)
            if not valid:
                raise RuntimeError(f"Campaign step {i} balance/allowance mismatch")
            # This test-only bridge checks receipt success and explicit balance
            # movements; it does NOT claim production receipt_bridge coverage.
            record_receipt_status(
                db_path, campaign_id, i, "RECEIPT_AND_BLOCK_MATCH", tx_hash
            )
            receipts.append({"sequence": i, "method": method, "tx_hash": tx_hash})
    finally:
        rpc.call("anvil_stopImpersonatingAccount", [wallet])

    completed = read_campaign(db_path, campaign_id)
    final_cash = rpc.balance(BASE_USDC, wallet)
    final_source = rpc.balance(source, wallet)
    final_dest = rpc.balance(destination, wallet)
    with sqlite3.connect(db_path) as db:
        campaign_rows = db.execute(
            "SELECT COUNT(*) FROM rebalance_campaigns"
        ).fetchone()[0]
        step_rows = db.execute(
            "SELECT COUNT(*) FROM rebalance_campaign_steps WHERE campaign_id=?",
            (campaign_id,),
        ).fetchone()[0]
    if (completed["state"] != "COMPLETED" or
            len([s for s in completed["steps"] if s["status"] == "CONFIRMED"]) != 3 or
            step_rows != 3 or campaign_rows != 1 or
            final_cash != initial_cash or final_source >= initial_source or
            final_dest <= initial_dest):
        raise RuntimeError("Campaign completion or persistent final portfolio mismatch")

    return {
        "status": "FORK_CAMPAIGN_STATE_AND_SQLITE_PASSED",
        "campaign_id": campaign_id, "campaign_state": completed["state"],
        "sqlite_db": db_path, "sqlite_campaign_rows": campaign_rows,
        "sqlite_step_rows": step_rows, "actor": wallet,
        "cash_initial_units": initial_cash, "cash_final_units": final_cash,
        "source_shares_initial": initial_source, "source_shares_final": final_source,
        "destination_shares_initial": initial_dest,
        "destination_shares_final": final_dest, "receipts": receipts,
        "scope": "Fork transactions + campaigns/next_action SQLite persistence",
        "not_tested": "Live quote journal/receipt_bridge, restart recovery UI, autonomous execution",
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Local Anvil campaign and SQLite rehearsal")
    p.add_argument("--fork-rpc", default="http://127.0.0.1:8545")
    p.add_argument("--wallet", required=True)
    p.add_argument("--source", required=True, choices=VAULTS)
    p.add_argument("--destination", required=True, choices=VAULTS)
    p.add_argument("--amount-usdc", type=int, default=1, choices=range(1, 11))
    p.add_argument("--db", help="Optional disposable SQLite test DB; must not be production DB")
    args = p.parse_args(argv)
    try:
        if args.db:
            candidate = Path(args.db).resolve()
            if candidate.exists() or candidate.name == "lapidus.sqlite3":
                raise ValueError("Provide a NEW, disposable SQLite path only")
            candidate.parent.mkdir(parents=True, exist_ok=True)
            db_path = str(candidate)
            with ForkRPC(args.fork_rpc) as rpc:
                result = run_campaign_fork(
                    rpc, args.wallet, args.source, args.destination,
                    args.amount_usdc * 10**6, db_path,
                )
        else:
            with tempfile.TemporaryDirectory(prefix="lvs-fork-campaign-") as d:
                db_path = str(Path(d) / "campaign_test.sqlite3")
                with ForkRPC(args.fork_rpc) as rpc:
                    result = run_campaign_fork(
                        rpc, args.wallet, args.source, args.destination,
                        args.amount_usdc * 10**6, db_path,
                    )
                result["sqlite_db"] = "temporary test database (removed on exit)"
        print(json.dumps(result, indent=2))
        print("LOCAL FORK ONLY. NO REAL BASE TRANSACTIONS.")
        return 0
    except Exception as exc:
        print(f"FORK CAMPAIGN TEST FAILED: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
