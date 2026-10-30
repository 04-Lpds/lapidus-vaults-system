"""Dedicated-wallet signed calldata safety regressions."""
import time
from dataclasses import replace
from unittest.mock import Mock
import pytest
from eth_account import Account
from lapidus_vaults.allocator import BASE_USDC
from lapidus_vaults.rpc_verify import _address_word, _word
from lapidus_vaults.unsigned_candidates import APPROVE, DEPOSIT, WITHDRAW
from lapidus_vaults.operations.dedicated_signer import prepare_signed_transaction, build_unsigned_transaction
from lapidus_vaults.operations.signer_policy import SignerPolicy, load_signer_policy

V = "0x" + "2" * 40
PROTECTED = "0x" + "3" * 40
AMOUNT = 800_000

def make(stage="approve", amount=AMOUNT):
    a = Account.create()
    wallet = a.address.lower()
    key = "0x" + bytes(a.key).hex()
    if stage == "approve":
        dest = BASE_USDC
        data = APPROVE + _address_word(V) + _word(amount)
    elif stage == "deposit":
        dest = V
        data = DEPOSIT + _word(amount) + _address_word(wallet)
    else:
        dest = V
        data = WITHDRAW + _word(amount) + _address_word(wallet) + _address_word(wallet)
    consumed = {"chain_id":8453,"stage":stage,"wallet":wallet,
                "vault":V,"amount_units":amount,"to":dest,
                "data":data,"gas":"0x186a0","value":"0x0",
                "observed_block":100,"authorized_at_unix":time.time(),
                "max_burned_shares":1_000_100}
    candidate = {"chain_id":8453,"value_wei":0,"from_address":wallet,"to_address":dest,
                 "data":data,"requested_assets_units":amount,
                 "method":stage+"_intent"}
    return a,key,consumed,candidate

def sign(a,key,c,plan,**changes):
    params=dict(consumed=c,candidate=plan,policy_wallet=a.address.lower(),
                protected_wallet=PROTECTED,private_key=key,nonce=0,
                gas_price_wei=6_000_000,approved_vaults=(V,),
                policy=make_policy(a.address.lower()), rpc=make_rpc(c["stage"]))
    params.update(changes)
    return prepare_signed_transaction(**params)

def test_exact_approval_can_be_signed_and_recovered():
    a,key,c,plan = make()
    result=sign(a,key,c,plan)
    assert result["tx_hash"].startswith("0x")
    assert len(result["tx_hash"])==66
    assert Account.recover_transaction(result["raw"]).lower()==a.address.lower()

def test_exact_deposit_can_be_signed():
    a,key,c,plan = make("deposit")
    assert Account.recover_transaction(sign(a,key,c,plan)["raw"]).lower()==a.address.lower()

def test_protected_wallet_key_rejected():
    a,key,c,plan=make()
    with pytest.raises(ValueError,match="differs from explicit policy"):
        sign(a,key,c,plan,protected_wallet=a.address.lower())

def test_oversized_amount_rejected():
    a,key,c,plan=make(amount=1_000_001)
    with pytest.raises(ValueError,match="exceeds configured"):
        sign(a,key,c,plan)

def test_wrong_spender_calldata_rejected():
    a,key,c,plan=make()
    data=APPROVE+_address_word(PROTECTED)+_word(AMOUNT)
    c["data"]=data
    plan["data"]=data
    with pytest.raises(ValueError,match="Calldata"):
        sign(a,key,c,plan)

def test_gas_cap_rejected():
    a,key,c,plan=make()
    with pytest.raises(ValueError,match="ETH fee"):
        sign(a,key,c,plan,gas_price_wei=100_000_000_000)

def test_unapproved_vault_rejected():
    a,key,c,plan=make()
    with pytest.raises(ValueError,match="differs from explicit policy"):
        sign(a,key,c,plan,approved_vaults=(PROTECTED,))


def make_policy(wallet):
    return SignerPolicy(wallet, PROTECTED, (8453,), (V,), (BASE_USDC,),
                        ("approve", "deposit", "withdraw"), 1_000_000,
                        50_000_000_000_000, 200_000_000_000_000)


