"""Deterministic synthetic admission scenarios. No RPC, keys or DB.

This is a decision/admission harness, NOT proof of fork execution.
"""
from __future__ import annotations
import argparse
import json
from decimal import Decimal
from ..allocator import BASE_USDC
from ..rpc_verify import _address_word,_word
from ..unsigned_candidates import APPROVE,DEPOSIT
from .campaign_admission import AdmissionPolicy,assess_admission

WALLET="0x"+"1"*40
VAULT="0x"+"2"*40
AMOUNT=800_000

def scenario(name:str)->dict:
    if name not in {"profitable","uneconomic","tampered"}:
        raise ValueError("Unknown simulation scenario")
    candidate=[
        {"sequence":1,"depends_on":[],"method":"approve_intent","chain_id":8453,
         "from_address":WALLET,"to_address":BASE_USDC,"value_wei":0,
         "data":APPROVE+_address_word(VAULT)+_word(AMOUNT),
         "requested_assets_units":AMOUNT},
        {"sequence":2,"depends_on":[1],"method":"deposit_intent","chain_id":8453,
         "from_address":WALLET,"to_address":VAULT,"value_wei":0,
         "data":DEPOSIT+_word(AMOUNT)+_address_word(WALLET),
         "requested_assets_units":AMOUNT},
    ]
    economics={"decision":"REBALANCE_REVIEW_ONLY",
         "submitted":False,"capital_units":5_000_000,
         "estimated_fee_usd":"0.001",
         "projected_historical_rate_yield_delta_usd":"0.10",
         "eth_usd_assumption":"3000"}
    if name=="uneconomic":
        economics["decision"]="HOLD_ESTIMATED_FEES_EXCEED_BENEFIT"
        economics["estimated_fee_usd"]="0.50"
    elif name=="tampered":
        candidate[1]["data"]=DEPOSIT+_word(AMOUNT+1)+_address_word(WALLET)
    admitted=assess_admission(economics=economics,candidates=candidate,
          wallet=WALLET,approved_vaults=(VAULT,))
    return {"scenario":name,"synthetic":True,"submitted":False,
            "campaign_created":False,"admission":admitted,
            "warning":"Synthetic data only: no transaction, campaign, RPC or execution."}

def main(argv=None):
    p=argparse.ArgumentParser(description="Offline artificial economics and campaign admission checks")
    p.add_argument("--scenario",choices=["profitable","uneconomic","tampered","all"],default="all")
    args=p.parse_args(argv)
    names=("profitable","uneconomic","tampered") if args.scenario=="all" else (args.scenario,)
    print(json.dumps([scenario(n) for n in names],indent=2))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
