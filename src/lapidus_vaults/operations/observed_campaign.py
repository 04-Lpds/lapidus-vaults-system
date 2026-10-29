"""Convert a verified portfolio observation into bounded unsigned candidates.

The output is a proposed campaign, NEVER authorization. Economic gate and
fresh on-chain execution quotes remain separate requirements.
"""
from __future__ import annotations
from dataclasses import asdict
from ..allocator import BASE_USDC, address_ok
from ..rpc_verify import _address_word, _word
from ..unsigned_candidates import UnsignedCandidate, APPROVE, DEPOSIT, WITHDRAW

def propose_candidates(report: dict, *, wallet: str,
                       approved_vaults: tuple[str, ...],
                       max_action_units: int = 1_000_000) -> tuple[UnsignedCandidate, ...]:
    p=report["plan"]
    if (p.get("chain_id")!=8453 or p.get("wallet")!=wallet.lower()
        or not address_ok(wallet) or not 0<max_action_units<=1_000_000
        or not approved_vaults or len(set(approved_vaults))!=len(approved_vaults)):
        raise ValueError("Invalid snapshot identity or bounded policy")
    changes=p.get("changes")
    if not isinstance(changes,list) or set(c["vault_address"] for c in changes)!=set(approved_vaults):
        raise ValueError("Snapshot must cover exactly approved vaults")
    # Require real native USDC availability for all deposits after planned withdrawals.
    deposits=sum(max(0,c["delta_units"]) for c in changes)
    withdraws=sum(max(0,-c["delta_units"]) for c in changes)
    if (any(type(c["delta_units"]) is not int for c in changes)
        or type(p.get("wallet_usdc_units")) is not int
        or p["wallet_usdc_units"]+withdraws<deposits):
        raise ValueError("Snapshot cash accounting invalid")
    # Run withdrawals first; require fresh preflight before live sends.
    actions=[]
    for c in sorted(changes,key=lambda x: (x["delta_units"]>0,x["vault_address"])):
        delta=c["delta_units"]
        if not delta: continue
        vault=c["vault_address"]
        amount=abs(delta)
        if amount>max_action_units: raise ValueError("Single proposed step exceeds live cap")
        if delta>0:
            # Deliberately require a fresh explicit approval to exact amount.
            actions.append(("approve_intent",vault,amount))
            actions.append(("deposit_intent",vault,amount))
        else:
            actions.append(("withdraw_intent",vault,amount))
    if len(actions)>6: raise ValueError("Too many proposed steps")
    out=[]
    for i,(method,vault,amount) in enumerate(actions,1):
        deps=(i-1,) if i>1 else ()
        if method=="approve_intent":
            to=BASE_USDC; calldata=APPROVE+_address_word(vault)+_word(amount)
        elif method=="deposit_intent":
            to=vault; calldata=DEPOSIT+_word(amount)+_address_word(wallet)
        else:
            to=vault; calldata=WITHDRAW+_word(amount)+_address_word(wallet)+_address_word(wallet)
        out.append(UnsignedCandidate(i,deps,method,8453,wallet.lower(),to,0,calldata,amount))
    return tuple(out)
