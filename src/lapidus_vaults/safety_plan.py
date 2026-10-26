"""v0.5-alpha: deterministic, non-executable rebalance transaction intentions.

This module does NOT create signed or broadcastable transactions. Every plan must
be revalidated against current chain state before any future live execution.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
import argparse
import json
from pathlib import Path
from typing import Mapping

from .wallet_plan import RebalancePreview, VaultChange, Policy, WalletSnapshot, build_rebalance

MAX_ACTIONS = 30

@dataclass(frozen=True)
class PlanAction:
    sequence: int
    kind: str
    vault: str
    amount_units: int
    depends_on: tuple[int, ...]
    precondition: str

@dataclass(frozen=True)
class SafetyPlan:
    status: str
    chain_id: int
    observed_block: int
    wallet: str
    capital_units: int
    starting_cash_units: int
    intended_idle_units: int
    projected_cash_units: int
    actions: tuple[PlanAction, ...]
    assumptions: tuple[str, ...]


def build_safety_plan(preview: RebalancePreview, *, allowances: Mapping[str, int],
                      max_gas_units: int, gas_price_wei: int,
                      available_eth_wei: int, l1_fee_budget_wei: int = 0) -> SafetyPlan:
    """Cash-conserving order with worst-case gas budget; not an execution authorization.

    The planner cannot certify actual withdrawable liquidity and never treats
    ERC-4626 maxWithdraw on Morpho V2 as an executable liquidity limit.
    """
    if any(type(v) is not int or v < 0 for v in
           (max_gas_units, gas_price_wei, available_eth_wei, l1_fee_budget_wei)):
        raise ValueError("Fee budget and gas parameters must be nonnegative integers")
    if max_gas_units == 0 or gas_price_wei == 0:
        raise ValueError("Positive per-transaction gas cap and gas-price ceiling required")
    if preview.snapshot.chain_id != 8453 or preview.snapshot.rpc_block < 0:
        raise ValueError("Requires Base snapshot")
    changes = preview.changes
    if not changes or len(changes) > 10:
        raise ValueError("Expected 1..10 vaults")
    if set(allowances) != {c.vault_address for c in changes}:
        raise ValueError("Allowance readings must cover exactly selected vaults")
    if any(type(a) is not int or a < 0 for a in allowances.values()):
        raise ValueError("Allowances must be nonnegative integer token base units")
    if sum(c.current_units for c in changes) + preview.snapshot.wallet_usdc_units != preview.capital_units:
        raise ValueError("Snapshot capital mismatch")
    if any(c.target_units - c.current_units != c.delta_units or c.target_units < 0
           or c.current_units < 0 for c in changes):
        raise ValueError("Malformed allocator delta")
    if sum(c.target_units for c in changes) + preview.target_idle_units != preview.capital_units:
        raise ValueError("Portfolio target mismatch")

    actions = []
    cash = preview.snapshot.wallet_usdc_units

    def add(kind: str, vault: str, amount: int, requires: tuple[int, ...], condition: str):
        actions.append(PlanAction(len(actions) + 1, kind, vault, amount, requires, condition))
        if len(actions) > MAX_ACTIONS:
            raise ValueError("Too many actions")

    for c in sorted(changes, key=lambda x: x.vault_address):
        if c.delta_units < 0:
            amount = -c.delta_units
            add("withdraw_intent", c.vault_address, amount, (),
                "Recheck liquidity, shares, current asset value and withdrawal preview")
            cash += amount  # hypothetical only; never a claim of executed cash

    withdrawal_ids = tuple(a.sequence for a in actions)
    for c in sorted(changes, key=lambda x: x.vault_address):
        if c.delta_units <= 0:
            continue
        amount = c.delta_units
        if amount > cash:
            raise ValueError("Deposits exceed hypothetical cash after withdrawals")
        if allowances[c.vault_address] < amount:
            # Requires approval to exact intended allowance; later execution
            # must verify token's approval semantics, existing allowance and state.
            add("approve_intent", c.vault_address, amount, withdrawal_ids,
                "Recheck allowance; token approval behavior not verified")
        deps = tuple(a.sequence for a in actions)
        add("deposit_intent", c.vault_address, amount, deps,
            "Recheck wallet cash, share preview, vault caps/restrictions and fees")
        cash -= amount

    if cash != preview.target_idle_units:
        raise ValueError("Planned ending cash does not equal target idle capital")
    gas_budget = (len(actions) * max_gas_units * gas_price_wei + l1_fee_budget_wei) if actions else 0
    if gas_budget > available_eth_wei:
        raise ValueError(f"Insufficient ETH fee ceiling: need {gas_budget} wei, have {available_eth_wei} wei")

    return SafetyPlan(
        status="NON_EXECUTABLE_REQUIRES_FRESH_PREFLIGHT",
        chain_id=preview.snapshot.chain_id,
        observed_block=preview.snapshot.rpc_block,
        wallet=preview.snapshot.wallet_address,
        capital_units=preview.capital_units,
        starting_cash_units=preview.snapshot.wallet_usdc_units,
        intended_idle_units=preview.target_idle_units,
        projected_cash_units=cash,
        actions=tuple(actions),
        assumptions=(
            "Indicative share values; withdrawal proceeds may differ or be unavailable",
            "Actions are unencoded intentions, not transactions or calldata",
            "Allowance, balances, block state and liquidity must be freshly verified",
            "Fee ceiling is supplied by caller, not a network fee prediction",
            "On any failed operation: halt, reread balances and replan; never replay stale steps",
            "Mainnet signing and broadcasting are absent",
        ),
    )


def render_plan(plan: SafetyPlan) -> str:
    return json.dumps(asdict(plan), indent=2)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Offline v0.5 read-only plan demonstration")
    parser.add_argument("--demo", action="store_true", required=True)
    args = parser.parse_args(argv)
    from decimal import Decimal
    from .wallet_plan import VaultHolding
    from .fork_roundtrip import VAULTS, ACTOR
    policy = Policy(VAULTS, Decimal("0.20"), Decimal("0.40"))
    snapshot = WalletSnapshot(ACTOR, 8453, 123, 0,
        (VaultHolding(VAULTS[0], 1, 10_000_000, 0, 0, 0),
         VaultHolding(VAULTS[1], 0, 0, 0, 0, 0)), "DEMO", "demo")
    preview = build_rebalance(snapshot, policy)
    plan = build_safety_plan(preview, allowances={v: 0 for v in VAULTS},
        max_gas_units=600_000, gas_price_wei=10_000_000,
        available_eth_wei=10**17, l1_fee_budget_wei=10**13)
    print(render_plan(plan))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
