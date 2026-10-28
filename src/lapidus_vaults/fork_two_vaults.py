"""Two-vault local Anvil fork transfer rehearsal.

This is a funding-and-movement integration exercise, NOT the production
campaign runner. It only writes to an explicitly loopback Anvil fork.
Both vaults must actually use Base native USDC; mismatched vaults abort
before any local funding or transaction.
"""
from __future__ import annotations
import argparse
import json
import sys

from .fork_roundtrip import (
    ForkRPC, VAULTS, BASE_USDC, ACTOR, ASSET, APPROVE, DEPOSIT,
    WITHDRAW, word, addr_word,
)

def run_two_vault_fork(rpc: ForkRPC, source: str, destination: str,
                       amount: int, *, usdc_holder: str | None = None,
                       actor_wallet: str | None = None) -> dict:
    if (source not in VAULTS or destination not in VAULTS or source==destination
            or type(amount) is not int or not 0<amount<=10_000_000):
        raise ValueError("Two distinct approved vaults and 1..10 USDC required")
    if (actor_wallet is None) == (usdc_holder is None):
        raise ValueError("Specify exactly one of actor_wallet or usdc_holder")
    chosen = actor_wallet if actor_wallet is not None else usdc_holder
    addr_word(chosen)  # Validate address before touching the fork
    actor = actor_wallet if actor_wallet is not None else ACTOR
    if int(rpc.call("eth_chainId",[]),16)!=8453:
        raise RuntimeError("Local Anvil must fork Base chain 8453")
    for vault in (source,destination):
        code=rpc.call("eth_getCode",[vault,"latest"])
        if not isinstance(code,str) or len(code)<10:
            raise RuntimeError("Vault bytecode missing on fork")
        asset=rpc.uint(vault,ASSET)
        if "0x"+format(asset,"040x")!=BASE_USDC:
            raise RuntimeError("Destination/source vault is not native Base USDC; no transactions attempted")
    # No fork mutation until BOTH vaults are validated. Real wallet mode uses
    # its existing forked USDC; no token holder, private key or live signature.
    if actor_wallet is not None:
        if rpc.balance(BASE_USDC, actor) < amount:
            raise RuntimeError("Fork wallet USDC balance is insufficient")
    else:
        if rpc.balance(BASE_USDC, usdc_holder) < amount:
            raise RuntimeError("Funding holder has insufficient forked USDC")
        rpc.call("anvil_setBalance", [actor, hex(10**18)])
        rpc.call("anvil_setBalance", [usdc_holder, hex(10**18)])
        rpc.call("anvil_impersonateAccount", [usdc_holder])
        try:
            rpc.transact(BASE_USDC, usdc_holder,
                         "0xa9059cbb" + addr_word(actor) + word(amount),
                         step="fund fork actor")
        finally:
            rpc.call("anvil_stopImpersonatingAccount", [usdc_holder])

    rpc.call("anvil_setBalance", [actor, hex(10**18)])
    if actor_wallet is not None:
        rpc.call("anvil_impersonateAccount", [actor])
    try:
        initial_cash = rpc.balance(BASE_USDC, actor)
        initial_a = rpc.balance(source, actor)
        initial_b = rpc.balance(destination, actor)
        if initial_cash < amount:
            raise RuntimeError("Fork actor USDC insufficient")
        receipts = []

        def perform(target, calldata, label):
            receipt = rpc.transact(target, actor, calldata, step=label)
            receipts.append({
                "step": label, "tx_hash": receipt.get("transactionHash"),
                "gas_used": int(receipt["gasUsed"], 16),
            })

        perform(BASE_USDC, APPROVE + addr_word(source) + word(amount),
                "approve source")
        perform(source, DEPOSIT + word(amount) + addr_word(actor),
                "deposit source")
        cash_after_a = rpc.balance(BASE_USDC, actor)
        shares_a = rpc.balance(source, actor)
        if cash_after_a != initial_cash - amount or shares_a <= initial_a:
            raise RuntimeError("Source deposit did not reconcile")
        perform(source, WITHDRAW + word(amount) + addr_word(actor) +
                addr_word(actor), "withdraw source")
        if rpc.balance(BASE_USDC, actor) != initial_cash:
            raise RuntimeError("Source withdrawal did not restore expected USDC")
        perform(BASE_USDC, APPROVE + addr_word(destination) + word(amount),
                "approve destination")
        perform(destination, DEPOSIT + word(amount) + addr_word(actor),
                "deposit destination")
        if (rpc.balance(BASE_USDC, actor) != initial_cash - amount or
                rpc.balance(destination, actor) <= initial_b):
            raise RuntimeError("Destination deposit reconciliation failed")
        perform(destination, WITHDRAW + word(amount) + addr_word(actor) +
                addr_word(actor), "withdraw destination")
        end_cash = rpc.balance(BASE_USDC, actor)
        end_a = rpc.balance(source, actor)
        end_b = rpc.balance(destination, actor)
        # ERC-4626 rounding can leave dust shares from deposit -> withdraw.
        # Preserve exact cash reconciliation and report share differences.
        if end_cash != initial_cash or end_a < initial_a or end_b < initial_b:
            raise RuntimeError("Final portfolio reconciliation failed")
        return {
            "status": "LOCAL_FORK_TWO_VAULT_REHEARSAL_PASSED",
            "mode": "existing_wallet" if actor_wallet is not None else "funded_anvil_account",
            "actor": actor,
            "source": source, "destination": destination, "amount_units": amount,
            "cash_initial_units": initial_cash, "cash_final_units": end_cash,
            "source_shares_initial": initial_a, "source_shares_final": end_a,
            "destination_shares_initial": initial_b, "destination_shares_final": end_b,
            "source_residual_shares": end_a - initial_a,
            "destination_residual_shares": end_b - initial_b,
            "receipts": receipts,
            "warning": "Fork contract operations only; not campaign orchestration.",
        }
    finally:
        if actor_wallet is not None:
            rpc.call("anvil_stopImpersonatingAccount", [actor])

def main(argv=None):
    p=argparse.ArgumentParser(description="Base fork two-vault native-USDC rehearsal ONLY")
    p.add_argument("--fork-rpc",default="http://127.0.0.1:8545")
    p.add_argument("--source",required=True,choices=VAULTS)
    p.add_argument("--destination",required=True,choices=VAULTS)
    funding=p.add_mutually_exclusive_group(required=True)
    funding.add_argument("--usdc-holder",help="Fund Anvil default actor from this forked holder")
    funding.add_argument("--actor-wallet",help="Impersonate this wallet ONLY on local Anvil; use its existing forked USDC")
    p.add_argument("--amount-usdc",type=int,default=1,choices=range(1,11))
    args=p.parse_args(argv)
    try:
        with ForkRPC(args.fork_rpc) as rpc:
            result=run_two_vault_fork(rpc,args.source,args.destination,
                                      args.amount_usdc*10**6,usdc_holder=args.usdc_holder,
                                      actor_wallet=args.actor_wallet)
        print(json.dumps(result,indent=2))
        print("LOCAL ANVIL FORK ONLY. NO BASE MAINNET TRANSACTION.")
        return 0
    except (ValueError,RuntimeError) as exc:
        print("FORK REHEARSAL BLOCKED: "+str(exc),file=sys.stderr)
        return 1

if __name__=="__main__":
    raise SystemExit(main())
