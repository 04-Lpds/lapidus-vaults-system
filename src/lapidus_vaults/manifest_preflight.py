"""Fail-closed, read-only checks for saved LVS manual-review manifests.

No signing, no transaction execution. Individual eth_call results are NOT
sequential simulation, capacity proof, or permission to use wallet software.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import sqlite3
import sys

from .allocator import BASE_CHAIN, BASE_USDC, address_ok
from .client import MorphoClient, MorphoAPIError
from .rpc_verify import ReadOnlyRPC, RPCError, _address_word, BALANCE_OF
from .wallet_plan import read_policy, read_wallet_snapshot

MAX_BLOCK_AGE = 20


def validate_manifest(manifest: dict, *, wallet: str, vaults: tuple[str, ...],
                      current_block: int, max_age_blocks: int = MAX_BLOCK_AGE) -> dict:
    """Authenticate local content hash, restrict scope, reject obsolete observations."""
    if not isinstance(manifest, dict) or manifest.get("schema") != "lvs-manual-review-v1" or manifest.get("status") != "REVIEW_ONLY_NO_EXECUTION":
        raise ValueError("Unknown or non-review manifest")
    body = {k: v for k, v in manifest.items() if k not in {"manifest_sha256", "status"}}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if digest != manifest.get("manifest_sha256"):
        raise ValueError("Manifest content hash mismatch")
    if not address_ok(wallet) or manifest.get("wallet") != wallet.lower() or manifest.get("chain_id") != BASE_CHAIN:
        raise ValueError("Wallet or chain mismatch")
    if list(vaults) != manifest.get("vault_allowlist") or not all(address_ok(v) for v in vaults):
        raise ValueError("Vault allowlist mismatch")
    if type(max_age_blocks) is not int or not 0 <= max_age_blocks <= 1000:
        raise ValueError("Invalid freshness policy")
    observed = manifest.get("observed_block")
    if type(observed) is not int or type(current_block) is not int or not 0 <= observed <= current_block:
        raise ValueError("Invalid or future observation block")
    age = current_block - observed
    if age > max_age_blocks:
        return {"status": "STALE_REPLAN_REQUIRED", "block_age": age, "independent_calls": [],
                "authorization": False}
    actions = manifest.get("candidates")
    if not isinstance(actions, list) or len(actions) > 30:
        raise ValueError("Invalid candidate set")
    for seq, action in enumerate(actions, 1):
        if not isinstance(action, dict) or action.get("sequence") != seq or action.get("chain_id") != BASE_CHAIN:
            raise ValueError("Malformed action order or chain")
        if action.get("from_address") != wallet.lower() or action.get("status") != "UNSIGNED_UNSIMULATED_DO_NOT_SUBMIT":
            raise ValueError("Unexpected action sender/status")
        if type(action.get("requested_assets_units")) is not int or not 0 < action["requested_assets_units"] <= manifest["max_action_units"]:
            raise ValueError("Action exceeds manifest cap")
        if type(action.get("value_wei")) is not int or action["value_wei"] != 0:
            raise ValueError("Candidate transfers ETH")
        if action.get("to_address") not in set(vaults) | {BASE_USDC}:
            raise ValueError("Unrecognized destination")
        if not isinstance(action.get("depends_on"), list) or any(type(d) is not int or d >= seq or d < 1 for d in action["depends_on"]):
            raise ValueError("Invalid action dependencies")
        data = action.get("data")
        if not isinstance(data, str) or not data.startswith("0x") or len(data) % 2:
            raise ValueError("Malformed calldata")
        try:
            bytes.fromhex(data[2:])
        except ValueError as exc:
            raise ValueError("Invalid calldata hex") from exc
        # Re-check ABI encoding exactly rather than trusting mutable SQLite rows.
        from .rpc_verify import _word
        from .unsigned_candidates import APPROVE, DEPOSIT, WITHDRAW
        from .rpc_verify import _address_word
        amount = action["requested_assets_units"]
        dest = action["to_address"]
        kind = action.get("method")
        if kind == "approve_intent":
            expected = APPROVE + _address_word(action.get("to_address") if False else action.get("vault", "")) if False else None
            # Approval spender is encoded inside the payload; require an approved vault.
            if dest != BASE_USDC or len(data) != 138:
                raise ValueError("Invalid approval destination/length")
            spender = "0x" + data[34:74]
            if spender not in vaults:
                raise ValueError("Approval spender outside allowlist")
            expected = APPROVE + _address_word(spender) + _word(amount)
        elif kind == "deposit_intent":
            if dest not in vaults:
                raise ValueError("Invalid deposit destination")
            expected = DEPOSIT + _word(amount) + _address_word(wallet)
        elif kind == "withdraw_intent":
            if dest not in vaults:
                raise ValueError("Invalid withdrawal destination")
            expected = WITHDRAW + _word(amount) + _address_word(wallet) + _address_word(wallet)
        else:
            raise ValueError("Unsupported action type")
        if data.lower() != expected.lower():
            raise ValueError("Calldata does not match bounded intent")
    return {"status": "FRESH_INDEPENDENT_CALL_CHECKS_ONLY", "block_age": age,
            "independent_calls": [], "authorization": False}


def inspect_saved_manifest(rpc: ReadOnlyRPC, manifest: dict, *, wallet: str,
                           vaults: tuple[str, ...], max_age_blocks: int = MAX_BLOCK_AGE) -> dict:
    if rpc.chain_id() != BASE_CHAIN:
        raise RPCError("Wrong chain")
    current_block = rpc.block_number()
    result = validate_manifest(manifest, wallet=wallet, vaults=vaults,
                               current_block=current_block, max_age_blocks=max_age_blocks)
    if result["status"] == "STALE_REPLAN_REQUIRED":
        return result
    # Reconfirm wallet cash at the current block. Do not pretend prior transaction
    # effects exist: independent calls do not simulate earlier approvals/withdrawals.
    balance = rpc.call_uint(BASE_USDC, BALANCE_OF, current_block, _address_word(wallet))
    result["current_wallet_usdc_units"] = balance
    checks = []
    for c in manifest["candidates"]:
        if c["method"] == "approve_intent":
            checks.append({"sequence": c["sequence"], "status": "NOT_SIMULATED_APPROVAL"})
            continue
        try:
            rpc.simulate_call(wallet, c["to_address"], c["data"], current_block)
            status = "INDEPENDENT_ETH_CALL_SUCCEEDED_ONLY"
        except RPCError:
            status = "INDEPENDENT_ETH_CALL_REVERTED_OR_FAILED"
        checks.append({"sequence": c["sequence"], "status": status})
    result["independent_calls"] = checks
    result["status"] = "REVIEW_ONLY_NOT_EXECUTABLE"
    return result


def load_manifest(db_path: str, row_id: int) -> dict:
    if type(row_id) is not int or row_id < 1:
        raise ValueError("Positive manifest ID required")
    with sqlite3.connect(db_path) as db:
        row = db.execute("SELECT payload_json FROM transaction_review_manifests WHERE id=?", (row_id,)).fetchone()
    if row is None:
        raise ValueError("Manifest not found")
    return json.loads(row[0])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Review saved LVS proposal freshness (never executes)")
    parser.add_argument("--manifest-id", type=int, required=True)
    parser.add_argument("--wallet", required=True)
    parser.add_argument("--allowlist", required=True)
    parser.add_argument("--db", default="data/lapidus.sqlite3")
    parser.add_argument("--max-age-blocks", type=int, default=MAX_BLOCK_AGE)
    args = parser.parse_args(argv)
    try:
        if not os.environ.get("BASE_RPC_URL"):
            raise ValueError("BASE_RPC_URL required")
        manifest = load_manifest(args.db, args.manifest_id)
        policy = read_policy(args.allowlist)
        with ReadOnlyRPC(os.environ["BASE_RPC_URL"]) as rpc:
            result = inspect_saved_manifest(rpc, manifest, wallet=args.wallet,
                                            vaults=policy.vault_addresses,
                                            max_age_blocks=args.max_age_blocks)
        print(json.dumps({"manifest_id": args.manifest_id, **result}, indent=2))
        print("READ ONLY. NO SIGNING, NO BROADCAST, NO EXECUTION APPROVAL.")
        return 0 if result["status"] == "REVIEW_ONLY_NOT_EXECUTABLE" else 2
    except (ValueError, OSError, sqlite3.Error, RPCError, MorphoAPIError) as exc:
        print(f"MANIFEST PREFLIGHT FAILED: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
