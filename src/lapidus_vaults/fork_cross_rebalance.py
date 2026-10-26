"""Fork-only cross-vault rebalance using the actual LVS wallet allocation policy.

No mainnet RPC writes. Transaction logic is test-only; build_rebalance is shared.
"""
from __future__ import annotations
import argparse
import json
import sys
import httpx
from decimal import Decimal

from .allocator import BASE_CHAIN
from .wallet_plan import Policy, WalletSnapshot, VaultHolding, build_rebalance
from .fork_roundtrip import (
    ForkRPC, VAULTS, ACTOR, BASE_USDC, APPROVE, DEPOSIT, WITHDRAW,
    addr_word, word, ASSET,
)

TRANSFER = "0xa9059cbb"
CONVERT_TO_ASSETS = "0x07a2d13a"
TEST_AMOUNT = 10_000_000
# We use the same policy percentages as the v0.4-beta planner.
POLICY = Policy(tuple(VAULTS), Decimal("0.20"), Decimal("0.40"))


def fork_snapshot(rpc: ForkRPC) -> WalletSnapshot:
    """Observe actual, mutable local-fork state; do not pretend this is verified mainnet."""
    holdings = []
    for vault in VAULTS:
        shares = rpc.balance(vault, ACTOR)
        assets = rpc.uint(vault, CONVERT_TO_ASSETS + word(shares))
        holdings.append(VaultHolding(vault, shares, assets, 0, 0, 0))
    return WalletSnapshot(ACTOR, BASE_CHAIN, 0, rpc.balance(BASE_USDC, ACTOR),
                          tuple(holdings), "fork-local", source="rpc")


def order_actions(preview):
    """Never schedule deposits ahead of required withdrawals."""
    withdrawals = tuple(c for c in preview.changes if c.delta_units < 0)
    deposits = tuple(c for c in preview.changes if c.delta_units > 0)
    return withdrawals + deposits


def validate_planned_cash(preview) -> None:
    """Reject plans that would spend more USDC than starting cash plus withdrawals."""
    available = preview.snapshot.wallet_usdc_units
    for change in order_actions(preview):
        if change.delta_units < 0:
            available += -change.delta_units
        else:
            if change.delta_units > available:
                raise RuntimeError("Planned deposits exceed cash after withdrawals")
            available -= change.delta_units


