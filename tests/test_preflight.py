"""Offline coverage for conservative static preflight."""
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
import pytest

from lapidus_vaults.preflight import preflight, Check
from lapidus_vaults.wallet_plan import WalletSnapshot, VaultHolding, build_rebalance, Policy
from lapidus_vaults.allocator import BASE_CHAIN

A = '0x050ce30b927da55177a4914ec73480238bad56f0'
B = '0x1e9e47583f15d45a10df48c0b1846e0492c795d7'
W = '0x' + 'a' * 40
POLICY = Policy((A, B), Decimal('0.20'), Decimal('0.40'))


def plan(wallet=60_000_000, assets=(100_000_000, 40_000_000), shares=(100_000_000, 100_000_000)):
    snap = WalletSnapshot(W, BASE_CHAIN, 100, wallet, (
        VaultHolding(A, shares[0], assets[0], 99, 100_000_000, 100_000_000),
        VaultHolding(B, shares[1], assets[1], 99, 100_000_000, 100_000_000),
    ), 'now')
    return build_rebalance(snap, POLICY)


class FakeRPC:
    def __init__(self):
        self.eth = 10**18
        self.allowance = 100_000_000
        self.max_withdraw = 100_000_000
        self.max_deposit = 100_000_000
        self.simulated = []
        self.revert = False

    def gas_price_wei(self):
        return 100_000_000

    def estimate_gas(self, sender, contract, data):
        assert sender == W
        return 150_000

    def chain_id(self):
        return BASE_CHAIN

    def eth_balance(self, addr, block):
        assert (addr, block) == (W, 100)
        return self.eth

    def call_uint(self, contract, selector, block, *args):
        assert block == 100
        return {
            '0xf1c7a58b': 10**12,
            '0xdd62ed3e': self.allowance,
            '0x402d267d': self.max_deposit,
            '0xce96cb77': self.max_withdraw,
            '0xef8b30f7': 20_000_000,
            '0x0a28a477': 20_000_000,
        }[selector]

    def simulate_call(self, sender, vault, data, block):
        assert sender == W and block == 100
        self.simulated.append((vault, data))
        if self.revert:
            from lapidus_vaults.rpc_verify import RPCError
            raise RPCError('reverted')
        return '0x' + '0' * 63 + '1'


def test_independent_actions_pass():
    rpc = FakeRPC()
    checks = preflight(plan(), rpc)
    assert [(c.action, c.status) for c in checks] == [('WITHDRAW', 'STATIC_PASS_LIMITS_UNVERIFIED'), ('DEPOSIT', 'STATIC_PASS_LIMITS_UNVERIFIED')]
    assert len(rpc.simulated) == 2
    assert any('Morpho V2 maxDeposit' in n for n in checks[1].observations)
    assert any('Morpho V2 maxWithdraw' in n for n in checks[0].observations)
    assert any('L1 bound' in n for n in checks[1].observations)


@pytest.mark.parametrize('attribute,value', [
    ('eth', 0), ('allowance', 0),
])
def test_blockers_fail_closed(attribute, value):
    rpc = FakeRPC()
    setattr(rpc, attribute, value)
    assert 'BLOCKED' in [c.status for c in preflight(plan(), rpc)]


def test_insufficient_current_usdc_does_not_credit_future_withdrawals():
    rpc = FakeRPC()
    checks = preflight(plan(wallet=0, assets=(100_000_000, 40_000_000)), rpc)
    assert checks[1].status == 'BLOCKED'
    assert 'Insufficient current wallet USDC' in ' '.join(checks[1].observations)


def test_eth_call_revert_is_blocker():
    rpc = FakeRPC()
    rpc.revert = True
    assert all(c.status == 'BLOCKED' for c in preflight(plan(), rpc))


def test_no_action_never_simulates():
    rpc = FakeRPC()
    assert all(c.status == 'NO_ACTION' for c in preflight(plan(0, (0, 0)), rpc))
    assert rpc.simulated == []


def test_demo_and_wrong_chain_rejected():
    rpc = FakeRPC()
    original = plan()
    demo = replace(original, snapshot=replace(original.snapshot, source='demo'))
    with pytest.raises(ValueError, match='Live RPC'):
        preflight(demo, rpc)
    rpc.chain_id = lambda: 1
    with pytest.raises(Exception, match='Wrong chain'):
        preflight(original, rpc)


def test_gas_budget_blocks_insufficient_eth():
    rpc = FakeRPC()
    rpc.eth = 2_001_000_000_000_000
    assert all(c.status == 'BLOCKED' for c in preflight(plan(), rpc))


def test_gas_estimate_failure_blocks():
    rpc = FakeRPC()
    from lapidus_vaults.rpc_verify import RPCError
    rpc.estimate_gas = lambda *args: (_ for _ in ()).throw(RPCError('cannot estimate'))
    assert all(c.status == 'BLOCKED' for c in preflight(plan(), rpc))
