"""Offline tests for read-only wallet-aware planner."""
from datetime import datetime, timezone
from decimal import Decimal
import json
import sqlite3
from types import SimpleNamespace

import pytest
from lapidus_vaults.allocator import BASE_CHAIN, BASE_USDC
from lapidus_vaults.wallet_plan import (
    Policy, VaultHolding, WalletSnapshot, build_rebalance, read_policy,
    read_wallet_snapshot, save_preview, main,
)

A = '0x050ce30b927da55177a4914ec73480238bad56f0'
B = '0x1e9e47583f15d45a10df48c0b1846e0492c795d7'
W = '0x' + 'a' * 40
POLICY = Policy((A, B), Decimal('0.20'), Decimal('0.40'))


def snap(wallet=60_000_000, amounts=(100_000_000, 40_000_000)):
    return WalletSnapshot(W, BASE_CHAIN, 100, wallet, (
        VaultHolding(A, 100, amounts[0], 95, 1_000_000, 2_000_000),
        VaultHolding(B, 50, amounts[1], 95, 1_000_000, 2_000_000),
    ), datetime.now(timezone.utc).isoformat())


def test_rebalance_mixed_withdraw_and_deposit():
    p = build_rebalance(snap(), POLICY)
    assert p.capital_units == 200_000_000
    assert p.target_idle_units == 40_000_000
    assert {c.vault_address: c.delta_units for c in p.changes} == {A: -20_000_000, B: 40_000_000}
    assert p.withdrawals_units == 20_000_000 and p.deposits_units == 40_000_000


def test_full_idle_balance_and_one_vault_cap():
    p = build_rebalance(snap(200_000_000, (0, 0)), POLICY)
    assert [c.delta_units for c in p.changes] == [80_000_000, 80_000_000]
    one = Policy((A,), Decimal('0.20'), Decimal('0.40'))
    s = WalletSnapshot(W, BASE_CHAIN, 5, 200_000_000,
                       (VaultHolding(A, 0, 0, 5, 100, 100),), 'now')
    assert build_rebalance(s, one).target_idle_units == 120_000_000


def test_zero_balances_and_rounding():
    assert build_rebalance(snap(0, (0, 0)), POLICY).capital_units == 0
    p = build_rebalance(snap(100_000_003, (0, 0)), POLICY)
    assert p.capital_units == p.target_idle_units + sum(c.target_units for c in p.changes)


def test_missing_holding_and_negative_balance_rejected():
    s = snap()
    incomplete = WalletSnapshot(s.wallet_address, s.chain_id, s.rpc_block, s.wallet_usdc_units,
                                (s.holdings[0],), s.observed_at)
    with pytest.raises(ValueError, match='exactly the selected'):
        build_rebalance(incomplete, POLICY)
    with pytest.raises(ValueError, match='Invalid wallet'):
        build_rebalance(snap(-1), POLICY)


def test_policy_validation(tmp_path):
    f = tmp_path / 'policy.json'
    obj = {'chain_id': BASE_CHAIN, 'asset_address': BASE_USDC,
           'approved_vault_addresses': [A, B],
           'idle_fraction': '0.20', 'per_vault_max_fraction': '0.40'}
    f.write_text(json.dumps(obj))
    assert read_policy(f) == POLICY
    obj['approved_vault_addresses'] = [A, A]
    f.write_text(json.dumps(obj))
    with pytest.raises(ValueError, match='Duplicate'):
        read_policy(f)


def test_wallet_rpc_reads_pinned_and_rejects_review(monkeypatch):
    from lapidus_vaults import wallet_plan
    rpc = SimpleNamespace(chain_id=lambda: BASE_CHAIN, block_number=lambda: 123)
    blocks = []
    def verify(_, __, vault, *, block, wallet):
        blocks.append(block)
        return SimpleNamespace(status='MATCHED_BASIC_READS', notes=(), chain_id=BASE_CHAIN,
                               rpc_block=block, vault_address=vault, wallet_address=wallet.lower(),
                               wallet_usdc_units=40_000_000, wallet_vault_shares=5,
                               wallet_vault_assets_estimated=80_000_000,
                               api_indexed_block=120, rpc_total_assets=100_000_000,
                               rpc_total_supply=100_000_000)
    monkeypatch.setattr(wallet_plan, 'verify_one', verify)
    result = read_wallet_snapshot(W, POLICY, rpc, object())
    assert blocks == [123, 123] and result.in_scope_capital_units == 200_000_000
    def reviewed(*args, **kwargs):
        return SimpleNamespace(status='REVIEW', notes=('indexer lag',))
    monkeypatch.setattr(wallet_plan, 'verify_one', reviewed)
    with pytest.raises(ValueError, match='requires review'):
        read_wallet_snapshot(W, POLICY, rpc, object())


def test_save_preview_sqlite(tmp_path):
    db = tmp_path / 'preview.sqlite3'
    assert save_preview(db, build_rebalance(snap(), POLICY), POLICY) == 1
    with sqlite3.connect(db) as conn:
        total, details = conn.execute('SELECT in_scope_capital_units, details_json FROM wallet_rebalance_previews').fetchone()
        assert total == '200000000'
        assert json.loads(details)['changes'][0]['delta_units'] == '-20000000'
        assert 'BASE_RPC_URL' not in details


def test_offline_demo_cli(tmp_path, monkeypatch, capsys):
    file = tmp_path / 'allowlist.json'
    file.write_text(json.dumps({'chain_id': BASE_CHAIN, 'asset_address': BASE_USDC,
        'approved_vault_addresses': [A, B]}))
    monkeypatch.delenv('BASE_RPC_URL', raising=False)
    assert main(['--demo', '--allowlist', str(file), '--db', str(tmp_path / 'db.sqlite3')]) == 0
    assert 'READ ONLY' in capsys.readouterr().out
