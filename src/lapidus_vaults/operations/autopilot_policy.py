"""Fail-closed policy gate for future unattended execution.

No keys, wallet connection or transaction sending. The same gate is called
before selecting each action. An explicit and narrow dedicated-wallet policy
must be supplied by a higher-level executor.
"""
from __future__ import annotations

from dataclasses import dataclass

from .next_action import next_campaign_action
from ..allocator import BASE_USDC, address_ok

@dataclass(frozen=True)
class AutoPolicy:
    wallet: str
    vault_allowlist: tuple[str, ...]
    max_action_units: int
    max_capital_units: int
    chain_id: int = 8453
    live_enabled: bool = False

    def __post_init__(self) -> None:
        if not address_ok(self.wallet) or self.wallet != self.wallet.lower():
            raise ValueError("Dedicated-wallet address must be normalized")
        if self.chain_id != 8453:
            raise ValueError("Base only")
        if not self.vault_allowlist or len(set(self.vault_allowlist)) != len(self.vault_allowlist):
            raise ValueError("Distinct, explicit vault allowlist required")
        if not all(address_ok(v) and v == v.lower() for v in self.vault_allowlist):
            raise ValueError("Vaults must be valid normalized addresses")
        if type(self.max_action_units) is not int or not 0 < self.max_action_units <= 1_000_000:
            raise ValueError("Unattended actions capped at 1 USDC")
        if type(self.max_capital_units) is not int or not 0 < self.max_capital_units <= 10_000_000:
            raise ValueError("Dedicated automated capital capped at 10 USDC")
        if self.max_action_units > self.max_capital_units:
            raise ValueError("Action exceeds capital cap")
        if self.live_enabled:
            raise ValueError("Unattended live signing is not implemented or authorized")


def next_auto_action(db_path: str, *, policy: AutoPolicy, observed_capital_units: int,
                     current_chain_id: int) -> dict:
    if current_chain_id != policy.chain_id:
        raise ValueError("Wrong chain")
    if type(observed_capital_units) is not int or not 0 <= observed_capital_units <= policy.max_capital_units:
        raise ValueError("Wallet capital outside unattended policy")
    projected = next_campaign_action(
        db_path, wallet=policy.wallet, approved_vaults=policy.vault_allowlist
    )
    if projected["status"] != "READY_FOR_MANUAL_PREFLIGHT":
        return {**projected, "automatic_submission_enabled": False}
    if projected["amount_units"] > policy.max_action_units:
        raise ValueError("Action exceeds unattended policy cap")
    if projected["method"] not in {"approve_intent", "deposit_intent", "withdraw_intent"}:
        raise ValueError("Unexpected campaign method")
    return {
        **projected,
        "automatic_submission_enabled": False,
        "status": "AUTO_POLICY_PASSED_AWAITING_SECURE_SIGNER",
        "warning": "No live signing or broadcasting; local fork rehearsal only",
    }
