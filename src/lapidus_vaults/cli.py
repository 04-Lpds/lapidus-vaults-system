"""Run `python -m lapidus_vaults --help` after adding src to PYTHONPATH."""
from __future__ import annotations
import argparse
import json
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import sys
from .client import MorphoClient, MorphoAPIError
from .models import VaultSnapshot
from .exposures import normalize_exposures
from .portfolio import calculate_lookthrough
from .storage import SnapshotStore
from .allocator import BASE_USDC, BASE_CHAIN, assess_vault, capital_to_units, propose, display_usdc, fraction, address_ok

DEFAULT_DB='data/lapidus.sqlite3'

def format_pct(numerator: str, denominator: str) -> str:
    total = int(denominator)
    return 'n/a' if total == 0 else f'{(Decimal(numerator)/Decimal(total)*100):.1f}%'

def parser() -> argparse.ArgumentParser:
    p=argparse.ArgumentParser(description='Lapidus Vault Systems: Morpho V2 read-only data collection')
    s=p.add_subparsers(dest='command', required=True)
    for cmd in ('list','collect'):
        q=s.add_parser(cmd)
        q.add_argument('--chain',type=int,default=8453,help='EVM chain ID, default 8453 (Base)')
        q.add_argument('--limit',type=int,default=5,help='Max vaults; public listed-vault directory')
        q.add_argument('--asset-address',help='Optional exact ERC-20 asset address filter')
        if cmd=='collect':q.add_argument('--db',default=DEFAULT_DB)
    r=s.add_parser('report');r.add_argument('--db',default=DEFAULT_DB)
    screen = s.add_parser('screen', help='Read-only expanded state/config/APY checks for USDC vaults')
    screen.add_argument('--chain', type=int, default=BASE_CHAIN)
    screen.add_argument('--limit', type=int, default=5)
    screen.add_argument('--asset-address', default=BASE_USDC)
    screen.add_argument('--db', default=DEFAULT_DB, help='Store screened observations in SQLite')
    plan = s.add_parser('plan', help='Hypothetical allocation across manually allowlisted vaults; NO transactions')
    plan.add_argument('--capital-usdc', required=True, help='Hypothetical USDC budget, e.g. 200')
    plan.add_argument('--allowlist', required=True, help='Local JSON specifying manually reviewed vault addresses')
    plan.add_argument('--limit', type=int, default=100, help='How many listed USDC vaults to discover')
    plan.add_argument('--db', default=DEFAULT_DB, help='Store screened observations in SQLite')
    exp = s.add_parser('exposures', help='Read-only Vault V2 adapter and underlying market holdings')
    exp.add_argument('--vault', required=True, action='append', help='Repeat for a few selected vault addresses')
    exp.add_argument('--chain', type=int, default=BASE_CHAIN)
    exp.add_argument('--db', default=DEFAULT_DB)
    look = s.add_parser('portfolio-exposures', help='Analyze saved paper plan against stored vault exposures; offline')
    look.add_argument('--plan-id', type=int, default=None, help='Saved paper plan ID; defaults to most recent')
    look.add_argument('--max-age-hours', type=int, default=48, help='Reject exposure observations older than this (default 48)')
    look.add_argument('--db', default=DEFAULT_DB)
    return p

