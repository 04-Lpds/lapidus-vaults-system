"""Fork exercise guard tests: no mainnet calls and preflight before mutations."""
import pytest
from lapidus_vaults.fork_two_vaults import run_two_vault_fork
from lapidus_vaults.fork_roundtrip import VAULTS, BASE_USDC

HOLDER="0x"+"a"*40

class Fake:
    def __init__(self,asset):
        self.asset=asset
        self.calls=[]
    def call(self,method,params):
        self.calls.append(method)
        if method=="eth_chainId": return hex(8453)
        if method=="eth_getCode": return "0x6001600155"
        raise AssertionError("Unexpected mutation or read: "+method)
    def uint(self,vault,selector):
        return int(self.asset,16)

def test_mismatched_assets_block_before_any_transaction():
    rpc=Fake("0x"+"f"*40)
    with pytest.raises(RuntimeError,match="not native Base USDC"):
        run_two_vault_fork(rpc,VAULTS[0],VAULTS[1],1_000_000,usdc_holder=HOLDER)
    assert "anvil_setBalance" not in rpc.calls
    assert "eth_sendTransaction" not in rpc.calls

def test_identical_vaults_rejected_without_rpc():
    rpc=Fake(BASE_USDC)
    with pytest.raises(ValueError,match="distinct"):
        run_two_vault_fork(rpc,VAULTS[0],VAULTS[0],1_000_000,usdc_holder=HOLDER)
    assert not rpc.calls

def test_amount_cap_enforced_before_rpc():
    rpc=Fake(BASE_USDC)
    with pytest.raises(ValueError):
        run_two_vault_fork(rpc,VAULTS[0],VAULTS[1],10_000_001,usdc_holder=HOLDER)
    assert not rpc.calls
