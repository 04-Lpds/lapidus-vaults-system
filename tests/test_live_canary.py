from unittest.mock import patch
import sqlite3
import pytest
from lapidus_vaults.operations.live_canary import prepare, run

W="0x"+"1"*40
P="0x"+"2"*40
A="0x"+"3"*40
B="0x"+"4"*40

def test_preparation_never_uses_signer_and_refuses_running_pilot(tmp_path):
    db=str(tmp_path/"journal.sqlite3")
    sqlite3.connect(db).close()
    (tmp_path/"journal.sqlite3.pilot.lock").write_text("active")
    with pytest.raises(RuntimeError,match="Stop live-pilot"):
        prepare(db_path=db,wallet=W,protected_wallet=P,allowlist="unused",
                rpc_url="https://example.invalid",source=A,destination=B)

def test_canary_requires_two_distinct_vaults(tmp_path):
    db=str(tmp_path/"journal.sqlite3")
    sqlite3.connect(db).close()
    with pytest.raises(ValueError,match="same-vault"):
        prepare(db_path=db,wallet=W,protected_wallet=P,allowlist="unused",
                rpc_url="https://example.invalid",source=A,destination=A)

def test_missing_campaign_never_signs(tmp_path):
    db=str(tmp_path/"journal.sqlite3")
    sqlite3.connect(db).close()
    with patch("lapidus_vaults.operations.live_canary.read_policy") as policy:
        policy.return_value.vault_addresses=(A,B)
        with pytest.raises(ValueError,match="Unknown campaign"):
            run(db_path=db,wallet=W,protected_wallet=P,allowlist="unused",
                rpc_url="https://example.invalid",private_key="no-key",
                campaign_id="not-created",
                execute_fn=lambda **kw: pytest.fail("Never sign missing campaign"))
