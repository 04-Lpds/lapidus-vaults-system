"""Keyless mainnet preflight cannot sign, broadcast, or create a DB."""
import pytest
from lapidus_vaults.operations.auto_preflight import preflight
from lapidus_vaults.operations import auto_preflight

WALLET="0x"+"1"*40
PROTECTED="0x"+"3"*40

def test_preflight_rejects_missing_db_without_network(tmp_path, monkeypatch):
    monkeypatch.setattr(auto_preflight, "read_policy",
                        lambda _: type("Policy", (), {
                            "vault_addresses":("0x"+"2"*40,)})())
    with pytest.raises(ValueError, match="does not exist"):
        preflight(db_path=str(tmp_path/"not-created.sqlite3"),wallet=WALLET,
                  protected_wallet=PROTECTED,allowlist_path="fake",
                  rpc_url="https://example.invalid")
    assert not (tmp_path/"not-created.sqlite3").exists()

def test_preflight_rejects_same_wallet_without_any_network(tmp_path):
    with pytest.raises(ValueError,match="must differ"):
        preflight(db_path=str(tmp_path/"whatever"),wallet=WALLET,
                  protected_wallet=WALLET,allowlist_path="fake",
                  rpc_url="https://example.invalid")

def test_reports_real_eth_with_dedicated_reserve(tmp_path, monkeypatch):
    db=tmp_path/"executor.sqlite"
    db.touch()
    monkeypatch.setattr(auto_preflight,"read_policy",
        lambda _: type("Policy",(),{"vault_addresses":("0x"+"2"*40,)})())
    class FakeRPC:
        def __init__(self,url): pass
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def chain_id(self): return 8453
        def block_number(self): return 53187628
        def eth_balance(self,wallet,block): return 1_000_000_000_000_000
        def gas_price_wei(self): return 6_000_000
    class FakeClient:
        def __enter__(self): return self
        def __exit__(self,*args): pass
    monkeypatch.setattr(auto_preflight,"ReadOnlyRPC",FakeRPC)
    monkeypatch.setattr(auto_preflight,"MorphoClient",FakeClient)
    def fake_plan(*args,**kwargs):
        assert kwargs["eth_reserve_wei"]==200_000_000_000_000
        return {"plan":{"capital_units":5_000_000}}
    monkeypatch.setattr(auto_preflight,"build_live_readonly_plan",fake_plan)
    result=preflight(db_path=str(db),wallet=WALLET,
                     protected_wallet=PROTECTED,allowlist_path="fake",
                     rpc_url="https://example.invalid")
    assert result["eth_balance_wei"]==1_000_000_000_000_000
    assert result["legacy_eth_reserve_wei"]==2_000_000_000_000_000
    assert result["executor_eth_reserve_wei"]==200_000_000_000_000
    assert result["status"]=="PREFLIGHT_READ_ONLY"
    assert result["campaign_status"]=="NO_ACTIVE_CAMPAIGN"
    assert result["submitted"] is False
