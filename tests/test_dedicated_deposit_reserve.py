"""Dedicated signing-wallet deposit quote uses smaller explicit ETH reserve.

Supervised default stays 0.002 ETH. Live quote remains bounded by estimated
gas and 3x quoted price even with the isolated executor reserve.
"""
import pytest
from lapidus_vaults.live_deposit import DepositQuotes
from lapidus_vaults.allocator import BASE_USDC
from lapidus_vaults.rpc_verify import _address_word

WALLET="0x"+"1"*40
VAULT="0x"+"2"*40

class FakeRPC:
    def chain_id(self): return 8453
    def block_number(self): return 100
    def call_address(self,*args): return BASE_USDC
    def call_uint(self,contract,sig,block,*words):
        if sig == "0x70a08231": return 5_000_000
        if sig == "0xdd62ed3e": return 0
        raise AssertionError(sig)
    def eth_balance(self,*args): return 1_000_000_000_000_000
    def estimate_gas(self,*args): return 90_000
    def gas_price_wei(self): return 6_000_000

def test_dedicated_quote_works_at_one_millieth_without_relaxing_gas_guard():
    with pytest.raises(ValueError,match="Insufficient ETH gas reserve"):
        DepositQuotes().quote(FakeRPC(),wallet=WALLET,vault=VAULT,
                             approved_vaults=(VAULT,),amount_units=800_000)
    q=DepositQuotes(min_gas_reserve_wei=200_000_000_000_000).quote(
        FakeRPC(),wallet=WALLET,vault=VAULT,
        approved_vaults=(VAULT,),amount_units=800_000)
    assert q["stage"]=="approve"
    assert q["amount_units"]==800000

def test_reserve_cannot_be_negative_or_exceed_supervised_default():
    with pytest.raises(ValueError,match="Invalid deposit ETH reserve"):
        DepositQuotes(min_gas_reserve_wei=0)
    with pytest.raises(ValueError,match="Invalid deposit ETH reserve"):
        DepositQuotes(min_gas_reserve_wei=2_000_000_000_000_001)
