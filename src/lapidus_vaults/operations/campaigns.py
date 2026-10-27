"""SQLite-backed supervised rebalance campaigns.

No signing, broadcasting, or transaction authorization. The campaign snapshot
is immutable; progress is recorded against explicitly identified plan steps.
"""
from __future__ import annotations
from dataclasses import asdict
import json
import sqlite3
from datetime import datetime, timezone
from typing import Iterable

from ..execution_coordinator import supervise_candidates
from ..unsigned_candidates import UnsignedCandidate

VALID_OBSERVATIONS = frozenset({
    "SUBMITTED", "PENDING_NOT_FOUND", "PENDING_MATCHED_INPUT",
    "RECEIPT_AND_BLOCK_MATCH", "REVERTED", "RECONCILIATION_MISMATCH", "FAILED",
})

def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()

def _init(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS rebalance_campaigns (
        campaign_id TEXT PRIMARY KEY,
        created_at TEXT NOT NULL,
        wallet TEXT NOT NULL,
        plan_json TEXT NOT NULL,
        state TEXT NOT NULL
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS rebalance_campaign_steps (
        campaign_id TEXT NOT NULL,
        sequence INTEGER NOT NULL,
        status TEXT NOT NULL,
        tx_hash TEXT,
        updated_at TEXT NOT NULL,
        PRIMARY KEY(campaign_id,sequence),
        FOREIGN KEY(campaign_id) REFERENCES rebalance_campaigns(campaign_id)
    )""")

def create_campaign(db_path: str, campaign_id: str, candidates: tuple[UnsignedCandidate, ...]) -> dict:
    if not isinstance(campaign_id,str) or not 1<=len(campaign_id)<=100 or not campaign_id.isascii() or not all(c.isalnum() or c in "-_" for c in campaign_id):
        raise ValueError("Invalid campaign ID")
    if not candidates or len(candidates)>30:
        raise ValueError("Expected 1..30 candidate steps")
    if len({c.from_address for c in candidates})!=1 or len({c.chain_id for c in candidates})!=1:
        raise ValueError("Mixed wallet or chain")
    if candidates[0].chain_id!=8453:
        raise ValueError("Base only")
    supervise_candidates(candidates,{})
    plan_json=json.dumps([asdict(c) for c in candidates],sort_keys=True)
    with sqlite3.connect(db_path,timeout=10) as db:
        db.execute("BEGIN IMMEDIATE")
        _init(db)
        active=db.execute("SELECT campaign_id FROM rebalance_campaigns WHERE state='ACTIVE'").fetchone()
        if active is not None:
            raise ValueError("An active campaign already exists; finish or explicitly halt it")
        db.execute("INSERT INTO rebalance_campaigns VALUES (?,?,?,?,?)",
                   (campaign_id,_timestamp(),candidates[0].from_address,plan_json,"ACTIVE"))
    return read_campaign(db_path,campaign_id)

def read_campaign(db_path: str, campaign_id: str) -> dict:
    with sqlite3.connect(db_path,timeout=10) as db:
        _init(db)
        row=db.execute("SELECT wallet,plan_json,state FROM rebalance_campaigns WHERE campaign_id=?",
                       (campaign_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown campaign")
        wallet,serialized,state=row
        steps=[UnsignedCandidate(**{**c,"depends_on":tuple(c["depends_on"])}) for c in json.loads(serialized)]
        records=db.execute("SELECT sequence,status,tx_hash FROM rebalance_campaign_steps WHERE campaign_id=?",
                           (campaign_id,)).fetchall()
    observations={seq:status for seq,status,_ in records}
    decisions=supervise_candidates(tuple(steps),observations)
    return {"campaign_id":campaign_id,"wallet":wallet,"state":state,
            "steps":[{**asdict(d),"tx_hash":next((h for seq,_,h in records if seq==d.sequence),None)}
                     for d in decisions]}

def record_receipt_status(db_path: str, campaign_id: str, sequence: int,
                          status: str, tx_hash: str) -> dict:
    """Record externally verified results; never claim validation of a supplied status.

    Callers must first verify transaction and receipt using LVS receipt checker.
    This module does not independently authenticate caller-provided observations.
    """
    if status not in VALID_OBSERVATIONS or type(sequence) is not int:
        raise ValueError("Invalid receipt observation")
    if not isinstance(tx_hash,str) or len(tx_hash)!=66 or not tx_hash.startswith("0x"):
        raise ValueError("Invalid transaction hash")
    try:
        int(tx_hash[2:],16)
    except ValueError as exc:
        raise ValueError("Invalid transaction hash") from exc
    with sqlite3.connect(db_path,timeout=10) as db:
        db.execute("BEGIN IMMEDIATE")
        _init(db)
        row=db.execute("SELECT plan_json,state FROM rebalance_campaigns WHERE campaign_id=?",
                       (campaign_id,)).fetchone()
        if row is None or row[1]!="ACTIVE":
            raise ValueError("Campaign is not active")
        plan=json.loads(row[0])
        if sequence<1 or sequence>len(plan):
            raise ValueError("Unknown campaign step")
        existing=db.execute("SELECT status,tx_hash FROM rebalance_campaign_steps WHERE campaign_id=? AND sequence=?",
                            (campaign_id,sequence)).fetchone()
        if existing and existing[1]!=tx_hash.lower():
            raise ValueError("Cannot replace the bound transaction hash")
        if existing and existing[0]=="RECEIPT_AND_BLOCK_MATCH" and status!="RECEIPT_AND_BLOCK_MATCH":
            raise ValueError("Confirmed receipt cannot regress")
        prior={n:st for n,st in db.execute("SELECT sequence,status FROM rebalance_campaign_steps WHERE campaign_id=?",
                                           (campaign_id,)).fetchall()}
        if sequence>1 and any(prior.get(i)!="RECEIPT_AND_BLOCK_MATCH" for i in range(1,sequence)):
            raise ValueError("Earlier steps must be confirmed before recording later activity")
        if existing is None:
            db.execute("INSERT INTO rebalance_campaign_steps VALUES (?,?,?,?,?)",
                       (campaign_id,sequence,status,tx_hash.lower(),_timestamp()))
        else:
            db.execute("UPDATE rebalance_campaign_steps SET status=?,updated_at=? WHERE campaign_id=? AND sequence=?",
                       (status,_timestamp(),campaign_id,sequence))
        updated={**prior,sequence:status}
        if all(updated.get(i)=="RECEIPT_AND_BLOCK_MATCH" for i in range(1,len(plan)+1)):
            db.execute("UPDATE rebalance_campaigns SET state='COMPLETED' WHERE campaign_id=?",(campaign_id,))
    return read_campaign(db_path,campaign_id)

def halt_campaign(db_path: str, campaign_id: str) -> None:
    """Administrative stop only; does not cancel transactions already submitted."""
    with sqlite3.connect(db_path,timeout=10) as db:
        db.execute("BEGIN IMMEDIATE")
        _init(db)
        changed=db.execute("UPDATE rebalance_campaigns SET state='HALTED' WHERE campaign_id=? AND state='ACTIVE'",
                           (campaign_id,)).rowcount
        if changed!=1:
            raise ValueError("No active campaign with that ID")
