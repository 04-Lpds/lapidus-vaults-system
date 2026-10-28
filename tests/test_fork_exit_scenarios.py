"""Pure reconciliation guardrails for fork-only exit scenarios."""
import pytest
from lapidus_vaults.testing.fork_exit_scenarios import verify_exit_outcome


def test_partial_withdraw_reconciles():
    assert verify_exit_outcome(
        before_usdc=0, after_usdc=2_500_000,
        before_shares=1_000, after_shares=750,
        requested_assets=2_500_000) == 2_500_000


def test_full_redeem_requires_zero_shares():
    assert verify_exit_outcome(
        before_usdc=2_500_000, after_usdc=9_999_999,
        before_shares=750, after_shares=0, full_exit=True) == 7_499_999
    with pytest.raises(RuntimeError, match="outstanding"):
        verify_exit_outcome(
            before_usdc=2_500_000, after_usdc=9_999_999,
            before_shares=750, after_shares=1, full_exit=True)


@pytest.mark.parametrize("before_usdc,after_usdc,before_shares,after_shares,requested", [
    (0, 0, 100, 0, None),
    (0, 10, 100, 100, None),
    (0, 9, 100, 0, 10),
])
def test_exit_rejects_bad_balance_deltas(before_usdc, after_usdc, before_shares, after_shares, requested):
    with pytest.raises(RuntimeError):
        verify_exit_outcome(
            before_usdc=before_usdc, after_usdc=after_usdc,
            before_shares=before_shares, after_shares=after_shares,
            requested_assets=requested)
