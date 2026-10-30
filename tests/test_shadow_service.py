"""Offline read-only monitoring service regression."""
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from lapidus_vaults.operations.shadow_service import service

W="0x"+"1"*40
V="0x"+"2"*40

def kwargs(tmp_path, fn):
    return dict(db_path=str(tmp_path/"shadow.sqlite3"),wallet=W,
        allowlist="unused.json",rpc_url="https://mainnet.base.org",
        eth_usd=Decimal("3000"),interval_seconds=60,max_cycles=3,
        failure_limit=2,cycle_fn=fn,sleep_fn=lambda seconds:None)

def test_service_runs_read_only_and_records(tmp_path):
    seen=[]
    def cycle(**kw):
        seen.append(kw)
        return {"decision":"HOLD_ESTIMATED_FEES_EXCEED_BENEFIT","rpc_block":123}
    with patch("lapidus_vaults.operations.shadow_service.read_policy"):
        out=service(**kwargs(tmp_path,cycle))
    assert out["status"]=="MONITORING_FINISHED"
    assert len(seen)==3 and out["submitted"] is False

def test_service_stops_after_consecutive_failures(tmp_path):
    def failed(**kw):raise RuntimeError("provider unavailable")
    with patch("lapidus_vaults.operations.shadow_service.read_policy"):
        out=service(**kwargs(tmp_path,failed))
    assert out["status"]=="STOPPED_AFTER_REPEATED_FAILURES"
    assert out["failed"]==2 and out["submitted"] is False

def test_service_refuses_non_https_provider(tmp_path):
    with patch("lapidus_vaults.operations.shadow_service.read_policy"):
        try:
            service(**{**kwargs(tmp_path,lambda **kw:None),
                      "rpc_url":"http://127.0.0.1:8545"})
        except ValueError:
            pass
        else:
            raise AssertionError("insecure provider accepted")
