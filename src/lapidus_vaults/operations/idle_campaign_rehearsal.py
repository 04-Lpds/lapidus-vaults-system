"""Offline admission rehearsal: controller-originated idle-USDC deposit campaign.

Does not sign, broadcast, alter operator DB or change live signing caps.
Tests that real observed-candidate logic creates a valid campaign from idle
capital and that current 1 USDC action caps are enforced.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict
from decimal import Decimal
import json
import tempfile
from pathlib import Path

from ..allocator import BASE_USDC
from .observed_campaign import propose_candidates
from .campaign_admission import assess_admission, AdmissionPolicy
from .campaigns import create_campaign, read_campaign

def rehearse(wallet:str,vaults:tuple[str,...],*,capital_units:int=5_000_000,
             idle_units:int=5_000_000,deposit_units:int=1_000_000)->dict:
    if (len(vaults)!=2 or len(set(vaults))!=2 or type(capital_units) is not int
        or type(idle_units) is not int or type(deposit_units) is not int
        or not 0<deposit_units<=1_000_000 or not deposit_units<=idle_units<=capital_units<=5_000_000):
        raise ValueError("Invalid bounded offline rehearsal")
    # One target change only. Other vault has zero change. The proposed
    # deposit is derived from an observed wallet-balance/delta snapshot.
    report={"plan":{"chain_id":8453,"wallet":wallet.lower(),
        "wallet_usdc_units":idle_units,"capital_units":capital_units,
        "changes":[{"vault_address":vaults[0],"delta_units":deposit_units},
                   {"vault_address":vaults[1],"delta_units":0}]}}
    candidates=propose_candidates(report,wallet=wallet.lower(),approved_vaults=vaults)
    economics={"decision":"REBALANCE_REVIEW_ONLY","submitted":False,
               "capital_units":capital_units,"estimated_fee_usd":"0.001",
               "projected_historical_rate_yield_delta_usd":"0.10",
               "eth_usd_assumption":"3000"}
    admission=assess_admission(economics=economics,
        candidates=[asdict(c) for c in candidates],wallet=wallet.lower(),
        approved_vaults=vaults,policy=AdmissionPolicy())
    if admission["status"]!="ADMISSION_REVIEW_ONLY":
        raise RuntimeError("Observed idle-deposit candidates rejected")
    with tempfile.TemporaryDirectory(prefix="lvs-idle-admission-") as tmp:
        db=str(Path(tmp)/"fork-rehearsal.sqlite3")
        created=create_campaign(db,"observed-idle-deposit",candidates)
        status=read_campaign(db,"observed-idle-deposit")
    return {"status":"OFFLINE_IDLE_CAMPAIGN_ADMITTED","steps":
            [c.method for c in candidates],"campaign_state":created["state"],
            "first_step_status":status["steps"][0]["status"],
            "admission":admission["status"],"submitted":False,
            "live_signer_enabled":False,
            "note":"Deterministic synthetic economics; this does NOT prove a real yield-triggered campaign or execution."}

def main(argv=None):
    p=argparse.ArgumentParser(description="Offline observed-idle candidate and admission rehearsal")
    p.add_argument("--wallet",required=True)
    p.add_argument("--source-vault",required=True)
    p.add_argument("--other-vault",required=True)
    a=p.parse_args(argv)
    try:
        print(json.dumps(rehearse(a.wallet,(a.source_vault,a.other_vault)),indent=2))
        return 0
    except (ValueError,RuntimeError):
        print(json.dumps({"status":"OFFLINE_IDLE_REHEARSAL_BLOCKED"}))
        return 2

if __name__=="__main__":
    raise SystemExit(main())
