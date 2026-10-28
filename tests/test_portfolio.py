"""Paper-only look-through integration and failure-mode checks (no network)."""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from datetime import datetime, timezone, timedelta
import json

import pytest

from lapidus_vaults import cli
from lapidus_vaults.allocator import BASE_USDC
from lapidus_vaults.exposures import ExposureReport, MarketPosition
from lapidus_vaults.portfolio import PaperPlan, PaperAllocation, calculate_lookthrough
from lapidus_vaults.storage import SnapshotStore

A = '0x' + 'a' * 40
B = '0x' + 'b' * 40
ADAPTER = '0x' + 'c' * 40
BTC = '0x' + 'd' * 64
ETH = '0x' + 'e' * 64


def report(address: str, *, btc: int, eth: int = 0, idle: int = 0, issues=()):
    positions = (MarketPosition(ADAPTER, BTC, 'cbBTC', 'USDC', btc),
                 MarketPosition(ADAPTER, ETH, 'WETH', 'USDC', eth))
    return ExposureReport(address, 'Gauntlet' if address == A else 'Galaxy',
                          btc+eth+idle, idle, btc+eth, positions, (), tuple(issues))


def plan(a=80_000_000, b=80_000_000, idle=40_000_000):
    return PaperPlan(1, 8453, BASE_USDC, a+b+idle, idle,
                     (PaperAllocation(A, 'Gauntlet', a), PaperAllocation(B, 'Galaxy', b)))


def sources():
    # Gauntlet: 93.29% BTC-backed, Galaxy: 100% BTC-backed.
    return {A: (11, report(A, btc=93_290_000, eth=6_710_000)),
            B: (12, report(B, btc=100_000_000))}


def test_two_vaults_shared_market_exact_proportions():
    result = calculate_lookthrough(plan(), sources())
    assert result.market_exposure_units == Decimal(160_000_000)
    assert result.wallet_idle_units == 40_000_000
    assert result.vault_internal_idle_units == 0
    assert result.markets[0].market_id == BTC
    assert result.markets[0].exposure_units == Decimal(154_632_000)
    assert result.markets[0].vault_addresses == (A, B)
    assert result.markets[1].exposure_units == Decimal(5_368_000)
    assert result.collateral_exposures == (('cbBTC', Decimal(154_632_000)), ('WETH', Decimal(5_368_000)))
    assert result.hhi_deployed_markets > Decimal('0.93')
    assert result.source_snapshot_ids == {A: 11, B: 12}


def test_idleness_inside_vault_is_not_wallet_idle():
    observed = {A: (11, report(A, btc=90_000_000, idle=10_000_000)),
                B: (12, report(B, btc=100_000_000))}
    result = calculate_lookthrough(plan(), observed)
    assert result.vault_internal_idle_units == Decimal(8_000_000)
    assert result.market_exposure_units == Decimal(152_000_000)
    assert result.wallet_idle_units == 40_000_000
    assert (result.market_exposure_units + result.vault_internal_idle_units + result.wallet_idle_units
            == result.capital_units)


def test_plan_no_vaults_is_all_wallet_idle():
    blank = PaperPlan(2, 8453, BASE_USDC, 200_000_000, 200_000_000, ())
    result = calculate_lookthrough(blank, {})
    assert result.market_exposure_units == 0
    assert result.hhi_deployed_markets is None
    assert not result.markets
    assert result.source_snapshot_ids == {}


@pytest.mark.parametrize('bad_sources, expected', [
    ({A: sources()[A]}, 'Missing underlying'),
    ({A: (11, report(A, btc=100_000_000, issues=('Nested adapter',))), B: sources()[B]}, 'Incomplete'),
    ({A: (11, replace(sources()[A][1], total_assets=99_000_000)), B: sources()[B]}, 'do not reconcile'),
    ({A: (11, replace(sources()[A][1], vault_address=B)), B: sources()[B]}, 'identity'),
])
def test_fail_closed_on_missing_or_bad_exposure(bad_sources, expected):
    with pytest.raises(ValueError, match=expected):
        calculate_lookthrough(plan(), bad_sources)


def test_fails_on_accounting_inconsistency_and_duplicate_allocations():
    bad = replace(plan(), wallet_idle_units=30_000_000)
    with pytest.raises(ValueError, match='do not reconcile'):
        calculate_lookthrough(bad, sources())
    bad2 = replace(plan(), allocations=(PaperAllocation(A, 'A', 80_000_000), PaperAllocation(A, 'B', 80_000_000)))
    with pytest.raises(ValueError, match='Duplicate paper allocation'):
        calculate_lookthrough(bad2, sources())


