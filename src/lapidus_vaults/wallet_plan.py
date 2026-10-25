"""Read-only, wallet-aware *indicative* rebalance planning for Base USDC Vault V2.

No keys, transaction construction, signing, broadcasting, or estimates of executable
withdrawal amounts. Plans are NOT instructions that can be sent to the chain.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
import json
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any

from .allocator import BASE_CHAIN, BASE_USDC, address_ok, display_usdc, fraction
from .client import MorphoClient, MorphoAPIError
from .rpc_verify import ReadOnlyRPC, RPCError, verify_one

DEFAULT_DB = 'data/lapidus.sqlite3'
ONE = Decimal(1)


@dataclass(frozen=True)
class Policy:
    vault_addresses: tuple[str, ...]
    idle_fraction: Decimal
    per_vault_max_fraction: Decimal


def read_policy(path: str | Path) -> Policy:
    """Read the existing, *manually selected* Base USDC research allowlist."""
    raw = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(raw, dict) or type(raw.get('chain_id')) is not int or raw['chain_id'] != BASE_CHAIN:
        raise ValueError('Allowlist must target Base chain ID 8453')
    if not isinstance(raw.get('asset_address'), str) or raw['asset_address'].lower() != BASE_USDC:
        raise ValueError('Allowlist must use native Base USDC')
    supplied = raw.get('approved_vault_addresses')
    if not isinstance(supplied, list) or not 1 <= len(supplied) <= 10:
        raise ValueError('Provide between 1 and 10 explicitly selected vault addresses')
    if not all(address_ok(v) for v in supplied):
        raise ValueError('Allowlist contains an invalid vault address')
    addresses = tuple(a.lower() for a in supplied)
    if len(set(addresses)) != len(addresses):
        raise ValueError('Duplicate vault address in allowlist')
    idle = fraction(raw.get('idle_fraction', '0.20'), 'idle_fraction')
    cap = fraction(raw.get('per_vault_max_fraction', '0.40'), 'per_vault_max_fraction')
    return Policy(addresses, idle, cap)


@dataclass(frozen=True)
class VaultHolding:
    vault_address: str
    share_balance: int
    estimated_assets_units: int
    api_indexed_block: int
    rpc_total_assets: int
    rpc_total_shares: int


@dataclass(frozen=True)
class WalletSnapshot:
    wallet_address: str
    chain_id: int
    rpc_block: int
    wallet_usdc_units: int
    holdings: tuple[VaultHolding, ...]
    observed_at: str
    source: str = 'rpc'

    @property
    def in_scope_capital_units(self) -> int:
        return self.wallet_usdc_units + sum(h.estimated_assets_units for h in self.holdings)


def read_wallet_snapshot(wallet: str, policy: Policy, rpc: ReadOnlyRPC, morpho: MorphoClient) -> WalletSnapshot:
    """Read selected wallet/vault positions at one RPC block; require basic verification to pass."""
    if not address_ok(wallet):
        raise ValueError('Wallet must be a public 20-byte EVM address')
    if rpc.chain_id() != BASE_CHAIN:
        raise RPCError('RPC endpoint is not Base mainnet (8453)')
    block = rpc.block_number()
    holdings: list[VaultHolding] = []
    wallet_usdc: int | None = None
    for address in policy.vault_addresses:
        evidence = verify_one(rpc, morpho, address, block=block, wallet=wallet)
        if evidence.status != 'MATCHED_BASIC_READS':
            raise ValueError(f'RPC/API verification requires review for vault {address}: {"; ".join(evidence.notes)}')
        if (evidence.chain_id != BASE_CHAIN or evidence.rpc_block != block or
                evidence.vault_address.lower() != address or
                evidence.wallet_address != wallet.lower() or
                evidence.wallet_usdc_units is None or evidence.wallet_vault_shares is None or
                evidence.wallet_vault_assets_estimated is None):
            raise ValueError(f'Incomplete or inconsistent pinned-block observation for {address}')
        if wallet_usdc is None:
            wallet_usdc = evidence.wallet_usdc_units
        elif wallet_usdc != evidence.wallet_usdc_units:
            raise ValueError('USDC balance changed between pinned-block vault reads')
        holdings.append(VaultHolding(
            vault_address=address, share_balance=evidence.wallet_vault_shares,
            estimated_assets_units=evidence.wallet_vault_assets_estimated,
            api_indexed_block=evidence.api_indexed_block,
            rpc_total_assets=evidence.rpc_total_assets,
            rpc_total_shares=evidence.rpc_total_supply,
        ))
    assert wallet_usdc is not None  # nonempty allowlist required by read_policy
    return WalletSnapshot(wallet.lower(), BASE_CHAIN, block, wallet_usdc, tuple(holdings),
                          datetime.now(timezone.utc).isoformat())


@dataclass(frozen=True)
class VaultChange:
    vault_address: str
    current_units: int
    target_units: int
    delta_units: int
    # A signed USDC difference is NOT a transaction amount or an ERC-4626 preview.


@dataclass(frozen=True)
class RebalancePreview:
    snapshot: WalletSnapshot
    capital_units: int
    target_idle_units: int
    changes: tuple[VaultChange, ...]

    @property
    def withdrawals_units(self) -> int:
        return sum(max(0, -c.delta_units) for c in self.changes)

    @property
    def deposits_units(self) -> int:
        return sum(max(0, c.delta_units) for c in self.changes)


def build_rebalance(snapshot: WalletSnapshot, policy: Policy) -> RebalancePreview:
    """Equal-weight USDC targets (capped) vs observed *estimated* wallet holdings."""
    if snapshot.chain_id != BASE_CHAIN or type(snapshot.rpc_block) is not int or snapshot.rpc_block < 0:
        raise ValueError('Wrong chain or invalid block')
    if not address_ok(snapshot.wallet_address) and snapshot.source != 'demo':
        raise ValueError('Invalid wallet')
    current = {h.vault_address.lower(): h for h in snapshot.holdings}
    if len(current) != len(snapshot.holdings) or set(current) != set(policy.vault_addresses):
        raise ValueError('Wallet observation does not cover exactly the selected vaults')
    if (type(snapshot.wallet_usdc_units) is not int or snapshot.wallet_usdc_units < 0 or
            any(type(h.estimated_assets_units) is not int or h.estimated_assets_units < 0 or
                type(h.share_balance) is not int or h.share_balance < 0 for h in snapshot.holdings)):
        raise ValueError('Invalid wallet holding balance')
    capital = snapshot.in_scope_capital_units
    deploy_budget = int((Decimal(capital) * (ONE - policy.idle_fraction)).to_integral_value(rounding=ROUND_DOWN))
    per_cap = int((Decimal(capital) * policy.per_vault_max_fraction).to_integral_value(rounding=ROUND_DOWN))
    target = min(per_cap, deploy_budget // len(policy.vault_addresses))
    changes = tuple(VaultChange(address, current[address].estimated_assets_units, target,
                                target - current[address].estimated_assets_units)
                    for address in sorted(policy.vault_addresses))
    target_idle = capital - target * len(changes)
    if snapshot.wallet_usdc_units + sum(-min(c.delta_units, 0) for c in changes) - sum(max(c.delta_units, 0) for c in changes) != target_idle:
        raise ValueError('Rebalance accounting did not reconcile')
    return RebalancePreview(snapshot, capital, target_idle, changes)


def save_preview(db_path: str | Path, preview: RebalancePreview, policy: Policy) -> int:
    """Append a non-executable preview to SQLite; do not store RPC URL or secrets."""
    p = Path(db_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p, timeout=5)
    try:
        with conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS wallet_rebalance_previews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                observed_at TEXT NOT NULL,
                chain_id INTEGER NOT NULL,
                rpc_block INTEGER NOT NULL,
                wallet_address TEXT NOT NULL,
                in_scope_capital_units TEXT NOT NULL,
                target_idle_units TEXT NOT NULL,
                details_json TEXT NOT NULL
            )''')
            data = {
                'source': preview.snapshot.source,
                'wallet_idle_units': str(preview.snapshot.wallet_usdc_units),
                'holdings': [{k: str(v) if isinstance(v, int) else v for k, v in asdict(h).items()}
                             for h in preview.snapshot.holdings],
                'changes': [{k: str(v) if isinstance(v, int) else v for k, v in asdict(c).items()}
                            for c in preview.changes],
                'policy': {'approved_vault_addresses': list(policy.vault_addresses),
                           'idle_fraction': str(policy.idle_fraction),
                           'per_vault_max_fraction': str(policy.per_vault_max_fraction)},
                'limitations': ['Not executable', 'No maxWithdraw/allowance checks',
                                'No gas or slippage estimates', 'Other holdings outside allowlist not counted'],
            }
            cur = conn.execute('''INSERT INTO wallet_rebalance_previews
                (observed_at, chain_id, rpc_block, wallet_address,
                 in_scope_capital_units, target_idle_units, details_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)''', (
                    preview.snapshot.observed_at, preview.snapshot.chain_id,
                    preview.snapshot.rpc_block, preview.snapshot.wallet_address,
                    str(preview.capital_units), str(preview.target_idle_units),
                    json.dumps(data, sort_keys=True),
                ))
            return int(cur.lastrowid)
    finally:
        conn.close()


def demo_wallet(policy: Policy) -> WalletSnapshot:
    """Deterministic offline demonstration: no claim about real chain state."""
    amounts = [60_000_000, 40_000_000] + [0] * len(policy.vault_addresses)
    holdings = tuple(VaultHolding(addr, 0, amounts[i], 0, 0, 0)
                     for i, addr in enumerate(policy.vault_addresses))
    return WalletSnapshot('DEMO (no blockchain reads)', BASE_CHAIN, 0,
                          100_000_000, holdings, datetime.now(timezone.utc).isoformat(), 'demo')


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description='Read-only wallet-aware hypothetical USDC rebalance planning')
    p.add_argument('--allowlist', required=True, help='Locally selected Base USDC vault policy JSON')
    p.add_argument('--wallet', help='Public wallet address for pinned-block RPC reads')
    p.add_argument('--demo', action='store_true', help='Use clearly fictional balances; no RPC calls')
    p.add_argument('--db', default=DEFAULT_DB)
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        policy = read_policy(args.allowlist)
        if args.demo:
            if args.wallet:
                raise ValueError('Use either --demo or --wallet, not both')
            snapshot = demo_wallet(policy)
        else:
            if not args.wallet or not address_ok(args.wallet):
                raise ValueError('Provide a public --wallet ADDRESS or use --demo')
            rpc_url = os.getenv('BASE_RPC_URL', '')
            if not rpc_url:
                raise ValueError('Set BASE_RPC_URL to a Base HTTPS JSON-RPC endpoint')
            with ReadOnlyRPC(rpc_url) as rpc, MorphoClient() as morpho:
                snapshot = read_wallet_snapshot(args.wallet, policy, rpc, morpho)
        preview = build_rebalance(snapshot, policy)
        row_id = save_preview(args.db, preview, policy)
        print(f'LVS — INDICATIVE WALLET REBALANCE | {snapshot.source.upper()} | '
              f'Base block {snapshot.rpc_block} | saved preview #{row_id}')
        print(f'  In-scope USDC capital: {display_usdc(preview.capital_units)}')
        print(f'  Wallet USDC current:  {display_usdc(snapshot.wallet_usdc_units)}')
        print(f'  Wallet USDC target:   {display_usdc(preview.target_idle_units)}')
        for change in preview.changes:
            direction = ('DEPOSIT' if change.delta_units > 0 else
                         'WITHDRAW' if change.delta_units < 0 else 'HOLD')
            print(f'  {change.vault_address}  current {display_usdc(change.current_units)} '
                  f'-> target {display_usdc(change.target_units)} USDC | '
                  f'{direction} {display_usdc(abs(change.delta_units))} USDC (indicative)')
        print(f'  Indicative withdrawals: {display_usdc(preview.withdrawals_units)} USDC')
        print(f'  Indicative deposits:    {display_usdc(preview.deposits_units)} USDC')
        print('READ ONLY: approximate share values. Not executable; liquidity, approvals, gas, '
              'other wallet assets, and transaction feasibility NOT checked.')
        return 0
    except (RPCError, MorphoAPIError, sqlite3.Error, OSError, ValueError, TypeError,
            json.JSONDecodeError, ZeroDivisionError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
