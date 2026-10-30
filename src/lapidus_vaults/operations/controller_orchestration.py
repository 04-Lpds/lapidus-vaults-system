"""Composition boundary for reviewed decision -> bounded fork execution.

NOT a live transaction CLI. No production signer is selected here.
"""
from __future__ import annotations
from pathlib import Path
from .controller_once import controller_once
from .auto_campaign import run_bounded_campaign

def run_fork_controller_cycle(*, db_path, wallet, protected_wallet, allowlist,
                              live_rpc_url, fork_rpc_url, eth_usd, campaign_id,
                              fork_private_key, controller_fn=controller_once,
                              driver_fn=run_bounded_campaign, execute_fn=None):
    """Integrate a live read-only decision with isolated fork execution.

    The supplied wallet's mainnet position and fork position may differ.
    The execution engine independently re-quotes before each local action.
    """
    if not fork_rpc_url.startswith("http://127.0.0.1:"):
        raise ValueError("Controller integration only accepts loopback Anvil")
    if Path("data/STOP_AUTOMATION").exists():
        raise RuntimeError("Emergency stop is active")
    decision=controller_fn(db_path=db_path,wallet=wallet,
        allowlist=allowlist,rpc_url=live_rpc_url,eth_usd=eth_usd,
        campaign_id=campaign_id)
    if decision.get("status")!="CAMPAIGN_CREATED_REVIEW_ONLY":
        return {"status":"CONTROLLER_NO_EXECUTION","decision":decision,"submitted":False}
    if decision.get("campaign_id")!=campaign_id or not isinstance(
            decision.get("economic_evidence"),dict):
        raise RuntimeError("Missing bound economic evidence")
    args=dict(db_path=db_path,campaign_id=campaign_id,wallet=wallet,
        protected_wallet=protected_wallet,allowlist_path=allowlist,
        rpc_url=fork_rpc_url,private_key=fork_private_key,
        admitted_economics=decision["economic_evidence"])
    if execute_fn is not None:
        args["execute_fn"]=execute_fn
    execution=driver_fn(**args)
    return {"status":"FORK_CONTROLLER_CYCLE_COMPLETE",
            "decision":decision,"execution":execution,
            "mainnet_broadcast":False}
