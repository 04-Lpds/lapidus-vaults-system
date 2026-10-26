"""Execute precisely the LVS unsigned candidates on a disposable Anvil Base fork.

Test-only adapter: only localhost ForkRPC can send transactions. Not a mainnet executor.
"""
from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal

import httpx

from .fork_roundtrip import (
    ForkRPC, VAULTS, ACTOR, BASE_USDC, ASSET, APPROVE, DEPOSIT,
    addr_word, word,
)
from .fork_cross_rebalance import fork_snapshot, TEST_AMOUNT
from .wallet_plan import Policy, build_rebalance
from .safety_plan import build_safety_plan
from .unsigned_candidates import make_unsigned_candidates, candidate_report

TRANSFER = "0xa9059cbb"
ALLOWANCE = "0xdd62ed3e"
POLICY = Policy(tuple(VAULTS), Decimal("0.20"), Decimal("0.40"))
GAS_CAP_PER_ACTION = 600_000


def bootstrap_local_fork(rpc: ForkRPC, holder: str) -> None:
    """Set up local-only funds and initial source position, no mainnet transfers."""
    addr_word(holder)
    if int(rpc.call("eth_chainId", []), 16) != 8453:
        raise RuntimeError("Not a Base fork")
    for vault in VAULTS:
        code = rpc.call("eth_getCode", [vault, "latest"])
        if not isinstance(code, str) or len(code) < 10:
            raise RuntimeError("Missing deployed vault contract")
        if f"0x{rpc.uint(vault, ASSET):040x}" != BASE_USDC:
            raise RuntimeError("Unexpected underlying token")
    if rpc.balance(BASE_USDC, ACTOR) != 0 or any(rpc.balance(v, ACTOR) != 0 for v in VAULTS):
        raise RuntimeError("Fresh fork required: test actor already owns USDC or vault shares")
    rpc.call("anvil_setBalance", [ACTOR, hex(10**18)])
    rpc.call("anvil_setBalance", [holder, hex(10**18)])
    rpc.call("anvil_impersonateAccount", [holder])
    try:
        rpc.transact(BASE_USDC, holder, TRANSFER + addr_word(ACTOR) + word(TEST_AMOUNT),
                     step="fund local actor")
    finally:
        rpc.call("anvil_stopImpersonatingAccount", [holder])
    if rpc.balance(BASE_USDC, ACTOR) != TEST_AMOUNT:
        raise RuntimeError("Local actor funding did not reconcile")
    rpc.transact(BASE_USDC, ACTOR, APPROVE + addr_word(VAULTS[0]) + word(TEST_AMOUNT),
                 step="approve initial local position")
    rpc.transact(VAULTS[0], ACTOR, DEPOSIT + word(TEST_AMOUNT) + addr_word(ACTOR),
                 step="seed local Gauntlet position")
    if rpc.balance(BASE_USDC, ACTOR) != 0 or rpc.balance(VAULTS[0], ACTOR) <= 0:
        raise RuntimeError("Initial fork portfolio did not reconcile")


