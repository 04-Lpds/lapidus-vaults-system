"""Read-only exact on-chain transaction matching to a saved LVS manifest.

Matches a transaction's calldata/recipient/owner/sender against one immutable
review candidate. Never signs, broadcasts, or treats a match as user approval.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
import os
import sqlite3
import sys

from .allocator import BASE_CHAIN
from .rpc_verify import ReadOnlyRPC, RPCError
from .wallet_plan import read_policy
from .manifest_preflight import load_manifest, validate_manifest
from .receipt_inspect import inspect_transaction, _hex_int

def match_candidate(rpc, manifest: dict, sequence: int, tx_hash: str, vaults: tuple[str,...]) -> dict:
    """Compare actual Base transaction fields to an exact, hash-validated candidate."""
    if type(sequence) is not int or sequence < 1:
        raise ValueError("Invalid candidate sequence")
    # Integrity-validate a historically saved proposal without applying freshness:
    # a confirmed transaction is necessarily in the past. Freshness and explicit
    # user authorization must be independently enforced BEFORE submission.
    original_block = manifest["observed_block"]
    validation = validate_manifest(manifest, wallet=manifest["wallet"], vaults=vaults,
                                   current_block=original_block, max_age_blocks=0)
    if validation["status"] != "FRESH_INDEPENDENT_CALL_CHECKS_ONLY":
        raise ValueError("Invalid saved manifest")
    candidates = manifest["candidates"]
    if sequence > len(candidates):
        raise ValueError("Sequence not in manifest")
    target = candidates[sequence-1]
    inspected = inspect_transaction(rpc, tx_hash, manifest["wallet"], vaults)
    if not inspected.get("verified"):
        return {"status": inspected["status"], "matched": False, "manifest_sha256": manifest["manifest_sha256"]}
    tx = rpc.request("eth_getTransactionByHash", [tx_hash])
    if tx is None:
        raise RPCError("Transaction disappeared after receipt inspection")
    exact_match = (
        str(tx.get("from", "")).lower() == target["from_address"].lower()
        and str(tx.get("to", "")).lower() == target["to_address"].lower()
        and str(tx.get("input", "")).lower() == target["data"].lower()
        and _hex_int(tx.get("value"), "tx value") == target["value_wei"]
        and _hex_int(tx.get("chainId"), "tx chain ID") == BASE_CHAIN
        and inspected["receipt_block"] >= manifest["observed_block"]
    )
    return {
        "status": "EXACT_ONCHAIN_CANDIDATE_MATCH" if exact_match else "TRANSACTION_MANIFEST_MISMATCH",
        "matched": exact_match, "tx_hash": tx_hash.lower(),
        "manifest_sha256": manifest["manifest_sha256"], "sequence": sequence,
        "receipt_block": inspected["receipt_block"], "confirmations": inspected["confirmations"],
        "authorization_proven": False, "financial_reconciliation_complete": False,
    }


def save_match(db: str, manifest_id: int, result: dict) -> int:
    with sqlite3.connect(db, timeout=5) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS transaction_candidate_matches(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            observed_at TEXT NOT NULL,
            manifest_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            result_json TEXT NOT NULL)""")
        row=conn.execute("""INSERT INTO transaction_candidate_matches
            (observed_at, manifest_id, status, result_json) VALUES (?, ?, ?, ?)""",
            (datetime.now(timezone.utc).isoformat(), manifest_id, result["status"],
             json.dumps(result, sort_keys=True)))
        return int(row.lastrowid)


def main(argv=None) -> int:
    p=argparse.ArgumentParser(description="Match Base receipt against an immutable LVS review intent")
    p.add_argument("--manifest-id", type=int, required=True)
    p.add_argument("--sequence", type=int, required=True)
    p.add_argument("--tx-hash", required=True)
    p.add_argument("--allowlist", required=True)
    p.add_argument("--db", default="data/lapidus.sqlite3")
    args=p.parse_args(argv)
    try:
        if not os.environ.get("BASE_RPC_URL"):
            raise ValueError("BASE_RPC_URL required")
        manifest=load_manifest(args.db,args.manifest_id)
        vaults=read_policy(args.allowlist).vault_addresses
        with ReadOnlyRPC(os.environ["BASE_RPC_URL"]) as rpc:
            result=match_candidate(rpc,manifest,args.sequence,args.tx_hash,vaults)
        result["match_id"]=save_match(args.db,args.manifest_id,result)
        print(json.dumps(result,indent=2))
        print("READ ONLY; exact matching does not prove authorization or financial reconciliation.")
        return 0 if result["matched"] else 2
    except (ValueError, OSError, sqlite3.Error, RPCError) as exc:
        print(f"TRANSACTION MATCH FAILED: {type(exc).__name__}",file=sys.stderr)
        return 1

if __name__=="__main__":
    raise SystemExit(main())
