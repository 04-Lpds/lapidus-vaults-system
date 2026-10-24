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
"""

class SnapshotStore:
    def __init__(self, path: str | Path):
        db = Path(path)
        db.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db)
        self.conn.row_factory = sqlite3.Row
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
