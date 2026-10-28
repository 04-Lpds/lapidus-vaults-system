"""Offline tests for production allocator reuse in fork rebalancing."""
from decimal import Decimal
from dataclasses import replace
import pytest
from lapidus_vaults.fork_cross_rebalance import order_actions, validate_planned_cash, POLICY
from lapidus_vaults.fork_roundtrip import VAULTS, ACTOR
from lapidus_vaults.wallet_plan import (
    WalletSnapshot, VaultHolding, build_rebalance,
)
from lapidus_vaults.allocator import BASE_CHAIN


def preview(idle=0, first=10_000_000, second=0):
    snapshot = WalletSnapshot(
        ACTOR, BASE_CHAIN, 10, idle,
        (VaultHolding(VAULTS[0], 10**18 if first else 0, first, 0, 0, 0),
         VaultHolding(VAULTS[1], 10**18 if second else 0, second, 0, 0, 0)),
        "offline")
    return build_rebalance(snapshot, POLICY)


def test_shared_allocator_produces_withdraw_then_deposit():
    result = preview()
    assert result.target_idle_units == 2_000_000
    steps = order_actions(result)
    assert [(s.vault_address, s.delta_units) for s in steps] == [
        (VAULTS[0], -6_000_000), (VAULTS[1], 4_000_000)]
    validate_planned_cash(result)


def test_ordering_is_independent_of_original_preview_change_order():
    p = preview()
    rearranged = replace(p, changes=tuple(reversed(p.changes)))
    assert order_actions(rearranged)[0].delta_units < 0
    validate_planned_cash(rearranged)


def test_reject_cash_deficit_from_malformed_preview():
    from lapidus_vaults.wallet_plan import VaultChange
    p = preview()
    invalid = replace(p, changes=(VaultChange(VAULTS[0], 10_000_000, 10_000_000, 0),
                                  VaultChange(VAULTS[1], 0, 11_000_000, 11_000_000)))
    with pytest.raises(RuntimeError, match="exceed"):
        validate_planned_cash(invalid)
