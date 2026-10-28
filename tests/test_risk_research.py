from __future__ import annotations

from decimal import Decimal
import json
import sqlite3

import pytest

from lapidus_vaults.allocator import BASE_CHAIN, BASE_USDC
from lapidus_vaults.risk_research import (
    _db, _even_sample, _uint, compute_overlap, discover, get_market_context, market_context, main,
)
from lapidus_vaults.storage import SnapshotStore

A = '0x' + 'a'*40
B = '0x' + 'b'*40
M1 = '0x' + '1'*64
M2 = '0x' + '2'*64
M3 = '0x' + '3'*64
POLICY = dict(chain_id=BASE_CHAIN, asset_address=BASE_USDC,
              approved_vault_addresses=[A, B], idle_fraction='0.20', per_vault_max_fraction='0.40')


def seed(db, *, missing=False, nonreconciled=False, b_has_m2=True):
    with SnapshotStore(db) as store:
        c = store.conn
        for address, name, positions in [
            (A, 'Alpha', [(M1, 90_000_000), (M2, 10_000_000)]),
            (B, 'Beta', [(M1, 100_000_000)] if not b_has_m2 else [(M1, 95_000_000), (M2, 5_000_000)]),
        ]:
            total = 100_000_000
            c.execute('''INSERT INTO exposure_snapshots
                (observed_at,chain_id,vault_address,name,total_assets,idle_assets,adapter_assets,issues_json,raw_json)
                VALUES ('2026-01-01T00:00:00Z',?,?,?,?,?,?,?,?)''',
                (BASE_CHAIN,address,name,str(total),'0',str(total+1 if nonreconciled else total),
                 json.dumps(['nested vault'] if missing else []),'{}'))
            sid = c.execute('SELECT last_insert_rowid()').fetchone()[0]
            for mid, units in positions:
                c.execute('''INSERT INTO exposure_market_positions
                  (snapshot_id,adapter_address,market_id,collateral_symbol,loan_symbol,supply_assets)
                  VALUES (?,?,?,?,?,?)''',
                  (sid,A,mid,'cbBTC' if mid == M1 else 'WETH','USDC',str(units)))
        c.commit()


def test_all_shared_market_contributors(tmp_path):
    path = tmp_path/'lvs.db'
    seed(path)
    with _db(str(path), require_existing=True) as conn:
        markets, allocations, idle = compute_overlap(conn, POLICY, 200_000_000)
    assert allocations == {A:80_000_000,B:80_000_000}
    assert idle == 40_000_000
    assert len(markets) == 2
    assert markets[0].market_id == M1
    assert markets[0].total_units == Decimal('148000000')
    assert len(markets[0].contributors) == 2
    assert markets[1].total_units == Decimal('12000000')
    assert len(markets[1].contributors) == 2


def test_zero_positions_not_counted_as_shared(tmp_path):
    path=tmp_path/'lvs.db'
    seed(path,b_has_m2=False)
    with _db(str(path), require_existing=True) as conn:
        markets, _, _ = compute_overlap(conn, POLICY, 200_000_000)
    assert len(markets[1].contributors) == 1


@pytest.mark.parametrize('kwargs', [{'missing':True},{'nonreconciled':True}])
def test_fail_closed_invalid_lookthrough(tmp_path,kwargs):
    path=tmp_path/'lvs.db'
    seed(path,**kwargs)
    with _db(str(path), require_existing=True) as conn:
        with pytest.raises(ValueError):
            compute_overlap(conn, POLICY, 200_000_000)


def test_duplicate_allowlist_rejected(tmp_path):
    path=tmp_path/'lvs.db';seed(path)
    with _db(str(path), require_existing=True) as conn:
        with pytest.raises(ValueError,match='Duplicate'):
            compute_overlap(conn,dict(POLICY,approved_vault_addresses=[A,A]),200_000_000)


def test_uint_rejects_float_and_negative_and_boolean():
    for value in [1.5,True,-1,'-2','1.0',' 23']:
        with pytest.raises(ValueError): _uint(value,'x')
    assert _uint('1000000000000000000000000000000','x') == 10**30