def _print_exposures(args: argparse.Namespace) -> int:
    if args.chain != BASE_CHAIN:
        raise ValueError('Initial exposure prototype supports Base only')
    if len(args.vault) > 10:
        raise ValueError('For initial validation, request no more than 10 vaults per call')
    if not all(address_ok(a) for a in args.vault):
        raise ValueError('All --vault arguments must be EVM addresses')
    with MorphoClient() as client, SnapshotStore(args.db) as store:
        for addr in args.vault:
            raw = client.v2_exposures(args.chain, addr)
            report = normalize_exposures(raw, expected_address=addr)
            store.insert_exposures(chain_id=args.chain, report=report, raw_data=raw)
            print(f'\nVAULT: {report.name}  {report.vault_address}')
            print(f'Total assets (USDC base units): {report.total_assets}')
            print(f'Idle assets: {report.idle_assets}')
            print(f'Adapter assets: {report.adapter_assets}')
            print('Markets (positions, not risk caps):')
            if not report.positions:
                print('  No direct Morpho Blue market positions returned')
            for position in sorted(report.positions, key=lambda p: p.supply_assets, reverse=True):
                weight = report.weight(position.supply_assets)
                pct = 'n/a' if weight is None else f'{weight * 100:.2f}%'
                print(f'  {position.collateral_symbol:>10} collateral | '
                      f'{position.loan_symbol:>5} loan | {pct:>8} | '
                      f'{position.market_id}')
            for other in report.other_adapters:
                print(f'  LOOK-THROUGH REQUIRED: {other.kind} {other.address} -> '
                      f'{other.destination_name or other.destination_address or "unidentified"}')
            print(f'Direct-market coverage: '
                  f'{"n/a" if report.weight(report.covered_assets) is None else str(round(report.weight(report.covered_assets)*100,2)) + "%"}')
            if report.issues:
                print('INCOMPLETE / CHECK:')
                for issue in report.issues:
                    print(f'  - {issue}')
            else:
                print('Reported market holdings reconciled; RPC freshness not checked.')
        print(f'Exposure observations stored in {args.db}')
    return 0

def _portfolio_exposures(args: argparse.Namespace) -> int:
    """Purely offline look-through of the latest (or requested) saved paper plan."""
    if not Path(args.db).exists():
        raise ValueError(f'No existing SQLite database at {args.db}; run exposures and plan first')
    with SnapshotStore(args.db) as store:
        plan = store.get_paper_plan(args.plan_id)
        sources = store.load_latest_exposures_for_plan(plan, max_age_hours=args.max_age_hours)
        report = calculate_lookthrough(plan, sources)
        analysis_id = store.insert_portfolio_analysis(report)

    def money(units: Decimal | int) -> str:
        return f'{Decimal(units) / Decimal(1_000_000):,.6f}'

    def pct(units: Decimal | int, denom: Decimal | int) -> str:
        return 'n/a' if not denom else f'{(Decimal(units) / Decimal(denom) * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP):.2f}%'

    print(f'\nLAPIDUS — PAPER PORTFOLIO LOOK-THROUGH (plan #{plan.id}, analysis #{analysis_id})')
    print(f'  Capital:                     {money(plan.capital_units)} USDC')
    print(f'  Wallet idle:                 {money(report.wallet_idle_units)} USDC')
    print(f'  USDC idle inside vaults:     {money(report.vault_internal_idle_units)} USDC')
    print(f'  In underlying lending markets: {money(report.market_exposure_units)} USDC')
    print(f'  TOTAL:                       {money(report.wallet_idle_units + report.vault_internal_idle_units + report.market_exposure_units)} USDC')
    print('\nMARKET EXPOSURES (from saved snapshots):')
    if not report.markets:
        print('  No underlying market positions')
    for market in report.markets:
        print(f'  {market.collateral_symbol:>10} / {market.loan_symbol:<5} '
              f'{money(market.exposure_units):>15} USDC  '
              f'{pct(market.exposure_units, plan.capital_units):>8} portfolio  '
              f'{pct(market.exposure_units, report.market_exposure_units):>8} markets  '
              f'{len(market.vault_addresses)} vault(s)  {market.market_id}')
    print('\nCOLLATERAL-BACKED LENDING EXPOSURES (not collateral owned):')
    for symbol, assets in report.collateral_exposures:
        print(f'  {symbol:<12} {money(assets):>15} USDC ({pct(assets, plan.capital_units)} of portfolio)')
    hhi = report.hhi_deployed_markets
    print(f'\n  Underlying-market HHI (among lending positions): '
          f'{"n/a (no lending positions)" if hhi is None else f"{hhi:.4f}"}')
    if report.markets:
        largest = report.markets[0]
        if len(largest.vault_addresses) > 1:
            print(f'  SHARED MARKET: {len(largest.vault_addresses)} vaults contribute to {largest.market_id}')
    print(f'  Source exposure snapshot IDs: {report.source_snapshot_ids}')
    print('  PAPER ONLY. Snapshots may be from different times; not RPC-verified. '
          'HHI measures concentration, NOT credit loss or probability of default.')
    print(f'  Saved analysis to {args.db}')
    return 0

