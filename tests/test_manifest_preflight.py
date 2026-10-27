"""Offline saved-review freshness and integrity checks."""
from copy import deepcopy
from unittest.mock import Mock
import pytest

from lapidus_vaults.fork_roundtrip import ACTOR, VAULTS
from lapidus_vaults.manifest_preflight import validate_manifest, inspect_saved_manifest
from test_transaction_review import synthetic_report
from lapidus_vaults.transaction_review import prepare_manifest


def manifest():
    return prepare_manifest(synthetic_report(), VAULTS, cap_usdc=10)


def test_fresh_and_stale_manifest():
    m = manifest()
    good = validate_manifest(m, wallet=ACTOR, vaults=VAULTS, current_block=1240)
    assert good["status"] == "FRESH_INDEPENDENT_CALL_CHECKS_ONLY"
    stale = validate_manifest(m, wallet=ACTOR, vaults=VAULTS, current_block=1260)
    assert stale["status"] == "STALE_REPLAN_REQUIRED"
    assert not stale["authorization"]


def test_tampering_invalidates_integrity():
    m = manifest()
    m["candidates"][0]["requested_assets_units"] = 1
    with pytest.raises(ValueError, match="hash mismatch"):
        validate_manifest(m, wallet=ACTOR, vaults=VAULTS, current_block=1240)


def test_independent_calls_cannot_authorize_execution():
    rpc = Mock()
    rpc.chain_id.return_value = 8453
    rpc.block_number.return_value = 1240
    rpc.call_uint.return_value = 0
    rpc.simulate_call.return_value = "0x" + "0"*64
    result = inspect_saved_manifest(rpc, manifest(), wallet=ACTOR, vaults=VAULTS)
    assert result["status"] == "REVIEW_ONLY_NOT_EXECUTABLE"
    assert not result["authorization"]
    assert len(result["independent_calls"]) == 3
    assert result["independent_calls"][1]["status"] == "NOT_SIMULATED_APPROVAL"


def test_future_block_rejected():
    with pytest.raises(ValueError, match="future"):
        validate_manifest(manifest(), wallet=ACTOR, vaults=VAULTS, current_block=1200)
