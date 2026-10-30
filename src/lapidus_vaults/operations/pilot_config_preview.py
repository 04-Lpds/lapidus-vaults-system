"""Validated proposed pilot settings. Preview-only, cannot authorize live funds."""
from __future__ import annotations
import argparse
from decimal import Decimal
import json
from pathlib import Path

def validate(raw:dict)->dict:
    if not isinstance(raw,dict) or set(raw)!={"managed_capital_usdc","max_action_usdc","cycle_seconds","idle_deployment_horizon_days","minimum_idle_deposit_usdc","rebalance_drift_bps"}:
        raise ValueError("Unknown or missing pilot setting")
    cap=Decimal(str(raw["managed_capital_usdc"]))
    action=Decimal(str(raw["max_action_usdc"]))
    minimum=Decimal(str(raw["minimum_idle_deposit_usdc"]))
    horizon=raw["idle_deployment_horizon_days"]
    interval=raw["cycle_seconds"]
    drift=raw["rebalance_drift_bps"]
    if not all(x.is_finite() and x.as_tuple().exponent>=-6 for x in (cap,action,minimum)):
        raise ValueError("Amounts must be finite USDC with at most six decimal places")
    if not (0<minimum<=action<=cap<=Decimal("20")):
        raise ValueError("Pilot proposed capital must be bounded at 20 USDC")
    if any(type(x) is not int for x in (horizon,interval,drift)):
        raise ValueError("Integer schedule and threshold required")
    if not (30<=horizon<=365 and 60<=interval<=86400 and 100<=drift<=10000):
        raise ValueError("Pilot schedule or drift limit invalid")
    return {"status":"VALID_PREVIEW_NOT_ACTIVE","live_signing_enabled":False,
        "settings":raw,"limitations":"Current live-pilot remains capped at 5 USDC and 1 USDC per action. This proposal does not change its financial authority or automatically deploy idle funds."}

def main(argv=None):
    p=argparse.ArgumentParser(description="Validate proposed $20 settings without changing live pilot")
    p.add_argument("--config",required=True)
    a=p.parse_args(argv)
    try:
        result=validate(json.loads(Path(a.config).read_text(encoding="utf8")))
        print(json.dumps(result,indent=2))
        return 0
    except (ValueError,KeyError,TypeError,OSError):
        print(json.dumps({"status":"INVALID_PREVIEW_CONFIGURATION","live_signing_enabled":False}))
        return 2

if __name__=="__main__":
    raise SystemExit(main())
