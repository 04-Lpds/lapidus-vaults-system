"""Exercise the actual list -> collect -> SQLite -> report CLI using a mock Morpho HTTP API.

These are offline integration tests, not claims of live-network validation.
"""
import httpx
import pytest

from lapidus_vaults import cli
from lapidus_vaults.client import MorphoClient
from lapidus_vaults.storage import SnapshotStore

VAULT_A = '0x' + 'a' * 40
VAULT_B = '0x' + 'b' * 40
USDC = '0x' + 'c' * 40  # test-only address; not a deployed token


def vault_state(address, total='1000', idle='100', withdrawable='300', allocated='900'):
    return {
        'chain_id': 8453, 'address': address, 'last_indexed_block': '12345',
        'total_assets': total, 'total_supply': total, 'withdrawable_assets': withdrawable,
        'allocated_assets': allocated, 'idle_assets': idle,
    }


def mock_client(handler):
    return MorphoClient(client=httpx.Client(
        base_url='https://example.test', transport=httpx.MockTransport(handler)
    ))


def test_complete_cli_workflow_with_mock_http_and_real_sqlite(tmp_path, monkeypatch, capsys):
    addresses = [VAULT_A, VAULT_B]

    def handler(request):
        if request.url.path == '/v0/vaults-v2':
            assert request.url.params['chain_ids'] == '8453'
            return httpx.Response(200, json={'data': [
                {'chain_id': 8453, 'vault_address': addr, 'name': 'Mock ' + str(i), 'asset_address': USDC}
                for i, addr in enumerate(addresses)
            ], 'cursor': None})
        for addr in addresses:
            if request.url.path == f'/v1/vaults-v2/8453:{addr}/state':
                return httpx.Response(200, json={'data': vault_state(addr)})
        return httpx.Response(404)

    http_client = mock_client(handler)
    monkeypatch.setattr(cli, 'MorphoClient', lambda: http_client)
    db_path = tmp_path / 'snapshots.sqlite3'
    try:
        assert cli.main(['list', '--chain', '8453', '--limit', '2']) == 0
        assert VAULT_A in capsys.readouterr().out

        assert cli.main(['collect', '--chain', '8453', '--limit', '2', '--db', str(db_path)]) == 0
        assert 'Saved' in capsys.readouterr().out

        with SnapshotStore(db_path) as store:
            assert store.count() == 2
            assert len(store.latest()) == 2

        assert cli.main(['report', '--db', str(db_path)]) == 0
        result = capsys.readouterr().out
        assert '10.0%' in result
        assert '30.0%' in result
        assert 'INDEX BLOCK' in result
    finally:
        http_client.http.close()


def test_pagination_for_listed_vaults():
    requested_cursors = []

    def handler(request):
        cursor = request.url.params.get('cursor')
        requested_cursors.append(cursor)
        if cursor is None:
            return httpx.Response(200, json={'data': [
                {'vault_address': VAULT_A, 'name': 'first'}], 'cursor': 'page-2'})
        assert cursor == 'page-2'
        return httpx.Response(200, json={'data': [
            {'vault_address': VAULT_B, 'name': 'second'}], 'cursor': None})

    c = mock_client(handler)
    try:
        names = [r['name'] for r in c.listed_v2_vaults(8453, limit=3)]
        assert names == ['first', 'second']
        assert requested_cursors == [None, 'page-2']
    finally:
        c.http.close()


def test_cli_refuses_to_store_identity_mismatch(tmp_path, monkeypatch, capsys):
    def handler(request):
        if request.url.path == '/v0/vaults-v2':
            return httpx.Response(200, json={'data': [
                {'vault_address': VAULT_A, 'name': 'test', 'asset_address': USDC}
            ], 'cursor': None})
        # Server returns state for a different vault: must reject it.
        return httpx.Response(200, json={'data': vault_state(VAULT_B)})

    c = mock_client(handler)
    monkeypatch.setattr(cli, 'MorphoClient', lambda: c)
    db_path = tmp_path / 'rejected.sqlite3'
    try:
        assert cli.main(['collect', '--chain', '8453', '--limit', '1', '--db', str(db_path)]) == 1
        assert 'identity mismatch' in capsys.readouterr().err
        with SnapshotStore(db_path) as store:
            assert store.count() == 0
    finally:
        c.http.close()