def run_rebalance(rpc: ForkRPC, usdc_holder: str) -> dict:
    addr_word(usdc_holder)
    if int(rpc.call("eth_chainId", []), 16) != BASE_CHAIN:
        raise RuntimeError("Expected fork of Base")
    for vault in VAULTS:
        code = rpc.call("eth_getCode", [vault, "latest"])
        if not isinstance(code, str) or len(code) < 10:
            raise RuntimeError("Vault bytecode unavailable")
        if f"0x{rpc.uint(vault, ASSET):040x}" != BASE_USDC:
            raise RuntimeError("Vault underlying asset mismatch")
    if any(rpc.balance(v, ACTOR) != 0 for v in VAULTS):
        raise RuntimeError("Use fresh fork: test actor already owns vault shares")
    if rpc.balance(BASE_USDC, ACTOR):
        raise RuntimeError("Use fresh fork: test actor already has USDC")
    rpc.call("anvil_setBalance", [ACTOR, hex(10**18)])
    rpc.call("anvil_setBalance", [usdc_holder, hex(10**18)])
    rpc.call("anvil_impersonateAccount", [usdc_holder])
    try:
        rpc.transact(BASE_USDC, usdc_holder,
                     TRANSFER + addr_word(ACTOR) + word(TEST_AMOUNT),
                     step="fund fork actor with 10 USDC")
    finally:
        rpc.call("anvil_stopImpersonatingAccount", [usdc_holder])
    if rpc.balance(BASE_USDC, ACTOR) != TEST_AMOUNT:
        raise RuntimeError("Fork funding amount did not reconcile")
    source, destination = VAULTS
    rpc.transact(BASE_USDC, ACTOR, APPROVE + addr_word(source) + word(TEST_AMOUNT),
                 step="approve initial Gauntlet deposit")
    rpc.transact(source, ACTOR, DEPOSIT + word(TEST_AMOUNT) + addr_word(ACTOR),
                 step="seed 10 USDC in Gauntlet")
    before = fork_snapshot(rpc)
    preview = build_rebalance(before, POLICY)
    validate_planned_cash(preview)
    steps = order_actions(preview)
    if len(steps) != 2 or steps[0].vault_address != source or steps[0].delta_units >= 0 or steps[1].vault_address != destination or steps[1].delta_units <= 0:
        raise RuntimeError("Unexpected allocator action order/shape")

    receipts = []
    for step in steps:
        amount = abs(step.delta_units)
        if step.delta_units < 0:
            before_cash = rpc.balance(BASE_USDC, ACTOR)
            receipt = rpc.transact(step.vault_address, ACTOR,
                                   WITHDRAW + word(amount) + addr_word(ACTOR) + addr_word(ACTOR),
                                   step="withdraw Gauntlet before Galaxy deposit")
            if rpc.balance(BASE_USDC, ACTOR) - before_cash != amount:
                raise RuntimeError("Withdrawal proceeds do not reconcile")
            receipts.append(("withdraw", int(receipt["gasUsed"], 16)))
        else:
            if rpc.balance(BASE_USDC, ACTOR) < amount:
                raise RuntimeError("Insufficient actually available cash for deposit")
            rpc.transact(BASE_USDC, ACTOR, APPROVE + addr_word(step.vault_address) + word(amount),
                         step="approve Galaxy deposit")
            before_cash = rpc.balance(BASE_USDC, ACTOR)
            receipt = rpc.transact(step.vault_address, ACTOR,
                                   DEPOSIT + word(amount) + addr_word(ACTOR),
                                   step="deposit to Galaxy after withdrawal")
            if before_cash - rpc.balance(BASE_USDC, ACTOR) != amount:
                raise RuntimeError("Galaxy deposit funds do not reconcile")
            receipts.append(("deposit", int(receipt["gasUsed"], 16)))

    after = fork_snapshot(rpc)
    if after.wallet_usdc_units != preview.target_idle_units:
        raise RuntimeError("Actual idle USDC differs from planner target")
    if after.holdings[1].share_balance <= 0 or after.holdings[0].share_balance <= 0:
        raise RuntimeError("Expected positive shares in both vaults")
    # Estimated asset values may drift by small share rounding; a dollar-exact
    # value comparison would confuse estimates with actual token cash movements.
    deviations = {c.vault_address: next(h.estimated_assets_units for h in after.holdings
                   if h.vault_address == c.vault_address) - c.target_units for c in preview.changes}
    if any(abs(d) > 2 for d in deviations.values()):
        raise RuntimeError(f"Vault holdings deviate from targets beyond 2 USDC base units: {deviations}")
    return {
        "status": "FORK_ONLY_CROSS_VAULT_REBALANCE_SUCCEEDED",
        "initial_capital_units": before.in_scope_capital_units,
        "planned_withdraw_units": -steps[0].delta_units,
        "planned_deposit_units": steps[1].delta_units,
        "target_idle_units": preview.target_idle_units,
        "actual_idle_units": after.wallet_usdc_units,
        "final_vault_asset_estimates_units": {h.vault_address: h.estimated_assets_units for h in after.holdings},
        "target_deviations_units": deviations,
        "actions_and_gas_used": receipts,
    }


def main(argv=None):
    p = argparse.ArgumentParser(description="Fork-only LVS allocator cross-vault transaction test")
    p.add_argument("--fork-rpc", default="http://127.0.0.1:8545")
    p.add_argument("--usdc-holder", required=True)
    args = p.parse_args(argv)
    try:
        with ForkRPC(args.fork_rpc) as rpc:
            out = run_rebalance(rpc, args.usdc_holder)
        print(json.dumps(out, indent=2))
        print("LOCAL FORK ONLY; no Base mainnet transaction, signing or production authorization")
        return 0
    except (RuntimeError, ValueError, httpx.HTTPError) as exc:
        print(f"FORK REBALANCE FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
