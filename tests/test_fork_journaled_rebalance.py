"""Offline persisted fork executor safeguards."""
import pytest
from unittest.mock import Mock
from dataclasses import asdict
from decimal import Decimal
from lapidus_vaults.testing.fork_journaled_rebalance import execute_saved_manifest, save_fork_result
from lapidus_vaults.transaction_review import prepare_manifest, store_manifest
from lapidus_vaults.manifest_preflight import load_manifest
from lapidus_vaults.wallet_plan import Policy, WalletSnapshot, VaultHolding, build_rebalance
from lapidus_vaults.safety_plan import build_safety_plan
from lapidus_vaults.fork_roundtrip import ACTOR, VAULTS


def make_manifest():
    policy=Policy(VAULTS, Decimal("0.20"), Decimal("0.40"))
    snapshot=WalletSnapshot(ACTOR,8453,100,0,
        (VaultHolding(VAULTS[0],1,10_000_000,0,0,0),
         VaultHolding(VAULTS[1],0,0,0,0,0)),"test","demo")
    plan=build_safety_plan(build_rebalance(snapshot,policy),
        allowances={v:0 for v in VAULTS}, max_gas_units=600000,
        gas_price_wei=1000000,available_eth_wei=10**18)
    return prepare_manifest({"plan":asdict(plan)}, VAULTS, cap_usdc=10)


def test_saved_manifest_reloads_and_rejects_stale(tmp_path):
    db=str(tmp_path/"fork.db")
    m=make_manifest()
    row=store_manifest(db,m)
    saved=load_manifest(db,row)
    assert saved["manifest_sha256"]==m["manifest_sha256"]
    rpc=Mock()
    with pytest.raises(RuntimeError,match="stale"):
        execute_saved_manifest(rpc,saved,current_block=121)
    rpc.transact.assert_not_called()


def test_success_result_can_be_recorded(tmp_path):
    db=str(tmp_path/"fork.db")
    assert save_fork_result(db,manifest_id=1,result={"status":"FORK_ONLY"})==1
