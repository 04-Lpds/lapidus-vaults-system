"""Offline fail-closed tests for the dedicated one-step runner."""
from pathlib import Path
import pytest
from lapidus_vaults.operations.auto_once import main, execute_once, execution_lock

W = "0x" + "1"*40
V = "0x" + "2"*40
P = "0x" + "3"*40

def test_main_does_not_enable_live_without_explicit_switch(capsys):
    assert main(["--wallet",W,"--protected-wallet",P,
                 "--allowlist","unused"]) == 0
    assert "DISABLED" in capsys.readouterr().out

def test_emergency_stop_prevents_network_or_key_access(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("data").mkdir()
    Path("data/STOP_AUTOMATION").write_text("STOP")
    with pytest.raises(RuntimeError,match="Emergency stop"):
        execute_once(db_path="data/journal.sqlite3",wallet=W,protected_wallet=P,
                     approved_vaults=(V,),allowlist_path="unused",
                     rpc_url="https://example.com",private_key="invalid",
                     max_capital_units=10_000_000)

def test_execution_lock_blocks_concurrent_runs(tmp_path):
    db=str(tmp_path/"db.sqlite")
    with execution_lock(db):
        with pytest.raises(FileExistsError):
            with execution_lock(db):
                pass
    assert not Path(db+".auto.lock").exists()


def test_missing_explicit_signer_policy_blocks_before_rpc(tmp_path, monkeypatch):
    from lapidus_vaults.operations import auto_once
    from unittest.mock import Mock
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("LVS_SIGNER_POLICY", raising=False)
    rpc = Mock(side_effect=AssertionError("must not access RPC"))
    monkeypatch.setattr(auto_once, "ReadOnlyRPC", rpc)
    with pytest.raises(ValueError, match="signer policy"):
        execute_once(db_path="unused.sqlite", wallet=W, protected_wallet=P,
                     approved_vaults=(V,), allowlist_path="unused",
                     rpc_url="https://example.invalid", private_key="invalid",
                     max_capital_units=10_000_000)
    rpc.assert_not_called()
    assert not Path("unused.sqlite").exists()
