"""Full unattended campaign lifecycle on a LOCAL Anvil Base fork.

Runs the *actual* auto_once orchestrator repeatedly across separate calls,
with production quote/consume, a disposable EIP-1559 key, signed transactions,
SQLite write-ahead submission reservations, and production receipt bridge.
All network transport is replaced by the strictly loopback-only ForkRPC.
No real Base transaction can be sent through this harness.
"""
from __future__ import annotations
from dataclasses import asdict

import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

from eth_account import Account

from ..fork_roundtrip import (
    ForkRPC, BASE_USDC, VAULTS, ASSET, addr_word, word,
)
from ..fork_campaign_rehearsal import LocalForkReadAdapter
from ..unsigned_candidates import UnsignedCandidate, APPROVE, DEPOSIT
from ..operations.campaigns import create_campaign, read_campaign
from ..operations.auto_journal import (
    reserve, mark_submitted, current,
)
from ..operations.dedicated_signer import prepare_signed_transaction
from ..operations import auto_once
from ..live_deposit import DepositQuotes
from .signed_fork_rehearsal import _send


@contextmanager
def _read_adapter(rpc: ForkRPC):
    yield LocalForkReadAdapter(rpc)


class _UnusedClient:
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return None


class _PendingOnce(DepositQuotes):
    def check_receipt(self, rpc, quote_id, tx_hash):
        return {
            "status": "PENDING_NOT_FOUND",
            "tx_hash": tx_hash,
            "matched": False,
        }


