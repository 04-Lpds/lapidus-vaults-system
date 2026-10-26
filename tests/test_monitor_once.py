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
