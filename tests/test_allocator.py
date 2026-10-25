"""Read-only screening/planning tests; zero wallet or broadcast functionality."""
import json
from decimal import Decimal

import httpx
import pytest

from lapidus_vaults.allocator import (
    BASE_USDC, Assessment, assess_vault, capital_to_units, display_usdc, propose
)
from lapidus_vaults.client import MorphoClient
from lapidus_vaults.models import VaultSnapshot, strict_uint
from lapidus_vaults import cli

VA = '0x' + 'a' * 40
VB = '0x' + 'b' * 40


def sample_parts(address=VA):
    listed = {'vault_address': address, 'asset_address': BASE_USDC, 'name': 'Sample'}
    state = {'chain_id':8453, 'address':address, 'last_indexed_block':'50000',
             'total_assets':'5000000000', 'total_supply':'5000000000',
             'withdrawable_assets':'2000000000', 'allocated_assets':'4500000000', 'idle_assets':'500000000'}
    conf = {'chain_id':8453, 'address':address, 'last_indexed_block':'50000',
            'asset':{'address':BASE_USDC, 'decimals':6},
            'gates':{k:None for k in ('send_shares','receive_shares','send_assets','receive_assets')}}
    apy = {'chain_id':8453, 'vault_address':address, 'last_indexed_block':'50000', 'lookback':'seven_days', 'apy':0.05}
    return listed, state, conf, apy


@pytest.mark.parametrize('value', [3.9, True, False, '-2', '-0', '1.2', '1e3', ' 12', -1, None, 2**256])
def test_strict_uint_rejects_unsafe_values(value):
    with pytest.raises(ValueError):
        strict_uint(value, 'test')


def test_strict_uint_accepts_int_and_decimal_string():
    assert strict_uint('100000000000000000000000000000000', 'test') == 10**32
    assert strict_uint(8453, 'chain') == 8453
    assert VaultSnapshot.from_api(sample_parts()[1]).total_assets == 5_000_000_000


def test_state_parser_rejects_fractional_float():
    listing, state, _, _ = sample_parts()
    state['total_assets'] = 3.9
    with pytest.raises(ValueError):
        VaultSnapshot.from_api(state)


def test_screen_passing_and_gates_rejection():
    l, s, c, a = sample_parts()
    good = assess_vault(listing=l, state=s, config=c, apy=a)
    assert good.preliminary_pass
    assert good.apy == Decimal('0.05')
    assert good.withdrawable_fraction == Decimal('0.4')
    c['gates']['receive_assets'] = '0x'+'f'*40
    blocked = assess_vault(listing=l, state=s, config=c, apy=a)
    assert not blocked.preliminary_pass
    assert 'Gate configured' in str(blocked.problems)


def test_screen_rejects_cross_vault_apy_and_stale_config():
    l, s, c, a = sample_parts()
    a['vault_address'] = VB
    c['last_indexed_block'] = '1'
    screening = assess_vault(listing=l, state=s, config=c, apy=a)
    assert 'APY identity mismatch' in screening.problems
    assert 'Configuration/state index blocks diverge' in screening.problems


def test_planner_caps_idle_and_base_units():
    a = assess_vault(listing=sample_parts(VA)[0], state=sample_parts(VA)[1], config=sample_parts(VA)[2], apy=sample_parts(VA)[3])
    b = assess_vault(listing=sample_parts(VB)[0], state=sample_parts(VB)[1], config=sample_parts(VB)[2], apy=sample_parts(VB)[3])
    allocations, idle = propose(200_000_000, [a,b], approved_addresses=[VA,VB])
    assert allocations[VA] == 80_000_000
    assert allocations[VB] == 80_000_000
    assert idle == 40_000_000
    assert sum(allocations.values()) + idle == 200_000_000
    assert display_usdc(idle) == '40.000000'
    assert propose(200_000_000, [a,b], approved_addresses=[]) == ({}, 200_000_000)
    with pytest.raises(ValueError):
        propose(200_000_000, [a,b], approved_addresses=[VA,VA])
    with pytest.raises(ValueError):
        propose(200_000_000, [a,b], approved_addresses=['0x'+'c'*40])


@pytest.mark.parametrize('value', ['0.0000001', '-1', '0', 'NaN', 'Infinity', 'abc'])
def test_bad_capital_amount(value):
    with pytest.raises(ValueError):
        capital_to_units(value)


def test_screen_and_plan_cli_with_mock_api(tmp_path, monkeypatch, capsys):
    def handler(req):
        if req.url.path == '/v0/vaults-v2':
            return httpx.Response(200, json={'data':[sample_parts(VA)[0], sample_parts(VB)[0]], 'cursor':None})
        for addr in (VA,VB):
            l, s, c, a = sample_parts(addr)
            selector = '8453:' + addr
            if req.url.path == f'/v1/vaults-v2/{selector}/state': return httpx.Response(200, json={'data':s})
            if req.url.path == f'/v0/vaults-v2/{selector}': return httpx.Response(200, json={'data':c})
            if req.url.path == f'/v1/vaults-v2/{selector}/apy-averages':
                assert req.url.params['lookback'] == 'seven_days'
                return httpx.Response(200, json={'data':a})
        return httpx.Response(404)
    client = MorphoClient(client=httpx.Client(base_url='https://test.invalid', transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(cli,'MorphoClient',lambda:client)
    try:
        assert cli.main(['screen', '--limit', '2']) == 0
        out = capsys.readouterr().out
        assert 'PRELIM' in out and '5.00%' in out
        policy = tmp_path/'allowlist.json'
        policy.write_text(json.dumps({'chain_id':8453, 'asset_address':BASE_USDC,
                                       'approved_vault_addresses':[VA,VB],
                                       'idle_fraction':'0.20', 'per_vault_max_fraction':'0.40'}))
        assert cli.main(['plan', '--capital-usdc', '200', '--allowlist', str(policy)]) == 0
        output = capsys.readouterr().out
        assert '80.000000' in output and '40.000000' in output
        assert 'TOTAL PORTFOLIO: 200.000000 USDC' in output
        assert 'ALLOCATED USDC: 160.000000' in output
        assert 'Sample (' in output
    finally:
        client.http.close()


def test_screen_persists_full_source_payload(tmp_path, monkeypatch, capsys):
    from lapidus_vaults.storage import SnapshotStore
    l, s, c, a = sample_parts()
    def handler(req):
        if req.url.path == '/v0/vaults-v2': return httpx.Response(200, json={'data':[l], 'cursor':None})
        if req.url.path.endswith('/state'): return httpx.Response(200, json={'data':s})
        if req.url.path.endswith('/apy-averages'): return httpx.Response(200, json={'data':a})
        return httpx.Response(200, json={'data':c})
    client = MorphoClient(client=httpx.Client(base_url='https://test.invalid',transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(cli, 'MorphoClient', lambda:client)
    dbfile=tmp_path/'screens.db'
    try:
        assert cli.main(['screen','--limit','1','--db',str(dbfile)]) == 0
        with SnapshotStore(dbfile) as store:
            row=store.conn.execute('SELECT * FROM vault_screenings').fetchone()
            assert row['preliminary_pass'] == 1
            assert row['apy_seven_days'] == '0.05'
            raw=json.loads(row['raw_json'])
            assert raw['config']['asset']['address'].lower() == BASE_USDC
    finally:
        client.http.close()
