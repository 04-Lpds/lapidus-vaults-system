"""Read-only operating-loop check for LVS: plan, classify drift, append audit journal.

One-shot command suitable for a future Windows Task Scheduler invocation.
Never signs, broadcasts, or automatically executes a rebalance.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys

from .allocator import address_ok
from .client import MorphoClient, MorphoAPIError
from .rpc_verify import ReadOnlyRPC, RPCError
from .live_readonly_plan import build_live_readonly_plan


def drift_summary(report: dict, *, threshold_bps: int) -> dict:
    """Flag desired absolute portfolio displacement relative to in-scope capital."""
    if type(threshold_bps) is not int or not 0 <= threshold_bps <= 10_000:
        raise ValueError("Drift threshold must be 0..10000 basis points")
    p = report["plan"]
    capital = p["capital_units"]
    changes = [a for a in p["actions"] if a["kind"] in {"withdraw_intent", "deposit_intent"}]
    gross_units = sum(a["amount_units"] for a in changes)
    # Gross single-sided displacement is half the sum of absolute portfolio
    # differences including idle cash. We intentionally report gross actions
    # separately, and use maximum individual vault deviation for the trigger.
    # Reconstruct each action amount as the delta for its vault.
    max_vault_delta = max((a["amount_units"] for a in changes), default=0)
    max_vault_bps = (max_vault_delta * 10_000 // capital) if capital else 0
    status = "REBALANCE_REVIEW" if max_vault_bps >= threshold_bps and changes else "NO_REBALANCE_NEEDED"
    return {
        "status": status,
        "max_vault_deviation_bps_floor": max_vault_bps,
        "gross_transaction_assets_units": gross_units,
        "threshold_bps": threshold_bps,
        "capital_units": capital,
        "action_count": len(p["actions"]),
    }


def save_observation(db_path: str, *, wallet: str, report: dict, assessment: dict) -> int:
    """Append an operational audit record; no RPC URLs or secrets persisted."""
    target = Path(db_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(target, timeout=5) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS monitoring_observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            observed_at TEXT NOT NULL,
            chain_id INTEGER NOT NULL,
            rpc_block INTEGER NOT NULL,
            wallet_address TEXT NOT NULL,
            status TEXT NOT NULL,
            capital_units TEXT NOT NULL,
            max_vault_deviation_bps INTEGER NOT NULL,
            report_json TEXT NOT NULL
        )""")
        cur = db.execute("""INSERT INTO monitoring_observations
            (observed_at, chain_id, rpc_block, wallet_address, status,
             capital_units, max_vault_deviation_bps, report_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (datetime.now(timezone.utc).isoformat(), 8453, report["plan"]["observed_block"],
             wallet.lower(), assessment["status"], str(assessment["capital_units"]),
             assessment["max_vault_deviation_bps_floor"],
             json.dumps({"assessment": assessment, "plan": report["plan"],
                         "verification": report["verification"]}, sort_keys=True)))
        return int(cur.lastrowid)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="One-shot, read-only LVS monitoring and audit journal")
    p.add_argument("--wallet", required=True)
    p.add_argument("--allowlist", required=True)
    p.add_argument("--db", default="data/lapidus.sqlite3")
    p.add_argument("--drift-bps", type=int, default=500,
                   help="Review if largest individual vault target discrepancy reaches this many bps")
    args = p.parse_args(argv)
    try:
        if not address_ok(args.wallet):
            raise ValueError("Invalid public wallet")
        if not os.environ.get("BASE_RPC_URL"):
            raise ValueError("BASE_RPC_URL required")
        with ReadOnlyRPC(os.environ["BASE_RPC_URL"]) as rpc, MorphoClient() as morpho:
            report = build_live_readonly_plan(args.wallet, args.allowlist, rpc, morpho)
        assessment = drift_summary(report, threshold_bps=args.drift_bps)
        number = save_observation(args.db, wallet=args.wallet, report=report, assessment=assessment)
        print(json.dumps({"observation_id": number, "rpc_block": report["plan"]["observed_block"],
                          **assessment}, indent=2))
        print("READ ONLY: monitoring does not authorize or broadcast transactions.")
        return 0
    except (ValueError, OSError, sqlite3.Error, RPCError, MorphoAPIError) as exc:
        print(f"MONITOR CHECK FAILED: {type(exc).__name__}. No successful observation recorded.",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
