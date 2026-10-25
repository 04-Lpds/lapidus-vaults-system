"""Exact, read-only look-through of a *hypothetical* vault portfolio.

This computes ownership-weighted exposure to underlying USDC lending positions;
it does NOT model defaults, mark-to-market PnL, withdrawals, or portfolio safety.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Mapping

from .allocator import BASE_CHAIN, BASE_USDC, address_ok
from .exposures import ExposureReport


@dataclass(frozen=True)
class PaperAllocation:
    vault_address: str
    vault_name: str
    amount_units: int


@dataclass(frozen=True)
class PaperPlan:
    id: int
    chain_id: int
    asset_address: str
    capital_units: int
    wallet_idle_units: int
    allocations: tuple[PaperAllocation, ...]


@dataclass(frozen=True)
class UnderlyingMarket:
    market_id: str
    collateral_symbol: str
    loan_symbol: str
    exposure_units: Decimal
    vault_addresses: tuple[str, ...]


@dataclass(frozen=True)
class LookthroughResult:
    plan_id: int
    capital_units: int
    wallet_idle_units: int
    vault_internal_idle_units: Decimal
    market_exposure_units: Decimal
    markets: tuple[UnderlyingMarket, ...]
    collateral_exposures: tuple[tuple[str, Decimal], ...]
    hhi_deployed_markets: Decimal | None
    source_snapshot_ids: dict[str, int]


def calculate_lookthrough(
    plan: PaperPlan,
    reports: Mapping[str, tuple[int, ExposureReport]],
) -> LookthroughResult:
    """Aggregate underlying market holdings across vaults, preserving decimals.

    Mapping keys are lowercase vault addresses. Each report has its stored
    exposure_snapshot ID so downstream results refer to exact source rows.
    Fail closed on missing, incomplete or irreconcilable source observations.
    Freshness checks are performed by the database-loading layer.
    """
    if plan.chain_id != BASE_CHAIN or plan.asset_address.lower() != BASE_USDC:
        raise ValueError('Only Base USDC paper portfolios are supported')
    if plan.capital_units <= 0 or plan.wallet_idle_units < 0:
        raise ValueError('Invalid paper portfolio capital/idle units')
    if any(not isinstance(x.amount_units, int) or x.amount_units < 0 or not address_ok(x.vault_address) for x in plan.allocations):
        raise ValueError('Invalid paper allocation')
    if sum(a.amount_units for a in plan.allocations) + plan.wallet_idle_units != plan.capital_units:
        raise ValueError('Paper plan allocations plus wallet idle do not reconcile')

    used: set[str] = set()
    # market ID -> [collateral symbol, loan symbol, weighted assets, vault addresses]
    markets: dict[str, list] = {}
    source_ids: dict[str, int] = {}
    with localcontext() as ctx:
        ctx.prec = 80
        internal_idle = Decimal(0)
        for allocation in plan.allocations:
            address = allocation.vault_address.lower()
            if address in used:
                raise ValueError(f'Duplicate paper allocation: {address}')
            used.add(address)
            if allocation.amount_units == 0:
                continue
            if address not in reports:
                raise ValueError(f'Missing underlying exposure snapshot for {address}; run exposures --vault ADDRESS')
            snapshot_id, report = reports[address]
            if report.vault_address.lower() != address or snapshot_id <= 0:
                raise ValueError(f'Incorrect exposure snapshot identity for {address}')
            if report.issues:
                raise ValueError(f'Incomplete exposure snapshot for {allocation.vault_name}: {"; ".join(report.issues)}')
            if report.total_assets <= 0 or report.idle_assets < 0:
                raise ValueError(f'Invalid vault assets in exposure snapshot for {address}')
            if report.idle_assets + sum(p.supply_assets for p in report.positions) != report.total_assets:
                raise ValueError(f'Market holdings and idle assets do not reconcile for {address}')
            source_ids[address] = snapshot_id
            fraction = Decimal(allocation.amount_units) / Decimal(report.total_assets)
            internal_idle += Decimal(report.idle_assets) * fraction
            for position in report.positions:
                if position.supply_assets < 0:
                    raise ValueError(f'Negative market position for {address}')
                if position.supply_assets == 0:
                    continue
                mid = position.market_id.lower()
                if not (mid.startswith('0x') and len(mid) == 66 and all(c in '0123456789abcdef' for c in mid[2:])):
                    raise ValueError(f'Invalid Morpho market ID for {address}')
                amount = Decimal(position.supply_assets) * fraction
                if mid not in markets:
                    markets[mid] = [position.collateral_symbol, position.loan_symbol, Decimal(0), set()]
                info = markets[mid]
                if (info[0].lower(), info[1].lower()) != (position.collateral_symbol.lower(), position.loan_symbol.lower()):
                    raise ValueError(f'Conflicting token metadata for shared market {mid}')
                info[2] += amount
                info[3].add(address)

        entries = tuple(sorted((UnderlyingMarket(
            market_id=mid, collateral_symbol=data[0], loan_symbol=data[1],
            exposure_units=data[2], vault_addresses=tuple(sorted(data[3])),
        ) for mid, data in markets.items()), key=lambda x: (-x.exposure_units, x.market_id)))
        market_total = sum((entry.exposure_units for entry in entries), Decimal(0))
        if abs(market_total + internal_idle + Decimal(plan.wallet_idle_units) - Decimal(plan.capital_units)) > Decimal('0.00000001'):
            raise ValueError('Derived look-through exposures do not reconcile to paper portfolio capital')
        collaterals: dict[str, Decimal] = {}
        for entry in entries:
            collaterals[entry.collateral_symbol] = collaterals.get(entry.collateral_symbol, Decimal(0)) + entry.exposure_units
        collateral_rows = tuple(sorted(collaterals.items(), key=lambda row: (-row[1], row[0])))
        hhi = (sum(((e.exposure_units / market_total) ** 2 for e in entries), Decimal(0))
               if market_total > 0 else None)
        return LookthroughResult(
            plan_id=plan.id, capital_units=plan.capital_units, wallet_idle_units=plan.wallet_idle_units,
            vault_internal_idle_units=internal_idle, market_exposure_units=market_total,
            markets=entries, collateral_exposures=collateral_rows,
            hhi_deployed_markets=hhi, source_snapshot_ids=source_ids,
        )
