"""Pure, read-only vault exposure risk gate. Research only: never authorizes trades.

Inputs are saved, independently inspected exposure reports, not live RPC proofs.
Missing/unresolved positions fail closed. Integer token units and Decimal math.
"""
from __future__ import annotations
import argparse
from collections import defaultdict
from decimal import Decimal
import json
from pathlib import Path

def assess_portfolio(*, holdings:dict, exposures:dict, max_market_bps:int=2500,
                     max_vault_bps:int=4000, max_unresolved_bps:int=0)->dict:
    if not all(type(x) is int and 0<=x<=10000 for x in
               (max_market_bps,max_vault_bps,max_unresolved_bps)):
        raise ValueError("Invalid risk limits")
    if not isinstance(holdings,dict) or not isinstance(exposures,dict) or not holdings:
        raise ValueError("Nonempty holdings and exposures required")
    if any(type(v) is not int or v<0 for v in holdings.values()):
        raise ValueError("Holdings must use nonnegative integer USDC base units")
    total=sum(holdings.values())
    if total<=0:
        raise ValueError("No assets")
    markets=defaultdict(Decimal)
    unresolved=0
    vault_positions={}
    for vault,amount in holdings.items():
        if not isinstance(vault,str) or not vault.startswith("0x") or len(vault)!=42:
            raise ValueError("Invalid vault identity")
        source=exposures.get(vault)
        if not isinstance(source,dict) or source.get("vault_address","").lower()!=vault.lower():
            raise ValueError("Missing matched exposure snapshot")
        underlying=source.get("total_assets_units")
        if type(underlying) is not int or underlying<=0:
            raise ValueError("Malformed source total")
        if source.get("issues"):
            return {"status":"BLOCK_INCOMPLETE_EXPOSURES","vault":vault,
                    "reason":"Reported adapter coverage incomplete","executable":False}
        idle=source.get("idle_assets_units")
        positions=source.get("markets")
        if type(idle) is not int or idle<0 or not isinstance(positions,list):
            raise ValueError("Malformed exposure data")
        seen=set()
        mapped=0
        for p in positions:
            mid=p.get("market_id")
            units=p.get("supply_assets_units")
            if (not isinstance(mid,str) or not mid.startswith("0x") or len(mid)!=66
                or type(units) is not int or units<0 or mid.lower() in seen):
                raise ValueError("Malformed or duplicated market position")
            seen.add(mid.lower())
            mapped+=units
            markets[mid.lower()]+=Decimal(amount)*Decimal(units)/Decimal(underlying)
        if idle+mapped!=underlying:
            return {"status":"BLOCK_UNRECONCILED_EXPOSURES","vault":vault,
                    "executable":False}
        unresolved+=Decimal(amount)*Decimal(underlying-idle-mapped)/Decimal(underlying)
        vault_positions[vault]=amount
    vault_hhi=sum((Decimal(n)/Decimal(total))**2 for n in vault_positions.values())
    market_hhi=sum((n/Decimal(total))**2 for n in markets.values())
    market_bps={k:int((v*10000/Decimal(total))) for k,v in markets.items()}
    vault_bps={k:int(Decimal(v)*10000/Decimal(total)) for k,v in vault_positions.items()}
    blockers=[]
    if any(x>max_market_bps for x in market_bps.values()):
        blockers.append("MARKET_CONCENTRATION")
    if any(x>max_vault_bps for x in vault_bps.values()):
        blockers.append("VAULT_CONCENTRATION")
    if unresolved*10000/total>max_unresolved_bps:
        blockers.append("UNRESOLVED_EXPOSURE")
    return {"status":"REVIEW_LIMIT_BREACH" if blockers else "WITHIN_CONFIGURED_LIMITS",
            "executable":False,"total_units":total,
            "vault_hhi":str(vault_hhi),"market_hhi":str(market_hhi),
            "vault_exposure_bps":vault_bps,
            "underlying_market_exposure_bps":market_bps,
            "limit_breaches":blockers,
            "limits":{"max_market_bps":max_market_bps,
                      "max_vault_bps":max_vault_bps,
                      "max_unresolved_bps":max_unresolved_bps},
            "limitations":"Offline snapshot only; no oracle, liquidity, adapter, bad-debt, correlation or on-chain verification. Cannot authorize a transaction."}

def main(argv=None):
    p=argparse.ArgumentParser(description="Read-only market and vault concentration assessment")
    p.add_argument("--snapshot",required=True,help="Local JSON file: holdings_units and exposures")
    a=p.parse_args(argv)
    obj=json.loads(Path(a.snapshot).read_text(encoding="utf-8"))
    result=assess_portfolio(holdings=obj["holdings_units"],exposures=obj["exposures"])
    print(json.dumps(result,indent=2,sort_keys=True))
    return 0 if result["status"]=="WITHIN_CONFIGURED_LIMITS" else 2

if __name__=="__main__":
    raise SystemExit(main())
