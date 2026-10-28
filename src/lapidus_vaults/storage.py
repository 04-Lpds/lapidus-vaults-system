"""Append-only SQLite data. Raw JSON retained to permit API-schema reprocessing."""
from __future__ import annotations
from pathlib import Path
import json
import sqlite3
from dataclasses import asdict
from typing import Any
from .models import VaultSnapshot

SCHEMA = """
CREATE TABLE IF NOT EXISTS vault_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observed_at TEXT NOT NULL,
    chain_id INTEGER NOT NULL,
    vault_address TEXT NOT NULL,
    name TEXT NOT NULL,
    asset_address TEXT NOT NULL,
    source_block INTEGER NOT NULL,
    total_assets TEXT NOT NULL,
    total_supply TEXT NOT NULL,
    withdrawable_assets TEXT NOT NULL,
    allocated_assets TEXT NOT NULL,
    idle_assets TEXT NOT NULL,
    raw_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_vault_time ON vault_snapshots(chain_id, vault_address, id DESC);
CREATE TABLE IF NOT EXISTS vault_screenings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observed_at TEXT NOT NULL,
    chain_id INTEGER NOT NULL,
    vault_address TEXT NOT NULL,
    name TEXT NOT NULL,
    source_block INTEGER NOT NULL,
    apy_seven_days TEXT,
    withdrawable_fraction TEXT,
    preliminary_pass INTEGER NOT NULL,
    problems_json TEXT NOT NULL,
    raw_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_screenings_vault_time ON vault_screenings(chain_id, vault_address, id DESC);
CREATE TABLE IF NOT EXISTS exposure_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observed_at TEXT NOT NULL,
    chain_id INTEGER NOT NULL,
    vault_address TEXT NOT NULL,
    name TEXT NOT NULL,
    total_assets TEXT NOT NULL,
    idle_assets TEXT NOT NULL,
    adapter_assets TEXT NOT NULL,
    issues_json TEXT NOT NULL,
    raw_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_exposures_vault_time ON exposure_snapshots(chain_id, vault_address, id DESC);
CREATE TABLE IF NOT EXISTS exposure_market_positions (
    snapshot_id INTEGER NOT NULL REFERENCES exposure_snapshots(id),
    adapter_address TEXT NOT NULL,
    market_id TEXT NOT NULL,
    collateral_symbol TEXT NOT NULL,
    loan_symbol TEXT NOT NULL,
    supply_assets TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_exposure_market ON exposure_market_positions(market_id, snapshot_id);
CREATE TABLE IF NOT EXISTS paper_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    chain_id INTEGER NOT NULL,
    asset_address TEXT NOT NULL,
    capital_units TEXT NOT NULL,
    wallet_idle_units TEXT NOT NULL,
    policy_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_plan_allocations (
    plan_id INTEGER NOT NULL REFERENCES paper_plans(id),
    vault_address TEXT NOT NULL,
    vault_name TEXT NOT NULL,
    amount_units TEXT NOT NULL,
    PRIMARY KEY(plan_id, vault_address)
);
CREATE TABLE IF NOT EXISTS portfolio_exposure_analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    plan_id INTEGER NOT NULL REFERENCES paper_plans(id),
    model_version TEXT NOT NULL,
    market_exposure_units TEXT NOT NULL,
    vault_internal_idle_units TEXT NOT NULL,
    hhi_deployed_markets TEXT,
    source_snapshot_ids_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS portfolio_market_exposures (
    analysis_id INTEGER NOT NULL REFERENCES portfolio_exposure_analyses(id),
    market_id TEXT NOT NULL,
    collateral_symbol TEXT NOT NULL,
    loan_symbol TEXT NOT NULL,
    exposure_units TEXT NOT NULL,
    vault_addresses_json TEXT NOT NULL,
    PRIMARY KEY(analysis_id, market_id)
);
"""