def _screen_or_plan(args: argparse.Namespace) -> int:
    from .allocator import Assessment
    if args.command == 'plan':
        policy = json.loads(Path(args.allowlist).read_text(encoding='utf-8'))
        if policy.get('chain_id') != BASE_CHAIN or (policy.get('asset_address') or '').lower() != BASE_USDC:
            raise ValueError('Only Base mainnet USDC is supported by this demonstration planner')
        approved = policy.get('approved_vault_addresses')
        if not isinstance(approved, list):
            raise ValueError('allowlist must include approved_vault_addresses as a list')
        if not all(isinstance(a, str) and address_ok(a) for a in approved):
            raise ValueError('Invalid address in approved_vault_addresses')
        # A zero-entry allowlist safely produces a 100% idle portfolio.
        idle_target = fraction(policy.get('idle_fraction', '0.20'), 'idle_fraction')
        max_vault = fraction(policy.get('per_vault_max_fraction', '0.40'), 'per_vault_max_fraction')
        capital = capital_to_units(args.capital_usdc)
        chain = BASE_CHAIN; asset = BASE_USDC
    else:
        chain = args.chain; asset = args.asset_address
        approved = []
    if chain != BASE_CHAIN or not isinstance(asset, str) or asset.lower() != BASE_USDC:
        raise ValueError('Initial screening MVP supports Base USDC only')
    assessments: list[Assessment] = []
    with MorphoClient() as client, SnapshotStore(args.db) as store:
        vaults = client.listed_v2_vaults(chain, limit=args.limit, asset_address=asset)
        # Fetch state/config/realized APY individually; incomplete reads are not assumed eligible.
        for vault in vaults:
            addr = vault['vault_address']
            try:
                state = client.v2_state(chain, addr)
                config = client.v2_config(chain, addr)
                apy = client.v2_apy(chain, addr)
                assessment = assess_vault(listing=vault, state=state, config=config, apy=apy, chain_id=chain, token_address=asset)
            except (MorphoAPIError, ValueError, KeyError, TypeError) as exc:
                print(f'REVIEW  {str(vault.get("name") or addr)[:28]:28}  Incomplete data: {exc}')
                continue
            assessments.append(assessment)
            store.insert_screening(assessment=assessment, listing=vault, state=state, config=config, apy=apy)
            status = 'PRELIM' if assessment.preliminary_pass else 'REVIEW'
            apy_display = 'n/a' if assessment.apy is None else f'{assessment.apy * 100:.2f}%'
            liq_display = 'n/a' if assessment.withdrawable_fraction is None else f'{assessment.withdrawable_fraction * 100:.1f}%'
            print(f'{status:6}  {assessment.name[:28]:28}  7d APY {apy_display:>8}  withdrawable {liq_display:>6}  {addr}')
            if assessment.problems:
                for issue in assessment.problems:
                    print(f'        - {issue}')
    print(f'Screening snapshots saved to {args.db} (vault_screenings).')
    print('PRELIM is a mechanical data check, NOT vault due diligence or approval. RPC freshness unverified.')
    if args.command == 'plan':
        allocations, idle = propose(capital, assessments, approved_addresses=approved, idle_fraction=idle_target, per_vault_max_fraction=max_vault)
        names = {row.vault_address.lower(): row.name for row in assessments}
        with SnapshotStore(args.db) as store:
            paper_plan_id = store.insert_paper_plan(
                capital_units=capital, wallet_idle_units=idle,
                allocations=allocations, names=names, policy=policy,
            )
        print(f'\nHYPOTHETICAL EQUAL-WEIGHT PLAN for {display_usdc(capital)} USDC (no transaction execution):')
        for addr, amount in allocations.items():
            print(f'  {names.get(addr.lower(), addr)} ({addr[:10]}...): '
                  f'{display_usdc(amount)} USDC ({Decimal(amount) / Decimal(capital) * 100:.1f}%)')
        allocated = sum(allocations.values())
        if allocated + idle != capital:
            raise ValueError('Internal accounting error: allocations + idle != capital')
        print(f'  ALLOCATED USDC: {display_usdc(allocated)}')
        print(f'  IDLE USDC: {display_usdc(idle)}')
        print(f'  TOTAL PORTFOLIO: {display_usdc(allocated + idle)} USDC (100.0%)')
        print(f'  Saved paper plan #{paper_plan_id} to {args.db}')
        print('No portfolio balances, fees, gas, stress risk, contracts, or executable withdrawal limits are modeled yet.')
    return 0

