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
        or tx.get("wallet",wallet)!=wallet or tx.get("vault",vault)!=vault
        or tx.get("amount_units",amount_units)!=amount_units
        or intended["chain_id"]!=tx.get("chain_id")
        or intended["method"]!=stage+"_intent"
        or intended["method"] not in {"approve_intent","deposit_intent","withdraw_intent"}):
        raise ValueError("Journaled authorization differs from campaign intention")
    if not isinstance(tx_hash,str):
        raise ValueError("Missing verified transaction hash")
    return record_receipt_status(db_path,campaign_id,sequence,status,tx_hash)

def bind_campaign_quote(db_path: str, campaign_id: str, sequence: int, quote_id: str) -> None:
    """Bind a journaled authorization to a campaign step before submitting.

    Must be invoked by trusted local orchestration after a fresh preflight.
    Never exposed as a browser endpoint.
    """
    if not isinstance(quote_id,str) or not quote_id or type(sequence) is not int:
        raise ValueError("Invalid campaign quote binding")
    with sqlite3.connect(db_path,timeout=10) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("""CREATE TABLE IF NOT EXISTS rebalance_campaign_quote_bindings (
            quote_id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL,
            sequence INTEGER NOT NULL, UNIQUE(campaign_id,sequence))""")
        campaign=db.execute("SELECT plan_json,state FROM rebalance_campaigns WHERE campaign_id=?",
                            (campaign_id,)).fetchone()
        auth=db.execute("""SELECT wallet,vault,stage,amount_units,transaction_json
                           FROM live_deposit_authorizations WHERE quote_id=?""",(quote_id,)).fetchone()
        if campaign is None or campaign[1]!="ACTIVE" or auth is None:
            raise ValueError("No active campaign and journaled authorization")
        steps=json.loads(campaign[0])
        if sequence<1 or sequence>len(steps):
            raise ValueError("Invalid campaign step")
        intended=steps[sequence-1]
        wallet,vault,stage,units,raw=auth
        tx=json.loads(raw)
        if (intended["from_address"]!=wallet or intended["requested_assets_units"]!=units
            or intended["method"]!=stage+"_intent" or intended["to_address"]!=tx.get("to")
            or intended["data"]!=tx.get("data") or intended["chain_id"]!=tx.get("chain_id")):
            raise ValueError("Journaled transaction does not match step")
        db.execute("""INSERT INTO rebalance_campaign_quote_bindings
                      (quote_id,campaign_id,sequence) VALUES (?,?,?)""",
                   (quote_id,campaign_id,sequence))


def advance_bound_campaign(db_path: str, quote_id: str, verified: dict) -> dict | None:
    """Advance only an explicitly bound campaign, from a live RPC-checked result."""
    with sqlite3.connect(db_path,timeout=10) as db:
        present=db.execute("""SELECT name FROM sqlite_master
                              WHERE type='table' AND name='rebalance_campaign_quote_bindings'""").fetchone()
        if present is None:
            return None
        row=db.execute("""SELECT campaign_id,sequence FROM rebalance_campaign_quote_bindings
                          WHERE quote_id=?""",(quote_id,)).fetchone()
    if row is None:
        return None
    with sqlite3.connect(db_path,timeout=10) as db:
        state=db.execute("SELECT state FROM rebalance_campaigns WHERE campaign_id=?",(row[0],)).fetchone()
    if state is not None and state[0]=="COMPLETED":
        return read_campaign(db_path,row[0])
    return apply_verified_receipt(db_path,row[0],row[1],quote_id,verified)