def make_rpc(stage):
    rpc = Mock()
    rpc.chain_id.return_value = 8453
    rpc.transaction_count.return_value = 0
    rpc.block_number.return_value = 100
    rpc.contract_exists.return_value = True
    rpc.call_address.return_value = BASE_USDC
    rpc.call_uint.side_effect = lambda contract, selector, block, *args: (
        10_000_000 if selector == "0x70a08231" else
        (0 if stage == "approve" else AMOUNT) if selector == "0xdd62ed3e" else 1_000_000)
    rpc.eth_balance.return_value = 10**17
    rpc.estimate_gas.return_value = 60000
    rpc.gas_price_wei.return_value = 6_000_000
    rpc.simulate_call.return_value = "0x" + _word(1_000_000)
    return rpc


@pytest.mark.parametrize("stage", ["approve", "deposit", "withdraw"])
def test_signing_never_broadcasts(stage):
    a, key, c, plan = make(stage)
    rpc = make_rpc(stage)
    result = sign(a, key, c, plan, rpc=rpc)
    assert Account.recover_transaction(result["raw"]).lower() == a.address.lower()
    rpc.request.assert_not_called()
    assert rpc.simulate_call.call_count == (0 if stage == "approve" else 1)


@pytest.mark.parametrize("field,value", [
    ("chain_id", 1), ("to", PROTECTED), ("wallet", PROTECTED),
    ("data", "0xdeadbeef"), ("value", "0x1"),
    ("gas", None), ("gas", "garbage"), ("gas", "0xfffffff"),
    ("amount_units", True), ("amount_units", -1),
])
def test_bad_transaction_never_reaches_signing(field, value, monkeypatch):
    a, key, c, plan = make()
    c[field] = value
    # Also corrupt the campaign to exercise validation beyond byte binding.
    mapping = {"chain_id":"chain_id", "to":"to_address", "wallet":"from_address",
               "data":"data", "amount_units":"requested_assets_units"}
    if field in mapping:
        plan[mapping[field]] = value
    signing = Mock(side_effect=AssertionError("must not sign"))
    monkeypatch.setattr(Account, "sign_transaction", signing)
    with pytest.raises(ValueError):
        sign(a, key, c, plan)
    signing.assert_not_called()


@pytest.mark.parametrize("change", [
    {"authorized_at_unix": 0}, {"authorized_at_unix": None},
    {"authorized_at_unix": float("nan")}, {"authorized_at_unix": time.time()+10000},
    {"observed_block": 1}, {"observed_block": 101}, {"observed_block": None},
])
def test_stale_or_missing_authorization_rejected(change):
    a, key, c, plan = make()
    c.update(change)
    with pytest.raises(ValueError, match="authorization|observation"):
        sign(a, key, c, plan)


@pytest.mark.parametrize("changes", [
    {"execution_address":PROTECTED}, {"protected_address": "bad"},
    {"execution_address":"0x"+"0"*40}, {"chain_allowlist":()},
    {"chain_allowlist":(1,)}, {"vault_allowlist":()},
    {"asset_allowlist":(V,)}, {"operation_allowlist":()},
    {"operation_allowlist":("transfer",)}, {"max_action_units":True},
    {"min_eth_reserve_wei":0}, {"max_total_fee_wei":0},
])
def test_invalid_policy(changes):
    with pytest.raises(ValueError):
        replace(make_policy("0x"+"1"*40), **changes)


def test_policy_file_requires_all_fields_and_rejects_secrets(tmp_path):
    import json
    from dataclasses import asdict
    path = tmp_path / "policy.json"
    policy = make_policy("0x"+"1"*40)
    path.write_text(json.dumps(asdict(policy)))
    assert load_signer_policy(str(path)) == policy
    for data in ({}, {**asdict(policy), "private_key":"not-a-key"}, [], None):
        path.write_text(json.dumps(data))
        with pytest.raises(ValueError, match="malformed"):
            load_signer_policy(str(path))
    with pytest.raises(ValueError, match="malformed"):
        load_signer_policy(str(tmp_path / "missing"))


def test_operation_disabled_in_policy():
    a, key, c, plan = make()
    policy = replace(make_policy(a.address.lower()), operation_allowlist=("withdraw",))
    with pytest.raises(ValueError, match="operation not permitted"):
        sign(a, key, c, plan, policy=policy)


@pytest.mark.parametrize("key", [None, "bad", "0xnope", "0x"+"00"*32])
def test_invalid_key_error_does_not_echo_input(key):
    a, _, c, plan = make()
    with pytest.raises(ValueError, match="Invalid runtime signing key"):
        sign(a, key, c, plan)


