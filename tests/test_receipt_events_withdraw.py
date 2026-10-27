"""Receipt-scoped token movements and bounded withdrawal quote regression tests."""
from unittest.mock import Mock
import pytest
from lapidus_vaults.live_withdraw import MAX_WITHDRAW, PREVIEW_WITHDRAW
from lapidus_vaults.receipt_events import reconcile_transfer_logs, TRANSFER_TOPIC, ZERO
from lapidus_vaults.live_withdraw import WithdrawalQuotes
from lapidus_vaults.fork_roundtrip import ACTOR, VAULTS, BASE_USDC

def transfer(token,src,dst,amount):
    return {"address":token,"topics":[TRANSFER_TOPIC,"0x"+("0"*24)+src[2:],"0x"+("0"*24)+dst[2:]],
            "data":"0x"+f"{amount:064x}"}

def test_deposit_receipt_exact_logs():
    logs=[transfer(BASE_USDC,ACTOR,VAULTS[0],5000000),
          transfer(VAULTS[0],ZERO,ACTOR,4776493404382974000),
          transfer(BASE_USDC,VAULTS[0],VAULTS[1],5000000)]
    result=reconcile_transfer_logs({"logs":logs},ACTOR,VAULTS[0],5000000,"deposit")
    assert result["matched"]
    assert result["minted_shares"]==4776493404382974000

def test_wrong_amount_receipt_fails():
    logs=[transfer(BASE_USDC,ACTOR,VAULTS[0],100),
          transfer(VAULTS[0],ZERO,ACTOR,10)]
    assert not reconcile_transfer_logs({"logs":logs},ACTOR,VAULTS[0],5000000,"deposit")["matched"]

def test_withdraw_receipt():
    logs=[transfer(VAULTS[0],ACTOR,ZERO,4776493404382974000),
          transfer(BASE_USDC,VAULTS[0],ACTOR,5000000)]
    assert reconcile_transfer_logs({"logs":logs},ACTOR,VAULTS[0],5000000,"withdraw")["matched"]

def withdrawal_rpc(*, max_units=0, preview_shares=10**18, simulated_shares=10**18):
    rpc=Mock()
    rpc.chain_id.return_value=8453
    rpc.block_number.return_value=123
    rpc.call_address.return_value=BASE_USDC
    rpc.eth_balance.return_value=10**17
    def call_uint(c,s,b,*args):
        if s=="0x70a08231": return 10**19
        if s==MAX_WITHDRAW: return max_units
        if s==PREVIEW_WITHDRAW: return preview_shares
        raise AssertionError("Unexpected selector: "+s)
    rpc.call_uint.side_effect=call_uint
    rpc.simulate_call.return_value="0x"+f"{simulated_shares:064x}"
    rpc.estimate_gas.return_value=200000
    rpc.gas_price_wei.return_value=6000000
    return rpc

def test_withdraw_quote_encoded_and_bounded():
    rpc=withdrawal_rpc()
    q=WithdrawalQuotes().quote(rpc,wallet=ACTOR,vault=VAULTS[0],approved_vaults=VAULTS,amount_units=5000000)
    assert q["stage"]=="withdraw"
    assert q["data"].startswith("0xb460af94")
    assert q["to"]==VAULTS[0]
    assert q["reported_max_withdraw_units"]==0
    assert q["max_withdraw_is_executable_limit"] is False
    assert q["expected_burned_shares"]==10**18
    assert q["max_burned_shares"]==10**18+10**14

def test_morpho_v2_zero_max_withdraw_allows_successful_simulation():
    rpc=withdrawal_rpc(max_units=0)
    q=WithdrawalQuotes().quote(rpc,wallet=ACTOR,vault=VAULTS[0],approved_vaults=VAULTS,amount_units=1_000_000)
    assert q["reported_max_withdraw_units"]==0
    rpc.simulate_call.assert_called_once()

def test_withdraw_rejects_share_burn_mismatch():
    rpc=withdrawal_rpc(simulated_shares=10**18+1)
    with pytest.raises(ValueError,match="differ from previewWithdraw"):
        WithdrawalQuotes().quote(rpc,wallet=ACTOR,vault=VAULTS[0],approved_vaults=VAULTS,amount_units=1_000_000)

def test_withdraw_consume_rejects_changed_share_quote():
    rpc=withdrawal_rpc()
    engine=WithdrawalQuotes()
    quote=engine.quote(rpc,wallet=ACTOR,vault=VAULTS[0],approved_vaults=VAULTS,amount_units=1_000_000)
    rpc=withdrawal_rpc(preview_shares=10**18+10**14+1,simulated_shares=10**18+10**14+1)
    with pytest.raises(ValueError,match="Withdrawal share preview exceeds reviewed maximum"):
        engine.consume(rpc,quote["quote_id"])

def test_withdraw_consume_exposes_verified_shares():
    rpc=withdrawal_rpc()
    engine=WithdrawalQuotes()
    quote=engine.quote(rpc,wallet=ACTOR,vault=VAULTS[0],approved_vaults=VAULTS,amount_units=1_000_000)
    ready=engine.consume(rpc,quote["quote_id"])
    assert ready["expected_burned_shares"]==quote["expected_burned_shares"]
    assert ready["reported_max_withdraw_units"]==quote["reported_max_withdraw_units"]

def test_withdraw_consume_rejects_gas_increase_with_specific_reason():
    rpc=withdrawal_rpc()
    engine=WithdrawalQuotes()
    quote=engine.quote(rpc,wallet=ACTOR,vault=VAULTS[0],approved_vaults=VAULTS,amount_units=1_000_000)
    rpc.estimate_gas.return_value=300000
    with pytest.raises(ValueError,match="Gas estimate increased"):
        engine.consume(rpc,quote["quote_id"])


def test_withdraw_consume_allows_bounded_share_drift_and_returns_fresh_shares():
    engine=WithdrawalQuotes()
    rpc=withdrawal_rpc()
    quote=engine.quote(rpc,wallet=ACTOR,vault=VAULTS[0],approved_vaults=VAULTS,amount_units=1_000_000)
    updated=10**18+10**13
    rpc=withdrawal_rpc(preview_shares=updated,simulated_shares=updated)
    ready=engine.consume(rpc,quote["quote_id"])
    assert ready["expected_burned_shares"]==updated
    assert ready["max_burned_shares"]==quote["max_burned_shares"]