def main(argv: list[str]|None=None) -> int:
    args=parser().parse_args(argv)
    try:
        if args.command=='report':
            if not Path(args.db).exists():
                print(f'No database at {args.db}. Run collect first.',file=sys.stderr);return 2
            with SnapshotStore(args.db) as db:
                rows=db.latest()
            if not rows:print('No snapshots yet.');return 0
            print(f'{"CHAIN":<7} {"NAME":<29} {"ADDRESS":<14} {"IDLE":>7} {"WITHDRAW":>9} {"INDEX BLOCK":>13}')
            for row in rows:
                print(f'{row["chain_id"]:<7} {row["name"][:29]:<29} {row["vault_address"][:12]:<14} '
                    f'{format_pct(row["idle_assets"],row["total_assets"]):>7} '
                    f'{format_pct(row["withdrawable_assets"],row["total_assets"]):>9} {row["source_block"]:>13}')
            print('\nPercentages are of raw underlying assets. No guarantee of instant or executable exit.')
            print('Block lag NOT verified against RPC yet; see rust/chain-worker.')
            return 0
        if args.command == 'exposures':
            return _print_exposures(args)
        if args.command == 'portfolio-exposures':
            return _portfolio_exposures(args)
        if args.command in ('screen','plan'):
            return _screen_or_plan(args)
        with MorphoClient() as c:
            vaults=c.listed_v2_vaults(args.chain,limit=args.limit,asset_address=args.asset_address)
            if not vaults:print('No listed V2 vaults matched the filters.');return 0
            print(f'Found {len(vaults)} listed V2 vaults; chain {args.chain}.')
            if args.command=='list':
                for v in vaults:
                    print(f'{v.get("name", "(unnamed)")[:35]:35} {v["vault_address"]} {v.get("asset_address", "")}')
                return 0
            with SnapshotStore(args.db) as store:
                for v in vaults:
                    addr=v['vault_address'];data=c.v2_state(args.chain,addr)
                    snap=VaultSnapshot.from_api(data,name=v.get('name') or '',asset_address=v.get('asset_address') or '')
                    if snap.chain_id!=args.chain or snap.vault_address.lower()!=addr.lower():
                        raise ValueError('API state identity mismatch')
                    store.insert(snap,data)
                    print(f'Saved: {snap.name or snap.vault_address} at index block {snap.source_block}')
            print(f'Collector completed. Database: {args.db}; run: python -m lapidus_vaults report --db {args.db}')
            return 0
    except (MorphoAPIError, ValueError, OSError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1

if __name__=='__main__':raise SystemExit(main())
