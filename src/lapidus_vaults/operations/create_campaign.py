"""CLI for a one-shot, read-only planned campaign snapshot.

No signing, sending, or unattended execution.
"""
from __future__ import annotations
import argparse
import json
import os
import sys

from ..client import MorphoClient
from ..rpc_verify import ReadOnlyRPC
from ..live_readonly_plan import build_live_readonly_plan
from .campaign_builder import build_campaign_from_live_plan


def main(argv=None):
    p=argparse.ArgumentParser(description="Create a non-executable supervised LVS campaign")
    p.add_argument("--wallet",required=True)
    p.add_argument("--allowlist",required=True)
    p.add_argument("--campaign-id",required=True)
    p.add_argument("--db",default="data/lapidus.sqlite3")
    p.add_argument("--max-action-usdc",type=int,default=10)
    args=p.parse_args(argv)
    if type(args.max_action_usdc) is not int or not 1<=args.max_action_usdc<=10:
        print("CAMPAIGN BLOCKED: action cap must be 1..10 USDC",file=sys.stderr)
        return 1
    if not os.environ.get("BASE_RPC_URL"):
        print("CAMPAIGN BLOCKED: BASE_RPC_URL required",file=sys.stderr)
        return 1
    try:
        with ReadOnlyRPC(os.environ["BASE_RPC_URL"]) as rpc, MorphoClient() as morpho:
            report=build_live_readonly_plan(args.wallet,args.allowlist,rpc,morpho)
            result=build_campaign_from_live_plan(
                args.db,args.campaign_id,args.wallet,args.allowlist,report,rpc,
                max_action_units=args.max_action_usdc*1_000_000)
        print(json.dumps(result,indent=2))
        print("CAMPAIGN JOURNALED ONLY. NO TRANSACTIONS AUTHORIZED OR SENT.")
        return 0
    except Exception as exc:
        # Do not reveal provider URLs or RPC credentials in exception messages.
        if isinstance(exc,ValueError):
            print("CAMPAIGN BLOCKED: "+str(exc),file=sys.stderr)
        else:
            print("CAMPAIGN BLOCKED: connectivity, policy, or local journal failure",file=sys.stderr)
        return 1


if __name__=="__main__":
    raise SystemExit(main())
