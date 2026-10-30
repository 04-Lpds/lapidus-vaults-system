"""Create one tightly bounded dedicated-wallet trial campaign, without signing.

Exactly two steps: USDC approve(vault, amount), then ERC4626 deposit(amount,
executor). The campaign is inert until a separate fresh quote + signing step.
"""
from __future__ import annotations
import argparse
import json
import os
import sqlite3
import sys

from ..allocator import BASE_USDC, address_ok
from ..rpc_verify import ReadOnlyRPC, _address_word, _word, BALANCE_OF, ASSET
from ..wallet_plan import read_policy
from ..unsigned_candidates import UnsignedCandidate, APPROVE, DEPOSIT
from .campaigns import create_campaign
from .auto_once import assert_no_orphaned_authorizations

MAX_TRIAL_UNITS = 800_000

def create_trial(*, db_path: str, wallet: str, protected_wallet: str,
                 vault: str, allowlist_path: str, rpc_url: str,
                 campaign_id: str, amount_units: int=MAX_TRIAL_UNITS) -> dict:
    if not all(address_ok(a) for a in (wallet, protected_wallet, vault)):
        raise ValueError("Invalid wallet or vault address")
    wallet, vault=wallet.lower(),vault.lower()
    if wallet==protected_wallet.lower():
        raise ValueError("Protected wallet cannot be the executor")
    if type(amount_units) is not int or not 0 < amount_units <= MAX_TRIAL_UNITS:
        raise ValueError("Trial cap: at most 0.8 USDC")
    if vault not in read_policy(allowlist_path).vault_addresses:
        raise ValueError("Vault not in explicit allowlist")
    if not os.path.isfile(db_path):
        raise ValueError("Dedicated test DB must exist before creating campaign")
    assert_no_orphaned_authorizations(db_path)
    with sqlite3.connect(db_path) as db:
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "autonomous_submissions" in tables and db.execute(
            "SELECT 1 FROM autonomous_submissions WHERE state!='CONFIRMED' LIMIT 1"
        ).fetchone():
            raise RuntimeError("Unresolved previous submission; manual review")
    with ReadOnlyRPC(rpc_url) as rpc:
        if rpc.chain_id()!=8453:
            raise ValueError("Base chain required")
        block=rpc.block_number()
        underlying=rpc.call_address(vault, ASSET, block)
        if underlying!=BASE_USDC:
            raise ValueError("Vault underlying is not native Base USDC")
        available=rpc.call_uint(BASE_USDC,BALANCE_OF,block,_address_word(wallet))
        if available<amount_units:
            raise ValueError("Executor lacks trial USDC")
        if rpc.eth_balance(wallet,block)<200_000_000_000_000:
            raise ValueError("Dedicated wallet lacks protected gas reserve")
    intent=(
        UnsignedCandidate(1,(),"approve_intent",8453,wallet,BASE_USDC,0,
                          APPROVE+_address_word(vault)+_word(amount_units),amount_units),
        UnsignedCandidate(2,(1,),"deposit_intent",8453,wallet,vault,0,
                          DEPOSIT+_word(amount_units)+_address_word(wallet),amount_units),
    )
    created=create_campaign(db_path,campaign_id,intent)
    return {
        "status":"TRIAL_CAMPAIGN_CREATED_NOT_AUTHORIZED",
        "campaign_id":campaign_id,"wallet":wallet,"vault":vault,
        "amount_units":amount_units,"steps":[
            {"sequence":1,"method":"approve_intent","amount_units":amount_units},
            {"sequence":2,"method":"deposit_intent","amount_units":amount_units},
        ],
        "observed_block":block,"submitted":False,
        "signing_key_accessed":False,
        "next":"Run keyless auto-preflight. Do not enable live signing.",
    }

def main(argv=None):
    p=argparse.ArgumentParser(description="Persist exactly 0.8 USDC trial campaign (read-only)")
    p.add_argument("--wallet",required=True)
    p.add_argument("--protected-wallet",required=True)
    p.add_argument("--allowlist",required=True)
    p.add_argument("--vault",required=True)
    p.add_argument("--db",required=True)
    p.add_argument("--campaign-id",default="executor-trial-001")
    args=p.parse_args(argv)
    try:
        result=create_trial(
            db_path=args.db,wallet=args.wallet,protected_wallet=args.protected_wallet,
            allowlist_path=args.allowlist,vault=args.vault,
            rpc_url=os.getenv("BASE_RPC_URL","https://mainnet.base.org"),
            campaign_id=args.campaign_id)
        print(json.dumps(result,indent=2))
        return 0
    except Exception as exc:
        print("TRIAL CAMPAIGN BLOCKED: "+type(exc).__name__+": "+str(exc),file=sys.stderr)
        return 1

if __name__=="__main__":
    raise SystemExit(main())
