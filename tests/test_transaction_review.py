"""Offline review-manifest consistency and persistence tests."""
import sqlite3
from dataclasses import asdict
from decimal import Decimal
import pytest
from lapidus_vaults.transaction_review import prepare_manifest, store_manifest
from lapidus_vaults.wallet_plan import Policy, WalletSnapshot, VaultHolding, build_rebalance
from lapidus_vaults.safety_plan import build_safety_plan
from lapidus_vaults.fork_roundtrip import VAULTS, ACTOR


def synthetic_report():
    policy = Policy(VAULTS, Decimal("0.20"), Decimal("0.40"))
    snap = WalletSnapshot(ACTOR, 8453, 1234, 0,
        (VaultHolding(VAULTS[0], 1, 10_000_000, 0, 0, 0),
         VaultHolding(VAULTS[1], 0, 0, 0, 0, 0)), "TEST", "demo")
    p = build_rebalance(snap, policy)
    safety = build_safety_plan(p, allowances={v: 0 for v in VAULTS},
                               max_gas_units=600_000, gas_price_wei=1_000_000,
                               available_eth_wei=10**18)
    return {"plan": asdict(safety)}


def test_hashed_manifest_is_deterministic_and_not_authorization():
    a = prepare_manifest(synthetic_report(), VAULTS, cap_usdc=10)
    b = prepare_manifest(synthetic_report(), VAULTS, cap_usdc=10)
    assert a == b
    assert a["status"] == "REVIEW_ONLY_NO_EXECUTION"
    assert len(a["manifest_sha256"]) == 64
    assert len(a["candidates"]) == 3
    assert all(c["status"] == "UNSIGNED_UNSIMULATED_DO_NOT_SUBMIT" for c in a["candidates"])


def test_rejects_cap_and_vault_mismatch():
    with pytest.raises(ValueError):
        prepare_manifest(synthetic_report(), VAULTS, cap_usdc=0)
    with pytest.raises(ValueError):
        prepare_manifest(synthetic_report(), (VAULTS[0],), cap_usdc=10)
    with pytest.raises(ValueError):
        prepare_manifest(synthetic_report(), VAULTS, cap_usdc=3)


def test_append_to_sqlite_journal(tmp_path):
    path = str(tmp_path / "journal.sqlite3")
    m = prepare_manifest(synthetic_report(), VAULTS, cap_usdc=10)
    assert store_manifest(path, m) == 1
    assert store_manifest(path, m) == 2
    with sqlite3.connect(path) as db:
        records = db.execute("SELECT manifest_sha256, status FROM transaction_review_manifests").fetchall()
    assert len(records) == 2
    assert records[0] == (m["manifest_sha256"], "REVIEW_ONLY_NO_EXECUTION")