class SnapshotStore:
    def __init__(self, path: str | Path):
        db = Path(path)
        db.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)

    def insert(self, snap: VaultSnapshot, raw_data: dict[str, Any]) -> None:
        data = asdict(snap)
        fields = ["observed_at", "chain_id", "vault_address", "name", "asset_address", "source_block", "total_assets", "total_supply", "withdrawable_assets", "allocated_assets", "idle_assets"]
        values = [str(data[f]) if f.endswith("assets") or f == "total_supply" else data[f] for f in fields]
        with self.conn:
            self.conn.execute(
                f"INSERT INTO vault_snapshots ({', '.join(fields)}, raw_json) VALUES ({', '.join('?' for _ in fields)}, ?)",
                [*values, json.dumps(raw_data, sort_keys=True)],
            )

    def insert_screening(self, *, assessment: Any, listing: dict[str, Any], state: dict[str, Any],
                         config: dict[str, Any], apy: dict[str, Any]) -> None:
        """Persist a read-only preliminary screening with its source API responses."""
        from datetime import datetime, timezone
        observed_at = datetime.now(timezone.utc).isoformat()
        with self.conn:
            self.conn.execute("""INSERT INTO vault_screenings (
                observed_at, chain_id, vault_address, name, source_block, apy_seven_days,
                withdrawable_fraction, preliminary_pass, problems_json, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (
                observed_at, assessment.chain_id, assessment.vault_address, assessment.name,
                assessment.source_block,
                None if assessment.apy is None else str(assessment.apy),
                None if assessment.withdrawable_fraction is None else str(assessment.withdrawable_fraction),
                int(assessment.preliminary_pass), json.dumps(assessment.problems),
                json.dumps({"listing": listing, "state": state, "config": config, "apy": apy}, sort_keys=True),
            ))

    def insert_exposures(self, *, chain_id: int, report: Any, raw_data: dict[str, Any]) -> None:
        """Append immutable observation; raw integer balances stored as TEXT."""
        from datetime import datetime, timezone
        with self.conn:
            cur = self.conn.execute("""INSERT INTO exposure_snapshots
                (observed_at, chain_id, vault_address, name, total_assets,
                 idle_assets, adapter_assets, issues_json, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""", (
                    datetime.now(timezone.utc).isoformat(), chain_id,
                    report.vault_address.lower(), report.name,
                    str(report.total_assets), str(report.idle_assets),
                    str(report.adapter_assets), json.dumps(report.issues),
                    json.dumps(raw_data, sort_keys=True),
                ))
            self.conn.executemany("""INSERT INTO exposure_market_positions
                (snapshot_id, adapter_address, market_id, collateral_symbol,
                 loan_symbol, supply_assets) VALUES (?, ?, ?, ?, ?, ?)""", [
                    (cur.lastrowid, pos.adapter_address.lower(), pos.market_id,
                     pos.collateral_symbol, pos.loan_symbol, str(pos.supply_assets))
                    for pos in report.positions
                ])

    def insert_paper_plan(self, *, capital_units: int, wallet_idle_units: int,
                          allocations: dict[str, int], names: dict[str, str],
                          policy: dict[str, Any]) -> int:
        """Save immutable *paper* allocations, not wallet balances or orders."""
        from datetime import datetime, timezone
        from .allocator import BASE_CHAIN, BASE_USDC, address_ok
        if capital_units <= 0 or wallet_idle_units < 0 or sum(allocations.values()) + wallet_idle_units != capital_units:
            raise ValueError('Paper plan totals do not reconcile')
        if any(not address_ok(addr) or not isinstance(units, int) or units < 0 for addr, units in allocations.items()):
            raise ValueError('Invalid paper plan allocation')
        with self.conn:
            cur = self.conn.execute('''INSERT INTO paper_plans (
                created_at, chain_id, asset_address, capital_units, wallet_idle_units, policy_json
            ) VALUES (?, ?, ?, ?, ?, ?)''', (
                datetime.now(timezone.utc).isoformat(), BASE_CHAIN, BASE_USDC,
                str(capital_units), str(wallet_idle_units), json.dumps(policy, sort_keys=True),
            ))
            self.conn.executemany('''INSERT INTO paper_plan_allocations (
                plan_id, vault_address, vault_name, amount_units
            ) VALUES (?, ?, ?, ?)''', [
                (cur.lastrowid, addr.lower(), names.get(addr.lower(), addr), str(units))
                for addr, units in allocations.items()
            ])
        return int(cur.lastrowid)

    def get_paper_plan(self, plan_id: int | None = None):
        """Read persisted paper portfolio without contacting external services."""
        from .portfolio import PaperPlan, PaperAllocation
        from .models import strict_uint
        if plan_id is None:
            row = self.conn.execute('SELECT * FROM paper_plans ORDER BY id DESC LIMIT 1').fetchone()
        else:
            row = self.conn.execute('SELECT * FROM paper_plans WHERE id = ?', (plan_id,)).fetchone()
        if row is None:
            raise ValueError('No saved paper plan. Run the plan command again first.')
        children = self.conn.execute('SELECT * FROM paper_plan_allocations WHERE plan_id = ? ORDER BY vault_address', (row['id'],)).fetchall()
        return PaperPlan(
            id=row['id'], chain_id=row['chain_id'], asset_address=row['asset_address'],
            capital_units=strict_uint(row['capital_units'], 'paper capital'),
            wallet_idle_units=strict_uint(row['wallet_idle_units'], 'wallet idle'),
            allocations=tuple(PaperAllocation(a['vault_address'], a['vault_name'], strict_uint(a['amount_units'], 'paper allocation')) for a in children),
        )

    def load_latest_exposures_for_plan(self, plan, *, max_age_hours: int = 48):
        """Pin the *latest* stored source snapshot ID for every funded vault."""
        from datetime import datetime, timezone, timedelta
        from .exposures import ExposureReport, MarketPosition
        from .models import strict_uint
        if not isinstance(max_age_hours, int) or max_age_hours <= 0 or max_age_hours > 720:
            raise ValueError('max_age_hours must be an integer from 1 to 720')
        now = datetime.now(timezone.utc)
        sources = {}
        for allocation in plan.allocations:
            if allocation.amount_units == 0:
                continue
            address = allocation.vault_address.lower()
            row = self.conn.execute('''SELECT * FROM exposure_snapshots
                WHERE chain_id = ? AND lower(vault_address) = ?
                ORDER BY id DESC LIMIT 1''', (plan.chain_id, address)).fetchone()
            if row is None:
                raise ValueError(f'Missing exposure data for {allocation.vault_name}; run exposures --vault {address}')
            observed = datetime.fromisoformat(row['observed_at'])
            if observed.tzinfo is None or observed > now + timedelta(minutes=5):
                raise ValueError(f'Invalid observation timestamp for {allocation.vault_name}')
            if now - observed > timedelta(hours=max_age_hours):
                raise ValueError(f'Exposure data too old for {allocation.vault_name}; rerun exposures --vault {address} (or adjust --max-age-hours)')
            positions = self.conn.execute('SELECT * FROM exposure_market_positions WHERE snapshot_id = ?', (row['id'],)).fetchall()
            report = ExposureReport(
                vault_address=row['vault_address'], name=row['name'],
                total_assets=strict_uint(row['total_assets'], 'total assets'),
                idle_assets=strict_uint(row['idle_assets'], 'idle assets'),
                adapter_assets=strict_uint(row['adapter_assets'], 'adapter assets'),
                positions=tuple(MarketPosition(
                    adapter_address=p['adapter_address'], market_id=p['market_id'],
                    collateral_symbol=p['collateral_symbol'], loan_symbol=p['loan_symbol'],
                    supply_assets=strict_uint(p['supply_assets'], 'supply assets'),
                ) for p in positions), other_adapters=(),
                issues=tuple(json.loads(row['issues_json'])),
            )
            sources[address] = (row['id'], report)
        return sources

    def insert_portfolio_analysis(self, result: Any) -> int:
        """Persist one reproducible paper look-through with exact source row IDs."""
        from datetime import datetime, timezone
        with self.conn:
            cur = self.conn.execute('''INSERT INTO portfolio_exposure_analyses (
                created_at, plan_id, model_version, market_exposure_units,
                vault_internal_idle_units, hhi_deployed_markets, source_snapshot_ids_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)''', (
                datetime.now(timezone.utc).isoformat(), result.plan_id, 'direct-market-lookthrough-v1',
                str(result.market_exposure_units), str(result.vault_internal_idle_units),
                None if result.hhi_deployed_markets is None else str(result.hhi_deployed_markets),
                json.dumps(result.source_snapshot_ids, sort_keys=True),
            ))
            self.conn.executemany('''INSERT INTO portfolio_market_exposures (
                analysis_id, market_id, collateral_symbol, loan_symbol, exposure_units, vault_addresses_json
            ) VALUES (?, ?, ?, ?, ?, ?)''', [
                (cur.lastrowid, p.market_id, p.collateral_symbol, p.loan_symbol,
                 str(p.exposure_units), json.dumps(p.vault_addresses)) for p in result.markets
            ])
        return int(cur.lastrowid)

    def latest(self) -> list[sqlite3.Row]:
        return self.conn.execute("""
          SELECT s.* FROM vault_snapshots s
          INNER JOIN (SELECT chain_id, lower(vault_address) AS v, MAX(id) AS id
                      FROM vault_snapshots GROUP BY chain_id, lower(vault_address)) t ON s.id=t.id
          ORDER BY s.chain_id, s.name, s.vault_address
        """).fetchall()

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM vault_snapshots").fetchone()[0]

    def close(self):self.conn.close()
    def __enter__(self):return self
    def __exit__(self, *_):self.close()
