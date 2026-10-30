"""Recheck persisted campaign authority under the caller's SQLite write lock."""
from __future__ import annotations

import json
import sqlite3


def assert_active_authorization(db: sqlite3.Connection, *, campaign_id: str,
                                sequence: int, quote_id: str,
                                consumed: dict, candidate: dict,
                                submitted_hash: str | None = None) -> None:
    if type(sequence) is not int or sequence < 1:
        raise ValueError("Invalid campaign sequence")
    saved = db.execute("""SELECT b.campaign_id,b.sequence,a.transaction_json,
        c.plan_json,c.state,c.wallet,a.wallet,a.vault,a.stage,a.amount_units
        FROM rebalance_campaign_quote_bindings b
        JOIN live_deposit_authorizations a ON a.quote_id=b.quote_id
        JOIN rebalance_campaigns c ON c.campaign_id=b.campaign_id
        WHERE b.quote_id=?""", (quote_id,)).fetchone()
    if (saved is None or saved[0] != campaign_id or saved[1] != sequence
            or saved[4] != "ACTIVE" or json.loads(saved[2]) != consumed
            or saved[5] != consumed.get("wallet")
            or tuple(saved[6:]) != (consumed.get("wallet"), consumed.get("vault"),
                                    consumed.get("stage"), consumed.get("amount_units"))):
        raise ValueError("No active exact persisted campaign authorization binding")
    plan = json.loads(saved[3])
    if not 1 <= sequence <= len(plan) or plan[sequence - 1] != candidate:
        raise ValueError("Candidate differs from persisted campaign authorization")
    progress = {n: (status, tx_hash) for n, status, tx_hash in db.execute(
        "SELECT sequence,status,tx_hash FROM rebalance_campaign_steps WHERE campaign_id=?",
        (campaign_id,))}
    if any(progress.get(n, (None,))[0] != "RECEIPT_AND_BLOCK_MATCH"
           for n in range(1, sequence)):
        raise ValueError("Earlier campaign steps are not confirmed")
    if sequence in progress:
        raise ValueError("Campaign step already has recorded activity; never resend")
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    journal = (db.execute("SELECT campaign_id,sequence,state,tx_hash,quote_id FROM autonomous_submissions").fetchall()
               if "autonomous_submissions" in tables else [])
    for cid, seq, state, tx_hash, quote in journal:
        is_current = (cid, seq, quote) == (campaign_id, sequence, quote_id)
        if is_current:
            if submitted_hash is None or state != "SUBMITTED" or tx_hash != submitted_hash:
                raise ValueError("Duplicate autonomous authorization; never resend")
        elif state != "CONFIRMED":
            raise ValueError("Prior autonomous transaction unresolved; never resend")
    if submitted_hash is not None and not any(
            (cid, seq, quote, state, tx_hash) == (campaign_id, sequence, quote_id, "SUBMITTED", submitted_hash)
            for cid, seq, state, tx_hash, quote in journal):
        raise ValueError("Missing durable submitted hash")
    for n in range(1, sequence):
        if not any(cid == campaign_id and seq == n and state == "CONFIRMED"
                   and tx_hash == progress[n][1]
                   for cid, seq, state, tx_hash, quote in journal):
            raise ValueError("Earlier campaign receipt and submission journal disagree")
