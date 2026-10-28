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
from .operations.campaigns import create_campaign, read_campaign
from .operations.campaign_quote import quote_next_campaign_action
from .operations.receipt_bridge import bind_campaign_quote, advance_bound_campaign
from .live_deposit import DepositQuotes
from .live_withdraw import WithdrawalQuotes
from .rpc_verify import ReadOnlyRPC
from .operations.next_action import next_campaign_action



class LocalForkReadAdapter(ReadOnlyRPC):
    """Expose only read-only production RPC interface over validated ForkRPC.

    The underlying ForkRPC refuses non-loopback endpoints. Mutations still
    require a separate, explicitly invoked fork-only test call.
    """
    def __init__(self, fork):
        self.fork = fork

    def request(self, method, params):
        reads = {
            "eth_chainId", "eth_blockNumber", "eth_getCode", "eth_call",
            "eth_getBalance", "eth_estimateGas", "eth_gasPrice",
            "eth_getTransactionReceipt", "eth_getTransactionByHash",
        }
        if method not in reads:
            raise ValueError("Read adapter cannot execute transactions")
        return self.fork.call(method, params)


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
        read_rpc = LocalForkReadAdapter(rpc)
        deposits, withdrawals = DepositQuotes(), WithdrawalQuotes()
        for i, ((method, target), data) in enumerate(zip(operations, calldata), 1):
            projected = next_campaign_action(
                db_path, wallet=wallet, approved_vaults=VAULTS
            )
            if (projected["status"] != "READY_FOR_MANUAL_PREFLIGHT" or
                    projected["sequence"] != i or projected["method"] != method):
                raise RuntimeError(f"Campaign did not unlock step {i}")

            # Exact production quote path: pinned checks and eth_call simulation.
            quoted = quote_next_campaign_action(
                db_path, wallet=wallet, approved_vaults=VAULTS,
                rpc=read_rpc, deposits=deposits, withdrawals=withdrawals
            )
            if (quoted["sequence"] != i or quoted["data"] != data
                    or quoted["to"] != target or
                    quoted["stage"] + "_intent" != method):
                raise RuntimeError(f"Fresh quote differs from step {i}")

            engine = withdrawals if method == "withdraw_intent" else deposits
            consumed = engine.consume(read_rpc, quoted["quote_id"])
            if consumed["data"] != data or consumed["to"] != target:
                raise RuntimeError("Consumed quote changed campaign calldata")

            # Mirror the console's actual authorization schema and binding.
            # The transaction is then submitted only through ForkRPC (loopback).
            from datetime import datetime, timezone
            with sqlite3.connect(db_path) as db:
                db.execute("""CREATE TABLE IF NOT EXISTS live_deposit_authorizations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    prepared_at TEXT NOT NULL,
                    quote_id TEXT UNIQUE NOT NULL,
                    wallet TEXT NOT NULL,
                    vault TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    amount_units INTEGER NOT NULL,
                    transaction_json TEXT NOT NULL)""")
                db.execute("""INSERT INTO live_deposit_authorizations
                    (prepared_at,quote_id,wallet,vault,stage,amount_units,transaction_json)
                    VALUES (?,?,?,?,?,?,?)""", (
                        datetime.now(timezone.utc).isoformat(), quoted["quote_id"],
                        consumed["wallet"], consumed["vault"], consumed["stage"],
                        consumed["amount_units"], json.dumps(consumed, sort_keys=True)
                    ))
            bind_campaign_quote(db_path, campaign_id, i, quoted["quote_id"])

            receipt = rpc.transact(target, wallet, data, step=f"campaign {method}")
            tx_hash = receipt["transactionHash"]

            # Simulate application restart: in-memory consumed quotes are gone.
            # Recover the exact original authorization from SQLite.
            fresh_engine = WithdrawalQuotes() if method == "withdraw_intent" else DepositQuotes()
            with sqlite3.connect(db_path) as db:
                row = db.execute(
                    "SELECT transaction_json FROM live_deposit_authorizations WHERE quote_id=?",
                    (quoted["quote_id"],)
                ).fetchone()
            if row is None:
                raise RuntimeError("Cannot recover original journaled authorization")
            fresh_engine.consumed[quoted["quote_id"]] = json.loads(row[0])
            verified = fresh_engine.check_receipt(read_rpc, quoted["quote_id"], tx_hash)
            if verified["status"] != "RECEIPT_AND_BLOCK_MATCH":
                raise RuntimeError(f"Production receipt checker rejected step {i}: {verified['status']}")
            state = advance_bound_campaign(db_path, quoted["quote_id"], verified)
            if state is None:
                raise RuntimeError("Production bound receipt bridge did not advance")
            receipts.append({
                "sequence": i, "method": method, "tx_hash": tx_hash,
                "receipt_status": verified["status"], "recovered_from_sqlite": True
            })
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
        "status": "FORK_CAMPAIGN_RECEIPT_BRIDGE_PASSED",
        "campaign_id": campaign_id, "campaign_state": completed["state"],
        "sqlite_db": db_path, "sqlite_campaign_rows": campaign_rows,
        "sqlite_step_rows": step_rows, "actor": wallet,
        "cash_initial_units": initial_cash, "cash_final_units": final_cash,
        "source_shares_initial": initial_source, "source_shares_final": final_source,
        "destination_shares_initial": initial_dest,
        "destination_shares_final": final_dest, "receipts": receipts,
        "scope": "Production quote/consume, persisted authorization recovery, production receipt checker and receipt_bridge on local fork",
        "not_tested": "Browser MetaMask UI, real Base transaction submission, autonomous execution",
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
            d = tempfile.mkdtemp(prefix="lvs-fork-campaign-")
            db_path = str(Path(d) / "campaign_test.sqlite3")
            with ForkRPC(args.fork_rpc) as rpc:
                result = run_campaign_fork(
                    rpc, args.wallet, args.source, args.destination,
                    args.amount_usdc * 10**6, db_path,
                )
            result["sqlite_db"] = db_path
            result["sqlite_note"] = "Disposable test DB retained for inspection; delete when finished."
        print(json.dumps(result, indent=2))
        print("LOCAL FORK ONLY. NO REAL BASE TRANSACTIONS.")
        return 0
    except Exception as exc:
        print(f"FORK CAMPAIGN TEST FAILED: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
