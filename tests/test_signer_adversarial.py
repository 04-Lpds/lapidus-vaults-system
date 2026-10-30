"""Adversarial signer boundary tests: disposable keys and intercepted transports."""
from contextlib import nullcontext
from dataclasses import asdict
import json
import sqlite3
import time
from unittest.mock import Mock

import pytest

from test_dedicated_signer import make, make_rpc, make_policy, sign, V, PROTECTED, AMOUNT
from lapidus_vaults.allocator import BASE_USDC
from lapidus_vaults.operations import dedicated_signer as signer
from lapidus_vaults.operations.auto_journal import reserve, current
from lapidus_vaults.operations.campaigns import create_campaign, halt_campaign
from lapidus_vaults.operations.receipt_bridge import bind_campaign_quote
from lapidus_vaults.unsigned_candidates import UnsignedCandidate


def journal_args(db, campaign="one", sequence=1, quote="q1"):
    return dict(db_path=str(db), campaign_id=campaign, sequence=sequence,
                quote_id=quote, wallet=PROTECTED, to_address=V,
                calldata="0x12345678", amount_units=AMOUNT)


def test_journal_blocks_unresolved_submission_from_another_campaign(tmp_path):
    db = tmp_path / "journal.sqlite"
    reserve(**journal_args(db))
    with pytest.raises(ValueError, match="unresolved"):
        reserve(**journal_args(db, campaign="two", quote="q2"))


def test_journal_cannot_start_at_later_step(tmp_path):
    with pytest.raises(ValueError, match="sequence"):
        reserve(**journal_args(tmp_path / "journal.sqlite", sequence=2))


@pytest.mark.parametrize("latest,pending,nonce", [(1,1,0), (0,1,0), (0,0,1)])
def test_no_signature_with_used_pending_or_future_nonce(latest, pending, nonce, monkeypatch):
    from eth_account import Account
    a, key, c, plan = make()
    rpc = make_rpc("approve")
    rpc.transaction_count.side_effect = lambda wallet, tag: latest if tag == "latest" else pending
    signing = Mock(side_effect=AssertionError("must not sign"))
    monkeypatch.setattr(Account, "sign_transaction", signing)
    with pytest.raises(ValueError, match="nonce|pending"):
        sign(a, key, c, plan, rpc=rpc, nonce=nonce)
    signing.assert_not_called()


def test_residual_allowance_exceeding_exact_action_is_rejected():
    a, key, c, plan = make("deposit")
    rpc = make_rpc("deposit")
    original = rpc.call_uint.side_effect
    rpc.call_uint.side_effect = lambda target, selector, block, *words: (
        AMOUNT + 1 if selector == "0xdd62ed3e" else original(target, selector, block, *words))
    with pytest.raises(ValueError, match="allowance"):
        sign(a, key, c, plan, rpc=rpc)


def test_base_l1_fee_cannot_spend_eth_reserve():
    a, key, c, plan = make()
    rpc = make_rpc("approve")
    rpc.eth_balance.return_value = 210_000_000_000_000
    original = rpc.call_uint.side_effect
    rpc.call_uint.side_effect = lambda target, selector, block, *words: (
        20_000_000_000_000 if selector == "0xf1c7a58b" else original(target, selector, block, *words))
    with pytest.raises(ValueError, match="reserve|fee"):
        sign(a, key, c, plan, rpc=rpc)


