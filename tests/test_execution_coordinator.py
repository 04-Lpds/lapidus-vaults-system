"""Coordinated multi-vault workflow must stop on uncertainty."""
import pytest
from lapidus_vaults.execution_coordinator import supervise_candidates
from lapidus_vaults.unsigned_candidates import UnsignedCandidate

def candidates():
    return tuple(UnsignedCandidate(i, deps, kind, 8453, "0x"+"1"*40,
         "0x"+"2"*40, 0, "0x", 1_000_000)
         for i,deps,kind in [
             (1, (), "withdraw_intent"),
             (2, (1,), "approve_intent"),
             (3, (1,2), "deposit_intent")])

def test_only_first_action_ready():
    assert [x.status for x in supervise_candidates(candidates(), {})] == [
        "READY_FOR_MANUAL_PREFLIGHT", "BLOCKED", "BLOCKED"]

def test_confirmed_releases_next_not_entire_bundle():
    assert [x.status for x in supervise_candidates(candidates(), {1:"RECEIPT_AND_BLOCK_MATCH"})] == [
        "CONFIRMED", "READY_FOR_MANUAL_PREFLIGHT", "BLOCKED"]

def test_pending_blocks_all_remaining():
    assert [x.status for x in supervise_candidates(candidates(), {1:"SUBMITTED"})] == [
        "AWAIT_CONFIRMATION", "BLOCKED", "BLOCKED"]

def test_revert_requires_replan():
    assert [x.status for x in supervise_candidates(candidates(), {1:"REVERTED"})] == [
        "HALT_REPLAN", "BLOCKED", "BLOCKED"]

def test_rejects_unrecognized_status_and_sequence():
    with pytest.raises(ValueError):
        supervise_candidates(candidates(), {9:"RECEIPT_AND_BLOCK_MATCH"})
    with pytest.raises(ValueError):
        supervise_candidates(candidates(), {1:"UNKNOWN"})

def test_all_confirmed_complete():
    observed={i:"RECEIPT_AND_BLOCK_MATCH" for i in (1,2,3)}
    assert all(x.status=="CONFIRMED" for x in supervise_candidates(candidates(), observed))
