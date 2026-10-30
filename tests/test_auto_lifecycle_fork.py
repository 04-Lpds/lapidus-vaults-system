"""The autonomous full-lifecycle harness must never contact live Base RPC."""
from lapidus_vaults.testing.auto_lifecycle_fork import main
from lapidus_vaults.fork_roundtrip import VAULTS

def test_auto_lifecycle_harness_refuses_remote_provider(capsys):
    code=main([
        "--fork-rpc","https://mainnet.base.org",
        "--usdc-holder","0x"+"1"*40,
        "--vault",VAULTS[0],
    ])
    assert code==1
    assert "AUTO LIFECYCLE FORK FAILED" in capsys.readouterr().out

def test_negative_scenarios_reject_before_funding():
    from unittest.mock import Mock
    from lapidus_vaults.testing.auto_lifecycle_fork import run_auto_lifecycle
    from lapidus_vaults.fork_roundtrip import BASE_USDC
    for scenario in ("uneconomic", "tampered"):
        rpc=Mock()
        rpc.call.side_effect=lambda method, _: ("0x2105" if method=="eth_chainId" else "0x60006000")
        rpc.uint.return_value=int(BASE_USDC,16)
        result=run_auto_lifecycle(rpc,"0x"+"3"*40,VAULTS[0],scenario=scenario)
        assert result["status"]=="FORK_SCENARIO_BLOCKED_NO_CAMPAIGN_TRANSACTIONS"
        assert result["signed_transactions"]==[]
        assert not result["campaign_created"]
        assert result["admission"]["status"]=="ADMISSION_BLOCKED"
        assert not any(c.args[0].startswith("anvil_") for c in rpc.call.call_args_list)
