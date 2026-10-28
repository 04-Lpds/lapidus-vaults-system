"""Offline security and dependency tests for local-only candidate execution."""
from dataclasses import replace
from unittest.mock import Mock
import pytest
from lapidus_vaults.fork_roundtrip import ACTOR, VAULTS
from lapidus_vaults.unsigned_candidates import UnsignedCandidate
from lapidus_vaults.testing.fork_candidate_rebalance import execute_candidates_locally


def candidate(**overrides):
    c = UnsignedCandidate(
        sequence=1, depends_on=(), method="withdraw_intent", chain_id=8453,
        from_address=ACTOR, to_address=VAULTS[0], value_wei=0,
        data="0xb460af94", requested_assets_units=1_000_000)
    return replace(c, **overrides)


def local_rpc():
    rpc = Mock()
    rpc.url = "http://127.0.0.1:8545"
    rpc.balance.return_value = 10**18
    return rpc


def test_rejects_remote_endpoint_without_sending():
    rpc = local_rpc()
    rpc.url = "https://mainnet.base.org"
    with pytest.raises(ValueError, match="Anvil"):
        execute_candidates_locally(rpc, [candidate()])
    rpc.transact.assert_not_called()


def test_rejects_sender_and_chain_mismatches():
    for change in (dict(chain_id=1), dict(value_wei=1),
                   dict(from_address="0x" + "a"*40)):
        rpc = local_rpc()
        with pytest.raises(RuntimeError, match="mismatch"):
            execute_candidates_locally(rpc, [candidate(**change)])
        rpc.transact.assert_not_called()


def test_rejects_unconfirmed_dependencies():
    rpc = local_rpc()
    with pytest.raises(RuntimeError, match="dependencies"):
        execute_candidates_locally(rpc, [candidate(depends_on=(2,))])
    rpc.transact.assert_not_called()


def test_rejects_invalid_method():
    rpc = local_rpc()
    with pytest.raises(RuntimeError, match="Unsupported"):
        execute_candidates_locally(rpc, [candidate(method="transfer_intent")])
    rpc.transact.assert_not_called()
