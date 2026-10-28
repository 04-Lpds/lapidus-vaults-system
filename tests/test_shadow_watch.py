"""No-key recurring monitoring controller tests."""
from decimal import Decimal
from pathlib import Path
from lapidus_vaults.operations.shadow_watch import monitor_loop

W="0x"+"1"*40

def test_periodic_journals_each_cycle_without_signing(tmp_path):
    db=tmp_path/"db.sqlite3"
    db.touch()
    calls=[]
    slept=[]
    def cycle(**kwargs):
        calls.append(kwargs)
        return {"decision":"HOLD_DRIFT_BELOW_THRESHOLD",
                "shadow_decision_id":len(calls),"rpc_block":123}
    result=monitor_loop(db_path=str(db),wallet=W,allowlist="fake",
        rpc_url="https://mainnet.base.org",eth_usd=Decimal("3000"),
        interval_seconds=60,max_cycles=3,cycle_fn=cycle,sleep_fn=slept.append)
    assert result["completed"]==3
    assert result["failed"]==0
    assert result["submitted"] is False
    assert slept==[60,60]

def test_failure_does_not_sign_or_disable_later_observations(tmp_path):
    db=tmp_path/"db.sqlite3"
    db.touch()
    calls=[]
    def cycle(**kwargs):
        calls.append(1)
        if len(calls)==1:raise RuntimeError("provider unavailable")
        return {"decision":"HOLD_ESTIMATED_FEES_EXCEED_BENEFIT",
                "shadow_decision_id":1,"rpc_block":124}
    result=monitor_loop(db_path=str(db),wallet=W,allowlist="fake",
        rpc_url="https://mainnet.base.org",eth_usd=Decimal("3000"),
        interval_seconds=60,max_cycles=2,cycle_fn=cycle,sleep_fn=lambda _:None)
    assert result["completed"]==1 and result["failed"]==1
    assert result["submitted"] is False
