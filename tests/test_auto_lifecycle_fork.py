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
