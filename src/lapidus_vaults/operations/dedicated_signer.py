"""Dedicated-wallet EIP-1559 signer with a durable no-resend boundary.

No key is persisted by LVS. Only call after production preflight and exact
campaign-to-quote verification. Each signed transaction is journaled BEFORE
eth_sendRawTransaction; any uncertainty blocks automatic retries.
"""
from __future__ import annotations

from pathlib import Path
import sqlite3
import time
import httpx

from ..allocator import BASE_CHAIN, BASE_USDC, address_ok
from ..rpc_verify import ReadOnlyRPC, _address_word, _word
from ..unsigned_candidates import APPROVE, DEPOSIT, WITHDRAW
from .auto_journal import reserve, mark_submitted
from .signer_policy import SignerPolicy
from .execution_authorization import assert_active_authorization
from ..preflight import GAS_PRICE_ORACLE, L1_FEE_UPPER_BOUND
from ..live_deposit import ALLOWANCE, DepositQuotes, MAX_BLOCK_AGE, MAX_QUOTE_AGE_SECONDS
from ..live_withdraw import WithdrawalQuotes

MAX_ACTION_UNITS = 1_000_000
MAX_TRANSACTION_FEE_WEI = 50_000_000_000_000  # 0.00005 ETH
MAX_GAS_LIMIT = 700_000

def build_unsigned_transaction(*, consumed: dict, candidate: dict,
                               policy_wallet: str, protected_wallet: str,
                               nonce: int, gas_price_wei: int,
                               approved_vaults: tuple[str, ...],
                               policy: SignerPolicy) -> dict:
    """Pure transaction construction and authorization; no keys or network I/O."""
    if not isinstance(policy, SignerPolicy):
        raise ValueError("Explicit dedicated signer policy required")
    signer = policy.execution_address
    if (policy_wallet != signer or protected_wallet != policy.protected_address
            or approved_vaults != policy.vault_allowlist):
        raise ValueError("Signer configuration differs from explicit policy")
    if (type(consumed.get("chain_id")) is not int
            or type(candidate.get("chain_id")) is not int
            or consumed.get("chain_id") not in policy.chain_allowlist
            or consumed.get("stage") not in policy.operation_allowlist):
        raise ValueError("Chain or operation not permitted by signer policy")
    if type(consumed.get("amount_units")) is not int:
        raise ValueError("Integer amount required")
    if consumed["amount_units"] > policy.max_action_units:
        raise ValueError("Amount exceeds configured signer cap")
    if (not address_ok(protected_wallet)
        or signer == protected_wallet.lower()
        or signer != policy_wallet.lower()
        or signer != consumed.get("wallet")):
        raise ValueError("Signer is not an approved separate dedicated wallet")
    if (consumed.get("chain_id") != BASE_CHAIN or
        candidate.get("chain_id") != BASE_CHAIN or
        type(candidate.get("value_wei")) is not int or candidate["value_wei"] != 0 or
        consumed.get("to") != candidate.get("to_address") or
        consumed.get("data") != candidate.get("data") or
        consumed.get("wallet") != candidate.get("from_address") or
        consumed.get("amount_units") != candidate.get("requested_assets_units") or
        consumed.get("amount_units", 0) > MAX_ACTION_UNITS or
        consumed.get("amount_units", 0) <= 0 or
        candidate.get("method") != consumed.get("stage","") + "_intent"):
        raise ValueError("Signed transaction differs from campaign or exceeds cap")
    stage = consumed["stage"]
    vault = consumed["vault"]
    to = consumed["to"]
    if stage == "approve":
        if to != BASE_USDC:
            raise ValueError("Unexpected token approval target")
    elif stage in {"deposit", "withdraw"}:
        if to != vault:
            raise ValueError("Unexpected vault target")
    else:
        raise ValueError("Unknown execution operation")
    if vault not in approved_vaults or not all(address_ok(v) for v in approved_vaults):
        raise ValueError("Vault not permitted by dedicated signer policy")
    if not address_ok(vault) or not address_ok(to):
        raise ValueError("Invalid execution target")
    amount = consumed["amount_units"]
    expected_data = (
        APPROVE + _address_word(vault) + _word(amount) if stage == "approve" else
        DEPOSIT + _word(amount) + _address_word(signer) if stage == "deposit" else
        WITHDRAW + _word(amount) + _address_word(signer) + _address_word(signer)
    )
    if not isinstance(consumed.get("data"), str) or consumed["data"].lower() != expected_data:
        raise ValueError("Calldata is not exact bounded vault operation")
    if consumed.get("value") != "0x0":
        raise ValueError("ETH value must be zero")
    try:
        if not isinstance(consumed.get("gas"), str) or not consumed["gas"].startswith("0x"):
            raise ValueError
        gas = int(consumed["gas"], 16)
    except (ValueError, TypeError):
        raise ValueError("Missing or malformed gas limit") from None
    if (type(nonce) is not int or not 0 <= nonce < 2**64 or
        not 21_000 <= gas <= MAX_GAS_LIMIT or
        type(gas_price_wei) is not int or
        not 0 < gas_price_wei <= 100_000_000_000):
        raise ValueError("Gas or nonce outside policy")
    max_fee = gas_price_wei * 2
    priority_fee = min(gas_price_wei, 1_000_000_000)
    max_total_fee_wei = policy.max_total_fee_wei
    if (type(max_total_fee_wei) is not int or
        not 0 < max_total_fee_wei <= 5_000_000_000_000_000):
        raise ValueError("Invalid maximum fee policy")
    if gas * max_fee > max_total_fee_wei:
        raise ValueError(f"Worst-case ETH fee {gas * max_fee} wei exceeds configured cap {max_total_fee_wei} wei (gas={gas}, maxFeePerGas={max_fee})")
    from eth_utils import to_checksum_address
    unsigned = {
        "chainId": BASE_CHAIN, "type": 2, "nonce": nonce,
        "to": to_checksum_address(to), "value": 0, "data": consumed["data"], "gas": gas,
        "maxFeePerGas": max_fee, "maxPriorityFeePerGas": priority_fee,
    }
    return unsigned


