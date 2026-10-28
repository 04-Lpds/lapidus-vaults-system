"""Read-only Base RPC verification of shortlisted Morpho Vault V2s.

All eth_call reads are pinned to a single block; this module contains no signing,
private-key handling, transaction simulation or broadcast capabilities.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any

import httpx

from .allocator import BASE_CHAIN, BASE_USDC, address_ok
from .client import MorphoClient, MorphoAPIError
from .models import strict_uint

# Fixed ERC-20/ERC-4626 signatures (no dynamic ABI dependency).
ASSET = '0x38d52e0f'                 # asset()
TOTAL_ASSETS = '0x01e1d114'         # totalAssets()
TOTAL_SUPPLY = '0x18160ddd'         # totalSupply()
BALANCE_OF = '0x70a08231'           # balanceOf(address)
CONVERT_TO_ASSETS = '0x07a2d13a'    # convertToAssets(uint256)
DECIMALS = '0x313ce567'             # decimals()
DEFAULT_DB = 'data/lapidus.sqlite3'


class RPCError(RuntimeError):
    pass


def _hex_uint(value: Any, label: str) -> int:
    if not isinstance(value, str) or not value.startswith('0x') or not value[2:]:
        raise RPCError(f'Invalid hex result for {label}')
    try:
        result = int(value[2:], 16)
    except ValueError as exc:
        raise RPCError(f'Invalid hex result for {label}') from exc
    if result < 0 or result >= 2**256:
        raise RPCError(f'Out-of-range hex result for {label}')
    return result


def _word(value: int) -> str:
    if type(value) is not int or not 0 <= value < 2**256:
        raise ValueError('ABI uint256 required')
    return f'{value:064x}'


def _address_word(address: str) -> str:
    if not address_ok(address):
        raise ValueError('Invalid EVM address')
    return '0' * 24 + address[2:].lower()


def _decode_word(result: Any, label: str) -> int:
    if not isinstance(result, str) or len(result) != 66 or not result.startswith('0x'):
        raise RPCError(f'Expected one ABI word from {label}')
    return _hex_uint(result, label)


def _decode_address(result: Any, label: str) -> str:
    number = _decode_word(result, label)
    if number >> 160:
        raise RPCError(f'Invalid ABI address padding from {label}')
    return f'0x{number:040x}'


class ReadOnlyRPC:
    """JSON-RPC transport intentionally exposes only allowlisted read methods."""
    def __init__(self, url: str, *, client: httpx.Client | None = None):
        if not isinstance(url, str) or not url.startswith('https://'):
            raise ValueError('BASE_RPC_URL must be a nonempty HTTPS RPC endpoint')
        self.url = url  # Never print or persist: hosted RPC URLs may contain credentials.
        self._own_client = client is None
        self.http = client or httpx.Client(timeout=httpx.Timeout(15.0), follow_redirects=False)
        self._next_id = 0

    def close(self) -> None:
        if self._own_client:
            self.http.close()

    def __enter__(self) -> 'ReadOnlyRPC':
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def request(self, method: str, params: list[Any]) -> Any:
        if method not in {'eth_chainId', 'eth_blockNumber', 'eth_getCode', 'eth_call', 'eth_getBalance', 'eth_estimateGas', 'eth_gasPrice', 'eth_getTransactionReceipt', 'eth_getTransactionByHash'}:
            raise ValueError('Only read-only JSON-RPC methods are permitted')
        self._next_id += 1
        try:
            response = self.http.post(self.url, json={
                'jsonrpc': '2.0', 'id': self._next_id, 'method': method, 'params': params,
            })
            response.raise_for_status()
            obj = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            # Avoid leaking a credential-bearing RPC URL in exception messages.
            raise RPCError(f'{method} request failed (verify connectivity and RPC provider)') from exc
        if not isinstance(obj, dict) or obj.get('id') != self._next_id:
            raise RPCError(f'Invalid RPC response envelope for {method}')
        if obj.get('error') is not None:
            raise RPCError(f'{method} returned an RPC error')
        if method in {'eth_getTransactionReceipt', 'eth_getTransactionByHash'}:
            if obj.get('result') is not None and not isinstance(obj.get('result'), dict):
                raise RPCError(f'Invalid transaction RPC result from {method}')
        elif not isinstance(obj.get('result'), str):
            raise RPCError(f'Missing RPC result from {method}')
        return obj['result']

    def chain_id(self) -> int:
        return _hex_uint(self.request('eth_chainId', []), 'chain_id')

    def block_number(self) -> int:
        return _hex_uint(self.request('eth_blockNumber', []), 'block_number')

    def gas_price_wei(self) -> int:
        """Current RPC gas quote, not a pinned-block or future guarantee."""
        return _hex_uint(self.request('eth_gasPrice', []), 'gas price')

    def estimate_gas(self, sender: str, contract: str, calldata: str) -> int:
        """Estimate from latest node state. Does not send a transaction."""
        if not address_ok(sender) or not address_ok(contract):
            raise ValueError('Invalid gas estimate addresses')
        if not isinstance(calldata, str) or not calldata.startswith('0x') or len(calldata) % 2:
            raise ValueError('Invalid gas estimate calldata')
        bytes.fromhex(calldata[2:])
        return _hex_uint(self.request('eth_estimateGas', [{
            'from': sender, 'to': contract, 'data': calldata,
        }]), 'gas estimate')

    def eth_balance(self, addr: str, block: int) -> int:
        if not address_ok(addr) or type(block) is not int or block < 0:
            raise ValueError('Invalid address or block')
        return _hex_uint(self.request('eth_getBalance', [addr, hex(block)]), 'ETH balance')

    def simulate_call(self, sender: str, contract: str, calldata: str, block: int) -> str:
        """Pinned eth_call from an address; never signs or broadcasts."""
        if not address_ok(sender) or not address_ok(contract) or type(block) is not int or block < 0:
            raise ValueError('Invalid simulation address/block')
        if not isinstance(calldata, str) or not calldata.startswith('0x') or len(calldata) % 2:
            raise ValueError('Invalid simulation calldata')
        bytes.fromhex(calldata[2:])
        return self.request('eth_call', [{'from': sender, 'to': contract, 'data': calldata}, hex(block)])

    def contract_exists(self, addr: str, block: int) -> bool:
        code = self.request('eth_getCode', [addr, hex(block)])
        if not isinstance(code, str) or not code.startswith('0x'):
            raise RPCError('Invalid contract bytecode result')
        return bool(code[2:]) and any(ch != '0' for ch in code[2:])

    def call_uint(self, contract: str, signature: str, block: int, *words: str) -> int:
        if not address_ok(contract):
            raise ValueError('Invalid contract address')
        out = self.request('eth_call', [{'to': contract, 'data': signature + ''.join(words)}, hex(block)])
        return _decode_word(out, signature)

    def call_address(self, contract: str, signature: str, block: int) -> str:
        if not address_ok(contract):
            raise ValueError('Invalid contract address')
        out = self.request('eth_call', [{'to': contract, 'data': signature}, hex(block)])
        return _decode_address(out, signature)


@dataclass(frozen=True)
class Verification:
    created_at: str
    chain_id: int
    rpc_block: int
    api_indexed_block: int
    vault_address: str
    underlying_asset: str
    asset_decimals: int
    rpc_total_assets: int
    api_total_assets: int
    rpc_total_supply: int
    api_total_supply: int
    wallet_address: str | None
    wallet_usdc_units: int | None
    wallet_vault_shares: int | None
    wallet_vault_assets_estimated: int | None
    status: str
    notes: tuple[str, ...]


def _difference_bps(a: int, b: int) -> Decimal:
    if a == b:
        return Decimal(0)
    if not b:
        return Decimal('Infinity')
    return Decimal(abs(a-b)) * Decimal(10000) / Decimal(b)


def verify_one(rpc: ReadOnlyRPC, morpho: MorphoClient, vault: str, *, block: int,
               wallet: str | None = None, max_lag_blocks: int = 150,
               amount_diff_tolerance_bps: int = 20,
               api_config: dict[str, Any] | None = None,
               api_state: dict[str, Any] | None = None) -> Verification:
    """Compare API observations to pinned-block on-chain reads, with explicit caveats."""
    if not address_ok(vault) or (wallet is not None and not address_ok(wallet)):
        raise ValueError('Vault/wallet must be valid 20-byte EVM addresses')
    if block < 0 or max_lag_blocks < 0 or not 0 <= amount_diff_tolerance_bps <= 1000:
        raise ValueError('Invalid block or threshold')

    # A portfolio can fetch all indexed API observations before pinning the RPC block.
    # Otherwise a later API read can naturally be ahead of an earlier pinned block.
    config = api_config if api_config is not None else morpho.v2_config(BASE_CHAIN, vault)
    state = api_state if api_state is not None else morpho.v2_state(BASE_CHAIN, vault)
    if (str(config.get('address', '')).lower() != vault.lower() or
            strict_uint(config.get('chain_id'), 'config.chain_id') != BASE_CHAIN or
            str(state.get('address', '')).lower() != vault.lower() or
            strict_uint(state.get('chain_id'), 'state.chain_id') != BASE_CHAIN):
        raise ValueError('API vault identity or chain mismatch')
    asset_data = config.get('asset')
    if not isinstance(asset_data, dict) or str(asset_data.get('address', '')).lower() != BASE_USDC:
        raise ValueError('API config underlying is not Base native USDC')

    indexed = strict_uint(state.get('last_indexed_block'), 'last_indexed_block')
    api_assets = strict_uint(state.get('total_assets'), 'total_assets')
    api_shares = strict_uint(state.get('total_supply'), 'total_supply')
    if not rpc.contract_exists(vault, block):
        raise RPCError('Vault has no contract bytecode at pinned block')
    if not rpc.contract_exists(BASE_USDC, block):
        raise RPCError('Expected USDC has no contract bytecode at pinned block')
    asset_rpc = rpc.call_address(vault, ASSET, block)
    if asset_rpc.lower() != BASE_USDC:
        raise RPCError('Vault asset() disagrees with expected Base USDC')
    decimals = rpc.call_uint(BASE_USDC, DECIMALS, block)
    if decimals != 6 or strict_uint(asset_data.get('decimals'), 'config.asset.decimals') != 6:
        raise RPCError('USDC decimals mismatch')
    rpc_assets = rpc.call_uint(vault, TOTAL_ASSETS, block)
    rpc_shares = rpc.call_uint(vault, TOTAL_SUPPLY, block)
    notes: list[str] = []
    if indexed > block:
        notes.append(f'API indexed block {indexed} is ahead of RPC block {block} by {indexed-block} blocks; try again or use a synchronized RPC provider')
    elif block - indexed > max_lag_blocks:
        notes.append(f'API indexer is {block-indexed} blocks behind RPC (limit {max_lag_blocks})')
    if _difference_bps(rpc_assets, api_assets) > amount_diff_tolerance_bps:
        notes.append('API vs RPC total assets differ beyond tolerance (potential block/time effects)')
    if _difference_bps(rpc_shares, api_shares) > amount_diff_tolerance_bps:
        notes.append('API vs RPC total shares differ beyond tolerance (possible fee accrual or lag)')

    wallet_usdc = wallet_shares = wallet_assets = None
    if wallet is not None:
        wallet_usdc = rpc.call_uint(BASE_USDC, BALANCE_OF, block, _address_word(wallet))
        wallet_shares = rpc.call_uint(vault, BALANCE_OF, block, _address_word(wallet))
        wallet_assets = rpc.call_uint(vault, CONVERT_TO_ASSETS, block, _word(wallet_shares))

    return Verification(
        created_at=datetime.now(timezone.utc).isoformat(), chain_id=BASE_CHAIN,
        rpc_block=block, api_indexed_block=indexed, vault_address=vault.lower(),
        underlying_asset=asset_rpc, asset_decimals=decimals,
        rpc_total_assets=rpc_assets, api_total_assets=api_assets,
        rpc_total_supply=rpc_shares, api_total_supply=api_shares,
        wallet_address=None if wallet is None else wallet.lower(),
        wallet_usdc_units=wallet_usdc, wallet_vault_shares=wallet_shares,
        wallet_vault_assets_estimated=wallet_assets,
        status='REVIEW' if notes else 'MATCHED_BASIC_READS', notes=tuple(notes),
    )


def save_verification(path: str, result: Verification) -> int:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, timeout=5)
    try:
        with conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS rpc_vault_verifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                chain_id INTEGER NOT NULL,
                vault_address TEXT NOT NULL,
                rpc_block INTEGER NOT NULL,
                api_indexed_block INTEGER NOT NULL,
                status TEXT NOT NULL,
                details_json TEXT NOT NULL)''')
            cur = conn.execute('''INSERT INTO rpc_vault_verifications (
                created_at, chain_id, vault_address, rpc_block,
                api_indexed_block, status, details_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)''', (
                result.created_at, result.chain_id, result.vault_address,
                result.rpc_block, result.api_indexed_block, result.status,
                json.dumps(asdict(result), sort_keys=True),
            ))
            return int(cur.lastrowid)
    finally:
        conn.close()


def _cli_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description='Read-only Morpho Vault V2 Base RPC verification')
    p.add_argument('--vault', action='append', default=[], help='Repeat to check multiple vaults (max 10)')
    p.add_argument('--allowlist', help='Use addresses from existing paper allowlist')
    p.add_argument('--wallet', help='Optional PUBLIC wallet address: no keys or signing')
    p.add_argument('--db', default=DEFAULT_DB)
    p.add_argument('--max-lag-blocks', type=int, default=150)
    return p


def main(argv: list[str] | None = None) -> int:
    args = _cli_parser().parse_args(argv)
    addresses: list[str] = list(args.vault)
    if args.allowlist:
        try:
            policy = json.loads(Path(args.allowlist).read_text(encoding='utf-8'))
            if not isinstance(policy, dict):
                raise ValueError('Allowlist JSON must be an object')
            if (policy.get('chain_id') != BASE_CHAIN or
                    str(policy.get('asset_address', '')).lower() != BASE_USDC):
                raise ValueError('Allowlist must target Base USDC')
            addresses += policy['approved_vault_addresses']
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            print(f'ERROR: Invalid allowlist: {exc}', file=sys.stderr)
            return 2
    addresses = list(dict.fromkeys(a.lower() for a in addresses))
    if not addresses or len(addresses) > 10 or not all(address_ok(a) for a in addresses):
        print('ERROR: Provide 1–10 valid vault addresses using --vault or --allowlist', file=sys.stderr)
        return 2
    if args.wallet is not None and not address_ok(args.wallet):
        print('ERROR: --wallet must be a public 20-byte EVM address', file=sys.stderr)
        return 2
    rpc_url = os.environ.get('BASE_RPC_URL', '')
    if not rpc_url:
        print('ERROR: Set BASE_RPC_URL to an HTTPS Base mainnet JSON-RPC endpoint', file=sys.stderr)
        return 2
    try:
        with ReadOnlyRPC(rpc_url) as rpc, MorphoClient() as morpho:
            chain = rpc.chain_id()
            if chain != BASE_CHAIN:
                raise RPCError(f'Expected Base chain {BASE_CHAIN}, provider returned {chain}')
            block = rpc.block_number()
            print(f'LVS — READ-ONLY RPC VERIFICATION | Base chain {chain} | pinned block {block}')
            reviewed = False
            for address in addresses:
                result = verify_one(rpc, morpho, address, block=block,
                                    wallet=args.wallet, max_lag_blocks=args.max_lag_blocks)
                row_id = save_verification(args.db, result)
                print(f'  Vault: {address} | status: {result.status} | saved RPC snapshot #{row_id}')
                print(f'    Asset: {result.underlying_asset}; decimals: {result.asset_decimals}')
                print(f'    Total assets [API / RPC]: {result.api_total_assets} / {result.rpc_total_assets} USDC base units')
                print(f'    Total shares [API / RPC]: {result.api_total_supply} / {result.rpc_total_supply}')
                print(f'    API indexed block: {result.api_indexed_block}; RPC block: {result.rpc_block}')
                if args.wallet:
                    print(f'    Wallet USDC: {result.wallet_usdc_units} base units; shares: {result.wallet_vault_shares}; estimated assets: {result.wallet_vault_assets_estimated} base units')
                for note in result.notes:
                    print(f'    REVIEW: {note}')
                reviewed = reviewed or result.status == 'REVIEW'
            print('Read-only identity/balance consistency check. NOT full adapter reconciliation, withdrawal validation or execution authorization.')
            return 2 if reviewed else 0
    except (RPCError, MorphoAPIError, ValueError, OSError, sqlite3.Error) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
