import copy
import json
import httpx
import pytest

from lapidus_vaults import cli
from lapidus_vaults.client import MorphoAPIError, MorphoClient
from lapidus_vaults.exposures import normalize_exposures
from lapidus_vaults.storage import SnapshotStore

VAULT = '0x' + 'a' * 40
ADAPTER = '0x' + 'b' * 40
NESTED = '0x' + 'c' * 40
MARKET = '0x' + 'd' * 64


def sample():
    return {
        'address': VAULT, 'name': 'Example USDC',
        'totalAssets': '100000000', 'idleAssets': '20000000',
        'adapters': {'items': [{
            '__typename': 'MorphoMarketV1Adapter', 'type': 'MorphoMarketV1Adapter',
            'address': ADAPTER, 'assets': '80000000',
            'positions': {'items': [{
                'market': {
                    'marketId': MARKET,
                    'loanAsset': {'symbol': 'USDC'},
                    'collateralAsset': {'symbol': 'WETH'},
                }, 'state': {'supplyAssets': '80000000'}
            }]},
        }]},
    }


def test_market_normalization_and_exact_decimal():
    r = normalize_exposures(sample(), expected_address=VAULT.upper().replace('0X', '0x'))
    assert r.complete
    assert r.total_assets == 100_000_000
    assert r.adapter_assets == 80_000_000
    assert r.covered_assets == 80_000_000
    assert str(r.weight(r.covered_assets)) == '0.8'
    assert r.positions[0].collateral_symbol == 'WETH'
    assert r.positions[0].market_id == MARKET


def test_nested_adapter_does_not_masquerade_as_market():
    data = sample()
    data['adapters']['items'][0]['assets'] = '60000000'
    data['adapters']['items'][0]['positions']['items'][0]['state']['supplyAssets'] = '60000000'
    data['adapters']['items'].append({
        '__typename': 'MetaMorphoAdapter', 'address': NESTED, 'assets': '20000000',
        'metaMorpho': {'address': '0x' + '1' * 40, 'name': 'Inner vault'},
    })
    r = normalize_exposures(data, expected_address=VAULT)
    assert r.covered_assets == 60_000_000
    assert not r.complete
    assert 'requires separate look-through' in r.issues[0]
    assert len(r.other_adapters) == 1
    assert r.other_adapters[0].destination_name == 'Inner vault'


def test_unreconciled_and_bad_data():
    data = sample()
    data['adapters']['items'][0]['assets'] = '70000000'
    assert len(normalize_exposures(data, expected_address=VAULT).issues) == 2
    data = sample()
    data['totalAssets'] = 1.2
    with pytest.raises(ValueError, match='unsigned integer'):
        normalize_exposures(data, expected_address=VAULT)
    data = sample()
    data['idleAssets'] = '100000001'
    with pytest.raises(ValueError, match='exceeds'):
        normalize_exposures(data, expected_address=VAULT)
    with pytest.raises(ValueError, match='mismatch'):
        normalize_exposures(sample(), expected_address=NESTED)


def test_potential_truncation_warning():
    data = sample()
    data['adapters']['items'][0]['positions']['items'] = (
        data['adapters']['items'][0]['positions']['items'] * 100
    )
    assert any('truncated' in issue for issue in normalize_exposures(data, expected_address=VAULT).issues)


def test_graphql_client_shapes_and_failures():
    def responder(request):
        assert request.method == 'POST'
        assert request.url.path == '/graphql'
        body = json.loads(request.content)
        assert body['variables'] == {'chainId': 8453, 'address': VAULT}
        assert 'MorphoMarketV1Adapter' in body['query']
        return httpx.Response(200, json={'data': {'vaultV2ByAddress': sample()}})

    with httpx.Client(base_url='https://api.morpho.org', transport=httpx.MockTransport(responder)) as http:
        with MorphoClient(client=http) as client:
            assert client.v2_exposures(8453, VAULT)['name'] == 'Example USDC'

    for payload in ({'errors': [{'message': 'invalid query'}]}, {'data': {'vaultV2ByAddress': None}}):
        with httpx.Client(base_url='https://api.morpho.org',
                          transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as http:
            with MorphoClient(client=http) as client:
                with pytest.raises(MorphoAPIError):
                    client.v2_exposures(8453, VAULT)


def test_exposure_storage_append_only(tmp_path):
    r = normalize_exposures(sample(), expected_address=VAULT)
    db = tmp_path / 'data' / 'lapidus.sqlite3'
    with SnapshotStore(db) as store:
        store.insert_exposures(chain_id=8453, report=r, raw_data=sample())
        store.insert_exposures(chain_id=8453, report=r, raw_data=sample())
        assert store.conn.execute('SELECT COUNT(*) FROM exposure_snapshots').fetchone()[0] == 2
        assert store.conn.execute('SELECT COUNT(*) FROM exposure_market_positions').fetchone()[0] == 2
        p = store.conn.execute('SELECT supply_assets FROM exposure_market_positions LIMIT 1').fetchone()[0]
        assert p == '80000000'
        assert store.conn.execute('SELECT raw_json FROM exposure_snapshots LIMIT 1').fetchone()[0]


def test_exposures_cli_offline(monkeypatch, tmp_path, capsys):
    class FakeClient:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def v2_exposures(self, chain, address):
            assert chain == 8453 and address == VAULT
            return sample()
    monkeypatch.setattr(cli, 'MorphoClient', FakeClient)
    path = str(tmp_path / 'test.db')
    assert cli.main(['exposures', '--vault', VAULT, '--db', path]) == 0
    out = capsys.readouterr().out
    assert 'Example USDC' in out and 'WETH' in out and '80.00%' in out
    assert cli.main(['exposures', '--vault', 'not-an-address', '--db', path]) == 1


def test_zero_asset_vault_weight():
    data = sample()
    data['totalAssets'] = '0'
    data['idleAssets'] = '0'
    data['adapters']['items'][0]['assets'] = '0'
    data['adapters']['items'][0]['positions']['items'][0]['state']['supplyAssets'] = '0'
    r = normalize_exposures(data, expected_address=VAULT)
    assert r.weight(0) is None
