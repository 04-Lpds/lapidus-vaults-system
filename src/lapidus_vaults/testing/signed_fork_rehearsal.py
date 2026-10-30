"""EIP-1559 signing rehearsal using a fresh disposable key on LOCAL Anvil only.

Tests real locally signed approval + ERC-4626 deposit, through production
quote/consume and independent receipt checks. NEVER sends on Base mainnet.
"""
from __future__ import annotations
import argparse
import json
import sys
import time

from eth_account import Account
from ..fork_roundtrip import ForkRPC, VAULTS, BASE_USDC, ASSET, addr_word, word
from ..fork_campaign_rehearsal import LocalForkReadAdapter
from ..live_deposit import DepositQuotes
from ..operations.dedicated_signer import prepare_signed_transaction
from ..operations.signer_policy import SignerPolicy

def _send(rpc: ForkRPC, raw: str, hash_expected: str) -> str:
    tx_hash = rpc.call("eth_sendRawTransaction", [raw])
    if tx_hash.lower() != hash_expected.lower():
        raise RuntimeError("Fork node returned unexpected signed transaction hash")
    # Receipt verification is separate and never triggers a second send.
    for _ in range(40):
        receipt=rpc.call("eth_getTransactionReceipt", [tx_hash])
        if receipt is not None:
            if int(receipt.get("status","0x0"),16)!=1:
                raise RuntimeError("Signed fork transaction reverted")
            return tx_hash
        time.sleep(0.5)
    raise RuntimeError("Signed transaction pending; do not resend")

def run_signed_fork(rpc: ForkRPC, holder: str, vault: str) -> dict:
    addr_word(holder)
    if vault not in VAULTS or int(rpc.call("eth_chainId", []),16) != 8453:
        raise ValueError("Only allowlisted vaults on local Base fork")
    code=rpc.call("eth_getCode",[vault,"latest"])
    if not isinstance(code,str) or len(code)<10 or (
        "0x"+format(rpc.uint(vault,ASSET),"040x") != BASE_USDC
    ):
        raise RuntimeError("Not a native Base USDC vault")
    amount=1_000_000
    if rpc.balance(BASE_USDC,holder)<amount:
        raise ValueError("Fork-only funding holder has insufficient USDC")
    actor=Account.create()
    wallet=actor.address.lower()
    key="0x"+bytes(actor.key).hex()
    rpc.call("anvil_setBalance",[wallet,hex(10**16)])
    rpc.call("anvil_setBalance",[holder,hex(10**16)])
    rpc.call("anvil_impersonateAccount",[holder])
    try:
        rpc.transact(BASE_USDC,holder,
                     "0xa9059cbb"+addr_word(wallet)+word(amount),
                     step="fork-only funding transfer")
    finally:
        rpc.call("anvil_stopImpersonatingAccount",[holder])
    if rpc.balance(BASE_USDC,wallet)!=amount:
        raise RuntimeError("Disposable signer funding mismatch")

    read=LocalForkReadAdapter(rpc)
    engine=DepositQuotes()
    verified=[]
    for sequence,expected_stage in [(1,"approve"),(2,"deposit")]:
        quote=engine.quote(read,wallet=wallet,vault=vault,
                           approved_vaults=(vault,),amount_units=amount)
        if quote["stage"]!=expected_stage:
            raise RuntimeError("Expected exact approve -> deposit progression")
        consumed=engine.consume(read,quote["quote_id"])
        consumed["authorized_at_unix"] = time.time()
        candidate={"method":expected_stage+"_intent","chain_id":8453,
                   "from_address":wallet,"to_address":consumed["to"],
                   "data":consumed["data"],"requested_assets_units":amount}
        nonce=int(rpc.call("eth_getTransactionCount",[wallet,"pending"]),16)
        signed=prepare_signed_transaction(
            consumed=consumed,candidate=candidate,policy_wallet=wallet,
            protected_wallet=holder,private_key=key,nonce=nonce,
            gas_price_wei=read.gas_price_wei(),approved_vaults=(vault,),
            # Anvil may inherit Base fork gas pricing much higher than the
            # production live cap. This override is LOCAL TEST ONLY.
            rpc=read, policy=SignerPolicy(
                wallet, holder.lower(), (8453,), (vault,), (BASE_USDC,),
                ("approve", "deposit"), amount, 5_000_000_000_000_000,
                2_000_000_000_000_000)
        )
        tx_hash=_send(rpc,signed["raw"],signed["tx_hash"])
        result=engine.check_receipt(read,quote["quote_id"],tx_hash)
        if result["status"]!="RECEIPT_AND_BLOCK_MATCH":
            raise RuntimeError("Production receipt reconciliation failed for signed fork tx")
        verified.append({"sequence":sequence,"stage":expected_stage,
                         "tx_hash":tx_hash,"receipt":result["status"]})
    if rpc.balance(BASE_USDC,wallet)!=0 or rpc.balance(vault,wallet)<=0:
        raise RuntimeError("Final signed depositor balances did not reconcile")
    return {"status":"SIGNED_ANVIL_FORK_ROUNDTRIP_PASSED",
            "actor":wallet,"vault":vault,
            "verified_transactions":verified,
            "local_only":True,"mainnet_signing_tested":False}

def main(argv=None) -> int:
    p=argparse.ArgumentParser(description="Sign two actual transactions on local Anvil only")
    p.add_argument("--fork-rpc",default="http://127.0.0.1:8545")
    p.add_argument("--usdc-holder",required=True)
    p.add_argument("--vault",required=True,choices=VAULTS)
    args=p.parse_args(argv)
    try:
        with ForkRPC(args.fork_rpc) as rpc:
            result=run_signed_fork(rpc,args.usdc_holder,args.vault)
        print(json.dumps(result,indent=2))
        print("LOCAL ANVIL ONLY. No mainnet transaction broadcast.")
        return 0
    except Exception as exc:
        print("SIGNED FORK TEST FAILED: "+type(exc).__name__+": "+str(exc),
              file=sys.stderr)
        return 1

if __name__=="__main__":
    raise SystemExit(main())