def test_even_sampler_spreads_directory():
    items=[{'vault_address':str(i)} for i in range(100)]
    assert [int(x['vault_address']) for x in _even_sample(items,5)] == [0,24,49,74,99]
    assert len(_even_sample(items,1)) == 1
    assert _even_sample(items,101) == items


class FakeMarketClient:
    def __init__(self, *, invalid_token=False, liquidity_fails=False):
        self.calls=[]
        self.invalid_token=invalid_token
        self.liquidity_fails=liquidity_fails
    def _get(self,path):
        self.calls.append(path)
        mid = path.split('/')[4].split(':')[1]
        if path.endswith('/state'):
            return {'data':{'chain_id':BASE_CHAIN,'market_id':mid,'total_supply_assets':'1000000',
                            'total_borrow_assets':'750000','last_indexed_block':'123'}}
        if path.endswith('/liquidity'):
            if self.liquidity_fails:
                raise ValueError('offline liquidity failure')
            return {'data':{'market_liquidity':'250000','reallocatable_liquidity':'500000'}}
        return {'data':{'chain_id':BASE_CHAIN,'market_id':mid,'loan_token':A if self.invalid_token else BASE_USDC,
                        'collateral_token':B,'lltv_wad':'860000000000000000',
                        'oracle_address':A,'irm_address':B}}


def test_market_state_parses_utilization():
    client=FakeMarketClient()
    record=get_market_context(client,M1)
    assert record['utilization'] == Decimal('0.75')
    assert record['lltv'] == Decimal('0.86')
    assert record['idle_cash'] == 250000
    assert record['liquidity']['reallocatable_liquidity'] == '500000'
    assert len(client.calls) == 3


def test_market_identity_wrong_token_rejected():
    with pytest.raises(ValueError,match='loan token'):
        get_market_context(FakeMarketClient(invalid_token=True),M1)


def test_unavailable_liquidity_doesnt_fake_zero():
    rec=get_market_context(FakeMarketClient(liquidity_fails=True),M1)
    assert rec['liquidity'] is None
    assert 'offline liquidity failure' in rec['liquidity_error']


def test_market_context_persists_only_unique_ids(tmp_path):
    from argparse import Namespace
    path=tmp_path/'lvs.db';seed(path)
    result=market_context(Namespace(db=str(path),vault=[A,B],max_markets=20),client=FakeMarketClient())
    assert len(result)==2
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT COUNT(*) FROM research_market_states').fetchone()[0]==2


def test_offline_overlap_cli(tmp_path,capsys):
    path=tmp_path/'lvs.db';seed(path)
    policy=tmp_path/'policy.json';policy.write_text(json.dumps(POLICY))
    assert main(['overlap','--allowlist',str(policy),'--capital-usdc','200','--db',str(path)])==0
    output=capsys.readouterr().out
    assert 'shared by 2+ vaults: 2' in output
    assert '148.000000 USDC' in output
    assert '12.000000 USDC' in output


def test_bad_candidate_limits(capsys):
    assert main(['candidates','--universe-limit','0']) == 1
    assert 'ERROR:' in capsys.readouterr().err


def test_missing_db_fail_closed(tmp_path,capsys):
    policy=tmp_path/'policy.json';policy.write_text(json.dumps(POLICY))
    assert main(['overlap','--allowlist',str(policy),'--capital-usdc','200','--db',str(tmp_path/'none.db')])==1
    assert 'not found' in capsys.readouterr().err


class FakeDiscoveryClient:
    def __init__(self):
        self.seen=[]
    def listed_v2_vaults(self,chain,*,limit,asset_address):
        assert chain == BASE_CHAIN and asset_address == BASE_USDC
        return [{'vault_address':'0x'+f'{i:040x}','name':f'Vault {i}'} for i in range(1,21)]
    def v2_state(self,chain,address):
        self.seen.append(address)
        raise ValueError('test missing state')


def test_candidate_discovery_samples_and_persists(tmp_path,capsys):
    from argparse import Namespace
    client=FakeDiscoveryClient()
    db=tmp_path/'candidate.db'
    records=discover(Namespace(universe_limit=20,inspect_limit=5,top=5,db=str(db)),client)
    assert len(records)==5
    assert len(client.seen)==5
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT COUNT(*) FROM research_candidates').fetchone()[0]==5
    assert 'deterministic sample' in capsys.readouterr().out
