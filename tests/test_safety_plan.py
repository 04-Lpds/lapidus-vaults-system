"""Read-only v0.5 planning invariants; no node, network or funds."""
from dataclasses import replace
from decimal import Decimal
import pytest

from lapidus_vaults.safety_plan import build_safety_plan
from lapidus_vaults.wallet_plan import Policy, WalletSnapshot, VaultHolding, build_rebalance
from lapidus_vaults.fork_roundtrip import VAULTS, ACTOR


def make_preview():
    policy = Policy(VAULTS, Decimal("0.20"), Decimal("0.40"))
    snapshot = WalletSnapshot(
        ACTOR, 8453, 123, 0,
        (VaultHolding(VAULTS[0], 1, 10_000_000, 0, 0, 0),
         VaultHolding(VAULTS[1], 0, 0, 0, 0, 0)), "demo", "demo")
    return build_rebalance(snapshot, policy)


def plan(**kwargs):
    opts = dict(allowances={v: 0 for v in VAULTS},
                max_gas_units=600_000, gas_price_wei=10_000_000,
                available_eth_wei=10**17, l1_fee_budget_wei=10**13)
    opts.update(kwargs)
    return build_safety_plan(make_preview(), **opts)


def test_orders_withdraw_approve_deposit_and_exact_idle():
    p = plan()
    assert [a.kind for a in p.actions] == [
        "withdraw_intent", "approve_intent", "deposit_intent"]
    assert [a.amount_units for a in p.actions] == [6_000_000, 4_000_000, 4_000_000]
    assert p.projected_cash_units == p.intended_idle_units == 2_000_000
    assert p.actions[2].depends_on == (1, 2)
    assert p.status == "NON_EXECUTABLE_REQUIRES_FRESH_PREFLIGHT"


def test_sufficient_allowance_skips_approval():
    p = plan(allowances={VAULTS[0]: 0, VAULTS[1]: 4_000_000})
    assert [a.kind for a in p.actions] == ["withdraw_intent", "deposit_intent"]


def test_fee_budget_blocks():
    with pytest.raises(ValueError, match="ETH"):
        plan(available_eth_wei=100)


def test_incomplete_allowances_block():
    with pytest.raises(ValueError, match="exactly"):
        plan(allowances={VAULTS[0]: 0})


def test_negative_gas_and_zero_ceiling_block():
    with pytest.raises(ValueError):
        plan(gas_price_wei=-1)
    with pytest.raises(ValueError):
        plan(max_gas_units=0)


def test_malformed_changes_block():
    from lapidus_vaults.wallet_plan import VaultChange
    p = make_preview()
    bad = replace(p, changes=(VaultChange(VAULTS[0], 10_000_000, 10_000_000, 123),
                              p.changes[1]))
    with pytest.raises(ValueError, match="delta"):
        build_safety_plan(bad, allowances={v: 0 for v in VAULTS},
                          max_gas_units=600_000, gas_price_wei=10_000_000,
                          available_eth_wei=10**17)
