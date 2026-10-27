"""Offline matching tests: no public RPC or transaction signing."""
from unittest.mock import Mock, patch
from copy import deepcopy
from lapidus_vaults.transaction_match import match_candidate
from lapidus_vaults.transaction_review import prepare_manifest
from test_transaction_review import synthetic_report
from lapidus_vaults.fork_roundtrip import VAULTS

HASH="0x"+"a1"*32

def test_exact_match_is_not_authorization():
    m=prepare_manifest(synthetic_report(),VAULTS,cap_usdc=10)
    c=m["candidates"][0]
    rpc=Mock()
    rpc.request.return_value={"from":c["from_address"],"to":c["to_address"],
        "input":c["data"],"value":"0x0","chainId":"0x2105"}
    with patch("lapidus_vaults.transaction_match.inspect_transaction",return_value={
        "verified":True,"receipt_block":m["observed_block"]+1,"confirmations":3}):
        result=match_candidate(rpc,m,1,HASH,VAULTS)
    assert result["status"]=="EXACT_ONCHAIN_CANDIDATE_MATCH"
    assert result["matched"]
    assert not result["authorization_proven"]
    assert not result["financial_reconciliation_complete"]


def test_mutated_calldata_must_not_match():
    m=prepare_manifest(synthetic_report(),VAULTS,cap_usdc=10)
    c=m["candidates"][0]
    rpc=Mock()
    rpc.request.return_value={"from":c["from_address"],"to":c["to_address"],
        "input":c["data"][:-2]+"ff","value":"0x0","chainId":"0x2105"}
    with patch("lapidus_vaults.transaction_match.inspect_transaction",return_value={
        "verified":True,"receipt_block":m["observed_block"]+1,"confirmations":3}):
        result=match_candidate(rpc,m,1,HASH,VAULTS)
    assert result["status"]=="TRANSACTION_MANIFEST_MISMATCH"
    assert not result["matched"]
