"""Read-only, deliberately conservative capital allocation *proposal*.

NOT: a risk model, investment recommendation, transaction planner, or vault approval.
A separately reviewed allowlist is mandatory; the engine never signs or submits.
"""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from typing import Any
import math
from .models import VaultSnapshot, strict_uint

BASE_USDC = '0x833589fcd6edb6e08f4c7c32d4f71b54bda02913'
BASE_CHAIN = 8453
UNIT = Decimal('1000000')


def address_ok(address: Any) -> bool:
    return isinstance(address, str) and len(address) == 42 and address.startswith('0x') and all(c in '0123456789abcdefABCDEF' for c in address[2:])


def fraction(value: Any, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ValueError(f'{field} must be a decimal string, int or Decimal')
    try:
        val = Decimal(str(value))
    except InvalidOperation as e:
        raise ValueError(f'{field} invalid decimal') from e
    if not val.is_finite() or val < 0 or val > 1:
        raise ValueError(f'{field} must be between 0 and 1')
    return val


def capital_to_units(value: str) -> int:
    try:
        dec = Decimal(value)
    except InvalidOperation as e:
        raise ValueError('Invalid USDC amount') from e
    if not dec.is_finite() or dec <= 0 or dec.as_tuple().exponent < -6:
        raise ValueError('Capital must be positive USDC with at most six decimal places')
    units = dec * UNIT
    if units != units.to_integral_value():
        raise ValueError('USDC value cannot represent a base-unit integer')
    return int(units)


@dataclass(frozen=True)
class Assessment:
    name: str
    chain_id: int
    vault_address: str
    source_block: int
    total_assets: int
    withdrawable_fraction: Decimal | None
    apy: Decimal | None
    problems: tuple[str, ...]

    @property
    def preliminary_pass(self) -> bool:
        return not self.problems


def assess_vault(*, listing: dict[str, Any], state: dict[str, Any], config: dict[str, Any], apy: dict[str, Any],
                 chain_id: int = BASE_CHAIN, token_address: str = BASE_USDC,
                 min_withdrawable_fraction: Decimal = Decimal('0.10'), max_api_block_spread: int = 100) -> Assessment:
    """Mechanical data/eligibility checks. Cannot establish smart-contract or economic safety."""
    addr = listing.get('vault_address')
    if not address_ok(addr):
        raise ValueError('Listing returned invalid vault address')
    issues: list[str] = []
    snap = VaultSnapshot.from_api(state, name=listing.get('name') or '', asset_address=listing.get('asset_address') or '')
    if snap.chain_id != chain_id or snap.vault_address.lower() != addr.lower():
        issues.append('State identity mismatch')
    if (listing.get('asset_address') or '').lower() != token_address.lower():
        issues.append('Listing asset mismatch')

    try:
        conf_chain = strict_uint(config['chain_id'], 'config.chain_id')
        conf_addr = config['address']
        asset = config['asset']
        conf_token = asset['address']
        decimals = strict_uint(asset['decimals'], 'asset.decimals')
        conf_block = strict_uint(config['last_indexed_block'], 'config.last_indexed_block')
        if conf_chain != chain_id or not address_ok(conf_addr) or conf_addr.lower() != addr.lower():
            issues.append('Configuration identity mismatch')
        if not address_ok(conf_token) or conf_token.lower() != token_address.lower() or decimals != 6:
            issues.append('Configuration underlying token mismatch')
        if abs(conf_block - snap.source_block) > max_api_block_spread:
            issues.append('Configuration/state index blocks diverge')
        gates = config['gates']
        if not isinstance(gates, dict) or any(gates.get(k, 'missing') is not None for k in ('send_shares','receive_shares','send_assets','receive_assets')):
            issues.append('Gate configured or gate metadata unavailable')
    except (KeyError, TypeError, ValueError) as exc:
        issues.append(f'Incomplete/invalid vault configuration: {exc}')

    parsed_apy: Decimal | None = None
    try:
        apy_chain = strict_uint(apy['chain_id'], 'apy.chain_id')
        apy_addr = apy['vault_address']
        apy_block = strict_uint(apy['last_indexed_block'], 'apy.last_indexed_block')
        if apy_chain != chain_id or not address_ok(apy_addr) or apy_addr.lower() != addr.lower():
            issues.append('APY identity mismatch')
        if abs(apy_block - snap.source_block) > max_api_block_spread:
            issues.append('APY/state index blocks diverge')
        if apy.get('lookback') != 'seven_days':
            issues.append('Unexpected APY lookback')
        apy_raw = apy['apy']
        if isinstance(apy_raw, bool) or not isinstance(apy_raw, (float, str, int)):
            raise ValueError('APY missing/non-numeric')
        parsed_apy = Decimal(str(apy_raw))
        if not parsed_apy.is_finite() or parsed_apy < -1 or parsed_apy > Decimal('0.5'):
            issues.append('APY anomalous or outside initial review range')
    except (KeyError, InvalidOperation, TypeError, ValueError) as exc:
        issues.append(f'Invalid APY data: {exc}')
    fraction_withdrawable = snap.withdrawable_fraction
    if fraction_withdrawable is None or fraction_withdrawable < min_withdrawable_fraction:
        issues.append('Reported withdrawal liquidity below screen threshold')
    if snap.total_assets == 0:
        issues.append('Empty vault')
    return Assessment(snap.name, snap.chain_id, snap.vault_address, snap.source_block,
                      snap.total_assets, fraction_withdrawable, parsed_apy, tuple(issues))


def propose(capital_units: int, assessments: list[Assessment], *, approved_addresses: list[str],
            idle_fraction: Decimal = Decimal('0.20'), per_vault_max_fraction: Decimal = Decimal('0.40')) -> tuple[dict[str, int], int]:
    """Equal-weight, capped, exact-unit hypothetical allocation. No APY optimization yet."""
    if capital_units <= 0:
        raise ValueError('capital_units must be positive')
    fraction(idle_fraction, 'idle_fraction')
    fraction(per_vault_max_fraction, 'per_vault_max_fraction')
    normalized = [a.lower() for a in approved_addresses]
    if len(normalized) != len(set(normalized)) or not all(address_ok(a) for a in normalized):
        raise ValueError('Duplicate or invalid allowlisted vault addresses')
    screened = {a.vault_address.lower(): a for a in assessments}
    selected = []
    for a in normalized:
        if a not in screened:
            raise ValueError('Approved vault not present in current screening results')
        if not screened[a].preliminary_pass:
            raise ValueError(f'Approved vault failed preliminary screen: {a}: {screened[a].problems}')
        selected.append(a)
    if not selected:
        return {}, capital_units
    # Round down every allocation to base units and retain remainder as idle USDC.
    deploy_budget = int((Decimal(capital_units) * (1 - idle_fraction)).to_integral_value(rounding=ROUND_DOWN))
    cap = int((Decimal(capital_units) * per_vault_max_fraction).to_integral_value(rounding=ROUND_DOWN))
    equal = deploy_budget // len(selected)
    per = min(cap, equal)
    allocations = {a: per for a in sorted(selected)}
    idle = capital_units - sum(allocations.values())
    return allocations, idle


def display_usdc(raw_units: int) -> str:
    return f'{Decimal(raw_units) / UNIT:,.6f}'
