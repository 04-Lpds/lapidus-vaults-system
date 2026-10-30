"""Keyless, read-only preflight for a dedicated Base executor.

This CLI NEVER loads signing secrets or exposes any JSON-RPC write method.
It does not create or authorize a campaign.
"""
from __future__ import annotations
import argparse
import json
import os
import sqlite3
import sys

from ..allocator import address_ok
from ..rpc_verify import ReadOnlyRPC
from ..live_readonly_plan import ETH_RESERVE, DEDICATED_EXECUTOR_ETH_RESERVE
from ..client import MorphoClient
from ..live_readonly_plan import build_live_readonly_plan
from ..wallet_plan import read_policy
from .auto_once import assert_no_orphaned_authorizations
from .autopilot_policy import AutoPolicy, next_auto_action
from .campaign_quote import quote_next_campaign_action
from ..live_deposit import DepositQuotes
from ..live_withdraw import WithdrawalQuotes
from .dedicated_signer import MAX_GAS_LIMIT
from .signer_policy import load_signer_policy


def preflight(*, db_path: str, wallet: str, protected_wallet: str,
              allowlist_path: str, rpc_url: str) -> dict:
    if not address_ok(wallet) or not address_ok(protected_wallet):
        raise ValueError("Invalid executor or protected wallet address")
    wallet=wallet.lower()
    if wallet==protected_wallet.lower():
        raise ValueError("Executor and protected wallet must differ")
    policy_file=read_policy(allowlist_path)
    policy=AutoPolicy(wallet=wallet, vault_allowlist=policy_file.vault_addresses,
                      max_action_units=1_000_000,max_capital_units=10_000_000)
    if not os.path.isfile(db_path):
        raise ValueError("Dedicated campaign DB does not exist; refusing to create it")
    assert_no_orphaned_authorizations(db_path)
    with sqlite3.connect(db_path, timeout=10) as db:
        tables={row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "autonomous_submissions" in tables and db.execute(
            "SELECT 1 FROM autonomous_submissions WHERE state!='CONFIRMED' LIMIT 1"
        ).fetchone():
            raise RuntimeError("Unresolved signed transaction; recovery required before preflight")
    with ReadOnlyRPC(rpc_url) as rpc:
        chain=rpc.chain_id()
        if chain!=8453:
            raise RuntimeError("Expected Base mainnet chain 8453")
        block=rpc.block_number()
        eth_wei=rpc.eth_balance(wallet,block)
        gas_price=rpc.gas_price_wei()
        # Report actual balance before the legacy planner deducts its
        # conservative 0.002 ETH reserve, which can leave zero fee budget.
        if eth_wei < DEDICATED_EXECUTOR_ETH_RESERVE:
            return {
                "status":"PREFLIGHT_ETH_RESERVE_BLOCKED",
                "wallet":wallet,"chain_id":chain,"rpc_block":block,
                "eth_balance_wei":eth_wei,
                "legacy_eth_reserve_wei":ETH_RESERVE,
                "executor_eth_reserve_wei":DEDICATED_EXECUTOR_ETH_RESERVE,
                "shortfall_wei":DEDICATED_EXECUTOR_ETH_RESERVE-eth_wei,
                "gas_price_wei":gas_price,
                "campaign_status":"NOT_EVALUATED_ETH_RESERVE",
                "signing_key_accessed":False,"submitted":False,
                "explanation":"Actual on-chain ETH is below the dedicated executor reserve; no transaction was authorized.",
            }
        with MorphoClient() as client:
            report=build_live_readonly_plan(
                wallet,allowlist_path,rpc,client,
                eth_reserve_wei=DEDICATED_EXECUTOR_ETH_RESERVE)
        capital=report["plan"]["capital_units"]
        action=next_auto_action(db_path,policy=policy,
                                observed_capital_units=capital,current_chain_id=chain)
        result={
            "status":"PREFLIGHT_READ_ONLY",
            "wallet":wallet,"chain_id":chain,"rpc_block":block,
            "capital_units":capital,"eth_balance_wei":eth_wei,
            "gas_price_wei":gas_price,
            "executor_eth_reserve_wei":DEDICATED_EXECUTOR_ETH_RESERVE,
            "legacy_eth_reserve_wei":ETH_RESERVE,
            "campaign_status":action["status"],
            "signing_key_accessed":False,"submitted":False,
        }
        if action["status"]!="AUTO_POLICY_PASSED_AWAITING_SECURE_SIGNER":
            return result
        try:
            signer_policy = load_signer_policy(os.environ.get("LVS_SIGNER_POLICY", ""))
            if (signer_policy.execution_address != wallet
                    or signer_policy.protected_address != protected_wallet.lower()
                    or signer_policy.vault_allowlist != policy.vault_allowlist
                    or action["method"].removesuffix("_intent") not in signer_policy.operation_allowlist
                    or action["amount_units"] > signer_policy.max_action_units):
                raise ValueError("Signer policy differs from campaign configuration")
        except ValueError:
            result["status"] = "PREFLIGHT_SIGNER_POLICY_BLOCKED"
            return result
        q=quote_next_campaign_action(
            db_path,wallet=wallet,approved_vaults=policy.vault_allowlist,
            rpc=rpc,deposits=DepositQuotes(min_gas_reserve_wei=signer_policy.min_eth_reserve_wei),
            withdrawals=WithdrawalQuotes(min_gas_reserve_wei=signer_policy.min_eth_reserve_wei))
        gas=int(q["gas"],16)
        worst_fee=gas*gas_price*2
        result.update(
            campaign_id=action["campaign_id"],sequence=action["sequence"],
            stage=q["stage"],amount_units=q["amount_units"],
            estimated_gas_limit=gas,worst_case_fee_wei=worst_fee,
            gas_cap_passed=(gas<=MAX_GAS_LIMIT and
                            worst_fee<=signer_policy.max_total_fee_wei),
            funded_for_worst_case_fee=eth_wei>=signer_policy.min_eth_reserve_wei+worst_fee,
        )
        result["status"]=("PREFLIGHT_READY_NO_BROADCAST" if
                          result["gas_cap_passed"] and result["funded_for_worst_case_fee"]
                          else "PREFLIGHT_GAS_OR_FUNDING_BLOCKED")
        return result


def main(argv=None) -> int:
    p=argparse.ArgumentParser(description="Read-only dedicated-wallet mainnet preflight; never signs")
    p.add_argument("--wallet",required=True)
    p.add_argument("--protected-wallet",required=True)
    p.add_argument("--allowlist",required=True)
    p.add_argument("--db",required=True)
    args=p.parse_args(argv)
    rpc_url=os.environ.get("BASE_RPC_URL","https://mainnet.base.org")
    try:
        print(json.dumps(preflight(
            db_path=args.db,wallet=args.wallet,
            protected_wallet=args.protected_wallet,
            allowlist_path=args.allowlist,rpc_url=rpc_url),indent=2))
        return 0
    except Exception as exc:
        print("PREFLIGHT BLOCKED: "+type(exc).__name__+": "+str(exc),file=sys.stderr)
        return 1

if __name__=="__main__":
    raise SystemExit(main())
