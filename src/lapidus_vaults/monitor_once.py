"""Read-only operating-loop check for LVS: plan, classify drift, append audit journal.

One-shot command suitable for a future Windows Task Scheduler invocation.
Never signs, broadcasts, or automatically executes a rebalance.
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

from .allocator import address_ok
from .client import MorphoClient, MorphoAPIError
from .rpc_verify import ReadOnlyRPC, RPCError
from .wallet_plan import read_policy, read_wallet_snapshot, build_rebalance


def observation_report(wallet: str, allowlist: str, rpc, morpho) -> dict:
    """Monitor holdings without requiring ETH for future execution intentions."""
    policy = read_policy(allowlist)
    snapshot = read_wallet_snapshot(wallet, policy, rpc, morpho)
    preview = build_rebalance(snapshot, policy)
    return {
        "verification": "Morpho API / pinned Base RPC basic verification passed",
        "plan": {
            "chain_id": snapshot.chain_id,
            "observed_block": snapshot.rpc_block,
            "capital_units": preview.capital_units,
            "wallet_usdc_units": snapshot.wallet_usdc_units,
            "target_idle_units": preview.target_idle_units,
            "changes": [
                {"vault_address": c.vault_address, "current_units": c.current_units,
                 "target_units": c.target_units, "delta_units": c.delta_units}
                for c in preview.changes
            ],
            "actions": [
                {"kind": "withdraw_intent" if c.delta_units < 0 else "deposit_intent",
                 "amount_units": abs(c.delta_units), "vault": c.vault_address}
                for c in preview.changes if c.delta_units != 0
            ],
        },
    }


def write_alert_status(path: str, payload: dict) -> None:
    """Atomic local JSON status artifact for an external notifier to inspect."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    try:
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(target)
    finally:
        tmp.unlink(missing_ok=True)


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



@contextmanager
def exclusive_monitor_lock(db_path: str):
    """O_EXCL prevents simultaneous monitoring of the same journal.

    A crash can leave a lock behind: fail closed and require operator review.
    No automatic stale-lock deletion, as that can race with a slow valid run.
    """
    lock_path = Path(str(db_path) + ".monitor.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise RuntimeError(f"Monitor already running or stale lock at {lock_path}") from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(f"pid={os.getpid()}\\nstarted_utc={datetime.now(timezone.utc).isoformat()}\\n")
        yield
    finally:
        lock_path.unlink(missing_ok=True)


def save_monitor_failure(db_path: str, *, wallet: str, failure_code: str) -> int:
    """Audit failed checks independently of successful monitoring observations."""
    if failure_code not in {"INPUT_ERROR", "RPC_ERROR", "API_ERROR", "DB_ERROR", "RUN_ERROR"}:
        raise ValueError("Invalid sanitized failure code")
    target = Path(db_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(target, timeout=5) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS monitoring_failures (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            occurred_at TEXT NOT NULL,
            wallet_address TEXT NOT NULL,
            failure_code TEXT NOT NULL
        )""")
        cur = db.execute("""INSERT INTO monitoring_failures
            (occurred_at, wallet_address, failure_code) VALUES (?, ?, ?)""",
            (datetime.now(timezone.utc).isoformat(), wallet.lower(), failure_code))
        return int(cur.lastrowid)

def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="One-shot, read-only LVS monitoring and audit journal")
    p.add_argument("--wallet", required=True)
    p.add_argument("--allowlist", required=True)
    p.add_argument("--db", default="data/lapidus.sqlite3")
    p.add_argument("--drift-bps", type=int, default=500,
                   help="Review if largest individual vault target discrepancy reaches this many bps")
    p.add_argument("--alert-file", default="data/monitor_status.json", help="Local JSON status for external alerting")
    p.add_argument("--create-campaign", action="store_true", help="Opt-in: journal a non-executable campaign when drift triggers")
    p.add_argument("--max-action-usdc", type=int, default=10)
    args = p.parse_args(argv)
    try:
        with exclusive_monitor_lock(args.db):
            try:
                if not address_ok(args.wallet):
                    raise ValueError("Invalid public wallet")
                if not os.environ.get("BASE_RPC_URL"):
                    raise ValueError("BASE_RPC_URL required")
                with ReadOnlyRPC(os.environ["BASE_RPC_URL"]) as rpc, MorphoClient() as morpho:
                    report = observation_report(args.wallet, args.allowlist, rpc, morpho)
                assessment = drift_summary(report, threshold_bps=args.drift_bps)
                number = save_observation(args.db, wallet=args.wallet, report=report, assessment=assessment)
                observed_positions = {c["vault_address"]: c["current_units"] for c in report["plan"]["changes"]}
                print(json.dumps({"observation_id": number, "rpc_block": report["plan"]["observed_block"],
                                  "wallet_usdc_units": report["plan"]["wallet_usdc_units"],
                                  "vault_estimated_assets_units": observed_positions,
                                  **assessment}, indent=2))
                if args.create_campaign and assessment["status"]=="REBALANCE_REVIEW":
                    if not 1<=args.max_action_usdc<=10:
                        raise ValueError("Campaign action cap must be 1..10 USDC")
                    from .live_readonly_plan import build_live_readonly_plan
                    from .operations.campaign_builder import build_campaign_from_live_plan
                    with ReadOnlyRPC(os.environ["BASE_RPC_URL"]) as rpc, MorphoClient() as morpho:
                        fresh=build_live_readonly_plan(args.wallet,args.allowlist,rpc,morpho)
                        campaign=build_campaign_from_live_plan(
                            args.db,f"observation-{number}",args.wallet,args.allowlist,fresh,rpc,
                            max_action_units=args.max_action_usdc*1_000_000)
                    print(json.dumps({"campaign_id":campaign["campaign_id"],"campaign_state":campaign["state"],
                                      "execution_enabled":False},indent=2))
                write_alert_status(args.alert_file, {"status": assessment["status"], "rpc_block": report["plan"]["observed_block"], "observation_id": number, "checked_at": datetime.now(timezone.utc).isoformat()})
                print("READ ONLY: monitoring does not authorize or broadcast transactions.")
                return 0
            except (ValueError, OSError, sqlite3.Error, RPCError, MorphoAPIError, RuntimeError) as exc:
                code = ("RPC_ERROR" if isinstance(exc, RPCError) else
                        "API_ERROR" if isinstance(exc, MorphoAPIError) else
                        "DB_ERROR" if isinstance(exc, sqlite3.Error) else
                        "INPUT_ERROR" if isinstance(exc, ValueError) else "RUN_ERROR")
                try:
                    save_monitor_failure(args.db, wallet=args.wallet, failure_code=code)
                except (OSError, sqlite3.Error):
                    print("MONITOR CHECK FAILED: failure journal unavailable.", file=sys.stderr)
                try:
                    write_alert_status(args.alert_file, {"status": "MONITOR_FAILED", "failure_code": code, "checked_at": datetime.now(timezone.utc).isoformat()})
                except OSError:
                    print("MONITOR CHECK FAILED: alert-file unavailable.", file=sys.stderr)
                print(f"MONITOR CHECK FAILED: {code}. No successful observation recorded.", file=sys.stderr)
                return 1
    except (RuntimeError, OSError) as exc:
        # Never bypass a running monitor or delete its lock automatically.
        print("MONITOR CHECK FAILED: concurrent run or lock/filesystem error.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