def test_conflicting_market_labels_are_rejected():
    b = report(B, btc=100_000_000)
    wrong = replace(b, positions=(MarketPosition(ADAPTER, BTC, 'WETH', 'USDC', 100_000_000),))
    with pytest.raises(ValueError, match='Conflicting token metadata'):
        calculate_lookthrough(plan(), {A: sources()[A], B: (12, wrong)})


def test_database_persists_plan_and_exposure_analysis(tmp_path):
    db = tmp_path / 'portfolio.sqlite3'
    with SnapshotStore(db) as store:
        for address, (_, r) in sources().items():
            store.insert_exposures(chain_id=8453, report=r, raw_data={'mock': address})
        policy = {'asset_address': BASE_USDC, 'idle_fraction': '0.20'}
        pid = store.insert_paper_plan(capital_units=200_000_000, wallet_idle_units=40_000_000,
                                      allocations={A: 80_000_000, B: 80_000_000},
                                      names={A: 'Gauntlet', B: 'Galaxy'}, policy=policy)
        loaded_plan = store.get_paper_plan()
        assert loaded_plan.id == pid
        fetched = store.load_latest_exposures_for_plan(loaded_plan)
        assert set(fetched) == {A, B}
        assert fetched[A][0] == 1 and fetched[B][0] == 2
        result = calculate_lookthrough(loaded_plan, fetched)
        analysis_id = store.insert_portfolio_analysis(result)
        result_saved = store.conn.execute('SELECT * FROM portfolio_exposure_analyses WHERE id=?', (analysis_id,)).fetchone()
        assert result_saved['plan_id'] == pid
        assert Decimal(result_saved['market_exposure_units']) == Decimal(160_000_000)
        assert json.loads(result_saved['source_snapshot_ids_json']) == {A: 1, B: 2}
        rows = store.conn.execute('SELECT market_id, exposure_units FROM portfolio_market_exposures WHERE analysis_id=? ORDER BY exposure_units DESC', (analysis_id,)).fetchall()
        assert {r['market_id']: Decimal(r['exposure_units']) for r in rows} == {BTC: Decimal(154_632_000), ETH: Decimal(5_368_000)}
        assert store.get_paper_plan(pid).capital_units == 200_000_000
    with SnapshotStore(db) as reopened:
        assert reopened.get_paper_plan(pid).wallet_idle_units == 40_000_000


def test_database_rejects_stale_exposure_source(tmp_path):
    with SnapshotStore(tmp_path / 'db.sqlite3') as store:
        r = report(A, btc=100_000_000)
        store.insert_exposures(chain_id=8453, report=r, raw_data={'mock': True})
        stale = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        store.conn.execute('UPDATE exposure_snapshots SET observed_at=?', (stale,))
        store.conn.commit()
        store.insert_paper_plan(capital_units=200_000_000, wallet_idle_units=120_000_000,
                                allocations={A: 80_000_000}, names={A: 'A'}, policy={})
        with pytest.raises(ValueError, match='too old'):
            store.load_latest_exposures_for_plan(store.get_paper_plan(), max_age_hours=48)
        assert A in store.load_latest_exposures_for_plan(store.get_paper_plan(), max_age_hours=96)


def test_portfolio_cli_output_and_saved_provenance(tmp_path, capsys):
    db = tmp_path / 'paper.sqlite3'
    with SnapshotStore(db) as store:
        for addr, (_, r) in sources().items():
            store.insert_exposures(chain_id=8453, report=r, raw_data={'mock': addr})
        store.insert_paper_plan(capital_units=200_000_000, wallet_idle_units=40_000_000,
                                allocations={A: 80_000_000, B: 80_000_000},
                                names={A: 'Gauntlet', B: 'Galaxy'}, policy={})
    assert cli.main(['portfolio-exposures', '--db', str(db)]) == 0
    output = capsys.readouterr().out
    assert '154.632000 USDC' in output
    assert '77.32%' in output
    assert '96.65%' in output
    assert '200.000000 USDC' in output
    assert 'SHARED MARKET: 2 vaults' in output
    with SnapshotStore(db) as store:
        assert store.conn.execute('SELECT COUNT(*) FROM portfolio_exposure_analyses').fetchone()[0] == 1
    assert cli.main(['portfolio-exposures', '--db', str(db), '--plan-id', '999']) == 1
    assert cli.main(['portfolio-exposures', '--db', str(db), '--max-age-hours', '0']) == 1


def test_portfolio_command_no_database_does_not_create_one(tmp_path):
    path = tmp_path / 'no_db.sqlite3'
    assert cli.main(['portfolio-exposures', '--db', str(path)]) == 1
    assert not path.exists()
