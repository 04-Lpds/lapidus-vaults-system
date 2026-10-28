"""Dedicated-wallet signed calldata safety regressions."""
import pytest
from eth_account import Account
from lapidus_vaults.allocator import BASE_USDC
from lapidus_vaults.rpc_verify import _address_word, _word
from lapidus_vaults.unsigned_candidates import APPROVE, DEPOSIT
from lapidus_vaults.operations.dedicated_signer import prepare_signed_transaction

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
    else:
        dest = V
        data = DEPOSIT + _word(amount) + _address_word(wallet)
    consumed = {"chain_id":8453,"stage":stage,"wallet":wallet,
                "vault":V,"amount_units":amount,"to":dest,
                "data":data,"gas":"0x186a0","value":"0x0"}
    candidate = {"chain_id":8453,"from_address":wallet,"to_address":dest,
                 "data":data,"requested_assets_units":amount,
                 "method":stage+"_intent"}
    return a,key,consumed,candidate

def sign(a,key,c,plan,**changes):
    params=dict(consumed=c,candidate=plan,policy_wallet=a.address.lower(),
                protected_wallet=PROTECTED,private_key=key,nonce=0,
                gas_price_wei=6_000_000,approved_vaults=(V,))
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
    with pytest.raises(ValueError,match="separate dedicated"):
        sign(a,key,c,plan,protected_wallet=a.address.lower())

def test_oversized_amount_rejected():
    a,key,c,plan=make(amount=1_000_001)
    with pytest.raises(ValueError,match="exceeds cap"):
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
    with pytest.raises(ValueError,match="not permitted"):
        sign(a,key,c,plan,approved_vaults=(PROTECTED,))
