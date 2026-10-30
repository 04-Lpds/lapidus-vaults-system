"""Completely offline checks for v0.4-alpha pinned-block RPC reader."""
import json
import sqlite3
from pathlib import Path

import httpx
import pytest
import pytest

from lapidus_vaults.allocator import BASE_CHAIN, BASE_USDC
from lapidus_vaults.rpc_verify import (
    ReadOnlyRPC, RPCError, verify_one, save_verification, _address_word,
    _word, _decode_address, _decode_word, _difference_bps, main,
)

VAULT = '0x050ce30b927da55177a4914ec73480238bad56f0'
WALLET = '0x' + '2' * 40


class MockMorpho:
    def v2_config(self, chain, address):
        return {'address': address, 'chain_id': BASE_CHAIN,
                'asset': {'address': BASE_USDC, 'decimals': 6}}

    def v2_state(self, chain, address):
        return {'address': address, 'chain_id': BASE_CHAIN,
                'last_indexed_block': '195', 'total_assets': '1000000',
                'total_supply': '900000000000000000', 'withdrawable_assets': '100'}


class FakeRPC:
    def __init__(self):
        self.calls = []
        self.code = True
        self.asset = BASE_USDC
        self.assets = 1000000
        self.supply = 900000000000000000

    def contract_exists(self, address, block):
        self.calls.append(('code', address, block))
        return self.code

    def call_address(self, address, selector, block):
        self.calls.append(('address', address, selector, block))
        return self.asset

    def call_uint(self, address, selector, block, *args):
        self.calls.append(('uint', address, selector, block, *args))
        from lapidus_vaults.rpc_verify import TOTAL_ASSETS, TOTAL_SUPPLY, DECIMALS, BALANCE_OF, CONVERT_TO_ASSETS
        return {TOTAL_ASSETS: self.assets, TOTAL_SUPPLY: self.supply,
                DECIMALS: 6, BALANCE_OF: 1234, CONVERT_TO_ASSETS: 8765}[selector]


def test_basic_success_and_pinned_calls():
    rpc = FakeRPC()
    result = verify_one(rpc, MockMorpho(), VAULT, block=200)
    assert result.status == 'MATCHED_BASIC_READS'
    assert result.rpc_block == 200
    assert result.wallet_usdc_units is None
    assert all(c[-1] == 200 for c in rpc.calls if c[0] == 'code')
    assert all(c[3] == 200 for c in rpc.calls if c[0] == 'uint')


def test_wallet_balances_are_optional_and_public_only():
    rpc = FakeRPC()
    result = verify_one(rpc, MockMorpho(), VAULT, block=200, wallet=WALLET)
    assert result.wallet_address == WALLET
    assert result.wallet_usdc_units == 1234
    assert result.wallet_vault_shares == 1234
    assert result.wallet_vault_assets_estimated == 8765
    assert any(c[-1] == _address_word(WALLET) for c in rpc.calls if c[0] == 'uint')


def test_fail_closed_asset_mismatch():
    rpc = FakeRPC();rpc.asset = WALLET
    with pytest.raises(RPCError, match='asset'):
        verify_one(rpc, MockMorpho(), VAULT, block=200)


def test_fail_closed_missing_bytecode():
    rpc = FakeRPC();rpc.code = False
    with pytest.raises(RPCError, match='bytecode'):
        verify_one(rpc, MockMorpho(), VAULT, block=200)


def test_lag_warning_and_api_ahead():
    lag = verify_one(FakeRPC(), MockMorpho(), VAULT, block=1000, max_lag_blocks=10)
    assert lag.status == 'REVIEW'
    assert 'blocks behind' in lag.notes[0]
    ahead = verify_one(FakeRPC(), MockMorpho(), VAULT, block=100)
    assert ahead.status == 'REVIEW'
    assert 'ahead' in ahead.notes[0]


def test_difference_tolerance_reported_not_mislabeled_safe():
    rpc = FakeRPC();rpc.assets = 1200000
    result = verify_one(rpc, MockMorpho(), VAULT, block=200)
    assert result.status == 'REVIEW'
    assert any('total assets' in n for n in result.notes)


def test_bad_api_identity():
    class Wrong(MockMorpho):
        def v2_state(self, chain, address):
            row = super().v2_state(chain, address)
            row['chain_id'] = 1
            return row
    with pytest.raises(ValueError, match='identity'):
        verify_one(FakeRPC(), Wrong(), VAULT, block=200)


def test_abi_validations():
    assert _word(2) == '0' * 63 + '2'
    assert _address_word(WALLET) == '0' * 24 + '2' * 40
    assert _decode_word('0x' + '0' * 63 + '1', 'test') == 1
    assert _decode_address('0x' + '0' * 24 + '2' * 40, 'test') == WALLET
    with pytest.raises(RPCError):
        _decode_word('0x2', 'bad')
    with pytest.raises(ValueError):
        _word(-1)
    with pytest.raises(ValueError):
        _address_word('0x123')
    assert _difference_bps(1, 0).is_infinite()


