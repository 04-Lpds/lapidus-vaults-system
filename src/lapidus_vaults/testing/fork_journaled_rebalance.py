"""Persist, reload, validate and execute exact saved LVS intents on localhost Anvil.

Uses test actor only. No production signer, no remote RPC writes, no live wallet.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys

import httpx

from ..fork_roundtrip import ForkRPC, VAULTS, ACTOR, BASE_USDC, addr_word
from .fork_cross_rebalance import fork_snapshot, TEST_AMOUNT
from .fork_candidate_rebalance import (
    bootstrap_local_fork, execute_candidates_locally, POLICY, GAS_CAP_PER_ACTION, ALLOWANCE
)
from ..wallet_plan import build_rebalance
from ..safety_plan import build_safety_plan
from ..unsigned_candidates import UnsignedCandidate
from ..transaction_review import prepare_manifest, store_manifest
from ..manifest_preflight import load_manifest, validate_manifest


def save_fork_result(db_path: str, *, manifest_id: int, result: dict) -> int:
    target = Path(db_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(target, timeout=5) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS fork_manifest_execution_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            manifest_id INTEGER NOT NULL,
            result_json TEXT NOT NULL
        )""")
        cur = db.execute(
            "INSERT INTO fork_manifest_execution_results (created_at, manifest_id, result_json) VALUES (?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(), manifest_id, json.dumps(result, sort_keys=True))
        )
        return int(cur.lastrowid)


def execute_saved_manifest(rpc: ForkRPC, manifest: dict, *, current_block: int) -> list[dict]:
    validation = validate_manifest(manifest, wallet=ACTOR, vaults=VAULTS,
                                   current_block=current_block, max_age_blocks=20)
    if validation["status"] != "FRESH_INDEPENDENT_CALL_CHECKS_ONLY":
        raise RuntimeError("Saved manifest is stale; do not execute")
    candidates = tuple(UnsignedCandidate(
        **{**c, "depends_on": tuple(c["depends_on"])}
    ) for c in manifest["candidates"])
    if tuple(c.method for c in candidates) != (
        "withdraw_intent", "approve_intent", "deposit_intent"
    ):
        raise RuntimeError("Unexpected test scenario")
    return execute_candidates_locally(rpc, candidates)


def run_journaled_fork(rpc: ForkRPC, holder: str, db_path: str) -> dict:
    bootstrap_local_fork(rpc, holder)
    before = fork_snapshot(rpc)
    preview = build_rebalance(before, POLICY)
    allowances = {
        vault: rpc.uint(BASE_USDC, ALLOWANCE + addr_word(ACTOR) + addr_word(vault))
        for vault in VAULTS
    }
    observed_block = int(rpc.call("eth_blockNumber", []), 16)
    # fork_snapshot() uses rpc_block=0; annotate local fork's actual block for freshness.
    preview = replace(preview, snapshot=replace(before, rpc_block=observed_block))
    plan = build_safety_plan(preview, allowances=allowances,
                             max_gas_units=GAS_CAP_PER_ACTION,
                             gas_price_wei=12_000_000,
                             available_eth_wei=10**18, l1_fee_budget_wei=10**13)
    manifest = prepare_manifest({"plan": asdict(plan)}, VAULTS, cap_usdc=10)
    if not manifest["candidates"]:
        raise RuntimeError("Expected nonempty test proposal")
    manifest_id = store_manifest(db_path, manifest)
    saved = load_manifest(db_path, manifest_id)
    if saved != json.loads(json.dumps(manifest)):
        raise RuntimeError("Persisted manifest roundtrip mismatch")

    try:
        receipts = execute_saved_manifest(rpc, saved, current_block=int(rpc.call("eth_blockNumber", []), 16))
        after = fork_snapshot(rpc)
        deviations = {
            change.vault_address: next(
                h.estimated_assets_units for h in after.holdings
                if h.vault_address == change.vault_address
            ) - change.target_units for change in preview.changes
        }
        if after.wallet_usdc_units != preview.target_idle_units or any(abs(v) > 2 for v in deviations.values()):
            raise RuntimeError("Final positions failed expected rebalance reconciliation")
        if any(h.share_balance == 0 for h in after.holdings):
            raise RuntimeError("Expected positive shares in both vaults")
        result = {
            "status": "FORK_ONLY_JOURNALED_REBALANCE_SUCCEEDED",
            "manifest_id": manifest_id,
            "manifest_sha256": manifest["manifest_sha256"],
            "receipt_records": receipts,
            "actual_idle_units": after.wallet_usdc_units,
            "target_idle_units": preview.target_idle_units,
            "vault_deviations_units": deviations,
            "live_broadcast_enabled": False,
        }
    except Exception as exc:
        # Record failure with no secrets or RPC URLs, then halt. No replay.
        save_fork_result(db_path, manifest_id=manifest_id,
                         result={"status": "FORK_ONLY_FAILED_HALT", "failure_type": type(exc).__name__})
        raise
    result["fork_result_id"] = save_fork_result(db_path, manifest_id=manifest_id, result=result)
    return result


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Execute persisted LVS review on disposable localhost Base fork")
    p.add_argument("--fork-rpc", default="http://127.0.0.1:8545")
    p.add_argument("--usdc-holder", required=True)
    p.add_argument("--db", default="data/fork_manifest_tests.sqlite3")
    args = p.parse_args(argv)
    try:
        with ForkRPC(args.fork_rpc) as rpc:
            result = run_journaled_fork(rpc, args.usdc_holder, args.db)
        print(json.dumps(result, indent=2))
        print("ANVIL FORK ONLY — NO BASE MAINNET SIGNING OR BROADCAST")
        return 0
    except (RuntimeError, ValueError, OSError, sqlite3.Error, httpx.HTTPError) as exc:
        print(f"JOURNALED FORK TEST FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
