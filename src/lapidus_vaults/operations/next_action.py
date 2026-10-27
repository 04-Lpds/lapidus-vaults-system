"""Read-only next-action projection for supervised campaigns.

It is not an authorization, transaction quote, or liquidity guarantee.
"""
import json
import sqlite3
from .campaigns import read_campaign

def next_campaign_action(db_path: str, *, wallet: str, approved_vaults: tuple[str,...]) -> dict:
    with sqlite3.connect(db_path,timeout=5) as db:
        exists=db.execute("""SELECT name FROM sqlite_master WHERE type='table'
            AND name='rebalance_campaigns'""").fetchone()
        row=(db.execute("""SELECT campaign_id,plan_json FROM rebalance_campaigns
            WHERE state='ACTIVE' ORDER BY created_at DESC LIMIT 1""").fetchone()
            if exists else None)
    if row is None:
        return {"status":"NO_ACTIVE_CAMPAIGN","execution_enabled":False}
    campaign_id,raw=row
    campaign=read_campaign(db_path,campaign_id)
    if campaign["wallet"]!=wallet.lower():
        raise ValueError("Active campaign belongs to a different wallet")
    steps=json.loads(raw)
    ready=[s for s in campaign["steps"] if s["status"]=="READY_FOR_MANUAL_PREFLIGHT"]
    if len(ready)>1:
        raise ValueError("Multiple ready steps")
    if not ready:
        return {"status":"NO_READY_STEP","campaign_id":campaign_id,
                "steps":campaign["steps"],"execution_enabled":False}
    step=ready[0]
    intent=steps[step["sequence"]-1]
    if intent["to_address"].lower()!=intent["to_address"] or intent["chain_id"]!=8453:
        raise ValueError("Invalid saved candidate")
    # Derive the vault from the candidate's operation to avoid treating the
    # underlying USDC approval contract as a vault.
    from ..allocator import BASE_USDC
    if intent["method"]=="approve_intent":
        vault="0x"+intent["data"][34:74]
        if intent["to_address"]!=BASE_USDC:
            raise ValueError("Unexpected approval destination")
    else:
        vault=intent["to_address"]
    if vault not in approved_vaults or intent["from_address"]!=wallet.lower():
        raise ValueError("Candidate no longer matches execution policy")
    if intent["requested_assets_units"]<=0 or intent["requested_assets_units"]>10_000_000:
        raise ValueError("Campaign action outside live transaction cap")
    return {"status":"READY_FOR_MANUAL_PREFLIGHT","campaign_id":campaign_id,
            "sequence":step["sequence"],"method":intent["method"],"vault":vault,
            "amount_units":intent["requested_assets_units"],
            "execution_enabled":False,
            "warning":"Display only. Fresh quote, network checks, simulation, and explicit MetaMask consent are mandatory."}