def run_auto_lifecycle(rpc: ForkRPC, holder: str, vault: str, *, scenario: str = "profitable", destination: str | None = None, inject_post_broadcast_crash: bool = False) -> dict:
    if scenario not in {"profitable", "uneconomic", "tampered"}:
        raise ValueError("Unknown synthetic fork scenario")
    addr_word(holder)
    if vault not in VAULTS or int(rpc.call("eth_chainId", []), 16) != 8453:
        raise ValueError("Expected allowlisted Base vault on local Anvil")
    if "0x" + format(rpc.uint(vault, ASSET), "040x") != BASE_USDC:
        raise RuntimeError("Vault is not native Base USDC")
    if len(rpc.call("eth_getCode", [vault, "latest"])) < 10:
        raise RuntimeError("Vault bytecode unavailable")
    if destination is not None and (destination not in VAULTS or destination == vault):
        raise ValueError("Distinct allowlisted fork destination required")
    if destination is not None and "0x" + format(rpc.uint(destination, ASSET), "040x") != BASE_USDC:
        raise ValueError("Destination underlying must be native Base USDC")
    amount = 800_000

    # Reject bad synthetic proposals BEFORE funding any disposable wallet.
    # This also ensures repeated negative scenarios cannot consume fork USDC.
    from dataclasses import asdict
    from ..operations.campaign_admission import assess_admission
    from ..rpc_verify import _address_word, _word
    from ..unsigned_candidates import APPROVE, DEPOSIT
    # A test-only wallet address is enough for admission. Never read a private key
    # or submit a transaction for a blocked scenario.
    if scenario != "profitable":
        mock_wallet = "0x" + "1" * 40
        mock_candidates = (
            UnsignedCandidate(1, (), "approve_intent", 8453,
                mock_wallet, BASE_USDC, 0,
                APPROVE + _address_word(vault) + _word(amount), amount),
            UnsignedCandidate(2, (1,), "deposit_intent", 8453,
                mock_wallet, vault, 0,
                DEPOSIT + _word(amount + (1 if scenario == "tampered" else 0)) + _address_word(mock_wallet), amount),
        )
        mock_economics = {
            "decision": "HOLD_ESTIMATED_FEES_EXCEED_BENEFIT" if scenario == "uneconomic"
                       else "REBALANCE_REVIEW_ONLY",
            "submitted": False, "capital_units": 5_000_000,
            "estimated_fee_usd": "0.50" if scenario == "uneconomic" else "0.001",
            "projected_historical_rate_yield_delta_usd": "0.10",
            "eth_usd_assumption": "3000",
        }
        admission = assess_admission(economics=mock_economics,
            candidates=[asdict(x) for x in mock_candidates],
            wallet=mock_wallet, approved_vaults=(vault,))
        if admission["status"] != "ADMISSION_BLOCKED":
            raise RuntimeError("Unsafe synthetic scenario unexpectedly admitted")
        return {
            "status": "FORK_SCENARIO_BLOCKED_NO_CAMPAIGN_TRANSACTIONS",
            "scenario": scenario, "admission": admission,
            "execution_mode": "cross_vault" if destination is not None else "deposit",
            "campaign_created": False, "signed_transactions": [],
            "mainnet_broadcast": False,
            "note": "Rejected before fork wallet funding, signing or campaign creation.",
        }

    # Seed a small surplus into the source vault: ERC-4626 share rounding
    # can make withdrawing exactly the deposited assets require extra shares.
    seed_amount = amount + 10_000 if destination is not None else amount
    if rpc.balance(BASE_USDC, holder) < seed_amount:
        raise RuntimeError("Fork holder lacks seed USDC")

    disposable = Account.create()
    wallet = disposable.address.lower()
    secret = "0x" + bytes(disposable.key).hex()
    rpc.call("anvil_setBalance", [wallet, hex(10**16)])
    rpc.call("anvil_setBalance", [holder, hex(10**16)])
    rpc.call("anvil_impersonateAccount", [holder])
    try:
        rpc.transact(BASE_USDC, holder,
                     "0xa9059cbb" + addr_word(wallet) + word(seed_amount),
                     step="fund disposable auto-test wallet")
    finally:
        rpc.call("anvil_stopImpersonatingAccount", [holder])

    if destination is not None:
        # Bootstrap the SOURCE position on the fork only. This setup is not
        # counted as an autonomous campaign send.
        rpc.call("anvil_impersonateAccount", [wallet])
        try:
            rpc.transact(BASE_USDC, wallet,
                APPROVE + addr_word(vault) + word(seed_amount),
                step="seed source vault approval")
            rpc.transact(vault, wallet,
                DEPOSIT + word(seed_amount) + addr_word(wallet),
                step="seed source vault position")
        finally:
            rpc.call("anvil_stopImpersonatingAccount", [wallet])
        initial_source_shares = rpc.balance(vault, wallet)
        if initial_source_shares <= 0:
            raise RuntimeError("Source seeding minted no shares")

    # The same observed-portfolio candidate builder as the live keyless
    # controller now produces the signed fork campaign actions.
    from ..operations.observed_campaign import propose_candidates
    fork_observation = {"plan": {
        "chain_id": 8453, "wallet": wallet,
        "observed_block": int(rpc.call("eth_blockNumber", []), 16),
        "capital_units": amount, "wallet_usdc_units": rpc.balance(BASE_USDC, wallet),
        "changes": ([{"vault_address": vault, "current_units": amount,
                       "target_units": 0, "delta_units": -amount},
                      {"vault_address": destination, "current_units": 0,
                       "target_units": amount, "delta_units": amount}]
                    if destination is not None else
                    [{"vault_address": vault, "current_units": 0,
                      "target_units": amount, "delta_units": amount}]),
    }}
    actions = propose_candidates(fork_observation, wallet=wallet,
                                  approved_vaults=(vault, destination) if destination is not None else (vault,))
    expected_methods = (["withdraw_intent", "approve_intent", "deposit_intent"]
                        if destination is not None else ["approve_intent", "deposit_intent"])
    if [a.method for a in actions] != expected_methods:
        raise RuntimeError("Fork proposal did not produce expected safe lifecycle")

    # Test journal is always isolated from operator's persistent DB.
    temporary = tempfile.mkdtemp(prefix="lvs-auto-lifecycle-")
    db_path = str(Path(temporary) / "fork-only.sqlite3")
    campaign_id = "local-autonomous-restart"
    # Explicit synthetic economics must NEVER be used by the mainnet controller.
    # This gate is local-only and has no private-key or production database access.
    from ..operations.campaign_admission import assess_admission
    economics = {
        "decision": "REBALANCE_REVIEW_ONLY" if scenario != "uneconomic"
                    else "HOLD_ESTIMATED_FEES_EXCEED_BENEFIT",
        "submitted": False, "capital_units": 5_000_000,
        "estimated_fee_usd": "0.001" if scenario != "uneconomic" else "0.50",
        "projected_historical_rate_yield_delta_usd": "0.10",
        "eth_usd_assumption": "3000",
    }
    proposed = [asdict(action) for action in actions]
    if scenario == "tampered":
        proposed[1]["data"] = DEPOSIT + word(amount + 1) + addr_word(wallet)
    admission = assess_admission(economics=economics, candidates=proposed,
                                 wallet=wallet, approved_vaults=(vault, destination) if destination is not None else (vault,))
    if admission["status"] != "ADMISSION_REVIEW_ONLY":
        if scenario == "profitable":
            raise RuntimeError("Profitable test scenario unexpectedly blocked")
        return {
            "status": "FORK_SCENARIO_BLOCKED_NO_CAMPAIGN_TRANSACTIONS",
            "scenario": scenario, "admission": admission,
            "campaign_created": False, "signed_transactions": [],
            "note": "Fork funding setup occurred before admission; no campaign actions were submitted.",
            "mainnet_broadcast": False,
        }
    if scenario != "profitable":
        raise RuntimeError("Unsafe scenario incorrectly admitted")
    # Only validated candidate bytes can enter the durable campaign journal.
    create_campaign(db_path, campaign_id, actions)
    sends = []

    def fork_broadcast(**kwargs):
        signed = prepare_signed_transaction(
            consumed=kwargs["consumed"], candidate=kwargs["candidate"],
            policy_wallet=kwargs["policy_wallet"],
            protected_wallet=kwargs["protected_wallet"],
            private_key=kwargs["private_key"],
            nonce=kwargs["nonce"],
            gas_price_wei=kwargs["gas_price_wei"],
            approved_vaults=kwargs["approved_vaults"],
            # Synthetic fork prices only; live default cap remains unchanged.
            max_total_fee_wei=5_000_000_000_000_000,
        )
        reserve(db_path, campaign_id=kwargs["campaign_id"],
                sequence=kwargs["sequence"], quote_id=kwargs["quote_id"],
                wallet=wallet, to_address=kwargs["consumed"]["to"],
                calldata=kwargs["consumed"]["data"], amount_units=amount)
        # Signed hash must exist in SQLite BEFORE any network activity.
        mark_submitted(db_path, campaign_id=kwargs["campaign_id"],
                       sequence=kwargs["sequence"], quote_id=kwargs["quote_id"],
                       tx_hash=signed["tx_hash"])
        if len(sends) >= len(actions):
            raise RuntimeError("Unexpected additional send")
        _send(rpc, signed["raw"], signed["tx_hash"])
        sends.append(signed["tx_hash"])
        if inject_post_broadcast_crash and len(sends) == 1:
            raise RuntimeError("INJECTED_CRASH_AFTER_BROADCAST")
        return {"state": "SUBMITTED", "tx_hash": signed["tx_hash"]}

    def nonce(_url, address, tag):
        return int(rpc.call("eth_getTransactionCount", [address, tag]), 16)

    def step():
        return auto_once.execute_once(
            db_path=db_path, wallet=wallet,
            protected_wallet=holder.lower(), approved_vaults=(vault, destination) if destination is not None else (vault,),
            allowlist_path="local-only-allowlist",
            rpc_url="https://local-only.invalid",
            private_key=secret, max_capital_units=10_000_000,
        )

    patches = [
        patch.object(auto_once, "ReadOnlyRPC",
                     lambda _: _read_adapter(rpc)),
        patch.object(auto_once, "MorphoClient", _UnusedClient),
        patch.object(auto_once, "build_live_readonly_plan",
                     lambda *a, **kw: {"plan": {"capital_units": amount}}),
        patch.object(auto_once, "read_nonce", nonce),
        patch.object(auto_once, "reserve_and_broadcast", fork_broadcast),
    ]
    for p in patches:
        p.start()
    try:
        from ..operations.campaigns import read_campaign
        observations = []
        for i in range(len(actions)):
            if i == 0 and inject_post_broadcast_crash:
                try:
                    step()
                except RuntimeError as exc:
                    if str(exc) != "INJECTED_CRASH_AFTER_BROADCAST":
                        raise
                else:
                    raise RuntimeError("Crash injection failed to trigger")
                if len(sends) != 1 or current(db_path,campaign_id)[0]["state"] != "SUBMITTED":
                    raise RuntimeError("Crash lost hash journal")
                submitted = {"state":"SUBMITTED","tx_hash":sends[0]}
            else:
                submitted = step()
            if submitted.get("state") != "SUBMITTED" or len(sends) != i + 1:
                raise RuntimeError(f"Autonomous step {i + 1} not submitted")
            observations.append(submitted)
            if i == 0:
                # Force one delayed receipt lookup, ensure no blind resend.
                with patch.object(auto_once, "WithdrawalQuotes" if destination is not None else "DepositQuotes", _PendingOnce):
                    pending = step()
                if pending.get("status") != "PENDING_NOT_FOUND" or len(sends) != 1:
                    raise RuntimeError("Unindexed receipt triggered a duplicate send")
            recovered = step()
            if recovered.get("status") != "RECEIPT_AND_BLOCK_MATCH":
                raise RuntimeError(f"Receipt recovery failed on step {i + 1}")
            observations.append(recovered)
        completed = read_campaign(db_path, campaign_id)
        if completed["state"] != "COMPLETED":
            raise RuntimeError("Campaign failed to complete")
        after = step()
        if after.get("status") != "NO_ACTIVE_CAMPAIGN" or len(sends) != len(actions):
            raise RuntimeError("Completed campaign attempted another send")
        journal = current(db_path, campaign_id)
        if [e["state"] for e in journal] != ["CONFIRMED"] * len(actions):
            raise RuntimeError("Journal does not match confirmed campaign")
        if destination is None:
            if rpc.balance(BASE_USDC, wallet) != 0 or rpc.balance(vault, wallet) <= 0:
                raise RuntimeError("Final fork balances mismatch")
        else:
            if (rpc.balance(BASE_USDC, wallet) != 0 or
                rpc.balance(destination, wallet) <= 0 or
                rpc.balance(vault, wallet) >= initial_source_shares):
                raise RuntimeError("Cross-vault balances failed reconciliation")

        return {
            "status": "AUTO_LIFECYCLE_FORK_PASSED",
            "scenario": scenario, "admission": admission,
            "execution_mode": "cross_vault" if destination is not None else "deposit",
            "campaign_id": campaign_id,
            "campaign_state": completed["state"],
            "disposable_wallet": wallet,
            "signed_transactions": sends,
            "journal": journal,
            "pending_without_resend": True,
            "post_broadcast_crash_injected": inject_post_broadcast_crash,
            "recovered_after_restart": True,
            "repeated_submission_count": len(sends),
            "mainnet_broadcast": False,
            "sqlite_db": db_path,
            "sqlite_note": "Disposable local-fork journal retained for inspection; delete later.",
        }
    finally:
        for p in reversed(patches):
            p.stop()


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Full signed autonomous campaign + receipt recovery on LOCAL Anvil only"
    )
    parser.add_argument("--fork-rpc", default="http://127.0.0.1:8545")
    parser.add_argument("--usdc-holder", required=True)
    parser.add_argument("--vault", required=True, choices=VAULTS)
    parser.add_argument("--destination", choices=VAULTS, help="Optional destination vault for unattended cross-vault lifecycle")
    parser.add_argument("--scenario", choices=("profitable", "uneconomic", "tampered"), default="profitable")
    parser.add_argument("--inject-post-broadcast-crash",action="store_true")
    args = parser.parse_args(argv)
    try:
        with ForkRPC(args.fork_rpc) as rpc:
            report = run_auto_lifecycle(rpc, args.usdc_holder, args.vault, scenario=args.scenario, destination=args.destination, inject_post_broadcast_crash=args.inject_post_broadcast_crash)
        print(json.dumps(report, indent=2))
        print("LOCAL ANVIL ONLY. No mainnet signing or broadcast.")
        return 0
    except Exception as exc:
        print(f"AUTO LIFECYCLE FORK FAILED: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
