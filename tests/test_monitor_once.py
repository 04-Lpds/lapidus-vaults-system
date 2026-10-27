"""Offline tests for monitored drift and durable audit records."""
import sqlite3
from lapidus_vaults.monitor_once import drift_summary, save_observation
from lapidus_vaults.fork_roundtrip import ACTOR


def report(capital, actions):
    return {"verification": "basic reads passed",
            "plan": {"chain_id": 8453, "observed_block": 1234,
                     "capital_units": capital, "actions": actions}}


def test_no_capital_means_no_rebalance():
    d = drift_summary(report(0, []), threshold_bps=500)
    assert d["status"] == "NO_REBALANCE_NEEDED"
    assert d["max_vault_deviation_bps_floor"] == 0


def test_five_percent_threshold_and_approval_excluded():
    candidate = lambda k, n: {"kind": k, "amount_units": n}
    r = report(10_000_000, [candidate("withdraw_intent", 600_000),
                              candidate("approve_intent", 3_000_000),
                              candidate("deposit_intent", 400_000)])
    d = drift_summary(r, threshold_bps=500)
    assert d["status"] == "REBALANCE_REVIEW"
    assert d["max_vault_deviation_bps_floor"] == 600
    assert d["gross_transaction_assets_units"] == 1_000_000
    assert drift_summary(r, threshold_bps=601)["status"] == "NO_REBALANCE_NEEDED"


def test_observations_append_without_credentials(tmp_path):
    file = tmp_path / "monitor.db"
    r = report(0, [])
    a = drift_summary(r, threshold_bps=500)
    assert save_observation(str(file), wallet=ACTOR, report=r, assessment=a) == 1
    assert save_observation(str(file), wallet=ACTOR, report=r, assessment=a) == 2
    with sqlite3.connect(file) as db:
        row = db.execute("SELECT rpc_block, status, capital_units FROM monitoring_observations WHERE id=2").fetchone()
    assert row == (1234, "NO_REBALANCE_NEEDED", "0")


def test_monitor_lock_prevents_overlapping_runs(tmp_path):
    import pytest
    from lapidus_vaults.monitor_once import exclusive_monitor_lock
    db = str(tmp_path / "monitor.db")
    with exclusive_monitor_lock(db):
        with pytest.raises(RuntimeError, match="lock"):
            with exclusive_monitor_lock(db):
                pass
    with exclusive_monitor_lock(db):
        pass


def test_monitor_failure_journal_sanitizes_errors(tmp_path):
    from lapidus_vaults.monitor_once import save_monitor_failure
    db_path = str(tmp_path / "monitor.db")
    assert save_monitor_failure(db_path, wallet=ACTOR, failure_code="RPC_ERROR") == 1
    with sqlite3.connect(db_path) as db:
        rows = db.execute("SELECT wallet_address, failure_code FROM monitoring_failures").fetchall()
    assert rows == [(ACTOR, "RPC_ERROR")]


def test_observation_report_does_not_require_eth_budget():
    from unittest.mock import Mock, patch
    from decimal import Decimal
    from lapidus_vaults.monitor_once import observation_report
    from lapidus_vaults.wallet_plan import Policy, WalletSnapshot, VaultHolding
    from lapidus_vaults.fork_roundtrip import VAULTS
    policy = Policy(VAULTS, Decimal("0.20"), Decimal("0.40"))
    snapshot = WalletSnapshot(ACTOR, 8453, 1234, 0,
        tuple(VaultHolding(v, 0, 0, 0, 0, 0) for v in VAULTS), "test", "rpc")
    with patch("lapidus_vaults.monitor_once.read_policy", return_value=policy), patch(
        "lapidus_vaults.monitor_once.read_wallet_snapshot", return_value=snapshot
    ):
        result = observation_report(ACTOR, "dummy", Mock(), Mock())
    assert result["plan"]["actions"] == []
    assert result["plan"]["capital_units"] == 0


def test_atomic_monitor_status_file(tmp_path):
    import json
    from lapidus_vaults.monitor_once import write_alert_status
    path = tmp_path / "status.json"
    write_alert_status(str(path), {"status": "NO_REBALANCE_NEEDED"})
    assert json.loads(path.read_text())["status"] == "NO_REBALANCE_NEEDED"
    write_alert_status(str(path), {"status": "MONITOR_FAILED", "failure_code": "RPC_ERROR"})
    assert json.loads(path.read_text())["failure_code"] == "RPC_ERROR"
    assert not (tmp_path / "status.json.tmp").exists()
