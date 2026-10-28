"""v0.5 read-only ERC-20/4626 unsigned transaction candidate encoding.

Candidates are NOT a signing bundle or validated execution authorization.
Strict local address/amount validation; no RPC writes or private keys.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Any

from .allocator import BASE_USDC, BASE_CHAIN, address_ok
from .safety_plan import SafetyPlan
from .rpc_verify import _word, _address_word

APPROVE = "0x095ea7b3"
DEPOSIT = "0x6e553f65"
WITHDRAW = "0xb460af94"
SUPPORTED = frozenset({"approve_intent", "deposit_intent", "withdraw_intent"})


@dataclass(frozen=True)
class UnsignedCandidate:
    sequence: int
    depends_on: tuple[int, ...]
    method: str
    chain_id: int
    from_address: str
    to_address: str
    value_wei: int
    data: str
    requested_assets_units: int
    status: str = "UNSIGNED_UNSIMULATED_DO_NOT_SUBMIT"


def make_unsigned_candidates(plan: SafetyPlan, *, approved_vaults: tuple[str, ...],
                             max_assets_per_action_units: int) -> tuple[UnsignedCandidate, ...]:
    if plan.status != "NON_EXECUTABLE_REQUIRES_FRESH_PREFLIGHT" or plan.chain_id != BASE_CHAIN:
        raise ValueError("Plan is not a valid Base read-only intention")
    if not address_ok(plan.wallet):
        raise ValueError("Invalid wallet")
    if type(max_assets_per_action_units) is not int or not 0 < max_assets_per_action_units <= 1_000_000_000:
        raise ValueError("Invalid explicit per-action USDC cap")
    if not approved_vaults or len(set(v.lower() for v in approved_vaults)) != len(approved_vaults) or not all(address_ok(v) for v in approved_vaults):
        raise ValueError("Explicit unique vault allowlist required")
    permitted = frozenset(v.lower() for v in approved_vaults)
    expected_sequence = 1
    candidates = []
    for action in plan.actions:
        if action.sequence != expected_sequence:
            raise ValueError("Actions must have contiguous sequence")
        expected_sequence += 1
        if action.kind not in SUPPORTED or action.vault.lower() not in permitted:
            raise ValueError("Unsupported method or vault outside allowlist")
        if type(action.amount_units) is not int or not 0 < action.amount_units <= max_assets_per_action_units:
            raise ValueError("Operation exceeds explicit USDC limit")
        if not all(type(dep) is int and 1 <= dep < action.sequence for dep in action.depends_on):
            raise ValueError("Invalid transaction dependencies")
        if len(set(action.depends_on)) != len(action.depends_on):
            raise ValueError("Repeated dependency")
        vault = action.vault.lower()
        if action.kind == "approve_intent":
            to = BASE_USDC
            data = APPROVE + _address_word(vault) + _word(action.amount_units)
        elif action.kind == "deposit_intent":
            to = vault
            data = DEPOSIT + _word(action.amount_units) + _address_word(plan.wallet)
        else:
            to = vault
            data = WITHDRAW + _word(action.amount_units) + _address_word(plan.wallet) + _address_word(plan.wallet)
        candidates.append(UnsignedCandidate(
            action.sequence, action.depends_on, action.kind,
            BASE_CHAIN, plan.wallet.lower(), to, 0, data,
            action.amount_units,
        ))
    return tuple(candidates)


def candidate_report(candidates: tuple[UnsignedCandidate, ...]) -> list[dict[str, Any]]:
    return [asdict(candidate) for candidate in candidates]
