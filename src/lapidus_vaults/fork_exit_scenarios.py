"""Fork-only exit-path scenarios for Morpho Vault V2.

Reuses the local-only ForkRPC guardrails. Does not broadcast to Base mainnet.
"""
from __future__ import annotations

import argparse
import json
import sys
import httpx

from .fork_roundtrip import (
    ForkRPC, VAULTS, ACTOR, BASE_USDC, APPROVE, DEPOSIT, WITHDRAW,
    addr_word, word,
)

REDEEM = "0xba087652"
TRANSFER = "0xa9059cbb"
PREVIEW_REDEEM = "0x4cdad506"
TEST_USDC_AMOUNT = 10_000_000


def verify_exit_outcome(*, before_usdc: int, after_usdc: int, before_shares: int,
                        after_shares: int, requested_assets: int | None = None,
                        full_exit: bool = False) -> int:
    """Validate balance deltas independently of tx receipt status."""
    received = after_usdc - before_usdc
    if received <= 0:
        raise RuntimeError("Exit did not increase actor USDC balance")
    if after_shares >= before_shares:
        raise RuntimeError("Exit did not decrease actor vault shares")
    if requested_assets is not None and received != requested_assets:
        raise RuntimeError("Withdrawal USDC amount did not reconcile")
    if full_exit and after_shares != 0:
        raise RuntimeError("Full redemption left vault shares outstanding")
    return received


def run_exit_scenarios(rpc: ForkRPC, vault: str, usdc_holder: str) -> dict:
    """Test partial withdraw then redeem remaining shares on local fork only."""
    if vault.lower() not in VAULTS:
        raise ValueError("Vault outside fixed research allowlist")
    addr_word(usdc_holder)
    if int(rpc.call("eth_chainId", []), 16) != 8453:
        raise RuntimeError("Expected Base fork chain ID 8453")
    if len(rpc.call("eth_getCode", [vault, "latest"])) < 10:
        raise RuntimeError("No deployed vault bytecode")
    if f"0x{rpc.uint(vault, '0x38d52e0f'):040x}" != BASE_USDC:
        raise RuntimeError("Vault underlying does not match Base USDC")

    # Actor is Anvil's throwaway account, never the user's MetaMask address.
    rpc.call("anvil_setBalance", [ACTOR, hex(10**18)])
    rpc.call("anvil_setBalance", [usdc_holder, hex(10**18)])
    rpc.call("anvil_impersonateAccount", [usdc_holder])
    try:
        rpc.transact(BASE_USDC, usdc_holder,
                     TRANSFER + addr_word(ACTOR) + word(TEST_USDC_AMOUNT),
                     step="fork-only funding transfer")
    finally:
        rpc.call("anvil_stopImpersonatingAccount", [usdc_holder])

    initial_usdc = rpc.balance(BASE_USDC, ACTOR)
    initial_shares = rpc.balance(vault, ACTOR)
    if initial_usdc < TEST_USDC_AMOUNT or initial_shares != 0:
        raise RuntimeError("Fork must have funded USDC and zero pre-existing actor shares")

    approval = rpc.transact(BASE_USDC, ACTOR,
                            APPROVE + addr_word(vault) + word(TEST_USDC_AMOUNT),
                            step="approve vault")
    deposit = rpc.transact(vault, ACTOR,
                           DEPOSIT + word(TEST_USDC_AMOUNT) + addr_word(ACTOR),
                           step="deposit 10 USDC")
    deposited_shares = rpc.balance(vault, ACTOR)
    deposited_cash = rpc.balance(BASE_USDC, ACTOR)
    if deposited_shares <= 0 or initial_usdc - deposited_cash != TEST_USDC_AMOUNT:
        raise RuntimeError("Deposit share/USDC reconciliation failed")

    partial_assets = 2_500_000
    partial = rpc.transact(vault, ACTOR, WITHDRAW + word(partial_assets)
                           + addr_word(ACTOR) + addr_word(ACTOR),
                           step="partial withdrawal of 2.5 USDC")
    partial_usdc = rpc.balance(BASE_USDC, ACTOR)
    partial_shares = rpc.balance(vault, ACTOR)
    verify_exit_outcome(before_usdc=deposited_cash, after_usdc=partial_usdc,
                        before_shares=deposited_shares, after_shares=partial_shares,
                        requested_assets=partial_assets)

    if partial_shares <= 0:
        raise RuntimeError("No shares remained for full exit test")
    preview_redeem = rpc.uint(vault, PREVIEW_REDEEM + word(partial_shares))
    # All remaining shares are redeemed, not a fixed amount of USDC.
    exit_tx = rpc.transact(vault, ACTOR, REDEEM + word(partial_shares)
                           + addr_word(ACTOR) + addr_word(ACTOR),
                           step="redeem all remaining shares")
    final_usdc = rpc.balance(BASE_USDC, ACTOR)
    final_shares = rpc.balance(vault, ACTOR)
    redeemed_assets = verify_exit_outcome(
        before_usdc=partial_usdc, after_usdc=final_usdc,
        before_shares=partial_shares, after_shares=final_shares, full_exit=True,
    )
    # previewRedeem predicts expected assets on the fork before execution;
    # no yield is deliberately accrued between preview and redemption.
    if redeemed_assets != preview_redeem:
        raise RuntimeError("previewRedeem disagrees with redemption asset delta")
    return {
        "vault": vault.lower(),
        "scenario": "deposit 10 USDC -> withdraw 2.5 USDC -> redeem 100% remaining shares",
        "shares_after_deposit": deposited_shares,
        "shares_after_partial_withdrawal": partial_shares,
        "shares_after_redeem": final_shares,
        "partial_received_units": partial_usdc - deposited_cash,
        "redeem_received_units": redeemed_assets,
        "preview_redeem_units": preview_redeem,
        "final_vs_initial_usdc_units": final_usdc - initial_usdc,
        "gas_used": {
            "approve": int(approval["gasUsed"], 16),
            "deposit": int(deposit["gasUsed"], 16),
            "partial_withdraw": int(partial["gasUsed"], 16),
            "redeem": int(exit_tx["gasUsed"], 16),
        },
        "status": "FORK_ONLY_EXIT_SCENARIOS_SUCCEEDED",
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Local-only Morpho partial withdrawal and full redemption test")
    parser.add_argument("--fork-rpc", default="http://127.0.0.1:8545")
    parser.add_argument("--vault", choices=VAULTS, required=True)
    parser.add_argument("--usdc-holder", required=True)
    args = parser.parse_args(argv)
    try:
        with ForkRPC(args.fork_rpc) as rpc:
            outcome = run_exit_scenarios(rpc, args.vault, args.usdc_holder)
        print(json.dumps(outcome, indent=2))
        print("LOCAL FORK ONLY — no mainnet broadcast; real liquidity not guaranteed.")
        return 0
    except (ValueError, RuntimeError, httpx.HTTPError) as exc:
        print(f"FORK EXIT TEST FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
