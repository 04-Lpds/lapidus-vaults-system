"""Fail-closed supervisor for multi-vault transaction candidates.

This coordinator never signs or broadcasts. Only confirmed, reconciled
dependencies may unlock a later candidate, and every step still requires
a fresh independent on-chain preflight.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .unsigned_candidates import UnsignedCandidate

CONFIRMED = "RECEIPT_AND_BLOCK_MATCH"
TERMINAL_FAILURES = frozenset({"REVERTED", "RECONCILIATION_MISMATCH", "FAILED"})
PENDING = frozenset({"PENDING_NOT_FOUND", "PENDING_MATCHED_INPUT", "SUBMITTED"})


@dataclass(frozen=True)
class StepDecision:
    sequence: int
    method: str
    status: str
    reason: str
    dependencies: tuple[int, ...]


def supervise_candidates(
    candidates: tuple[UnsignedCandidate, ...],
    observations: Mapping[int, str],
) -> tuple[StepDecision, ...]:
    """Evaluate a sequence, never converting unsigned calldata into permission.

    At most one unconfirmed step can be READY_FOR_MANUAL_PREFLIGHT.
    Any pending/failed/unknown previous step halts progress to avoid
    accidentally executing an outdated allocation after partial success.
    """
    if not isinstance(candidates, tuple) or len(candidates) > 30:
        raise ValueError("Expected bounded candidate tuple")
    if set(observations) - {c.sequence for c in candidates}:
        raise ValueError("Unknown observed step")
    decisions = []
    seen = set()
    blocked = False
    for index, candidate in enumerate(candidates, 1):
        if candidate.sequence != index or any(dep not in seen for dep in candidate.depends_on):
            raise ValueError("Invalid candidate sequence or dependency")
        if candidate.status != "UNSIGNED_UNSIMULATED_DO_NOT_SUBMIT":
            raise ValueError("Unexpected executable candidate")
        seen.add(candidate.sequence)
        observed = observations.get(candidate.sequence)
        if observed is not None and observed not in PENDING | TERMINAL_FAILURES | {CONFIRMED}:
            raise ValueError("Unrecognized receipt state")
        if observed == CONFIRMED:
            if blocked:
                raise ValueError("Confirmed later action conflicts with unresolved earlier action")
            status, reason = "CONFIRMED", "Matching receipt and balance reconciliation recorded"
        elif observed in TERMINAL_FAILURES:
            status, reason = "HALT_REPLAN", "Failed or mismatched transaction; refresh entire portfolio"
            blocked = True
        elif observed in PENDING:
            status, reason = "AWAIT_CONFIRMATION", "Do not resend; check submitted transaction"
            blocked = True
        elif blocked or any(observations.get(dep) != CONFIRMED for dep in candidate.depends_on):
            status, reason = "BLOCKED", "Prior or dependent step requires resolution"
        else:
            status, reason = "READY_FOR_MANUAL_PREFLIGHT", "Re-read balances, liquidity, fees and simulate before MetaMask"
            blocked = True
        decisions.append(StepDecision(candidate.sequence, candidate.method, status,
                                      reason, candidate.depends_on))
    return tuple(decisions)
