"""Transaction-scoped ERC-20 Transfer log reconciliation; no block-level inference."""
from __future__ import annotations
from .allocator import BASE_USDC, address_ok

TRANSFER_TOPIC="0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
APPROVAL_TOPIC="0x8c5be1e5ebec7d5bd14f71427d1e84f3dd0314c0f7b2291e5b200ac8c7c3b925"
ZERO="0x"+"0"*40

def _topic_addr(topic):
    if not isinstance(topic,str) or len(topic)!=66 or not topic.startswith("0x"):
        raise ValueError("Malformed transfer address topic")
    raw=topic[2:].lower()
    if raw[:24]!="0"*24:
        raise ValueError("Transfer topic not address-padded")
    return "0x"+raw[24:]

def _transfers(receipt):
    results=[]
    for entry in receipt.get("logs",[]):
        topics=entry.get("topics",[])
        if not topics or str(topics[0]).lower()!=TRANSFER_TOPIC:
            continue
        if len(topics)!=3 or not address_ok(entry.get("address","")):
            raise ValueError("Malformed ERC20 Transfer log")
        data=entry.get("data")
        if not isinstance(data,str) or len(data)!=66:
            raise ValueError("Malformed Transfer amount")
        results.append((entry["address"].lower(),_topic_addr(topics[1]),
                        _topic_addr(topics[2]),int(data,16)))
    return results

def reconcile_transfer_logs(receipt,wallet,vault,amount_units,stage):
    """Logs belong to this receipt, so no unrelated same-block transfers counted.

    Still not full independent accounting proof: tokens could emit misleading events.
    """
    if not address_ok(wallet) or not address_ok(vault) or stage not in {"approve","deposit","withdraw"}:
        raise ValueError("Invalid scoped reconciliation")
    if type(amount_units) is not int or amount_units<=0:
        raise ValueError("Invalid amount")
    wallet,vault=wallet.lower(),vault.lower()
    transfers=_transfers(receipt)
    if stage=="deposit":
        paid=sum(n for token,src,dst,n in transfers if token==BASE_USDC and src==wallet and dst==vault)
        minted=sum(n for token,src,dst,n in transfers if token==vault and src==ZERO and dst==wallet)
        match=paid==amount_units and minted>0
        data={"usdc_to_vault_units":paid,"minted_shares":minted}
    elif stage=="withdraw":
        received=sum(n for token,src,dst,n in transfers if token==BASE_USDC and dst==wallet)
        burned=sum(n for token,src,dst,n in transfers if token==vault and src==wallet and dst==ZERO)
        match=received==amount_units and burned>0
        data={"usdc_to_wallet_units":received,"burned_shares":burned}
    else:
        approvals = []
        for entry in receipt.get("logs", []):
            topics = entry.get("topics", [])
            if (entry.get("address", "").lower() != BASE_USDC or not topics
                    or str(topics[0]).lower() != APPROVAL_TOPIC):
                continue
            raw = entry.get("data")
            if (len(topics) != 3 or not isinstance(raw, str)
                    or not raw.startswith("0x") or len(raw) != 66):
                raise ValueError("Malformed ERC20 Approval log")
            owner, spender = _topic_addr(topics[1]), _topic_addr(topics[2])
            if owner == wallet:
                approvals.append((owner, spender, int(raw, 16)))
        wallet_movements = any(token in {BASE_USDC, vault} and wallet in {src, dst}
                               for token, src, dst, _ in transfers)
        match = approvals == [(wallet, vault, amount_units)] and not wallet_movements
        data = {"exact_approval_event": match}
    return {"status":"RECEIPT_LOGS_MATCH" if match else "RECEIPT_LOGS_MISMATCH",
            "transaction_scoped":True,"matched":match,**data,
            "financial_reconciliation_complete":False}
