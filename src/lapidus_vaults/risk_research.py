"""Experimental read-only vault discovery, market context and portfolio overlaps.

Standalone: python -m lapidus_vaults.risk_research --help
Additive module: deliberately does not modify the existing cli.py/schema.
Market balances are API/indexer observations, not verified withdrawable funds or credit-risk ratings.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN, localcontext
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any

from .allocator import BASE_CHAIN, BASE_USDC, UNIT, address_ok, capital_to_units, fraction
from .client import MorphoAPIError, MorphoClient

DEFAULT_DB = 'data/lapidus.sqlite3'
WAD = Decimal(10) ** 18


def _uint(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError(f'{field} must be an unsigned integer/string')
    if isinstance(value, str) and (not value or not value.isascii() or not value.isdecimal()):
        raise ValueError(f'{field} must contain only decimal digits')
    n = int(value)
    if n < 0:
        raise ValueError(f'{field} must be nonnegative')
    return n


def _db(path: str, *, require_existing: bool = False) -> sqlite3.Connection:
    target = Path(path)
    if require_existing and not target.is_file():
        raise ValueError(f'Existing LVS database not found: {path}')
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, timeout=3)
    conn.row_factory = sqlite3.Row
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _selected_addresses(db: sqlite3.Connection, addresses: list[str]) -> dict[str, sqlite3.Row]:
    if not addresses or len(addresses) > 20:
        raise ValueError('Provide 1 to 20 vault addresses')
    result: dict[str, sqlite3.Row] = {}
    for address in addresses:
        if not address_ok(address):
            raise ValueError(f'Invalid vault address: {address}')
        row = db.execute('''SELECT id, vault_address, name, total_assets, idle_assets,
            adapter_assets, issues_json, observed_at
            FROM exposure_snapshots WHERE chain_id=? AND lower(vault_address)=?
            ORDER BY id DESC LIMIT 1''', (BASE_CHAIN, address.lower())).fetchone()
        if row is None:
            raise ValueError(f'No saved exposures for {address}; run exposures first')
        result[address.lower()] = row
    return result


def _active_positions(db: sqlite3.Connection, snapshot: sqlite3.Row) -> list[sqlite3.Row]:
    issues = json.loads(snapshot['issues_json'])
    if not isinstance(issues, list) or issues:
        raise ValueError(f'Exposure snapshot #{snapshot["id"]} incomplete: {issues}')
    total = _uint(snapshot['total_assets'], 'total_assets')
    idle = _uint(snapshot['idle_assets'], 'idle_assets')
    adapter = _uint(snapshot['adapter_assets'], 'adapter_assets')
    if total <= 0 or idle + adapter != total:
        raise ValueError(f'Exposure snapshot #{snapshot["id"]}: vault balance mismatch')
    rows = db.execute('''SELECT market_id, collateral_symbol, loan_symbol, supply_assets
        FROM exposure_market_positions WHERE snapshot_id=?''', (snapshot['id'],)).fetchall()
    if sum(_uint(p['supply_assets'], 'supply_assets') for p in rows) != adapter:
        raise ValueError(f'Exposure snapshot #{snapshot["id"]}: markets do not reconcile with adapters')
    for p in rows:
        if not (isinstance(p['market_id'], str) and len(p['market_id']) == 66
                and p['market_id'].startswith('0x') and
                all(c in '0123456789abcdefABCDEF' for c in p['market_id'][2:])):
            raise ValueError(f'Exposure snapshot #{snapshot["id"]}: invalid market ID')
    return rows


@dataclass(frozen=True)
class SharedMarket:
    market_id: str
    loan: str
    collateral: str
    total_units: Decimal
    contributors: tuple[tuple[str, Decimal], ...]


def compute_overlap(db: sqlite3.Connection, policy: dict[str, Any], capital_units: int) -> tuple[list[SharedMarket], dict[str, int], int]:
    if policy.get('chain_id') != BASE_CHAIN or str(policy.get('asset_address', '')).lower() != BASE_USDC:
        raise ValueError('Only Base USDC portfolios are supported')
    approved = policy.get('approved_vault_addresses')
    if not isinstance(approved, list) or not approved or len(approved) > 20 or not all(address_ok(x) for x in approved):
        raise ValueError('Need 1-20 approved vault addresses in allowlist JSON')
    if len(set(a.lower() for a in approved)) != len(approved):
        raise ValueError('Duplicate vault addresses in allowlist')
    idle_fraction = fraction(policy.get('idle_fraction', '0.20'), 'idle_fraction')
    max_vault = fraction(policy.get('per_vault_max_fraction', '0.40'), 'per_vault_max_fraction')
    snapshots = _selected_addresses(db, approved)
    # Match v0.2's equal-weight, capped, integer-base-unit allocation policy.
    deploy = int((Decimal(capital_units) * (1 - idle_fraction)).to_integral_value(rounding=ROUND_DOWN))
    cap = int((Decimal(capital_units) * max_vault).to_integral_value(rounding=ROUND_DOWN))
    each = min(deploy // len(approved), cap)
    weights = {a.lower(): each for a in approved}
    idle_units = capital_units - sum(weights.values())
    market_map: dict[str, dict[str, Any]] = {}
    with localcontext() as ctx:
        ctx.prec = 100
        for address, allocation in weights.items():
            snapshot = snapshots[address]
            positions = _active_positions(db, snapshot)
            factor = Decimal(allocation) / Decimal(_uint(snapshot['total_assets'], 'total_assets'))
            for position in positions:
                supply = _uint(position['supply_assets'], 'supply_assets')
                if not supply:  # Configured but economically zero: do not count as a shared exposure.
                    continue
                mid = position['market_id'].lower()
                meta = (position['loan_symbol'], position['collateral_symbol'])
                entry = market_map.setdefault(mid, {'meta': meta, 'contributors': defaultdict(Decimal)})
                if entry['meta'] != meta:
                    raise ValueError(f'Inconsistent market metadata: {mid}')
                entry['contributors'][address] += Decimal(supply) * factor
        markets = [SharedMarket(mid, v['meta'][0], v['meta'][1],
                                +sum(v['contributors'].values(), Decimal(0)),
                                tuple(sorted((addr, +amt) for addr, amt in v['contributors'].items() if amt > 0)))
                   for mid, v in market_map.items()]
    markets.sort(key=lambda m: (-m.total_units, m.market_id))
    return markets, weights, idle_units


def overlap(args: argparse.Namespace) -> None:
    policy = json.loads(Path(args.allowlist).read_text(encoding='utf8'))
    capital = capital_to_units(args.capital_usdc)
    with _db(args.db, require_existing=True) as db:
        markets, weights, idle = compute_overlap(db, policy, capital)
    print('\nLVS — SHARED-MARKET CONTRIBUTIONS (OFFLINE, HYPOTHETICAL)')
    print(f'Portfolio: {Decimal(capital)/UNIT:,.6f} USDC; vault allocations: {Decimal(sum(weights.values()))/UNIT:,.6f}; wallet idle: {Decimal(idle)/UNIT:,.6f}')
    for addr, units in weights.items():
        print(f'  {addr}  {Decimal(units)/UNIT:,.6f} USDC')
    shared = [m for m in markets if len(m.contributors) >= 2]
    print(f'Active underlying markets: {len(markets)}; shared by 2+ vaults: {len(shared)}')
    if not shared:
        print('No shared active market IDs observed in saved snapshots.')
    for m in shared:
        share = m.total_units / Decimal(capital) * 100
        severity = ' [MATERIAL >= 10% PORTFOLIO]' if share >= 10 else ''
        print(f'\n  {m.collateral}/{m.loan}  {m.total_units/UNIT:.6f} USDC  {share:.3f}% total{severity}')
        print(f'  market ID: {m.market_id}')
        for addr, amt in sorted(m.contributors, key=lambda pair: -pair[1]):
            print(f'    {addr}: {amt/UNIT:.6f} USDC ({amt/m.total_units*100:.3f}% of this market exposure)')
    print('\nThese are reported USDC lending positions, NOT collateral tokens owned or modeled credit losses.')
    print('Source observations can have different timestamps; no fresh RPC verification performed.')


def _even_sample(items: list[dict[str, Any]], size: int) -> list[dict[str, Any]]:
    """Spread inspections across address-sorted directory instead of taking the first N."""
    if size >= len(items):
        return items
    if size == 1:
        return [items[len(items)//2]]
    return [items[(i * (len(items)-1)) // (size-1)] for i in range(size)]


def discover(args: argparse.Namespace, client: MorphoClient | None = None) -> list[dict[str, Any]]:
    owns = client is None
    client = client or MorphoClient()
    rows: list[dict[str, Any]] = []
    try:
        listed = client.listed_v2_vaults(BASE_CHAIN, limit=args.universe_limit, asset_address=BASE_USDC)
        picked = _even_sample(sorted(listed, key=lambda r: str(r['vault_address']).lower()), args.inspect_limit)
        print(f'Listed Base USDC V2 vaults returned: {len(listed)} (directory cap {args.universe_limit}); inspecting {len(picked)} spread across that directory.')
        for item in picked:
            address = item['vault_address']
            name = str(item.get('name') or address)
            try:
                state = client.v2_state(BASE_CHAIN, address)
                conf = client.v2_config(BASE_CHAIN, address)
                apy = client.v2_apy(BASE_CHAIN, address)
                from .allocator import assess_vault
                assessment = assess_vault(listing=item, state=state, config=conf, apy=apy)
                record = dict(address=address.lower(), name=name,
                              total_assets=assessment.total_assets, apy=assessment.apy,
                              withdrawable=assessment.withdrawable_fraction,
                              preliminary=assessment.preliminary_pass,
                              issues=list(assessment.problems))
            except (MorphoAPIError, ValueError, KeyError, TypeError) as exc:
                record = dict(address=address.lower(), name=name, total_assets=None,
                              apy=None, withdrawable=None, preliminary=False,
                              issues=[f'Incomplete API response: {exc}'])
            rows.append(record)
    finally:
        if owns:
            client.close()
    # Sort by reported assets as a research queue, NOT a risk- or yield-based ranking.
    rows.sort(key=lambda r: (not r['preliminary'], -(r['total_assets'] or 0), r['address']))
    if args.db:
        with _db(args.db) as db:
            db.execute('''CREATE TABLE IF NOT EXISTS research_candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT, collected_at TEXT NOT NULL,
                vault_address TEXT NOT NULL, name TEXT NOT NULL,
                assets_base_units TEXT, apy TEXT, withdrawable_fraction TEXT,
                preliminary_pass INTEGER NOT NULL, issues_json TEXT NOT NULL)''')
            at = _now()
            db.executemany('''INSERT INTO research_candidates
                (collected_at, vault_address, name, assets_base_units, apy,
                 withdrawable_fraction, preliminary_pass, issues_json)
                VALUES (?,?,?,?,?,?,?,?)''', [
                (at, r['address'], r['name'], None if r['total_assets'] is None else str(r['total_assets']),
                 None if r['apy'] is None else str(r['apy']),
                 None if r['withdrawable'] is None else str(r['withdrawable']),
                 int(r['preliminary']), json.dumps(r['issues'])) for r in rows])
            db.commit()
    print('\nCANDIDATE RESEARCH QUEUE (not investment approvals):')
    for r in rows[:args.top]:
        tvl = 'n/a' if r['total_assets'] is None else f"{Decimal(r['total_assets'])/UNIT:,.0f}"
        apy = 'n/a' if r['apy'] is None else f"{r['apy']*100:.2f}%"
        liq = 'n/a' if r['withdrawable'] is None else f"{r['withdrawable']*100:.1f}%"
        print(f"  {'PRELIM' if r['preliminary'] else 'REVIEW':6}  {r['name'][:28]:28} TVL {tvl:>14} USDC  APY {apy:>7}  wd {liq:>6}")
        print(f"           {r['address']}")
        if r['issues']:
            print('           ' + '; '.join(r['issues'])[:175])
    print('\nSelection is a deterministic sample of the listed directory, NOT an exhaustive best-vault ranking.')
    print('Sort priority is preliminary data completeness, then reported vault assets—not financial safety.')
    print('Review shortlisted vaults manually before any allowlisting or live action.')
    return rows


def get_market_context(client: MorphoClient, market_id: str) -> dict[str, Any]:
    if not (isinstance(market_id, str) and len(market_id) == 66 and market_id[:2] == '0x' and
            all(c in '0123456789abcdefABCDEF' for c in market_id[2:])):
        raise ValueError('Invalid 32-byte Morpho market ID')
    selector = f'{BASE_CHAIN}:{market_id}'
    conf = client._get(f'/v0/blue/markets/{selector}')['data']
    state = client._get(f'/v0/blue/markets/{selector}/state')['data']
    if not isinstance(conf, dict) or not isinstance(state, dict):
        raise ValueError('Invalid Morpho market configuration or state')
    if str(conf.get('market_id','')).lower() != market_id.lower() or str(state.get('market_id','')).lower() != market_id.lower():
        raise ValueError('Market identity differs between request, configuration and state')
    if _uint(conf.get('chain_id'), 'config.chain_id') != BASE_CHAIN or _uint(state.get('chain_id'), 'state.chain_id') != BASE_CHAIN:
        raise ValueError('Market chain mismatch')
    if str(conf.get('loan_token','')).lower() != BASE_USDC:
        raise ValueError('Market loan token is not canonical Base USDC')
    lltv = Decimal(_uint(conf.get('lltv_wad'), 'lltv_wad')) / WAD
    if not 0 <= lltv <= 1:
        raise ValueError('Invalid LLTV')
    supply = _uint(state.get('total_supply_assets'), 'total_supply_assets')
    debt = _uint(state.get('total_borrow_assets'), 'total_borrow_assets')
    if supply < debt:
        raise ValueError('Market total borrow exceeds total supply')
    liquidity: dict[str, Any] | None = None
    liquidity_error: str | None = None
    try:
        liquidity = client._get(f'/v0/blue/markets/{selector}/liquidity')['data']
        if not isinstance(liquidity, dict):
            raise ValueError('Invalid market liquidity response')
    except (MorphoAPIError, ValueError) as exc:
        liquidity_error = str(exc)
    return dict(market_id=market_id.lower(), chain_id=BASE_CHAIN, config=conf, state=state,
                liquidity=liquidity, liquidity_error=liquidity_error,
                lltv=lltv, supply=supply, borrow=debt,
                utilization=(None if supply == 0 else Decimal(debt)/Decimal(supply)),
                idle_cash=supply-debt)


def market_context(args: argparse.Namespace, client: MorphoClient | None = None) -> list[dict[str, Any]]:
    with _db(args.db, require_existing=True) as db:
        snaps = _selected_addresses(db, args.vault)
        markets: set[str] = set()
        for snap in snaps.values():
            for pos in _active_positions(db, snap):
                if _uint(pos['supply_assets'], 'supply_assets') > 0:
                    markets.add(pos['market_id'].lower())
    if len(markets) > args.max_markets:
        raise ValueError(f'{len(markets)} active markets found; max {args.max_markets}. Increase --max-markets deliberately.')
    owns = client is None
    client = client or MorphoClient()
    results: list[dict[str, Any]] = []
    try:
        for mid in sorted(markets):
            results.append(get_market_context(client, mid))
    finally:
        if owns:
            client.close()
    # Persist only complete config/state observations; liquidity is optional and marked missing.
    with _db(args.db) as db:
        db.execute('''CREATE TABLE IF NOT EXISTS research_market_states (
            id INTEGER PRIMARY KEY AUTOINCREMENT, collected_at TEXT NOT NULL,
            chain_id INTEGER NOT NULL, market_id TEXT NOT NULL, lltv_wad TEXT NOT NULL,
            total_supply_assets TEXT NOT NULL, total_borrow_assets TEXT NOT NULL,
            last_indexed_block TEXT, raw_config_json TEXT NOT NULL,
            raw_state_json TEXT NOT NULL, raw_liquidity_json TEXT, liquidity_error TEXT)''')
        at = _now()
        db.executemany('''INSERT INTO research_market_states (
            collected_at, chain_id, market_id, lltv_wad, total_supply_assets,
            total_borrow_assets, last_indexed_block, raw_config_json, raw_state_json,
            raw_liquidity_json, liquidity_error) VALUES (?,?,?,?,?,?,?,?,?,?,?)''', [
            (at, BASE_CHAIN, r['market_id'], str(r['config']['lltv_wad']), str(r['supply']), str(r['borrow']),
             None if r['state'].get('last_indexed_block') is None else str(r['state']['last_indexed_block']),
             json.dumps(r['config'], sort_keys=True), json.dumps(r['state'], sort_keys=True),
             None if r['liquidity'] is None else json.dumps(r['liquidity'], sort_keys=True),
             r['liquidity_error']) for r in results])
        db.commit()
    print(f'\nMORPHO BLUE MARKET CONTEXT — {len(results)} unique active market(s) (indexed API, not RPC verified)')
    for r in results:
        util = 'n/a' if r['utilization'] is None else f"{r['utilization']*100:.2f}%"
        print(f"  Market {r['market_id']}")
        print(f"    LLTV {r['lltv']*100:.2f}%  utilization {util}  total supplied {Decimal(r['supply'])/UNIT:,.2f} USDC")
        print(f"    total borrowed {Decimal(r['borrow'])/UNIT:,.2f} USDC; simple unborrowed supply {Decimal(r['idle_cash'])/UNIT:,.2f} USDC")
        print(f"    oracle {r['config'].get('oracle_address','?')}  IRM {r['config'].get('irm_address','?')}")
        if r['liquidity'] is None:
            print(f"    separate liquidity endpoint unavailable: {r['liquidity_error']}")
        else:
            val = r['liquidity'].get('market_liquidity')
            realloc = r['liquidity'].get('reallocatable_liquidity')
            print(f"    reported market liquidity: {'n/a' if val is None else str(Decimal(_uint(val,'market_liquidity'))/UNIT) + ' USDC'}")
            print(f"    separately reallocatable: {'n/a' if realloc is None else str(Decimal(_uint(realloc,'reallocatable_liquidity'))/UNIT) + ' USDC'} (NOT added to cash)")
    print(f'Saved indexed observations in {args.db}. No credit loss model or transaction execution.')
    return results


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description='LVS v0.3 research: offline market overlap, candidate discovery, and market state')
    subs = p.add_subparsers(dest='command', required=True)
    s = subs.add_parser('overlap', help='Show ALL shared markets and per-vault amounts from saved exposures')
    s.add_argument('--capital-usdc', required=True)
    s.add_argument('--allowlist', required=True)
    s.add_argument('--db', default=DEFAULT_DB)
    s = subs.add_parser('candidates', help='Sample listed Base USDC vaults and run preliminary checks')
    s.add_argument('--universe-limit', type=int, default=200, help='Max listed directory entries, cap 500')
    s.add_argument('--inspect-limit', type=int, default=20, help='Detailed HTTP checks, cap 60')
    s.add_argument('--top', type=int, default=15)
    s.add_argument('--db', default=DEFAULT_DB, help='Append candidate observations to SQLite')
    s = subs.add_parser('market-risk', help='Query immutable config and live indexed state for markets in saved vault exposures')
    s.add_argument('--vault', action='append', required=True, help='Repeat for more vault addresses')
    s.add_argument('--max-markets', type=int, default=20)
    s.add_argument('--db', default=DEFAULT_DB)
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == 'overlap':
            overlap(args)
        elif args.command == 'candidates':
            if not 1 <= args.universe_limit <= 500 or not 1 <= args.inspect_limit <= 60 or not 1 <= args.top <= args.inspect_limit:
                raise ValueError('Limits: 1<=universe-limit<=500, 1<=inspect-limit<=60, 1<=top<=inspect-limit')
            discover(args)
        elif args.command == 'market-risk':
            if not 1 <= args.max_markets <= 40:
                raise ValueError('--max-markets must be between 1 and 40')
            market_context(args)
        return 0
    except (ValueError, MorphoAPIError, OSError, sqlite3.Error, json.JSONDecodeError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
