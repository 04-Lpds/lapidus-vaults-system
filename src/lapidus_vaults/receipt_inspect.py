"""Read-only inspection of confirmed Base transaction receipts and post-state.

Does not sign, broadcast, claim causality, or imply a full balance reconciliation.
"""
from __future__ import annotations
import argparse
import json
import os
import sys

from .allocator import BASE_CHAIN, BASE_USDC, address_ok
from .rpc_verify import ReadOnlyRPC, RPCError, _address_word, BALANCE_OF, CONVERT_TO_ASSETS, _word
from .wallet_plan import read_policy

ALLOWED_SELECTORS = {"0x095ea7b3": "approve", "0x6e553f65": "deposit",
                     "0xb460af94": "withdraw", "0xba087652": "redeem"}
EXPECTED_LENGTH = {"approve": 138, "deposit": 138, "withdraw": 202, "redeem": 202}


def _hex_int(value, field):
    if not isinstance(value, str) or not value.startswith("0x"):
        raise ValueError(f"Missing {field}")
    try:
        return int(value, 16)
    except ValueError as exc:
        raise ValueError(f"Invalid {field}") from exc


def inspect_transaction(rpc: ReadOnlyRPC, tx_hash: str, wallet: str,
                        allowed_vaults: tuple[str, ...], *, min_confirmations: int = 2) -> dict:
    """Validate observable facts; cannot prove a hypothetical plan was followed."""
    if not isinstance(tx_hash, str) or len(tx_hash) != 66 or not tx_hash.startswith("0x"):
        raise ValueError("A 32-byte transaction hash is required")
    try:
        bytes.fromhex(tx_hash[2:])
    except ValueError as exc:
        raise ValueError("Malformed transaction hash") from exc
    if not address_ok(wallet) or not allowed_vaults or not all(address_ok(x) for x in allowed_vaults):
        raise ValueError("Wallet and explicit vault allowlist required")
    if type(min_confirmations) is not int or not 1 <= min_confirmations <= 10000:
        raise ValueError("Invalid minimum confirmations")
    if rpc.chain_id() != BASE_CHAIN:
        raise RPCError("Expected Base chain ID 8453")
    receipt = rpc.request("eth_getTransactionReceipt", [tx_hash])
    tx = rpc.request("eth_getTransactionByHash", [tx_hash])
    if receipt is None or tx is None:
        return {"status": "PENDING_OR_NOT_FOUND", "tx_hash": tx_hash, "verified": False}
    if str(tx.get("hash", "")).lower() != tx_hash.lower() or str(receipt.get("transactionHash", "")).lower() != tx_hash.lower():
        raise RPCError("RPC transaction hash mismatch")
    if str(tx.get("from", "")).lower() != wallet.lower():
        raise ValueError("Transaction signer is not the selected wallet")
    to = str(tx.get("to") or "").lower()
    if to not in ({v.lower() for v in allowed_vaults} | {BASE_USDC}):
        raise ValueError("Transaction destination not an explicitly selected vault or USDC")
    data = tx.get("input")
    if not isinstance(data, str) or len(data) < 10:
        raise ValueError("Missing transaction calldata")
    method = ALLOWED_SELECTORS.get(data[:10].lower())
    if method is None or len(data) != EXPECTED_LENGTH[method]:
        raise ValueError("Unrecognized contract method or calldata shape")
    if (method == "approve") != (to == BASE_USDC):
        raise ValueError("Selector and destination mismatch")
    if _hex_int(tx.get("value"), "ETH value") != 0:
        raise ValueError("Unexpected payable ETH")
    if method == "approve":
        spender = "0x" + data[34:74].lower()
        if spender not in {v.lower() for v in allowed_vaults}:
            raise ValueError("Approval spender is not an allowlisted vault")
    if method in {"deposit", "withdraw", "redeem"}:
        receiver = "0x" + data[34+64:74+64].lower()
        if receiver != wallet.lower():
            raise ValueError("Contract receiver is not the selected wallet")
    if method in {"withdraw", "redeem"}:
        owner = "0x" + data[34+128:74+128].lower()
        if owner != wallet.lower():
            raise ValueError("Contract owner is not the selected wallet")
    block = _hex_int(receipt.get("blockNumber"), "receipt block")
    latest = rpc.block_number()
    confirmations = max(0, latest - block + 1)
    success = _hex_int(receipt.get("status"), "receipt status") == 1
    if confirmations < min_confirmations:
        return {"status": "AWAITING_CONFIRMATIONS", "tx_hash": tx_hash, "confirmations": confirmations, "verified": False}
    # A confirmed receipt is not sufficient proof of expected cash/share deltas.
    # These are observable post-receipt balances, not per-transaction deltas.
    balances = {"wallet_usdc_units": rpc.call_uint(BASE_USDC, BALANCE_OF, block, _address_word(wallet)),
                "vault_shares": {}, "vault_estimated_assets_units": {}}
    for vault in allowed_vaults:
        shares = rpc.call_uint(vault, BALANCE_OF, block, _address_word(wallet))
        balances["vault_shares"][vault] = shares
        balances["vault_estimated_assets_units"][vault] = rpc.call_uint(vault, CONVERT_TO_ASSETS, block, _word(shares))
    return {
        "status": "RECEIPT_SUCCESS_POST_STATE_OBSERVED" if success else "RECEIPT_REVERTED",
        "verified": success,
        "tx_hash": tx_hash.lower(), "method": method, "wallet": wallet.lower(),
        "destination": to, "receipt_block": block, "latest_block": latest,
        "confirmations": confirmations, "gas_used": _hex_int(receipt.get("gasUsed"), "gas used"),
        "post_receipt_block_balances": balances,
        "limitations": ["No proof of pre/post transaction balance deltas",
                        "No matching against a stored LVS plan or user approval",
                        "No independent event log or adapter liquidity verification"],
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Inspect Base transaction receipt and post-state (read-only)")
    p.add_argument("--tx-hash", required=True)
    p.add_argument("--wallet", required=True)
    p.add_argument("--allowlist", required=True)
    p.add_argument("--min-confirmations", type=int, default=2)
    args = p.parse_args(argv)
    if not os.environ.get("BASE_RPC_URL"):
        print("BASE_RPC_URL required", file=sys.stderr)
        return 1
    try:
        vaults = read_policy(args.allowlist).vault_addresses
        with ReadOnlyRPC(os.environ["BASE_RPC_URL"]) as rpc:
            out = inspect_transaction(rpc, args.tx_hash, args.wallet, vaults,
                                      min_confirmations=args.min_confirmations)
        print(json.dumps(out, indent=2))
        print("READ ONLY. NO SIGNING OR BROADCAST.")
        return 0 if out.get("verified") else 2
    except (ValueError, RPCError, OSError) as exc:
        print(f"RECEIPT INSPECTION FAILED: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
