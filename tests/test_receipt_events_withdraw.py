"""Receipt-scoped token movements and bounded withdrawal quote regression tests."""
from unittest.mock import Mock
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

def test_withdraw_quote_encoded_and_bounded():
    rpc=Mock()
    rpc.chain_id.return_value=8453
    rpc.block_number.return_value=123
    rpc.call_address.return_value=BASE_USDC
    rpc.eth_balance.return_value=10**17
    rpc.call_uint.side_effect=lambda c,s,b,*args: 10**19 if s=="0x70a08231" else 10**18
    rpc.simulate_call.return_value="0x"+"0"*63+"1"
    rpc.estimate_gas.return_value=200000
    rpc.gas_price_wei.return_value=6000000
    q=WithdrawalQuotes().quote(rpc,wallet=ACTOR,vault=VAULTS[0],approved_vaults=VAULTS,amount_units=5000000)
    assert q["stage"]=="withdraw"
    assert q["data"].startswith("0xb460af94")
    assert q["to"]==VAULTS[0]
