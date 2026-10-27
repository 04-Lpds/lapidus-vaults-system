"""Mock-only receipt inspector tests: no RPC network or signing."""
from unittest.mock import Mock
import pytest
from lapidus_vaults.receipt_inspect import inspect_transaction
from lapidus_vaults.fork_roundtrip import ACTOR, VAULTS, BASE_USDC

HASH = "0x" + "ab"*32

def mockrpc(method="deposit", success=True):
    rpc = Mock()
    rpc.chain_id.return_value = 8453
    rpc.block_number.return_value = 110
    selector = {"deposit":"0x6e553f65", "approve":"0x095ea7b3"}[method]
    data = selector + (("0"*24 + VAULTS[0][2:]) if method == "approve" else "0"*64) + (
        "0"*64 if method == "approve" else "0"*24 + ACTOR[2:])
    receipt = {"transactionHash":HASH, "blockNumber":"0x64",
               "status":"0x1" if success else "0x0", "gasUsed":"0x5208"}
    tx = {"hash":HASH,"from":ACTOR, "to": BASE_USDC if method == "approve" else VAULTS[0],
          "input":data,"value":"0x0"}
    rpc.request.side_effect=lambda method,params: receipt if method=="eth_getTransactionReceipt" else tx
    rpc.call_uint.return_value=0
    return rpc, tx


def test_post_state_from_confirmed_deposit():
    rpc, _ = mockrpc()
    out=inspect_transaction(rpc,HASH,ACTOR,VAULTS)
    assert out["status"]=="RECEIPT_SUCCESS_POST_STATE_OBSERVED"
    assert out["confirmations"]==11
    assert out["post_receipt_block_balances"]["wallet_usdc_units"]==0


def test_reverted_receipt_never_verified():
    rpc,_=mockrpc(success=False)
    out=inspect_transaction(rpc,HASH,ACTOR,VAULTS)
    assert out["status"]=="RECEIPT_REVERTED"
    assert not out["verified"]


def test_reject_mismatched_signer():
    rpc,tx=mockrpc()
    tx["from"]="0x"+"1"*40
    with pytest.raises(ValueError,match="signer"):
        inspect_transaction(rpc,HASH,ACTOR,VAULTS)


def test_reject_approval_to_unallowlisted_spender():
    rpc,tx=mockrpc(method="approve")
    tx["input"]="0x095ea7b3"+"0"*24+"1"*40+"0"*64
    with pytest.raises(ValueError,match="spender"):
        inspect_transaction(rpc,HASH,ACTOR,VAULTS)


def test_confirmations_gate_and_absent_transaction():
    rpc,_=mockrpc()
    out=inspect_transaction(rpc,HASH,ACTOR,VAULTS,min_confirmations=12)
    assert out["status"]=="AWAITING_CONFIRMATIONS"
    rpc,_=mockrpc()
    rpc.request.return_value=None
    rpc.request.side_effect=None
    assert inspect_transaction(rpc,HASH,ACTOR,VAULTS)["status"]=="PENDING_OR_NOT_FOUND"