def test_wrong_key_never_falls_back():
    a, _, c, plan = make()
    other = Account.create()
    with pytest.raises(ValueError, match="does not match"):
        sign(a, "0x"+bytes(other.key).hex(), c, plan)


@pytest.mark.parametrize("failure", ["chain", "asset", "contract", "reserve", "allowance", "gas", "unavailable"])
def test_fresh_rpc_safety_failures(failure):
    a, key, c, plan = make()
    rpc = make_rpc("approve")
    if failure == "chain": rpc.chain_id.return_value = 1
    if failure == "asset": rpc.call_address.return_value = V
    if failure == "contract": rpc.contract_exists.return_value = False
    if failure == "reserve": rpc.eth_balance.return_value = 200_000_000_000_000
    if failure == "allowance": rpc.call_uint.side_effect = lambda *args: 2**256-1
    if failure == "gas": rpc.estimate_gas.return_value = 200000
    if failure == "unavailable": rpc.block_number.side_effect = RuntimeError("RPC unavailable")
    with pytest.raises((ValueError, RuntimeError)):
        sign(a, key, c, plan, rpc=rpc)


def test_changed_approval_stage_requires_replan():
    a, key, c, plan = make()
    with pytest.raises(ValueError, match="changed"):
        sign(a, key, c, plan, rpc=make_rpc("deposit"))


def test_withdrawal_share_cap_still_enforced():
    a, key, c, plan = make("withdraw")
    c["max_burned_shares"] = 999999
    with pytest.raises(ValueError, match="share preview"):
        sign(a, key, c, plan)


def test_unsigned_construction_needs_no_key_or_rpc():
    a, _, c, plan = make()
    tx = build_unsigned_transaction(consumed=c, candidate=plan,
        policy_wallet=a.address.lower(), protected_wallet=PROTECTED,
        nonce=0, gas_price_wei=6_000_000, approved_vaults=(V,),
        policy=make_policy(a.address.lower()))
    assert tx["chainId"] == 8453 and tx["value"] == 0 and tx["type"] == 2


@pytest.mark.parametrize("outcome", ["success", "timeout", "wrong_hash", "bad_envelope"])
def test_broadcast_is_journaled_before_transport_and_never_retried(tmp_path, monkeypatch, outcome):
    import json
    import sqlite3
    import httpx
    from contextlib import nullcontext
    from lapidus_vaults.operations import dedicated_signer as module
    from lapidus_vaults.operations.auto_journal import current

    a, key, c, plan = make()
    db_path = str(tmp_path / "journal.sqlite")
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE rebalance_campaign_steps (campaign_id TEXT, sequence INTEGER, status TEXT, tx_hash TEXT)")
        db.execute("CREATE TABLE rebalance_campaign_quote_bindings (campaign_id TEXT, sequence INTEGER, quote_id TEXT)")
        db.execute("CREATE TABLE live_deposit_authorizations (quote_id TEXT, transaction_json TEXT, wallet TEXT, vault TEXT, stage TEXT, amount_units INTEGER)")
        db.execute("CREATE TABLE rebalance_campaigns (campaign_id TEXT, plan_json TEXT, state TEXT, wallet TEXT)")
        db.execute("INSERT INTO rebalance_campaign_quote_bindings VALUES ('campaign',1,'quote')")
        db.execute("INSERT INTO live_deposit_authorizations VALUES (?,?,?,?,?,?)",
                   ("quote", json.dumps(c), c["wallet"], c["vault"], c["stage"], c["amount_units"]))
        db.execute("INSERT INTO rebalance_campaigns VALUES (?,?,?,?)", ("campaign", json.dumps([plan]), "ACTIVE", c["wallet"]))
    rpc = make_rpc("approve")
    monkeypatch.setattr(module, "ReadOnlyRPC", lambda _: nullcontext(rpc))
    sends = []

    class Transport:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, url, *, json):
            saved = current(db_path, "campaign")
            assert saved[0]["state"] == "SUBMITTED"
            assert saved[0]["tx_hash"]
            assert json["method"] == "eth_sendRawTransaction"
            sends.append(json)
            if outcome == "timeout":
                raise httpx.ReadTimeout("secret URL must not escape")
            response = Mock()
            response.json.return_value = ([] if outcome == "bad_envelope" else {
                "id": 1, "result": saved[0]["tx_hash"] if outcome == "success" else "0x"+"00"*32})
            return response

    monkeypatch.setattr(module.httpx, "Client", Transport)
    params = dict(db_path=db_path, campaign_id="campaign", sequence=1,
        quote_id="quote", consumed=c, candidate=plan,
        policy_wallet=a.address.lower(), protected_wallet=PROTECTED,
        private_key=key, rpc_url="https://example.invalid", nonce=0,
        gas_price_wei=6_000_000, approved_vaults=(V,), policy=make_policy(a.address.lower()))
    if outcome == "success":
        assert module.reserve_and_broadcast(**params)["state"] == "SUBMITTED"
    else:
        with pytest.raises(RuntimeError, match="Never resend") as error:
            module.reserve_and_broadcast(**params)
        assert "secret URL" not in str(error.value)
    with pytest.raises(ValueError, match="never resend"):
        module.reserve_and_broadcast(**params)
    assert len(sends) == 1


