"""Read-only Vault V2 look-through, with explicit limits on what can be inferred.

Amounts remain USDC token base units (integers); cap IDs are *not* holdings.
Nested vault adapters are marked unresolved rather than inventing look-through.
"""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from .models import strict_uint

MAX_API_ITEMS = 100

@dataclass(frozen=True)
class MarketPosition:
    adapter_address: str
    market_id: str
    collateral_symbol: str
    loan_symbol: str
    supply_assets: int

@dataclass(frozen=True)
class OtherAdapter:
    address: str
    kind: str
    assets: int
    destination_address: str | None
    destination_name: str | None

@dataclass(frozen=True)
class ExposureReport:
    vault_address: str
    name: str
    total_assets: int
    idle_assets: int
    adapter_assets: int
    positions: tuple[MarketPosition, ...]
    other_adapters: tuple[OtherAdapter, ...]
    issues: tuple[str, ...]

    def weight(self, assets: int) -> Decimal | None:
        return None if self.total_assets == 0 else Decimal(assets) / Decimal(self.total_assets)

    @property
    def covered_assets(self) -> int:
        return sum(p.supply_assets for p in self.positions)

    @property
    def complete(self) -> bool:
        return not self.issues


def _dict(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{where} must be an object")
    return value


def _items(parent: dict[str, Any], field: str) -> list[dict[str, Any]]:
    child = _dict(parent.get(field), field)
    values = child.get("items")
    if not isinstance(values, list) or any(not isinstance(row, dict) for row in values):
        raise ValueError(f"{field}.items must be a list of objects")
    return values


def normalize_exposures(payload: dict[str, Any], *, expected_address: str) -> ExposureReport:
    """Normalize documented Morpho GraphQL shape; fail on malformed amounts."""
    vault = _dict(payload, 'vault')
    address = vault.get('address')
    if not isinstance(address, str) or address.lower() != expected_address.lower():
        raise ValueError('Exposure response vault address mismatch')
    total = strict_uint(vault.get('totalAssets'), 'totalAssets')
    idle = strict_uint(vault.get('idleAssets'), 'idleAssets')
    if idle > total:
        raise ValueError('idleAssets exceeds totalAssets')
    adapters = _items(vault, 'adapters')
    issues: list[str] = []
    if len(adapters) >= MAX_API_ITEMS:
        issues.append('Adapter list may be truncated (100-item query limit)')
    positions: list[MarketPosition] = []
    other: list[OtherAdapter] = []
    adapter_total = 0
    seen_adapters: set[str] = set()
    for adapter in adapters:
        aaddr = adapter.get('address')
        if not isinstance(aaddr, str) or len(aaddr) != 42 or not aaddr.startswith('0x'):
            raise ValueError('Invalid adapter address')
        if aaddr.lower() in seen_adapters:
            raise ValueError('Duplicate adapter address')
        seen_adapters.add(aaddr.lower())
        assets = strict_uint(adapter.get('assets'), 'adapter.assets')
        adapter_total += assets
        kind = str(adapter.get('__typename') or adapter.get('type') or 'UNKNOWN')
        if kind == 'MorphoMarketV1Adapter':
            rows = _items(adapter, 'positions')
            if len(rows) >= MAX_API_ITEMS:
                issues.append(f'Adapter {aaddr}: positions may be truncated (100-item limit)')
            subtotal = 0
            for item in rows:
                market = _dict(item.get('market'), 'position.market')
                mid = market.get('marketId')
                if not isinstance(mid, str) or not mid:
                    raise ValueError('Missing marketId')
                state = _dict(item.get('state'), 'position.state')
                supply = strict_uint(state.get('supplyAssets'), 'position.state.supplyAssets')
                subtotal += supply
                collateral = market.get('collateralAsset')
                loan = market.get('loanAsset')
                positions.append(MarketPosition(
                    adapter_address=aaddr, market_id=mid,
                    collateral_symbol=(collateral or {}).get('symbol') or 'unknown',
                    loan_symbol=(loan or {}).get('symbol') or 'unknown',
                    supply_assets=supply,
                ))
            if subtotal != assets:
                issues.append(f'Adapter {aaddr}: market holdings do not reconcile with adapter assets')
        else:
            nested = adapter.get('metaMorpho') or adapter.get('innerVault') or {}
            if not isinstance(nested, dict):
                nested = {}
            other.append(OtherAdapter(
                address=aaddr, kind=kind, assets=assets,
                destination_address=nested.get('address'),
                destination_name=nested.get('name'),
            ))
            issues.append(f'Adapter {aaddr}: {kind} requires separate look-through')
    if idle + adapter_total != total:
        issues.append('Idle + reported adapter holdings do not exactly reconcile with vault total assets')
    return ExposureReport(
        vault_address=address, name=str(vault.get('name') or address),
        total_assets=total, idle_assets=idle, adapter_assets=adapter_total,
        positions=tuple(positions), other_adapters=tuple(other), issues=tuple(issues),
    )
