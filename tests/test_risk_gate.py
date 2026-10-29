import pytest
from lapidus_vaults.operations.risk_gate import assess_portfolio

A="0x"+"1"*40
B="0x"+"2"*40
M="0x"+"a"*64
N="0x"+"b"*64

def snap(vault, market, units=1000000, idle=0):
    return {"vault_address":vault,"total_assets_units":units,
            "idle_assets_units":idle,
            "issues":[],"markets":[{"market_id":market,"supply_assets_units":units-idle}]}

def test_same_underlying_market_across_two_vaults_is_aggregated():
    out=assess_portfolio(holdings={A:400000,B:400000},
        exposures={A:snap(A,M),B:snap(B,M)},max_market_bps=6000,max_vault_bps=6000)
    assert out["underlying_market_exposure_bps"][M]==10000
    assert out["status"]=="REVIEW_LIMIT_BREACH"
    assert out["market_hhi"]=="1"
    assert out["executable"] is False

def test_distinct_markets_and_vaults_within_50_percent_limits():
    out=assess_portfolio(holdings={A:1000000,B:1000000},
        exposures={A:snap(A,M),B:snap(B,N)},max_market_bps=5000,max_vault_bps=5000)
    assert out["status"]=="WITHIN_CONFIGURED_LIMITS"
    assert out["market_hhi"]=="0.50"

def test_incomplete_lookthrough_fails_closed():
    bad=snap(A,M)
    bad["issues"]=["Nested vault adapter unresolved"]
    out=assess_portfolio(holdings={A:1000000},exposures={A:bad})
    assert out["status"]=="BLOCK_INCOMPLETE_EXPOSURES"

def test_unreconciled_assets_fails_closed():
    bad=snap(A,M)
    bad["markets"][0]["supply_assets_units"]-=1
    out=assess_portfolio(holdings={A:1000000},exposures={A:bad})
    assert out["status"]=="BLOCK_UNRECONCILED_EXPOSURES"

def test_invalid_units_rejected():
    with pytest.raises(ValueError):
        assess_portfolio(holdings={A:0.1},exposures={A:snap(A,M)})