def test_no_binding_means_no_key_access_or_rpc(tmp_path, monkeypatch):
    import sqlite3
    from lapidus_vaults.operations import dedicated_signer as module
    a, key, c, plan = make()
    path = str(tmp_path / "empty.sqlite")
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE rebalance_campaign_steps (campaign_id TEXT, sequence INTEGER, status TEXT, tx_hash TEXT)")
        db.execute("CREATE TABLE rebalance_campaign_quote_bindings (campaign_id TEXT, sequence INTEGER, quote_id TEXT)")
        db.execute("CREATE TABLE live_deposit_authorizations (quote_id TEXT, transaction_json TEXT, wallet TEXT, vault TEXT, stage TEXT, amount_units INTEGER)")
        db.execute("CREATE TABLE rebalance_campaigns (campaign_id TEXT, plan_json TEXT, state TEXT, wallet TEXT)")
    rpc = Mock(side_effect=AssertionError("must not read RPC"))
    monkeypatch.setattr(module, "ReadOnlyRPC", rpc)
    with pytest.raises(ValueError, match="persisted campaign authorization"):
        module.reserve_and_broadcast(db_path=path, campaign_id="campaign", sequence=1,
            quote_id="missing", consumed=c, candidate=plan,
            policy_wallet=a.address.lower(), protected_wallet=PROTECTED,
            private_key="invalid", rpc_url="https://example.invalid", nonce=0,
            gas_price_wei=6_000_000, approved_vaults=(V,), policy=make_policy(a.address.lower()))
    rpc.assert_not_called()


def test_actual_signed_gas_limit_must_preserve_reserve():
    a, key, c, plan = make()
    c["gas"] = hex(700000)  # Previously reviewed limit, larger than fresh estimate.
    rpc = make_rpc("approve")
    rpc.eth_balance.return_value = 210_000_000_000_000
    # Fresh quote at 6m gas price fits, but 700k * (2 * 20m) does not.
    with pytest.raises(ValueError, match="maximum signing fee"):
        sign(a, key, c, plan, rpc=rpc, gas_price_wei=20_000_000)


def test_authorization_expires_during_rpc_verification(monkeypatch):
    from lapidus_vaults.operations import dedicated_signer as module
    a, key, c, plan = make()
    c["authorized_at_unix"] = 1000
    monkeypatch.setattr(module.time, "time", Mock(side_effect=[1001, 1100]))
    with pytest.raises(ValueError, match="expired during RPC"):
        sign(a, key, c, plan)


def test_explicit_policy_cannot_be_omitted():
    a, key, c, plan = make()
    with pytest.raises(ValueError, match="Explicit dedicated signer policy"):
        sign(a, key, c, plan, policy=None)


@pytest.mark.parametrize("data", [
    APPROVE + _address_word(V) + _word(2**256-1),
    DEPOSIT + _word(AMOUNT) + _address_word(PROTECTED),
    APPROVE + _address_word(V) + _word(AMOUNT) + "00",
])
def test_exact_approval_selector_and_argument_lengths_enforced(data):
    a, key, c, plan = make()
    c["data"] = plan["data"] = data
    with pytest.raises(ValueError, match="Calldata"):
        sign(a, key, c, plan)
