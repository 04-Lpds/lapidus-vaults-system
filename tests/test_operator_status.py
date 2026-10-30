import json
import sqlite3
from lapidus_vaults.operations.operator_status import inspect

def test_empty_db_is_safe(tmp_path):
    path=tmp_path/"empty.sqlite3"
    sqlite3.connect(path).close()
    result=inspect(str(path))
    assert result["status"]=="NO_UNRESOLVED_JOURNAL_ACTIVITY"
    assert result["read_only"] is True
    assert result["shadow_decision_count"]==0

def test_pending_hash_is_visible_and_never_mutated(tmp_path):
    path=tmp_path/"exec.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE rebalance_campaigns(campaign_id TEXT,state TEXT,created_at TEXT)")
        db.execute("INSERT INTO rebalance_campaigns VALUES ('trial','ACTIVE','now')")
        db.execute("CREATE TABLE autonomous_submissions(campaign_id TEXT, sequence INTEGER,state TEXT,tx_hash TEXT,updated_at TEXT)")
        db.execute("INSERT INTO autonomous_submissions VALUES ('trial',1,'SUBMITTED','0xabc','now')")
        db.execute("CREATE TABLE shadow_decisions(id INTEGER,observed_at TEXT,rpc_block INTEGER,decision TEXT,details_json TEXT)")
        db.execute("INSERT INTO shadow_decisions VALUES (1,'now',123,'HOLD',?)",
                   (json.dumps({"estimated_fee_usd":"0.65"}),))
    before=path.read_bytes()
    out=inspect(str(path))
    after=path.read_bytes()
    assert out["status"]=="REVIEW_REQUIRED"
    assert out["unresolved_submissions"][0]["tx_hash"]=="0xabc"
    assert out["latest_economic_decision"]["fee_estimate_usd"]=="0.65"
    assert before==after
