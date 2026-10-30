"""Fail-closed tests for the dedicated-wallet pilot loop."""
import sqlite3
from decimal import Decimal
from unittest.mock import patch
import pytest
from lapidus_vaults.operations.live_pilot import cycle

W="0x"+"1"*40
P="0x"+"2"*40
V="0x"+"3"*40

def test_no_signer_loaded_in_default_review_mode(tmp_path):
    db=str(tmp_path/"pilot.sqlite3")
    sqlite3.connect(db).close()
    with patch("lapidus_vaults.operations.live_pilot.read_policy") as policy:
        policy.return_value.vault_addresses=(V,)
        result=cycle(db_path=db,wallet=W,protected_wallet=P,allowlist="ignored",
          rpc_url="https://example.invalid",eth_usd=Decimal("3000"),
          controller_fn=lambda **kw: {"status":"HOLD","decision":"HOLD_DRIFT_BELOW_THRESHOLD"})
    assert result["status"]=="NO_ACTION"
    assert result["submitted"] is False

def test_no_new_campaign_before_pending_recovery(tmp_path):
    db=str(tmp_path/"pilot.sqlite3")
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE autonomous_submissions (state TEXT)")
        conn.execute("INSERT INTO autonomous_submissions VALUES ('SUBMITTED')")
    with patch("lapidus_vaults.operations.live_pilot.read_policy") as policy:
        policy.return_value.vault_addresses=(V,)
        result=cycle(db_path=db,wallet=W,protected_wallet=P,allowlist="ignored",
          rpc_url="https://example.invalid",eth_usd=Decimal("3000"),
          controller_fn=lambda **kw: pytest.fail("Must recover before planning"))
    assert result["status"]=="ACTIVE_OR_PENDING_REVIEW_ONLY"

def test_live_key_required(tmp_path):
    db=str(tmp_path/"pilot.sqlite3")
    sqlite3.connect(db).close()
    with patch("lapidus_vaults.operations.live_pilot.read_policy") as policy:
        policy.return_value.vault_addresses=(V,)
        with pytest.raises(ValueError,match="SIGNER_KEY_MISSING"):
            cycle(db_path=db,wallet=W,protected_wallet=P,allowlist="ignored",
                rpc_url="https://example.invalid",eth_usd=Decimal("3000"),
                execute_live=True)

def test_existing_campaign_not_implicitly_authorized(tmp_path):
    db=str(tmp_path/"pilot.sqlite3")
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE rebalance_campaigns (campaign_id TEXT, state TEXT, created_at TEXT)")
        conn.execute("INSERT INTO rebalance_campaigns VALUES ('old-manual','ACTIVE','2026-10-29')")
    with patch("lapidus_vaults.operations.live_pilot.read_policy") as policy:
        policy.return_value.vault_addresses=(V,)
        with patch("eth_account.Account.from_key") as account:
            account.return_value.address=W
            result=cycle(db_path=db,wallet=W,protected_wallet=P,allowlist="ignored",
                rpc_url="https://example.invalid",eth_usd=Decimal("3000"),
                execute_live=True,private_key="synthetic-test-only",
                controller_fn=lambda **kw: pytest.fail("Must not create campaign"),
                execute_fn=lambda **kw: pytest.fail("Must never sign unauthorized campaign"))
    assert result["status"]=="UNAUTHORIZED_EXISTING_CAMPAIGN"

def test_managed_capital_excludes_dust_without_investing_it():
    from lapidus_vaults.wallet_plan import Policy, WalletSnapshot, VaultHolding, build_rebalance
    from datetime import datetime, timezone
    policy=Policy((V,),Decimal("0.20"),Decimal("0.40"))
    snap=WalletSnapshot(W,8453,123,5_000_202,
                        (VaultHolding(V,0,0,123,1,1),),
                        datetime.now(timezone.utc).isoformat())
    preview=build_rebalance(snap,policy,managed_capital_cap_units=5_000_000)
    assert preview.capital_units == 5_000_000
    assert preview.changes[0].target_units == 2_000_000
    assert preview.target_idle_units == 3_000_202
    with pytest.raises(ValueError,match="strict cap"):
        build_rebalance(snap,policy,managed_capital_cap_units=4_000_000)

def test_persistent_economic_authorization_is_exact_and_expires(tmp_path):
    from lapidus_vaults.operations.live_pilot import _authorize, _evidence_valid
    from lapidus_vaults.unsigned_candidates import UnsignedCandidate, APPROVE
    from lapidus_vaults.rpc_verify import _address_word, _word
    from lapidus_vaults.allocator import BASE_USDC
    from lapidus_vaults.operations.campaigns import create_campaign
    db=str(tmp_path/"auth.sqlite3")
    a=UnsignedCandidate(1,(),"approve_intent",8453,W,BASE_USDC,0,
                        APPROVE+_address_word(V)+_word(500000),500000)
    create_campaign(db,"pilot-test",(a,))
    e={"decision":"REBALANCE_REVIEW_ONLY","submitted":False,
       "capital_units":5_000_000,"estimated_fee_usd":"0.001",
       "projected_historical_rate_yield_delta_usd":"0.05",
       "eth_usd_assumption":"3000"}
    _authorize(db,"pilot-test",W,(V,),e)
    assert _evidence_valid(db,"pilot-test",W,(V,),require_fresh=True)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE pilot_authorizations SET authorized_at='2026-01-01T00:00:00+00:00'")
    assert not _evidence_valid(db,"pilot-test",W,(V,),require_fresh=True)
    assert _evidence_valid(db,"pilot-test",W,(V,),require_fresh=False)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE pilot_authorizations SET economics_json='{}'")
    assert not _evidence_valid(db,"pilot-test",W,(V,),require_fresh=False)

def test_bad_signer_input_has_safe_diagnostic(tmp_path):
    db=str(tmp_path/"pilot.sqlite3")
    sqlite3.connect(db).close()
    with patch("lapidus_vaults.operations.live_pilot.read_policy") as policy:
        policy.return_value.vault_addresses=(V,)
        with pytest.raises(ValueError,match="SIGNER_KEY_INVALID_FORMAT"):
            cycle(db_path=db,wallet=W,protected_wallet=P,allowlist="ignored",
                  rpc_url="https://example.invalid",eth_usd=Decimal("3000"),
                  execute_live=True,private_key="not a private key")
