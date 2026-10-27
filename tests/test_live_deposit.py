"""Offline safety tests for staged wallet approval and deposit quotes."""
from unittest.mock import Mock
import pytest

from lapidus_vaults.live_deposit import DepositQuotes, MAX_UNITS
from lapidus_vaults.fork_roundtrip import ACTOR, VAULTS, BASE_USDC
from lapidus_vaults.live_deposit_ui import LIVE_DEPOSIT_HTML


def setup_rpc(allowance=0):
    rpc=Mock()
    rpc.chain_id.return_value=8453
    rpc.block_number.return_value=100
    rpc.call_address.return_value=BASE_USDC
    rpc.call_uint.side_effect=lambda contract, selector, block, *args: (
        10_000_000 if selector=="0x70a08231"
        else allowance if selector=="0xdd62ed3e"
        else 1000000)
    rpc.eth_balance.return_value=10**17
    rpc.estimate_gas.return_value=60000
    rpc.gas_price_wei.return_value=10**8
    rpc.simulate_call.return_value="0x"+"0"*63+"1"
    return rpc


def test_exact_amount_approval_then_consumed():
    q=DepositQuotes()
    rpc=setup_rpc()
    first=q.quote(rpc,wallet=ACTOR,vault=VAULTS[0],approved_vaults=VAULTS,amount_units=5_000_000)
    assert first["stage"]=="approve"
    ready=q.consume(rpc,first["quote_id"])
    assert ready["to"]==BASE_USDC
    assert ready["data"]==first["data"]
    with pytest.raises(ValueError):
        q.consume(rpc,first["quote_id"])


def test_bounded_amounts_and_mismatched_vault():
    q=DepositQuotes()
    rpc=setup_rpc()
    for amount in (0,-1,MAX_UNITS+1,True):
        with pytest.raises(ValueError):
            q.quote(rpc,wallet=ACTOR,vault=VAULTS[0],approved_vaults=VAULTS,amount_units=amount)
    with pytest.raises(ValueError):
        q.quote(rpc,wallet=ACTOR,vault="0x"+"1"*40,approved_vaults=VAULTS,amount_units=1)


def test_deposit_requires_successful_eth_call():
    rpc=setup_rpc(allowance=5_000_000)
    q=DepositQuotes()
    result=q.quote(rpc,wallet=ACTOR,vault=VAULTS[0],approved_vaults=VAULTS,amount_units=5_000_000)
    assert result["stage"]=="deposit"
    rpc.simulate_call.assert_called_once()


def test_invalid_approval_cannot_be_extended():
    rpc=setup_rpc(allowance=1_000_000)
    with pytest.raises(ValueError,match="revoke"):
        DepositQuotes().quote(rpc,wallet=ACTOR,vault=VAULTS[0],
                              approved_vaults=VAULTS,amount_units=5_000_000)


def test_browser_writes_only_on_explicit_click():
    assert "eth_sendTransaction" in LIVE_DEPOSIT_HTML
    assert "liveSubmit" in LIVE_DEPOSIT_HTML
    assert "window.confirm(message)" in LIVE_DEPOSIT_HTML


def test_successful_deposit_receipt_requires_matching_block_deltas():
    q=DepositQuotes()
    rpc=setup_rpc(allowance=5_000_000)
    item=q.quote(rpc,wallet=ACTOR,vault=VAULTS[0],approved_vaults=VAULTS,amount_units=5_000_000)
    q.consume(rpc,item["quote_id"])
    tx_hash="0x"+"ab"*32
    rpc.request.side_effect=lambda method, params: (
        {"hash":tx_hash,"from":ACTOR,"to":VAULTS[0],"input":item["data"],
         "value":"0x0","chainId":"0x2105"} if method=="eth_getTransactionByHash" else
        {"transactionHash":tx_hash,"status":"0x1","blockNumber":"0x65","gasUsed":"0x10000"})
    def call_uint(contract,selector,block,*args):
        if selector=="0x70a08231":
            if contract==BASE_USDC:
                return 10_000_000 if block==100 else 5_000_000
            return 0 if block==100 else 5000000
        return 5000000
    rpc.call_uint.side_effect=call_uint
    result=q.check_receipt(rpc,item["quote_id"],tx_hash)
    assert result["status"]=="BLOCK_LEVEL_BALANCE_MATCH"
    assert result["cash_delta_units"]==-5_000_000
    assert result["share_delta"]==5_000_000
    assert not result["financial_reconciliation_complete"]
