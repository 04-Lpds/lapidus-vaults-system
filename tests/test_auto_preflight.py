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


@pytest.mark.parametrize("configured,eth,expected", [
    (False, 10**15, "PREFLIGHT_SIGNER_POLICY_BLOCKED"),
    (True, 10**15, "PREFLIGHT_READY_NO_BROADCAST"),
    (True, 205_000_000_000_000, "PREFLIGHT_GAS_OR_FUNDING_BLOCKED"),
])
def test_preflight_requires_policy_and_preserves_fee_reserve(tmp_path, monkeypatch, configured, eth, expected):
    import json
    from contextlib import nullcontext
    from unittest.mock import Mock
    from dataclasses import asdict
    from lapidus_vaults.operations.signer_policy import SignerPolicy
    from lapidus_vaults.allocator import BASE_USDC
    vault = "0x"+"2"*40
    db = tmp_path / "campaign.sqlite"
    db.touch()
    monkeypatch.delenv("LVS_SIGNER_POLICY", raising=False)
    if configured:
        policy = SignerPolicy(WALLET, PROTECTED, (8453,), (vault,), (BASE_USDC,),
                              ("deposit",), 1_000_000, 50_000_000_000_000, 200_000_000_000_000)
        path = tmp_path / "policy.json"
        path.write_text(json.dumps(asdict(policy)))
        monkeypatch.setenv("LVS_SIGNER_POLICY", str(path))
    rpc = Mock()
    rpc.chain_id.return_value = 8453
    rpc.block_number.return_value = 100
    rpc.eth_balance.return_value = eth
    rpc.gas_price_wei.return_value = 100_000_000
    monkeypatch.setattr(auto_preflight, "ReadOnlyRPC", lambda _: nullcontext(rpc))
    monkeypatch.setattr(auto_preflight, "MorphoClient", lambda: nullcontext(Mock()))
    monkeypatch.setattr(auto_preflight, "read_policy", lambda _: type("Policy", (), {"vault_addresses":(vault,)})())
    monkeypatch.setattr(auto_preflight, "build_live_readonly_plan", lambda *a, **kw: {"plan":{"capital_units":1_000_000}})
    monkeypatch.setattr(auto_preflight, "next_auto_action", lambda *a, **kw: {
        "status":"AUTO_POLICY_PASSED_AWAITING_SECURE_SIGNER", "method":"deposit_intent",
        "amount_units":800_000, "campaign_id":"campaign", "sequence":1})
    quote = Mock(return_value={"gas":"0x186a0", "stage":"deposit", "amount_units":800_000})
    monkeypatch.setattr(auto_preflight, "quote_next_campaign_action", quote)
    result = preflight(db_path=str(db), wallet=WALLET, protected_wallet=PROTECTED,
                       allowlist_path="unused", rpc_url="https://example.invalid")
    assert result["status"] == expected
    assert result["submitted"] is False and result["signing_key_accessed"] is False
    if not configured:
        quote.assert_not_called()
