"""Crash-safe reservation journal for bounded unattended execution.

This module only records intentions and transaction outcomes. It cannot sign
or broadcast. A reservation is committed BEFORE any external send attempt;
after a crash or unknown outcome, it must never be automatically re-sent.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

STATES = frozenset({"SUBMISSION_UNKNOWN", "SUBMITTED", "CONFIRMED", "HALTED"})

def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()

def _schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS autonomous_submissions(
        campaign_id TEXT NOT NULL,
        sequence INTEGER NOT NULL,
        quote_id TEXT NOT NULL UNIQUE,
        wallet TEXT NOT NULL,
        to_address TEXT NOT NULL,
        calldata TEXT NOT NULL,
        amount_units INTEGER NOT NULL,
        state TEXT NOT NULL,
        tx_hash TEXT,
        updated_at TEXT NOT NULL,
        PRIMARY KEY(campaign_id,sequence)
    )""")

def reserve(db_path: str, *, campaign_id: str, sequence: int, quote_id: str,
            wallet: str, to_address: str, calldata: str,
            amount_units: int) -> dict:
    if (not campaign_id or not quote_id or
        type(sequence) is not int or sequence < 1 or
        type(amount_units) is not int or not 0 < amount_units <= 1_000_000):
        raise ValueError("Invalid bounded autonomous reservation")
    from ..allocator import address_ok
    if (not address_ok(wallet) or not address_ok(to_address)
        or not isinstance(calldata, str) or not calldata.startswith("0x")
        or len(calldata) % 2 != 0):
        raise ValueError("Invalid transaction identity")
    bytes.fromhex(calldata[2:])
    with sqlite3.connect(db_path, timeout=10) as db:
        db.execute("BEGIN IMMEDIATE")
        _schema(db)
        exists = db.execute(
            "SELECT campaign_id,sequence,state FROM autonomous_submissions "
            "WHERE campaign_id=? OR quote_id=?", (campaign_id, quote_id)
        ).fetchall()
        if exists:
            # Never quietly turn an unknown or submitted transaction into
            # a second send. Entire campaign is blocked until resolved.
            raise ValueError("An autonomous campaign already has a reservation; resolve before retrying")
        db.execute("""INSERT INTO autonomous_submissions VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (campaign_id, sequence, quote_id, wallet.lower(), to_address.lower(),
             calldata.lower(), amount_units, "SUBMISSION_UNKNOWN", None, _utc()))
    return {"state": "SUBMISSION_UNKNOWN", "campaign_id": campaign_id, "sequence": sequence}

def mark_submitted(db_path: str, *, campaign_id: str, sequence: int,
                   quote_id: str, tx_hash: str) -> None:
    if not isinstance(tx_hash, str) or len(tx_hash) != 66 or not tx_hash.startswith("0x"):
        raise ValueError("Invalid transaction hash")
    bytes.fromhex(tx_hash[2:])
    with sqlite3.connect(db_path, timeout=10) as db:
        db.execute("BEGIN IMMEDIATE")
        _schema(db)
        row = db.execute("""SELECT state,tx_hash,quote_id FROM autonomous_submissions
            WHERE campaign_id=? AND sequence=?""", (campaign_id, sequence)).fetchone()
        if not row or row[2] != quote_id or row[0] not in {"SUBMISSION_UNKNOWN","SUBMITTED"}:
            raise ValueError("Reservation not eligible for submission update")
        if row[1] is not None and row[1] != tx_hash.lower():
            raise ValueError("Cannot replace bound transaction hash")
        db.execute("""UPDATE autonomous_submissions SET state='SUBMITTED',
            tx_hash=?,updated_at=? WHERE campaign_id=? AND sequence=?""",
            (tx_hash.lower(),_utc(),campaign_id,sequence))

def mark_verified(db_path: str, *, campaign_id: str, sequence: int,
                  tx_hash: str, verified: dict) -> None:
    # Only accept a previously journaled hash and independently produced
    # matching receipt evidence; do not accept an arbitrary success boolean.
    if (verified.get("status") != "RECEIPT_AND_BLOCK_MATCH"
        or verified.get("matched") is not True
        or verified.get("balance_match") is not True
        or not isinstance(verified.get("transaction_event_reconciliation"), dict)
        or verified["transaction_event_reconciliation"].get("matched") is not True
        or verified.get("tx_hash","").lower() != tx_hash.lower()):
        raise ValueError("Insufficient independent receipt evidence")
    with sqlite3.connect(db_path, timeout=10) as db:
        db.execute("BEGIN IMMEDIATE")
        _schema(db)
        row=db.execute("""SELECT state,tx_hash FROM autonomous_submissions
            WHERE campaign_id=? AND sequence=?""",(campaign_id,sequence)).fetchone()
        if not row or row[0]!="SUBMITTED" or row[1]!=tx_hash.lower():
            raise ValueError("No matching submitted hash")
        db.execute("""UPDATE autonomous_submissions SET state='CONFIRMED',updated_at=?
            WHERE campaign_id=? AND sequence=?""",(_utc(),campaign_id,sequence))

def current(db_path: str, campaign_id: str) -> list[dict]:
    with sqlite3.connect(db_path, timeout=10) as db:
        _schema(db)
        rows=db.execute("""SELECT sequence,quote_id,state,tx_hash FROM
            autonomous_submissions WHERE campaign_id=? ORDER BY sequence""",
            (campaign_id,)).fetchall()
    return [{"sequence":r[0],"quote_id":r[1],"state":r[2],"tx_hash":r[3]}
            for r in rows]
