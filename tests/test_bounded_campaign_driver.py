"""Bounded campaign runner must never enable unattended mainnet signing."""
from decimal import Decimal
from unittest.mock import patch
from lapidus_vaults.operations.auto_campaign import run_bounded_campaign
from lapidus_vaults.operations.campaigns import create_campaign, record_receipt_status
from lapidus_vaults.operations.observed_campaign import propose_candidates
from lapidus_vaults.wallet_plan import Policy
W="0x"+"1"*40
V="0x"+"2"*40
P=Policy((V,),Decimal("0.2"),Decimal("0.4"))
def setup(tmp_path):
    db=str(tmp_path/"sandbox.sqlite3")
    report={"plan":{"chain_id":8453,"wallet":W,"observed_block":2,
       "wallet_usdc_units":800000,"capital_units":800000,
       "changes":[{"vault_address":V,"delta_units":800000}]}}
    actions=propose_candidates(report,wallet=W,approved_vaults=(V,))
    create_campaign(db,"case1",actions)
    return db
EC={"decision":"REBALANCE_REVIEW_ONLY","submitted":False,"capital_units":800000,
    "estimated_fee_usd":"0.001","projected_historical_rate_yield_delta_usd":"0.10",
    "eth_usd_assumption":"3000"}

def driver(db,rpc,executor):
    with patch("lapidus_vaults.operations.auto_campaign.read_policy",return_value=P):
        return run_bounded_campaign(db_path=db,campaign_id="case1",wallet=W,
            protected_wallet="0x"+"3"*40,allowlist_path="unused",
            rpc_url=rpc,private_key="fork-only-unused",
            admitted_economics=EC,execute_fn=executor,sleep_fn=lambda _:None)

def test_general_driver_refuses_mainnet(tmp_path):
    db=setup(tmp_path)
    def forbidden(**kwargs):
        raise AssertionError("Signer invoked on mainnet")
    result=driver(db,"https://mainnet.base.org",forbidden)
    assert result["status"]=="LIVE_EXECUTION_NOT_AUTHORIZED"

def test_general_driver_advances_local_receipts(tmp_path):
    db=setup(tmp_path)
    count=[0]
    def fake_executor(**kwargs):
        count[0]+=1
        record_receipt_status(db,"case1",count[0],
            "RECEIPT_AND_BLOCK_MATCH","0x"+format(count[0],"064x"))
        return {"status":"RECEIPT_AND_BLOCK_MATCH"}
    result=driver(db,"http://127.0.0.1:8545",fake_executor)
    assert result["status"]=="CAMPAIGN_COMPLETED"
    assert count[0]==2

def test_general_driver_rejects_uneconomic(tmp_path):
    db=setup(tmp_path)
    with patch("lapidus_vaults.operations.auto_campaign.read_policy",return_value=P):
        result=run_bounded_campaign(db_path=db,campaign_id="case1",wallet=W,
            protected_wallet="0x"+"3"*40,allowlist_path="unused",
            rpc_url="http://127.0.0.1:8545",private_key="unused",
            admitted_economics={**EC,"decision":"HOLD_ESTIMATED_FEES_EXCEED_BENEFIT"},
            execute_fn=lambda **kw: (_ for _ in ()).throw(AssertionError("called")))
    assert result["status"]=="ADMISSION_BLOCKED"
