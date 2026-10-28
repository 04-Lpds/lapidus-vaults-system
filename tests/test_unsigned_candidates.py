"""Offline unsigned calldata encoder tests; no chain calls."""
from dataclasses import replace
from decimal import Decimal
import pytest
from lapidus_vaults.fork_roundtrip import VAULTS, ACTOR, BASE_USDC
from lapidus_vaults.wallet_plan import WalletSnapshot, VaultHolding, Policy, build_rebalance
from lapidus_vaults.safety_plan import build_safety_plan
from lapidus_vaults.unsigned_candidates import make_unsigned_candidates


def plan():
    policy = Policy(VAULTS, Decimal("0.20"), Decimal("0.40"))
    snap = WalletSnapshot(ACTOR, 8453, 123, 0,
        (VaultHolding(VAULTS[0], 1, 10_000_000, 0, 0, 0),
         VaultHolding(VAULTS[1], 0, 0, 0, 0, 0)), "test", "demo")
    preview = build_rebalance(snap, policy)
    return build_safety_plan(preview, allowances={v: 0 for v in VAULTS},
        max_gas_units=600_000, gas_price_wei=10_000_000, available_eth_wei=10**17)


def test_selectors_receivers_and_approval_spender():
    txs = make_unsigned_candidates(plan(), approved_vaults=VAULTS, max_assets_per_action_units=10_000_000)
    assert [t.method for t in txs] == ["withdraw_intent", "approve_intent", "deposit_intent"]
    assert [t.data[:10] for t in txs] == ["0xb460af94", "0x095ea7b3", "0x6e553f65"]
    assert txs[1].to_address == BASE_USDC
    assert txs[1].data[10:74] == "0"*24 + VAULTS[1][2:]
    assert txs[2].data[-64:] == "0"*24 + ACTOR[2:]
    assert all(t.value_wei == 0 and t.chain_id == 8453 for t in txs)
    assert txs[-1].depends_on == (1, 2)


def test_reject_outside_allowlist_and_amount_over_cap():
    with pytest.raises(ValueError, match="allowlist"):
        make_unsigned_candidates(plan(), approved_vaults=(VAULTS[0],), max_assets_per_action_units=10_000_000)
    with pytest.raises(ValueError, match="limit"):
        make_unsigned_candidates(plan(), approved_vaults=VAULTS, max_assets_per_action_units=3_000_000)


def test_reject_bad_status_sequence_and_dependencies():
    p = plan()
    with pytest.raises(ValueError, match="valid Base"):
        make_unsigned_candidates(replace(p, status="EXECUTE"), approved_vaults=VAULTS, max_assets_per_action_units=10_000_000)
    bad = replace(p, actions=(replace(p.actions[0], sequence=2),) + p.actions[1:])
    with pytest.raises(ValueError, match="sequence"):
        make_unsigned_candidates(bad, approved_vaults=VAULTS, max_assets_per_action_units=10_000_000)


def test_zero_actions_no_transactions():
    p = plan()
    assert make_unsigned_candidates(replace(p, actions=()), approved_vaults=VAULTS, max_assets_per_action_units=10_000_000) == ()
