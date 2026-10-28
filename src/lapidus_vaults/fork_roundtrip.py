"""Local Anvil fork-only integration test: approval, deposit, withdrawal, reconciliation.

Requires locally running Anvil with explicit fork URL. Never accepts remote hosts.
No secrets, signing, or transactions sent to Base mainnet.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from urllib.parse import urlparse
import httpx

BASE_USDC = "0x833589fcd6edb6e08f4c7c32d4f71b54bd a02913".replace(" ", "")
VAULTS = (
    "0x050ce30b927da55177a4914ec73480238bad56f0",
    "0x1e9e47583f15d45a10df48c0b1846e0492c795d7",
)
# Anvil default prefunded account; only used on localhost fork.
ACTOR = "0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266"
APPROVE = "0x095ea7b3"
BALANCE_OF = "0x70a08231"
DEPOSIT = "0x6e553f65"
WITHDRAW = "0xb460af94"
ASSET = "0x38d52e0f"


def word(n: int) -> str:
    if type(n) is not int or not 0 <= n < 2**256:
        raise ValueError("Invalid uint256")
    return f"{n:064x}"


def addr_word(a: str) -> str:
    if not isinstance(a, str) or len(a) != 42 or not a.startswith("0x"):
        raise ValueError("Invalid address")
    int(a[2:], 16)
    return "0" * 24 + a[2:].lower()


def validate_local_endpoint(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment or parsed.port is None:
        raise ValueError("FORK_RPC_URL must be a plain local Anvil HTTP endpoint with explicit port")


class ForkRPC:
    def __init__(self, url: str):
        validate_local_endpoint(url)
        self.client = httpx.Client(timeout=20, trust_env=False)
        self.url = url
        self.seq = 0

    def __enter__(self): return self
    def __exit__(self, *args): self.client.close()

    def call(self, method: str, params: list):
        allowed = {"eth_chainId", "eth_blockNumber", "eth_getCode", "eth_call", "eth_getBalance", "eth_getTransactionReceipt", "eth_getTransactionByHash", "eth_estimateGas", "eth_gasPrice", "eth_sendTransaction", "anvil_setBalance", "anvil_setStorageAt", "anvil_impersonateAccount", "anvil_stopImpersonatingAccount"}
        if method not in allowed:
            raise ValueError("Unsupported local-fork RPC method")
        self.seq += 1
        res = self.client.post(self.url, json={"jsonrpc": "2.0", "id": self.seq, "method": method, "params": params})
        res.raise_for_status()
        data = res.json()
        if data.get("id") != self.seq or data.get("error") is not None or "result" not in data:
            raise RuntimeError(f"Local fork method {method} failed")
        return data["result"]

    def uint(self, address: str, data: str) -> int:
        return int(self.call("eth_call", [{"to": address, "data": data}, "latest"]), 16)

    def balance(self, token: str, holder: str) -> int:
        return self.uint(token, BALANCE_OF + addr_word(holder))

    def transact(self, contract: str, sender: str, data: str, *, step: str) -> dict:
        txid = self.call("eth_sendTransaction", [{"from": sender, "to": contract, "data": data, "gas": hex(6_000_000)}])
        receipt = None
        # Forked nodes can take time to fetch upstream storage and mine a tx.
        # Never resubmit the transaction while waiting for the original receipt.
        for attempt in range(40):
            receipt = self.call("eth_getTransactionReceipt", [txid])
            if receipt is not None:
                break
            if attempt < 39:
                time.sleep(0.5)
        if not receipt:
            raise RuntimeError(
                f"{step}: receipt not mined after 20s on local Anvil; "
                f"tx={txid}. Check Anvil auto-mining, pending tx, and upstream RPC logs."
            )
        if int(receipt.get("status", "0x0"), 16) != 1:
            gas = receipt.get("gasUsed", "unknown")
            try:
                self.call("eth_call", [{"from": sender, "to": contract, "data": data}, "latest"])
                reason = "historical revert reason unavailable (post-state eth_call succeeded)"
            except RuntimeError:
                reason = "post-state eth_call reverted (RPC did not expose revert details)"
            raise RuntimeError(f"{step}: local tx reverted; hash={txid}, gasUsed={gas}; {reason}")
        return receipt


def run_fork_test(rpc: ForkRPC, vault: str, amount: int, *, usdc_holder: str):
    if vault.lower() not in VAULTS or amount <= 0 or amount > 10_000_000:
        raise ValueError("Allowlisted vault and amount of up to 10 USDC required")
    if int(rpc.call("eth_chainId", []), 16) != 8453:
        raise RuntimeError("Local fork must have Base chain ID 8453")
    # Guard against accidental empty non-forked node or incorrect contract.
    code = rpc.call("eth_getCode", [vault, "latest"])
    if not isinstance(code, str) or len(code) < 10:
        raise RuntimeError("Expected actual Vault V2 bytecode on fork")
    asset = rpc.uint(vault, ASSET)
    if f"0x{asset:040x}" != BASE_USDC:
        raise RuntimeError("Vault asset is not native Base USDC")
    if not isinstance(usdc_holder, str) or len(usdc_holder) != 42 or not usdc_holder.startswith("0x"):
        raise ValueError("Invalid USDC holder address")
    # Fund actor's gas ONLY in local fork. Copy 10 USDC via an impersonated
    # on-chain holder (no storage-slot assumptions).
    rpc.call("anvil_setBalance", [ACTOR, hex(10**18)])
    rpc.call("anvil_setBalance", [usdc_holder, hex(10**18)])
    rpc.call("anvil_impersonateAccount", [usdc_holder])
    try:
        rpc.transact(BASE_USDC, usdc_holder, "0xa9059cbb" + addr_word(ACTOR) + word(amount), step="fund test account via USDC transfer")
    finally:
        rpc.call("anvil_stopImpersonatingAccount", [usdc_holder])
    before_usdc = rpc.balance(BASE_USDC, ACTOR)
    before_shares = rpc.balance(vault, ACTOR)
    if before_usdc < amount:
        raise RuntimeError("Fork funding transfer did not succeed")
    approval = rpc.transact(BASE_USDC, ACTOR, APPROVE + addr_word(vault) + word(amount), step="approve vault USDC")
    deposit = rpc.transact(vault, ACTOR, DEPOSIT + word(amount) + addr_word(ACTOR), step="deposit USDC into vault")
    after_deposit_shares = rpc.balance(vault, ACTOR)
    minted = after_deposit_shares - before_shares
    if minted <= 0:
        raise RuntimeError("Deposit minted no shares")
    after_deposit_usdc = rpc.balance(BASE_USDC, ACTOR)
    if before_usdc - after_deposit_usdc != amount:
        raise RuntimeError("Deposit USDC balance reconciliation failed")
    # Withdraw at most original deposit; fees/rounding may restrict withdrawable assets.
    withdrawal = rpc.transact(vault, ACTOR, WITHDRAW + word(amount) + addr_word(ACTOR) + addr_word(ACTOR), step="withdraw USDC from vault")
    final_usdc = rpc.balance(BASE_USDC, ACTOR)
    final_shares = rpc.balance(vault, ACTOR)
    if final_usdc - after_deposit_usdc != amount or final_shares > after_deposit_shares:
        raise RuntimeError("Withdrawal reconciliation failed")
    return {
        "vault": vault, "amount_usdc_units": amount, "minted_shares": minted,
        "remaining_shares": final_shares - before_shares,
        "approval_gas_used": int(approval["gasUsed"], 16),
        "deposit_gas_used": int(deposit["gasUsed"], 16),
        "withdraw_gas_used": int(withdrawal["gasUsed"], 16),
        "status": "FORK_ONLY_ROUND_TRIP_SUCCEEDED",
    }


def main(argv=None):
    p = argparse.ArgumentParser(description="Isolated local Anvil fork integration test ONLY")
    p.add_argument("--fork-rpc", default="http://127.0.0.1:8545")
    p.add_argument("--vault", choices=VAULTS, required=True)
    p.add_argument("--usdc-holder", required=True, help="On-chain USDC holder to impersonate ONLY within local fork")
    p.add_argument("--amount-usdc", type=int, default=10, choices=range(1, 11), metavar="{1..10}")
    args = p.parse_args(argv)
    try:
        with ForkRPC(args.fork_rpc) as rpc:
            result = run_fork_test(rpc, args.vault, args.amount_usdc * 10**6, usdc_holder=args.usdc_holder)
        print(json.dumps(result, indent=2))
        print("LOCAL FORK ONLY. No Base transaction broadcast.")
        return 0
    except (ValueError, RuntimeError, httpx.HTTPError) as exc:
        print(f"FORK TEST FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
