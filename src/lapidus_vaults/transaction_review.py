"""Persistent manual-review ledger: no signing, wallet connection or broadcast.

An intention record is NOT approval. Receipt matching is read-only and does
not establish full pre/post cashflow reconciliation.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys

from .allocator import address_ok
from .client import MorphoClient, MorphoAPIError
from .live_readonly_plan import build_live_readonly_plan
from .rpc_verify import ReadOnlyRPC, RPCError
from .safety_plan import SafetyPlan, PlanAction
from .unsigned_candidates import make_unsigned_candidates, candidate_report
from .wallet_plan import read_policy


def prepare_manifest(report: dict, vaults: tuple[str, ...], *, cap_usdc: int) -> dict:
    raw = report["plan"]
    plan = SafetyPlan(**{**raw, "actions": tuple(
        PlanAction(**{**a, "depends_on": tuple(a["depends_on"])})
        for a in raw["actions"]), "assumptions": tuple(raw["assumptions"])})
    if type(cap_usdc) is not int or not 0 < cap_usdc <= 1000:
        raise ValueError("Explicit per-action cap must be 1..1000 USDC")
    actions = candidate_report(make_unsigned_candidates(
        plan, approved_vaults=vaults, max_assets_per_action_units=cap_usdc * 10**6))
    body = {"schema": "lvs-manual-review-v1", "wallet": plan.wallet.lower(),
            "chain_id": plan.chain_id, "observed_block": plan.observed_block,
            "capital_units": plan.capital_units, "max_action_units": cap_usdc * 10**6,
            "vault_allowlist": list(vaults), "candidates": actions,
            "limitations": ["NOT authorized, simulated or approved for execution",
                            "Not an EIP-1559 transaction bundle",
                            "Requires fresh per-step preflight and independent wallet review"]}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    body["manifest_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    body["status"] = "REVIEW_ONLY_NO_EXECUTION"
    return body


def store_manifest(db_path: str, manifest: dict) -> int:
    """Append immutable manifest; existing identical manifests retain distinct IDs."""
    target = Path(db_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(target, timeout=5) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS transaction_review_manifests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            manifest_sha256 TEXT NOT NULL,
            wallet_address TEXT NOT NULL,
            chain_id INTEGER NOT NULL,
            observed_block INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status='REVIEW_ONLY_NO_EXECUTION')
        )""")
        row = db.execute("""INSERT INTO transaction_review_manifests
            (created_at, manifest_sha256, wallet_address, chain_id, observed_block,
             payload_json, status) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (datetime.now(timezone.utc).isoformat(), manifest["manifest_sha256"],
             manifest["wallet"], manifest["chain_id"], manifest["observed_block"],
             json.dumps(manifest, sort_keys=True), "REVIEW_ONLY_NO_EXECUTION"))
        return int(row.lastrowid)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Persist non-executable LVS transaction review manifest")
    p.add_argument("--wallet", required=True)
    p.add_argument("--allowlist", required=True)
    p.add_argument("--db", default="data/lapidus.sqlite3")
    p.add_argument("--max-action-usdc", type=int, default=100)
    args = p.parse_args(argv)
    try:
        if not address_ok(args.wallet) or not os.environ.get("BASE_RPC_URL"):
            raise ValueError("Valid wallet and BASE_RPC_URL required")
        policy = read_policy(args.allowlist)
        with ReadOnlyRPC(os.environ["BASE_RPC_URL"]) as rpc, MorphoClient() as morpho:
            report = build_live_readonly_plan(args.wallet, args.allowlist, rpc, morpho)
        manifest = prepare_manifest(report, policy.vault_addresses, cap_usdc=args.max_action_usdc)
        manifest_id = store_manifest(args.db, manifest)
        print(json.dumps({"manifest_id": manifest_id, **manifest}, indent=2))
        print("REVIEW ONLY. DO NOT SUBMIT CANDIDATES TO A WALLET.")
        return 0
    except (ValueError, OSError, sqlite3.Error, RPCError, MorphoAPIError) as exc:
        print(f"REVIEW MANIFEST FAILED: {type(exc).__name__}. No wallet action taken.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