def test_rpc_rejects_non_read_methods():
    with ReadOnlyRPC('https://example.test') as rpc:
        with pytest.raises(ValueError, match='read-only'):
            rpc.request('eth_sendRawTransaction', [])


def test_json_rpc_mock_transport_and_pinned_block():
    recorded = []
    def handler(request):
        body = json.loads(request.content)
        recorded.append(body)
        if body['method'] == 'eth_chainId':
            result = hex(BASE_CHAIN)
        elif body['method'] == 'eth_blockNumber':
            result = '0x100'
        elif body['method'] == 'eth_getCode':
            result = '0x6000'
        else:
            result = '0x' + '0'*63 + '6'
        return httpx.Response(200, json={'jsonrpc':'2.0','id':body['id'],'result':result})
    with httpx.Client(transport=httpx.MockTransport(handler)) as hc:
        with ReadOnlyRPC('https://example.test', client=hc) as rpc:
            assert rpc.chain_id() == BASE_CHAIN
            assert rpc.block_number() == 256
            assert rpc.contract_exists(VAULT, 256)
            assert rpc.call_uint(VAULT, '0xdeadbeef', 256) == 6
    assert recorded[2]['params'][1] == '0x100'
    assert recorded[3]['params'][1] == '0x100'


def test_json_rpc_error_does_not_echo_rpc_url():
    url = 'https://example.test/secret-api-key'
    def handler(request):
        return httpx.Response(400, text='invalid key')
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with ReadOnlyRPC(url, client=client) as rpc:
            with pytest.raises(RPCError) as captured:
                rpc.chain_id()
            assert 'secret' not in str(captured.value)


def test_db_append_does_not_persist_rpc_url(tmp_path):
    result = verify_one(FakeRPC(), MockMorpho(), VAULT, block=200, wallet=WALLET)
    p = tmp_path / 'data' / 'lapidus.sqlite3'
    assert save_verification(str(p), result) == 1
    assert save_verification(str(p), result) == 2
    with sqlite3.connect(p) as conn:
        rows = conn.execute('SELECT status, details_json FROM rpc_vault_verifications').fetchall()
    assert len(rows) == 2
    assert rows[0][0] == 'MATCHED_BASIC_READS'
    assert json.loads(rows[0][1])['wallet_address'] == WALLET


def test_cli_missing_env_and_addresses(monkeypatch, capsys):
    monkeypatch.delenv('BASE_RPC_URL', raising=False)
    assert main(['--vault', VAULT]) == 2
    assert 'BASE_RPC_URL' in capsys.readouterr().err
    assert main(['--vault', 'bad']) == 2


def test_cli_rejects_allowlist_wrong_asset(tmp_path, monkeypatch):
    monkeypatch.setenv('BASE_RPC_URL', 'https://example.test')
    file = tmp_path/'allow.json'
    file.write_text(json.dumps({'chain_id': 8453, 'asset_address': WALLET,
                               'approved_vault_addresses': [VAULT]}))
    assert main(['--allowlist', str(file)]) == 2


def test_module_help_exposes_commands():
    from lapidus_vaults.rpc_verify import _cli_parser
    help_text = _cli_parser().format_help()
    assert '--allowlist' in help_text and '--wallet' in help_text


def test_nonce_read_uses_validated_rpc_transport():
    calls = []
    def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        return httpx.Response(200, json={"jsonrpc":"2.0", "id":payload["id"], "result":"0x7"})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with ReadOnlyRPC("https://example.test", client=client) as rpc:
            assert rpc.transaction_count(WALLET, "latest") == 7
            assert rpc.transaction_count(WALLET, "pending") == 7
            with pytest.raises(ValueError, match="nonce"):
                rpc.transaction_count(WALLET, "earliest")
    assert [p["method"] for p in calls] == ["eth_getTransactionCount"] * 2
    assert [p["params"] for p in calls] == [[WALLET, "latest"], [WALLET, "pending"]]


@pytest.mark.parametrize("result", [None, {}, 1, "not-hex"])
def test_bad_nonce_result_fails_closed(result):
    def handler(request):
        payload = json.loads(request.content)
        return httpx.Response(200, json={"jsonrpc":"2.0", "id":payload["id"], "result":result})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with ReadOnlyRPC("https://example.test", client=client) as rpc:
            with pytest.raises(RPCError):
                rpc.transaction_count(WALLET, "pending")


def test_nonce_transport_traceback_does_not_reveal_endpoint():
    import traceback
    def handler(request):
        return httpx.Response(403, text="denied")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with ReadOnlyRPC("https://example.test/private-rpc-credential", client=client) as rpc:
            try:
                rpc.transaction_count(WALLET, "pending")
            except RPCError as exc:
                assert "private-rpc-credential" not in "".join(traceback.format_exception(exc))
            else:
                raise AssertionError("transport failure must propagate as sanitized RPCError")
