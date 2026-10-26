"""Offline regression tests for v0.5 read-only live planning."""
from unittest.mock import Mock, patch
import pytest

from lapidus_vaults.live_readonly_plan import (
    build_live_readonly_plan, ETH_RESERVE,
)
from lapidus_vaults.fork_roundtrip import VAULTS, ACTOR


def test_reject_invalid_address_before_external_reads():
    with pytest.raises(ValueError, match="wallet"):
        build_live_readonly_plan("not-an-address", "dummy.json", Mock(), Mock())


def test_reject_invalid_gas_cap_before_external_reads():
    with pytest.raises(ValueError, match="Gas cap"):
        build_live_readonly_plan(ACTOR, "dummy.json", Mock(), Mock(), gas_cap=1)


def test_pinned_allowance_reads_and_readonly_budget():
    from decimal import Decimal
    from lapidus_vaults.wallet_plan import Policy, WalletSnapshot, VaultHolding
    policy = Policy(VAULTS, Decimal("0.20"), Decimal("0.40"))
    snapshot = WalletSnapshot(ACTOR, 8453, 99, 0, (
        VaultHolding(VAULTS[0], 1, 10_000_000, 0, 0, 0),
        VaultHolding(VAULTS[1], 0, 0, 0, 0, 0)), "offline", "rpc")
    rpc = Mock()
    rpc.call_uint.return_value = 0
    rpc.eth_balance.return_value = ETH_RESERVE + 10**17
    rpc.gas_price_wei.return_value = 10**7
    with patch("lapidus_vaults.live_readonly_plan.read_policy", return_value=policy), patch(
        "lapidus_vaults.live_readonly_plan.read_wallet_snapshot", return_value=snapshot
    ):
        out = build_live_readonly_plan(ACTOR, "dummy.json", rpc, Mock())
    assert out["status"] == "VERIFIED_READ_ONLY_PLAN_NOT_EXECUTABLE"
    assert [a["kind"] for a in out["plan"]["actions"]] == [
        "withdraw_intent", "approve_intent", "deposit_intent"]
    assert rpc.call_uint.call_count == 2
    assert all(c.args[2] == 99 for c in rpc.call_uint.call_args_list)
    assert out["plan"]["projected_cash_units"] == 2_000_000


def test_low_eth_budget_fails_closed():
    from decimal import Decimal
    from lapidus_vaults.wallet_plan import Policy, WalletSnapshot, VaultHolding
    policy = Policy(VAULTS, Decimal("0.20"), Decimal("0.40"))
    snapshot = WalletSnapshot(ACTOR, 8453, 99, 0, (
        VaultHolding(VAULTS[0], 1, 10_000_000, 0, 0, 0),
        VaultHolding(VAULTS[1], 0, 0, 0, 0, 0)), "offline", "rpc")
    rpc = Mock()
    rpc.call_uint.return_value = 0
    rpc.eth_balance.return_value = 0
    rpc.gas_price_wei.return_value = 10**7
    with patch("lapidus_vaults.live_readonly_plan.read_policy", return_value=policy), patch(
        "lapidus_vaults.live_readonly_plan.read_wallet_snapshot", return_value=snapshot
    ):
        with pytest.raises(ValueError, match="ETH"):
            build_live_readonly_plan(ACTOR, "dummy.json", rpc, Mock())
