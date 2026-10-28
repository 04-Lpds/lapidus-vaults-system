"""Offline regressions for live shadow economics without RPC network or keys."""
from decimal import Decimal
import pytest
from lapidus_vaults.operations.live_shadow_economics import evaluate_live, parse_apy
from lapidus_vaults.allocator import BASE_USDC

W="0x"+"1"*40
A="0x"+"2"*40
B="0x"+"3"*40

def report(a=2_000_000,b=2_000_000):
    return {"plan":{"wallet":W,"chain_id":8453,"observed_block":100,
        "capital_units":5_000_000,
        "changes":[{"vault_address":A,"delta_units":a,"current_units":0,"target_units":a},
                   {"vault_address":B,"delta_units":b,"current_units":0,"target_units":b}],
        "actions":[{"kind":"deposit_intent","vault":A,"amount_units":a},
                   {"kind":"deposit_intent","vault":B,"amount_units":b}]}}
class Morpho:
    def __init__(self,apys):self.apys=apys
    def v2_apy(self,chain,vault,lookback):
        assert chain==8453 and lookback=="seven_days"
        return {"vault_address":vault,"lookback":lookback,"apy":self.apys[vault]}
class RPC:
    def gas_price_wei(self):return 6_000_000
    def call_uint(self,*args):return 0
    def estimate_gas(self,*args):return 90_000

def test_fee_hold_real_historical_apy():
    r=evaluate_live(report(),rpc=RPC(),morpho=Morpho({A:0.04,B:0.05}),
                    eth_usd=Decimal("3000"))
    assert r["decision"]=="HOLD_ESTIMATED_FEES_EXCEED_BENEFIT"
    assert len(r["gas_steps"])==4
    assert all(x["source"]=="rpc_estimate" for x in r["gas_steps"])
    assert not r["submitted"]

def test_apy_spread_subtracts_yield_lost_on_withdrawal():
    p=report(a=-1_000_000,b=1_000_000)
    p["plan"]["actions"]=[{"kind":"withdraw_intent","amount_units":1_000_000,"vault":A},
                          {"kind":"deposit_intent","amount_units":1_000_000,"vault":B}]
    r=evaluate_live(p,rpc=RPC(),morpho=Morpho({A:0.09,B:0.08}),
                    eth_usd=Decimal("3000"))
    assert Decimal(r["estimated_annual_yield_delta_usd"])==Decimal("-0.01")
    assert r["decision"]=="HOLD_NO_POSITIVE_HISTORICAL_YIELD_DELTA"

def test_missing_apy_halts():
    with pytest.raises(ValueError,match="Missing historical APY"):
        parse_apy({"vault_address":A,"lookback":"seven_days","apy":None},A)
    with pytest.raises(ValueError,match="identity"):
        parse_apy({"vault_address":B,"lookback":"seven_days","apy":0.05},A)

def test_fallback_is_identified():
    class NoGas(RPC):
        def estimate_gas(self,*args):raise RuntimeError("not simulated")
    r=evaluate_live(report(),rpc=NoGas(),morpho=Morpho({A:0.04,B:0.05}),
                    eth_usd=Decimal("3000"))
    assert all(x["source"]=="policy_fallback_unexecutable" for x in r["gas_steps"])
