"""Prepare a persistent, non-executable campaign from an observed live plan.

Withdrawals must pass a fresh vault eth_call before a campaign can be created.
This is *not* a guarantee of future instant exit liquidity. Every eventual
transaction needs its own immediately preceding preflight.
"""
from __future__ import annotations
from dataclasses import asdict
from .campaigns import create_campaign
from ..safety_plan import SafetyPlan, PlanAction
from ..unsigned_candidates import make_unsigned_candidates
from ..live_withdraw import WithdrawalQuotes
from ..wallet_plan import read_policy

def build_campaign_from_live_plan(db_path: str, campaign_id: str, wallet: str,
                                  allowlist_path: str, live_report: dict, rpc,
                                  *, max_action_units: int = 10_000_000,
                                  max_block_lag: int = 10) -> dict:
    if live_report.get("status")!="VERIFIED_READ_ONLY_PLAN_NOT_EXECUTABLE":
        raise ValueError("Requires verified live plan")
    raw=live_report.get("plan")
    if not isinstance(raw,dict):
        raise ValueError("Missing safety plan")
    policy=read_policy(allowlist_path)
    if raw.get("wallet")!=wallet.lower() or raw.get("chain_id")!=8453:
        raise ValueError("Wrong wallet or chain")
    if rpc.chain_id()!=8453 or not 0<=rpc.block_number()-raw["observed_block"]<=max_block_lag:
        raise ValueError("Plan stale or wrong RPC network")
    actions=tuple(PlanAction(**{**step,"depends_on":tuple(step["depends_on"])})
                  for step in raw["actions"])
    if not actions:
        raise ValueError("No rebalance actions to persist")
    plan=SafetyPlan(**{**raw,"actions":actions,"assumptions":tuple(raw["assumptions"])})
    candidates=make_unsigned_candidates(plan,approved_vaults=policy.vault_addresses,
                                        max_assets_per_action_units=max_action_units)
    # Recheck each intended withdrawal against the underlying live vault.
    # The Morpho V2 maxWithdraw value is *not* considered a reliable limit.
    for action in actions:
        if action.kind=="withdraw_intent":
            WithdrawalQuotes().quote(
                rpc,wallet=wallet,vault=action.vault,
                approved_vaults=policy.vault_addresses,amount_units=action.amount_units)
    result=create_campaign(db_path,campaign_id,candidates)
    result["withdrawal_preflight"]="PASSED_AT_CREATION_ONLY"
    result["execution_enabled"]=False
    result["warning"]="Live quotes may become stale; all actions need fresh preflight before authorization"
    return result
