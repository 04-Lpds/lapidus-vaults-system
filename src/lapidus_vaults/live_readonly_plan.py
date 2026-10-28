"""Read-only v0.5 mainnet planning: verified wallet/API, allowances, ETH and fee ceiling.

No transaction bytes, signing, secrets or broadcasting.
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from dataclasses import asdict

from .allocator import address_ok, display_usdc
from .client import MorphoClient, MorphoAPIError
from .rpc_verify import ReadOnlyRPC, RPCError, _address_word
from .wallet_plan import read_policy, read_wallet_snapshot, build_rebalance
from .safety_plan import build_safety_plan, SafetyPlan, PlanAction
from .unsigned_candidates import make_unsigned_candidates, candidate_report

ALLOWANCE = "0xdd62ed3e"
# Conservative *policy assumptions*, not measured execution gas costs.
DEFAULT_GAS_CAP = 600_000
GAS_PRICE_BUFFER_MULTIPLIER = 2
L1_BUDGET_PER_ACTION = 50_000_000_000_000  # fixed contingency; not a calibrated upper bound
ETH_RESERVE = 2_000_000_000_000_000
DEDICATED_EXECUTOR_ETH_RESERVE = 200_000_000_000_000  # 0.0002 ETH; isolated 5-USDC executor


def build_live_readonly_plan(wallet: str, allowlist: str, rpc: ReadOnlyRPC,
                             morpho: MorphoClient, *, gas_cap: int = DEFAULT_GAS_CAP,
                             eth_reserve_wei: int = ETH_RESERVE) -> dict:
    """Snapshot at verified block; separate latest-state gas quote is labeled."""
    if not address_ok(wallet):
        raise ValueError("Valid public wallet address required")
    if type(gas_cap) is not int or not 21_000 <= gas_cap <= 2_000_000:
        raise ValueError("Gas cap must be in [21000, 2000000] units per step")
    if type(eth_reserve_wei) is not int or not 0 <= eth_reserve_wei <= ETH_RESERVE:
        raise ValueError("Invalid explicit ETH reserve")
    policy = read_policy(allowlist)
    snapshot = read_wallet_snapshot(wallet, policy, rpc, morpho)
    preview = build_rebalance(snapshot, policy)
    allowances = {
        vault: rpc.call_uint(
            "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
            ALLOWANCE, snapshot.rpc_block, _address_word(wallet), _address_word(vault))
        for vault in policy.vault_addresses
    }
    eth_balance = rpc.eth_balance(wallet, snapshot.rpc_block)
    gas_price = rpc.gas_price_wei()
    # Network quote is latest-state (not block-pinned). The 2x ceiling and L1
    # contingency are policy assumptions and not proof of sufficient future fees.
    ceiling = gas_price * GAS_PRICE_BUFFER_MULTIPLIER
    if gas_price <= 0:
        raise ValueError("RPC gas price was zero")
    # Maximum number of action intents is bounded by selected-vault count.
    l1_allowance = len(policy.vault_addresses) * 2 * L1_BUDGET_PER_ACTION
    plan = build_safety_plan(
        preview, allowances=allowances, max_gas_units=gas_cap,
        gas_price_wei=ceiling, available_eth_wei=max(0, eth_balance - eth_reserve_wei),
        l1_fee_budget_wei=l1_allowance)
    return {
        "status": "VERIFIED_READ_ONLY_PLAN_NOT_EXECUTABLE",
        "verification": "Morpho indexed API and Base RPC basic reads passed at snapshot block",
        "eth_balance_wei": eth_balance,
        "eth_reserved_wei": eth_reserve_wei,
        "latest_gas_price_wei": gas_price,
        "policy_gas_price_ceiling_wei": ceiling,
        "per_action_gas_cap_units": gas_cap,
        "l1_budget_assumption_wei": l1_allowance,
        "allowances_units": allowances,
        "plan": asdict(plan),
        "missing_live_execution_gates": [
            "Liquidity/capacity checks for actual underlying vault adapters",
            "Independently simulated sequential transaction sequence with recent state",
            "Actual encoded unsigned EIP-1559 candidate transactions and measured fees",
            "Explicit user review, manual wallet authorization, bounded execution",
            "Per-transaction receipt monitoring and fresh post-transaction reconciliation",
        ],
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Build read-only plan from verified live Base observations")
    p.add_argument("--wallet", required=True)
    p.add_argument("--allowlist", required=True)
    p.add_argument("--max-gas-units", type=int, default=DEFAULT_GAS_CAP)
    p.add_argument("--show-unsigned-candidates", action="store_true", help="Show unverified calldata candidates; no transactions sent")
    p.add_argument("--max-action-usdc", type=int, default=1000, help="Hard maximum per-intent USDC amount (default 1000)")
    args = p.parse_args(argv)
    if not os.environ.get("BASE_RPC_URL"):
        print("ERROR: BASE_RPC_URL is required", file=sys.stderr)
        return 1
    try:
        with ReadOnlyRPC(os.environ["BASE_RPC_URL"]) as rpc, MorphoClient() as morpho:
            output = build_live_readonly_plan(args.wallet, args.allowlist, rpc, morpho,
                                               gas_cap=args.max_gas_units)
        if args.show_unsigned_candidates:
            from .wallet_plan import read_policy
            policy = read_policy(args.allowlist)
            raw = output["plan"]
            plan = SafetyPlan(**{**raw, "actions": tuple(PlanAction(**{**a, "depends_on": tuple(a["depends_on"])}) for a in raw["actions"]), "assumptions": tuple(raw["assumptions"])})
            output["unsigned_candidates"] = candidate_report(make_unsigned_candidates(
                plan, approved_vaults=policy.vault_addresses,
                max_assets_per_action_units=args.max_action_usdc * 10**6))
        print(json.dumps(output, indent=2))
        print("READ ONLY. NO TRANSACTION SIGNING OR BROADCAST.")
        return 0
    except (ValueError, OSError, RPCError, MorphoAPIError) as exc:
        # Never echo external RPC endpoint or provider credentials.
        detail = str(exc) if isinstance(exc, ValueError) else "check connectivity or provider/API synchronization"
        print(f"READ-ONLY PLAN FAILED: {type(exc).__name__}: {detail}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