def bound_execution(tmp_path, monkeypatch):
    a, key, c, plan = make()
    path = str(tmp_path / "bound.sqlite")
    candidate = UnsignedCandidate(1, (), "approve_intent", 8453, a.address.lower(),
                                  BASE_USDC, 0, c["data"], AMOUNT)
    plan = json.loads(json.dumps(asdict(candidate)))
    create_campaign(path, "one", (candidate,))
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE live_deposit_authorizations(
            quote_id TEXT, wallet TEXT, vault TEXT, stage TEXT,
            amount_units INTEGER, transaction_json TEXT)""")
        db.execute("INSERT INTO live_deposit_authorizations VALUES (?,?,?,?,?,?)",
                   ("q1", c["wallet"], c["vault"], c["stage"], AMOUNT, json.dumps(c)))
    bind_campaign_quote(path, "one", 1, "q1")
    rpc = make_rpc("approve")
    monkeypatch.setattr(signer, "ReadOnlyRPC", lambda _: nullcontext(rpc))
    transport = Mock()
    transport.return_value.__enter__ = Mock(return_value=transport)
    transport.return_value.__exit__ = Mock(return_value=False)
    # No blockchain transport is ever allowed to reach a socket.
    transport.post.side_effect = AssertionError("must not broadcast")
    monkeypatch.setattr(signer.httpx, "Client", transport)
    params = dict(db_path=path, campaign_id="one", sequence=1, quote_id="q1",
        consumed=c, candidate=plan, policy_wallet=a.address.lower(),
        protected_wallet=PROTECTED, private_key=key, rpc_url="https://example.invalid",
        nonce=0, gas_price_wei=6_000_000, approved_vaults=(V,), policy=make_policy(a.address.lower()))
    return params, transport


def test_halt_during_signing_blocks_broadcast(tmp_path, monkeypatch):
    params, transport = bound_execution(tmp_path, monkeypatch)
    original = signer.prepare_signed_transaction
    def sign_and_halt(**kwargs):
        result = original(**kwargs)
        halt_campaign(params["db_path"], "one")
        return result
    monkeypatch.setattr(signer, "prepare_signed_transaction", sign_and_halt)
    with pytest.raises(ValueError, match="active|ACTIVE|halt"):
        signer.reserve_and_broadcast(**params)
    transport.post.assert_not_called()


def test_expiry_after_signing_blocks_broadcast(tmp_path, monkeypatch):
    params, transport = bound_execution(tmp_path, monkeypatch)
    clock = [time.time()]
    monkeypatch.setattr(signer.time, "time", lambda: clock[0])
    original = signer.prepare_signed_transaction
    def sign_and_delay(**kwargs):
        result = original(**kwargs)
        clock[0] += 100
        return result
    monkeypatch.setattr(signer, "prepare_signed_transaction", sign_and_delay)
    with pytest.raises(ValueError, match="expired|expired signer authorization"):
        signer.reserve_and_broadcast(**params)
    transport.post.assert_not_called()


def test_nonce_changes_after_signing_blocks_send(tmp_path, monkeypatch):
    params, transport = bound_execution(tmp_path, monkeypatch)
    original = signer.prepare_signed_transaction
    def sign_then_other_activity(**kwargs):
        result = original(**kwargs)
        kwargs["rpc"].transaction_count.return_value = 1
        return result
    monkeypatch.setattr(signer, "prepare_signed_transaction", sign_then_other_activity)
    with pytest.raises(ValueError, match="nonce"):
        signer.reserve_and_broadcast(**params)
    transport.post.assert_not_called()
    assert current(params["db_path"], "one")[0]["state"] == "SUBMITTED"
    with pytest.raises(ValueError, match="Duplicate"):
        signer.reserve_and_broadcast(**params)


def test_crash_after_hash_persisted_never_signs_or_sends_again(tmp_path, monkeypatch):
    params, transport = bound_execution(tmp_path, monkeypatch)
    original = signer.mark_submitted
    def persist_and_crash(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("crash after committed signed hash")
    monkeypatch.setattr(signer, "mark_submitted", persist_and_crash)
    signing = Mock(wraps=signer.prepare_signed_transaction)
    monkeypatch.setattr(signer, "prepare_signed_transaction", signing)
    with pytest.raises(RuntimeError, match="crash after committed"):
        signer.reserve_and_broadcast(**params)
    saved = current(params["db_path"], "one")[0]
    assert saved["state"] == "SUBMITTED" and saved["tx_hash"]
    with pytest.raises(ValueError, match="Duplicate"):
        signer.reserve_and_broadcast(**params)
    assert signing.call_count == 1
    transport.post.assert_not_called()


def test_emergency_stop_created_after_signing_blocks_send(tmp_path, monkeypatch):
    from pathlib import Path
    monkeypatch.chdir(tmp_path)
    params, transport = bound_execution(tmp_path, monkeypatch)
    original = signer.prepare_signed_transaction
    def sign_then_stop(**kwargs):
        result = original(**kwargs)
        Path("data").mkdir()
        Path("data/STOP_AUTOMATION").touch()
        return result
    monkeypatch.setattr(signer, "prepare_signed_transaction", sign_then_stop)
    with pytest.raises(ValueError, match="Emergency stop"):
        signer.reserve_and_broadcast(**params)
    transport.post.assert_not_called()


def test_l1_oracle_unavailable_blocks_signature():
    a, key, c, plan = make()
    rpc = make_rpc("approve")
    original = rpc.call_uint.side_effect
    def fail_oracle(target, selector, block, *words):
        if selector == "0xf1c7a58b":
            raise RuntimeError("oracle unavailable")
        return original(target, selector, block, *words)
    rpc.call_uint.side_effect = fail_oracle
    with pytest.raises(RuntimeError, match="oracle unavailable"):
        sign(a, key, c, plan, rpc=rpc)


def test_candidate_eth_value_cannot_be_silently_discarded():
    a, key, c, plan = make()
    plan["value_wei"] = 1
    with pytest.raises(ValueError, match="differs from campaign"):
        sign(a, key, c, plan)


def test_bound_later_step_cannot_skip_campaign_confirmation(tmp_path, monkeypatch):
    params, transport = bound_execution(tmp_path, monkeypatch)
    with sqlite3.connect(params["db_path"]) as db:
        second = {**params["candidate"], "sequence":2, "depends_on":[1]}
        db.execute("UPDATE rebalance_campaigns SET plan_json=?", (json.dumps([params["candidate"], second]),))
        db.execute("UPDATE rebalance_campaign_quote_bindings SET sequence=2")
    params.update(sequence=2, candidate=second)
    with pytest.raises(ValueError, match="Earlier campaign steps"):
        signer.reserve_and_broadcast(**params)
    transport.post.assert_not_called()


def test_chain_advancing_during_safety_reads_invalidates_authorization():
    from lapidus_vaults.operations.dedicated_signer import validate_nonce_and_budget
    a, _, c, _ = make()
    rpc = make_rpc("approve")
    # Oracle/balance/nonce reads may be slow enough for the head to advance
    # beyond the reviewed block window, while wall-clock TTL still passes.
    rpc.block_number.side_effect = [100, 111]
    with pytest.raises(ValueError, match="expired during RPC"):
        validate_nonce_and_budget(rpc=rpc, consumed=c, policy=make_policy(a.address.lower()),
                                  nonce=0, gas_price_wei=6_000_000)
