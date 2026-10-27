"""Bridge independently checked receipts into supervised rebalance campaigns.

Only the authenticated local checker should invoke this with its own result.
No RPC writes and no permission to broadcast are granted by this adapter.
"""
from __future__ import annotations
import json
import sqlite3
from .campaigns import record_receipt_status, read_campaign

ACCEPTED = frozenset({"PENDING_NOT_FOUND","PENDING_MATCHED_INPUT",
                      "RECEIPT_AND_BLOCK_MATCH","REVERTED","RECONCILIATION_MISMATCH"})

def apply_verified_receipt(db_path: str, campaign_id: str, sequence: int,
                           quote_id: str, verified: dict) -> dict:
    if type(sequence) is not int or not isinstance(verified,dict):
        raise ValueError("Invalid receipt binding")
    status=verified.get("status")
    tx_hash=verified.get("tx_hash")
    if status not in ACCEPTED or not verified.get("matched") and status!="PENDING_NOT_FOUND":
        raise ValueError("Receipt is not verified")
    if status=="RECEIPT_AND_BLOCK_MATCH":
        events=verified.get("transaction_event_reconciliation")
        if (not isinstance(events,dict) or events.get("matched") is not True
            or verified.get("balance_match") is not True):
            raise ValueError("Missing independent receipt and balance evidence")
    with sqlite3.connect(db_path,timeout=10) as db:
        row=db.execute("""SELECT plan_json FROM rebalance_campaigns
                          WHERE campaign_id=? AND state='ACTIVE'""",(campaign_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown active campaign")
        steps=json.loads(row[0])
        if not 1<=sequence<=len(steps):
            raise ValueError("Unknown action sequence")
        intended=steps[sequence-1]
        authorization=db.execute("""SELECT wallet,vault,stage,amount_units,transaction_json
                                   FROM live_deposit_authorizations WHERE quote_id=?""",
                                 (quote_id,)).fetchone()
    if authorization is None:
        raise ValueError("Quote lacks authorization journal")
    wallet,vault,stage,amount_units,serialized=authorization
    tx=json.loads(serialized)
    if (intended["from_address"]!=wallet or intended["requested_assets_units"]!=amount_units
        or intended["to_address"]!=tx.get("to") or intended["data"]!=tx.get("data")
        or intended["chain_id"]!=tx.get("chain_id")
        or intended["method"]!=stage+"_intent"
        or intended["method"] not in {"approve_intent","deposit_intent","withdraw_intent"}):
        raise ValueError("Journaled authorization differs from campaign intention")
    if not isinstance(tx_hash,str):
        raise ValueError("Missing verified transaction hash")
    return record_receipt_status(db_path,campaign_id,sequence,status,tx_hash)
