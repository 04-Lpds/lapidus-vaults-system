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
