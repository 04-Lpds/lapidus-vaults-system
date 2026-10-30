"""Offline admission-to-campaign persistence rehearsal.

Uses a temporary SQLite database, fake addresses, zero RPC calls and NO keys.
Never creates a campaign in the user's real executor database.
"""
from __future__ import annotations
from dataclasses import asdict
import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from ..unsigned_candidates import UnsignedCandidate
from .campaigns import create_campaign, read_campaign
from .synthetic_admission import WALLET, VAULT, AMOUNT
from .campaign_admission import assess_admission
from ..allocator import BASE_USDC
from ..rpc_verify import _address_word, _word
from ..unsigned_candidates import APPROVE, DEPOSIT

def rehearse(name: str) -> dict:
    if name not in {"profitable", "uneconomic", "tampered"}:
        raise ValueError("Unknown scenario")
    economics = {
        "decision": "REBALANCE_REVIEW_ONLY",
        "submitted": False,
        "capital_units": 5_000_000,
        "estimated_fee_usd": "0.001",
        "projected_historical_rate_yield_delta_usd": "0.10",
        "eth_usd_assumption": "3000",
    }
    candidates = [
        UnsignedCandidate(1, (), "approve_intent", 8453, WALLET,
                          BASE_USDC, 0, APPROVE+_address_word(VAULT)+_word(AMOUNT), AMOUNT),
        UnsignedCandidate(2, (1,), "deposit_intent", 8453, WALLET,
                          VAULT, 0, DEPOSIT+_word(AMOUNT)+_address_word(WALLET), AMOUNT),
    ]
    if name == "uneconomic":
        economics["decision"] = "HOLD_ESTIMATED_FEES_EXCEED_BENEFIT"
        economics["estimated_fee_usd"] = "0.50"
    if name == "tampered":
        c = candidates[1]
        candidates[1] = UnsignedCandidate(c.sequence, c.depends_on, c.method,
              c.chain_id, c.from_address, c.to_address, c.value_wei,
              DEPOSIT+_word(AMOUNT+1)+_address_word(WALLET),c.requested_assets_units)
    admission = assess_admission(economics=economics,
        candidates=[asdict(c) for c in candidates],wallet=WALLET,
        approved_vaults=(VAULT,))
    with TemporaryDirectory(prefix="lvs-synthetic-campaign-") as temp:
        db = str(Path(temp)/"scenario.sqlite3")
        if admission["status"] == "ADMISSION_REVIEW_ONLY":
            saved = create_campaign(db, "synthetic-rehearsal", tuple(candidates))
            loaded = read_campaign(db, "synthetic-rehearsal")
            if loaded["state"] != "ACTIVE" or len(loaded["steps"])!=2:
                raise AssertionError("Campaign round-trip mismatch")
            return {"scenario":name,"admission":admission["status"],
                 "isolated_campaign_persisted":True,"campaign_state":loaded["state"],
                 "step_count":len(loaded["steps"]),"submitted":False,
                 "warning":"Temporary test DB only; no real wallet, signing or transaction."}
        if Path(db).exists():
            raise AssertionError("Blocked case unexpectedly opened campaign database")
        return {"scenario":name,"admission":admission["status"],
                "isolated_campaign_persisted":False,"submitted":False}

def main(argv=None):
    p=argparse.ArgumentParser(description="Offline economic admission -> temporary campaign journal")
    p.add_argument("--scenario",choices=("all","profitable","uneconomic","tampered"),default="all")
    args=p.parse_args(argv)
    names=("profitable","uneconomic","tampered") if args.scenario=="all" else (args.scenario,)
    print(json.dumps([rehearse(x) for x in names],indent=2))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