def execution_fee_budget(*, rpc: ReadOnlyRPC, data: str, gas: int,
                         max_fee_per_gas: int, block: int) -> tuple[int, int]:
    """L2 maximum plus a 2x current Base oracle L1 bound, not an inclusion guarantee."""
    # These transactions have fixed calldata, no access list and bounded fields.
    # 256 bytes covers the RLP envelope/signature overhead; 512 is a size floor.
    size = max(512, len(data[2:]) // 2 + 256)
    l1 = rpc.call_uint(GAS_PRICE_ORACLE, L1_FEE_UPPER_BOUND, block, _word(size))
    if type(l1) is not int or not 0 < l1 < 2**256:
        raise ValueError("Missing or invalid Base L1 fee bound")
    return gas * max_fee_per_gas + 2 * l1, 2 * l1


def validate_nonce_and_budget(*, rpc: ReadOnlyRPC, consumed: dict,
                              policy: SignerPolicy, nonce: int,
                              gas_price_wei: int) -> int:
    block = rpc.block_number()
    if rpc.chain_id() != BASE_CHAIN:
        raise ValueError("Wrong RPC chain at signing boundary")
    total, _ = execution_fee_budget(rpc=rpc, data=consumed["data"],
        gas=int(consumed["gas"], 16), max_fee_per_gas=2 * gas_price_wei, block=block)
    if total > policy.max_total_fee_wei:
        raise ValueError("Combined L1/L2 fee exceeds configured cap")
    if rpc.eth_balance(policy.execution_address, block) < policy.min_eth_reserve_wei + total:
        raise ValueError("Insufficient ETH for reserve and maximum signing fee including L1")
    latest = rpc.transaction_count(policy.execution_address, "latest")
    pending = rpc.transaction_count(policy.execution_address, "pending")
    if type(latest) is not int or type(pending) is not int or latest != pending or nonce != latest:
        raise ValueError("Stale nonce or other pending wallet transaction")
    head = rpc.block_number()
    if (not 0 <= time.time() - consumed["authorized_at_unix"] <= MAX_QUOTE_AGE_SECONDS
            or head < block
            or not 0 <= head - consumed["observed_block"] <= MAX_BLOCK_AGE):
        raise ValueError("Authorization expired during RPC verification")
    return total


def validate_fresh_state(*, rpc: ReadOnlyRPC, consumed: dict,
                         policy: SignerPolicy) -> None:
    """Recheck a recent authorization against pinned current wallet/vault state."""
    authorized = consumed.get("authorized_at_unix")
    if (type(authorized) not in (int, float)
            or not 0 <= time.time() - authorized <= MAX_QUOTE_AGE_SECONDS):
        raise ValueError("Missing or expired signer authorization")
    block = rpc.block_number()
    observed = consumed.get("observed_block")
    if (type(observed) is not int or not 0 <= block - observed <= MAX_BLOCK_AGE):
        raise ValueError("Stale or future signer observation")
    for address in (consumed["vault"], BASE_USDC):
        if not rpc.contract_exists(address, block):
            raise ValueError("Execution contract missing at pinned block")
    engine_type = WithdrawalQuotes if consumed["stage"] == "withdraw" else DepositQuotes
    fresh = engine_type(min_gas_reserve_wei=policy.min_eth_reserve_wei).quote(
        rpc, wallet=policy.execution_address, vault=consumed["vault"],
        approved_vaults=policy.vault_allowlist, amount_units=consumed["amount_units"])
    for field in ("stage", "wallet", "vault", "chain_id", "amount_units", "to", "data", "value"):
        if fresh[field] != consumed.get(field):
            raise ValueError("Vault state or transaction changed; replan required")
    allowance = rpc.call_uint(BASE_USDC, ALLOWANCE, fresh["observed_block"],
        _address_word(policy.execution_address), _address_word(consumed["vault"]))
    if (type(allowance) is not int or allowance < 0
            or allowance > policy.max_action_units
            or (consumed["stage"] == "approve" and allowance != 0)
            or (consumed["stage"] == "deposit" and allowance != consumed["amount_units"])):
        raise ValueError("Existing allowance differs from exact dedicated action policy")
    if fresh["gas_estimate_units"] > int(consumed["gas"], 16):
        raise ValueError("Fresh gas estimate exceeds authorized gas limit")
    if consumed["stage"] == "withdraw":
        cap = consumed.get("max_burned_shares")
        if type(cap) is not int or fresh["expected_burned_shares"] > cap:
            raise ValueError("Withdrawal share preview exceeds authorized maximum")


def prepare_signed_transaction(*, consumed: dict, candidate: dict,
                               policy_wallet: str, protected_wallet: str,
                               private_key: str, nonce: int, gas_price_wei: int,
                               approved_vaults: tuple[str, ...],
                               policy: SignerPolicy, rpc: ReadOnlyRPC) -> dict:
    """Construct, verify fresh state and sign; this function cannot broadcast."""
    unsigned = build_unsigned_transaction(
        consumed=consumed, candidate=candidate, policy_wallet=policy_wallet,
        protected_wallet=protected_wallet, nonce=nonce, gas_price_wei=gas_price_wei,
        approved_vaults=approved_vaults, policy=policy)
    from eth_account import Account
    try:
        if not isinstance(private_key, str) or not private_key.startswith("0x"):
            raise ValueError
        account = Account.from_key(private_key)
    except (ValueError, TypeError):
        raise ValueError("Invalid runtime signing key") from None
    signer = account.address.lower()
    if signer != policy.execution_address:
        raise ValueError("Signing key does not match dedicated execution address")
    validate_fresh_state(rpc=rpc, consumed=consumed, policy=policy)
    total_fee = validate_nonce_and_budget(rpc=rpc, consumed=consumed,
        policy=policy, nonce=nonce, gas_price_wei=gas_price_wei)
    signed = Account.sign_transaction(unsigned, private_key)
    raw_hex = signed.raw_transaction.hex()
    hash_hex = signed.hash.hex()
    return {"raw": raw_hex if raw_hex.startswith("0x") else "0x" + raw_hex,
            "tx_hash": hash_hex if hash_hex.startswith("0x") else "0x" + hash_hex,
            "signer": signer, "max_fee_wei": total_fee}


def reserve_and_broadcast(*, db_path: str, campaign_id: str, sequence: int,
                          quote_id: str, consumed: dict, candidate: dict,
                          policy_wallet: str, protected_wallet: str,
                          private_key: str, rpc_url: str, nonce: int,
                          gas_price_wei: int,
                          approved_vaults: tuple[str, ...], policy: SignerPolicy) -> dict:
    """One-shot signed send; never retries an uncertain broadcast.

    Caller must obtain a freshly simulated quote and persist the exact
    authorization/binding BEFORE using this method. This method does not
    generate a quote or unlock later campaign actions.
    """
    if Path("data/STOP_AUTOMATION").exists():
        raise ValueError("Emergency stop before signing")
    from urllib.parse import urlparse
    parsed = urlparse(rpc_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Live RPC must use a trusted HTTPS provider")
    authority = dict(campaign_id=campaign_id, sequence=sequence, quote_id=quote_id,
                     consumed=consumed, candidate=candidate)
    with sqlite3.connect(db_path, timeout=10) as db:
        db.execute("BEGIN IMMEDIATE")
        assert_active_authorization(db, **authority)
    # Verify persisted authorization before accessing key material or signing.
    with ReadOnlyRPC(rpc_url) as rpc:
        built = prepare_signed_transaction(
            consumed=consumed, candidate=candidate,
            policy_wallet=policy_wallet, protected_wallet=protected_wallet,
            private_key=private_key, nonce=nonce, gas_price_wei=gas_price_wei,
            approved_vaults=approved_vaults, policy=policy, rpc=rpc)
    reserve(db_path,campaign_id=campaign_id,sequence=sequence,quote_id=quote_id,
            wallet=policy_wallet,to_address=consumed["to"],
            calldata=consumed["data"],amount_units=consumed["amount_units"])
    # Write the deterministic signed hash before any broadcast I/O. If we crash,
    # the next run must only *look up this hash*, never sign/send a replacement.
    mark_submitted(db_path,campaign_id=campaign_id,sequence=sequence,
                   quote_id=quote_id,tx_hash=built["tx_hash"])
    # A durable hash already exists. Any failure below halts without resending.
    # Serialize the final authority check and send against administrative DB
    # changes: a halt that commits before this lock always prevents submission.
    with sqlite3.connect(db_path, timeout=10) as db:
        db.execute("BEGIN IMMEDIATE")
        assert_active_authorization(db, **authority, submitted_hash=built["tx_hash"])
        with ReadOnlyRPC(rpc_url) as rpc:
            validate_fresh_state(rpc=rpc, consumed=consumed, policy=policy)
            validate_nonce_and_budget(rpc=rpc, consumed=consumed, policy=policy,
                                      nonce=nonce, gas_price_wei=gas_price_wei)
        if Path("data/STOP_AUTOMATION").exists():
            raise ValueError("Emergency stop before broadcast")
        try:
            with httpx.Client(timeout=15, follow_redirects=False) as client:
                response = client.post(rpc_url, json={
                    "jsonrpc": "2.0", "id": 1, "method": "eth_sendRawTransaction",
                    "params": [built["raw"]],
                })
                response.raise_for_status()
                payload = response.json()
                if (not isinstance(payload, dict) or payload.get("id") != 1
                    or payload.get("error") is not None
                    or str(payload.get("result", "")).lower() != built["tx_hash"].lower()):
                    raise RuntimeError("RPC did not acknowledge the exact signed transaction")
        except (httpx.HTTPError, ValueError, RuntimeError):
            raise RuntimeError(
                "Broadcast outcome uncertain. Saved hash is authoritative; "
                "check its receipt before ANY further action. Never resend."
            ) from None
    return {"state":"SUBMITTED","tx_hash":built["tx_hash"],
            "campaign_id":campaign_id,"sequence":sequence,
            "warning":"Hash recorded; receipt verification required before next action"}
