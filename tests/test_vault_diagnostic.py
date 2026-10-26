"""Mocked tests: hypothetical amounts must never look executable."""
from lapidus_vaults.vault_diagnostic import inspect_vault
from lapidus_vaults.allocator import BASE_CHAIN
from lapidus_vaults.preflight import GAS_PRICE_ORACLE
from lapidus_vaults.rpc_verify import RPCError
import pytest

V = '0x050ce30b927da55177a4914ec73480238bad56f0'
W = '0x' + 'a' * 40


class Stub:
    def chain_id(self): return BASE_CHAIN
    def contract_exists(self, address, block):
        assert (address, block) == (V, 123)
        return True
    def eth_balance(self, wallet, block): return 0
    def gas_price_wei(self): return 100000
    def call_uint(self, contract, signature, block, *words):
        assert block == 123
        return {'0x70a08231': 0, '0xdd62ed3e': 0, '0xef8b30f7': 100,
                '0x0a28a477': 100, '0xf1c7a58b': 20000}[signature]
    def estimate_gas(self, *args): raise AssertionError('Should not estimate from fictitious balances')
    def simulate_call(self, *args): raise AssertionError('Should not simulate fictitious funds')


def test_nonzero_unfunded_is_observational_only():
    v = inspect_vault(Stub(), V, W, 10_000_000, 123)
    assert v.preview_deposit_shares == 100
    assert v.preview_withdraw_shares == 100
    assert v.l1_fee_upper_bound_wei == 20000
    assert v.simulation == 'NOT_ATTEMPTED_NO_STATE_OVERRIDE'
    assert v.usdc_balance == 0 and v.usdc_allowance == 0


@pytest.mark.parametrize('amount', [0, -1, 1_000_000_001, True])
def test_invalid_amount_rejected(amount):
    with pytest.raises(ValueError):
        inspect_vault(Stub(), V, W, amount, 123)


def test_wrong_chain_rejected():
    rpc = Stub()
    rpc.chain_id = lambda: 1
    with pytest.raises(ValueError, match='Base'):
        inspect_vault(rpc, V, W, 1000000, 123)
