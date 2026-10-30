"""Dedicated mainnet trial creation must reject bad inputs before RPC writes."""
import pytest
from lapidus_vaults.operations import auto_trial_campaign as trial

W="0x"+"1"*40
P="0x"+"3"*40
V="0x"+"2"*40

def test_trial_rejects_over_cap_without_network(tmp_path):
    with pytest.raises(ValueError,match="0.8 USDC"):
        trial.create_trial(db_path=str(tmp_path/"db"),wallet=W,
            protected_wallet=P,vault=V,allowlist_path="none",
            rpc_url="https://example.invalid",campaign_id="test",amount_units=800001)

def test_trial_rejects_protected_wallet_without_network(tmp_path):
    with pytest.raises(ValueError,match="Protected wallet"):
        trial.create_trial(db_path=str(tmp_path/"db"),wallet=P,
            protected_wallet=P,vault=V,allowlist_path="none",
            rpc_url="https://example.invalid",campaign_id="test")

def test_trial_creates_exact_two_steps_without_network_writes(tmp_path,monkeypatch):
    db=tmp_path/"existing.sqlite3"
    db.touch()
    monkeypatch.setattr(trial,"read_policy",
        lambda _:type("Policy",(),{"vault_addresses":(V,)})())
    class FakeRPC:
        def __init__(self,url): pass
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def chain_id(self):return 8453
        def block_number(self):return 53188742
        def call_address(self,*a):return trial.BASE_USDC
        def call_uint(self,*a):return 5_000_000
        def eth_balance(self,*a):return 1_000_000_000_000_000
    monkeypatch.setattr(trial,"ReadOnlyRPC",FakeRPC)
    out=trial.create_trial(db_path=str(db),wallet=W,
        protected_wallet=P,vault=V,allowlist_path="fake",
        rpc_url="https://example.invalid",campaign_id="trial-test")
    assert out["status"]=="TRIAL_CAMPAIGN_CREATED_NOT_AUTHORIZED"
    assert len(out["steps"])==2
    assert out["steps"][0]["amount_units"]==800000
    assert not out["submitted"]
    with pytest.raises(ValueError,match="active campaign"):
        trial.create_trial(db_path=str(db),wallet=W,
            protected_wallet=P,vault=V,allowlist_path="fake",
            rpc_url="https://example.invalid",campaign_id="trial-test-duplicate")
