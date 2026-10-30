"""Signed fork harness cannot be redirected to a remote provider."""
from lapidus_vaults.testing.signed_fork_rehearsal import main
from lapidus_vaults.fork_roundtrip import VAULTS

def test_signed_rehearsal_requires_loopback_http(capsys):
    result=main(["--fork-rpc","https://mainnet.base.org",
                 "--usdc-holder","0x"+"1"*40,"--vault",VAULTS[0]])
    assert result==1
    assert "SIGNED FORK TEST FAILED" in capsys.readouterr().err
