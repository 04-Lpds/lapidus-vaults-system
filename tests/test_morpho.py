import httpx
from decimal import Decimal
from lapidus_vaults.client import MorphoClient, MorphoAPIError
from lapidus_vaults.models import VaultSnapshot
from lapidus_vaults.storage import SnapshotStore
from lapidus_vaults.cli import format_pct
import pytest

ADDRESS='0x'+'1'*40

def state(**kw):
    result = dict(chain_id=8453, address=ADDRESS,last_indexed_block='12345', total_assets='1000',
                  total_supply='1000',withdrawable_assets='300',allocated_assets='900', idle_assets='100')
    result.update(kw)
    return result

def test_exact_ratios():
    s=VaultSnapshot.from_api(state(),observed_at='2026-10-21T00:00:00+00:00')
    assert s.withdrawable_fraction==Decimal('0.3')
    assert s.idle_fraction==Decimal('0.1')
    assert format_pct('300','1000')=='30.0%'

def test_missing_data_is_not_zero():
    x=state();del x['total_assets']
    with pytest.raises(ValueError):VaultSnapshot.from_api(x)

def test_invalid_overflow():
    with pytest.raises(ValueError):VaultSnapshot.from_api(state(withdrawable_assets='1001'))

def test_zero_balance():
    s=VaultSnapshot.from_api(state(total_assets='0', allocated_assets='0', idle_assets='0',withdrawable_assets='0'))
    assert s.withdrawable_fraction is None
    assert format_pct('0','0')=='n/a'

def test_persist_and_report(tmp_path):
    s=VaultSnapshot.from_api(state(),name='Example USDC Vault')
    with SnapshotStore(tmp_path/'test.db') as db:
        db.insert(s,state())
        assert db.count()==1
        rows=db.latest()
        assert rows[0]['name']=='Example USDC Vault'
        assert rows[0]['total_assets']=='1000'

def test_http_client_public_api_shape():
    def handler(req):
        if req.url.path=='/v0/vaults-v2':
            return httpx.Response(200,json={'data':[{'chain_id':8453,'vault_address':ADDRESS,'name':'Vault A','asset_address':ADDRESS}], 'cursor':None})
        return httpx.Response(200,json={'data':state()})
    http=httpx.Client(base_url='https://test.invalid',transport=httpx.MockTransport(handler))
    c=MorphoClient(client=http)
    assert c.listed_v2_vaults(8453,limit=5)[0]['name']=='Vault A'
    assert c.v2_state(8453,ADDRESS)['total_assets']=='1000'
    http.close()

def test_http_error_does_not_fabricate_snapshot():
    http=httpx.Client(base_url='https://test.invalid',transport=httpx.MockTransport(lambda _:httpx.Response(503)))
    c=MorphoClient(client=http)
    with pytest.raises(MorphoAPIError):c.v2_state(8453,ADDRESS)
    http.close()