def execute_candidates_locally(rpc: ForkRPC, candidates, *, wallet: str = ACTOR) -> list[dict]:
    """Fail-stop local-only executor. No retries, no rewriting calldata."""
    if wallet.lower() != ACTOR or rpc.url not in {"http://127.0.0.1:8545", "http://localhost:8545"}:
        raise ValueError("Test execution only supports the Anvil actor on localhost:8545")
    finished = set()
    records = []
    for tx in candidates:
        if tx.from_address != wallet.lower() or tx.chain_id != 8453 or tx.value_wei != 0:
            raise RuntimeError("Candidate sender, chain or value mismatch")
        if any(dep not in finished for dep in tx.depends_on):
            raise RuntimeError("Candidate dependencies not confirmed")
        if tx.status != "UNSIGNED_UNSIMULATED_DO_NOT_SUBMIT":
            raise RuntimeError("Unexpected candidate status")
        if tx.method == "deposit_intent":
            available = rpc.balance(BASE_USDC, wallet)
            if available < tx.requested_assets_units:
                raise RuntimeError("Rebalance deposit exceeds observed fork USDC")
        elif tx.method == "withdraw_intent":
            if rpc.balance(tx.to_address, wallet) <= 0:
                raise RuntimeError("No observed vault shares to withdraw")
        elif tx.method == "approve_intent":
            if tx.to_address != BASE_USDC:
                raise RuntimeError("Approval cannot target a non-USDC contract")
        else:
            raise RuntimeError("Unsupported method")
        before_cash = rpc.balance(BASE_USDC, wallet)
        receipt = rpc.transact(tx.to_address, wallet, tx.data,
                               step=f"candidate #{tx.sequence} {tx.method}")
        after_cash = rpc.balance(BASE_USDC, wallet)
        if tx.method == "withdraw_intent" and after_cash - before_cash != tx.requested_assets_units:
            raise RuntimeError("Withdrawal token balance delta failed")
        if tx.method == "deposit_intent" and before_cash - after_cash != tx.requested_assets_units:
            raise RuntimeError("Deposit token balance delta failed")
        if tx.method == "approve_intent" and after_cash != before_cash:
            raise RuntimeError("Approval unexpectedly moved USDC")
        finished.add(tx.sequence)
        records.append({"sequence": tx.sequence, "method": tx.method,
                        "transaction_hash": receipt["transactionHash"],
                        "gas_used": int(receipt["gasUsed"], 16),
                        "cash_after_units": after_cash})
    return records


def run_candidate_rebalance(rpc: ForkRPC, holder: str) -> dict:
    bootstrap_local_fork(rpc, holder)
    before = fork_snapshot(rpc)
    preview = build_rebalance(before, POLICY)
    allowances = {
        vault: rpc.uint(BASE_USDC, ALLOWANCE + addr_word(ACTOR) + addr_word(vault))
        for vault in VAULTS
    }
    plan = build_safety_plan(preview, allowances=allowances,
                             max_gas_units=GAS_CAP_PER_ACTION,
                             gas_price_wei=12_000_000,
                             available_eth_wei=10**18,
                             l1_fee_budget_wei=10**13)
    candidates = make_unsigned_candidates(plan, approved_vaults=VAULTS,
                                          max_assets_per_action_units=TEST_AMOUNT)
    if tuple(tx.method for tx in candidates) != (
            "withdraw_intent", "approve_intent", "deposit_intent"):
        raise RuntimeError("Unexpected test scenario action sequence")
    records = execute_candidates_locally(rpc, candidates)
    after = fork_snapshot(rpc)
    if after.wallet_usdc_units != plan.intended_idle_units:
        raise RuntimeError("Final idle cash deviates from planned cash")
    if any(h.share_balance <= 0 for h in after.holdings):
        raise RuntimeError("Rebalance failed to retain positions in both vaults")
    deviations = {
        change.vault_address: next(
            h.estimated_assets_units for h in after.holdings
            if h.vault_address == change.vault_address
        ) - change.target_units
        for change in preview.changes
    }
    if any(abs(delta) > 2 for delta in deviations.values()):
        raise RuntimeError(f"Final position deviations exceed two USDC raw units: {deviations}")
    return {
        "status": "FORK_ONLY_EXACT_CANDIDATES_REBALANCE_SUCCEEDED",
        "initial_capital_units": preview.capital_units,
        "target_idle_units": plan.intended_idle_units,
        "actual_idle_units": after.wallet_usdc_units,
        "actions": candidate_report(candidates),
        "receipts": records,
        "final_vault_asset_estimates_units": {
            h.vault_address: h.estimated_assets_units for h in after.holdings
        },
        "target_deviations_units": deviations,
        "live_broadcast_enabled": False,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Anvil-only exact-candidate integration test")
    parser.add_argument("--fork-rpc", default="http://127.0.0.1:8545")
    parser.add_argument("--usdc-holder", required=True)
    args = parser.parse_args(argv)
    try:
        with ForkRPC(args.fork_rpc) as rpc:
            result = run_candidate_rebalance(rpc, args.usdc_holder)
        print(json.dumps(result, indent=2))
        print("LOCAL ANVIL ONLY. NO BASE MAINNET TRANSACTION BROADCAST.")
        return 0
    except (RuntimeError, ValueError, httpx.HTTPError) as exc:
        print(f"EXACT-CANDIDATE FORK TEST FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
