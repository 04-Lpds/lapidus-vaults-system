"""Create a live manual quote from the *next* explicitly approved campaign step.

This is not autonomous execution: separate consume, wallet consent and receipt
checks remain mandatory. Any mismatch with original candidate fails closed.
"""
from __future__ import annotations
import json
import sqlite3
from .next_action import next_campaign_action

def quote_next_campaign_action(db_path, *, wallet, approved_vaults, rpc, deposits, withdrawals):
    action=next_campaign_action(db_path,wallet=wallet,approved_vaults=approved_vaults)
    if action["status"]!="READY_FOR_MANUAL_PREFLIGHT":
        raise ValueError("Campaign has no action eligible for fresh quote")
    with sqlite3.connect(db_path,timeout=5) as db:
        row=db.execute("SELECT plan_json FROM rebalance_campaigns WHERE campaign_id=? AND state='ACTIVE'",
                       (action["campaign_id"],)).fetchone()
    if row is None:
        raise ValueError("Campaign changed during quote")
    candidate=json.loads(row[0])[action["sequence"]-1]
    expected_stage=action["method"].removesuffix("_intent")
    engine=withdrawals if expected_stage=="withdraw" else deposits
    q=engine.quote(rpc,wallet=wallet,vault=action["vault"],approved_vaults=approved_vaults,
                   amount_units=action["amount_units"])
    if (q["stage"]!=expected_stage or q["wallet"]!=candidate["from_address"]
        or q["to"]!=candidate["to_address"] or q["data"]!=candidate["data"]
        or q["amount_units"]!=candidate["requested_assets_units"]
        or q["chain_id"]!=candidate["chain_id"]):
        engine.pending.pop(q["quote_id"],None)
        raise ValueError("Fresh transaction differs from campaign; replan required")
    return {**q,"campaign_id":action["campaign_id"],"sequence":action["sequence"],
            "manual_authorization_required":True}
