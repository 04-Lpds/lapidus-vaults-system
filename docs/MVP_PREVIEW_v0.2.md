# Lapidus Vault Systems — v0.2 Read-only MVP Preview

**Status:** Tested with a simulated API, not yet live-tested against Morpho since these changes. No contract interaction or signing. Designed for your current PyCharm/Windows setup.

## What changed from v0.1.1

- `client.py`: new `v2_config(chain_id, vault_address)` and `v2_apy(chain_id, vault_address, lookback="seven_days")`. We now read *three* routes for each vault: state, config and realized APY. The existing `/v0/vaults-v2` directory remains the initial discovery request.
- `models.py`: `strict_uint()` replaces permissive `int()` ingestion of token balances; floats, boolean values and malformed strings are rejected instead of silently truncated.
- `allocator.py`: new *read-only* preliminary screening plus simple capped, **equal-weight** hypothetical USDC allocation math. This is **not** a risk-optimized strategy or recommendation.
- `storage.py`: additive `vault_screenings` table. Existing `vault_snapshots` and local database remain compatible; raw listing/state/config/APY payloads and screening outcomes are retained for auditing.
- `cli.py`: new `screen` and `plan` commands; no wallet, private keys, approvals, transactions or RPC freshness checks.
- `tests/test_allocator.py`: offline tests for bad data, gate metadata, APY identity, exact amounts, allowlists, full CLI flow and SQLite writes.

## How to install into your existing local project

Before changing anything, make a copy or Git commit of your working v0.1.1 project, including your existing SQLite database if you want to preserve it.

**Easiest:** extract the v0.2 zip into a **new folder**, open the resulting `lapidus-vault-systems` project folder in PyCharm, create/select a Python `.venv`, and run the tests. Your old project and its database remain untouched.

**To upgrade the existing folder:** copy only the changed files from the zip (`src/lapidus_vaults/{client,models,storage,cli,allocator}.py`, `tests/test_allocator.py`, `pyproject.toml`, and `examples_allowlist.json`). Do **not** overwrite your existing `data/lapidus.sqlite3`. The new SQLite table will be added when you first use the new CLI. Make a backup before database schema changes.

In PowerShell, from the project root, with `.venv` configured:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -v
```

Expected in this preview: **34 passing offline tests**.

## Live testing (read-only on-chain; does save local SQLite data)

First, use the new `screen` command on a few Base USDC vaults:

```powershell
.\.venv\Scripts\python.exe -m lapidus_vaults screen --limit 3
```

This queries listed V2 vaults on Base with the USDC token address filter, then fetches each vault's state, configuration and seven-day realized APY. It writes records to `data/lapidus.sqlite3`, table `vault_screenings`. The report says `PRELIM` or `REVIEW` based on basic identity, gates, liquidity and indexed-block consistency checks.

**PRELIM is not approval or safety certification.** It does not check the latest RPC block, smart-contract audits, underlying credit risk, allocations, oracle robustness, protocol dependencies, fees, real withdrawability, or vault governance in sufficient depth to control money.

Next, try the hypothetical capital planner. The provided JSON has an **empty approval list** on purpose:

```powershell
.\.venv\Scripts\python.exe -m lapidus_vaults plan --capital-usdc 200 --allowlist examples_allowlist.json --limit 3
```

Until you separately review and explicitly allowlist vault addresses, its output must be **100% idle USDC**. If you add reviewed addresses, it proposes capped equal-weight allocations but never sends a transaction. This is a learning/prototyping tool, not deployment advice.

To inspect the new data in DB Browser for SQLite, select `vault_screenings` from Browse Data; `vault_snapshots` is still the original data-collection table.

## Monday, October 26 MVP target — acceptance criteria

- Existing `list -> collect -> report` continues to work.
- Live `screen` succeeds on a few Base USDC vaults; malformed/inconsistent vault data is rejected or flagged.
- `vault_screenings` persists source JSON and reasoned screening outcomes in SQLite.
- `plan` produces a reproducible, exact-unit **hypothetical** allocation among explicitly reviewed vaults, retaining an idle reserve.
- `pytest` passes locally; we manually compare at least one vault's data with Morpho's UI/docs.

This is the **first read-only analytical MVP**, **not** the originally envisioned autonomous capital manager. Future milestones are portfolio/wallet read-only positions, deeper underlying-risk analysis, on-chain RPC freshness checks, transaction simulation, manually supervised deposits/withdrawals, and only much later unattended execution after testing and controls.

## Reference documentation

- https://docs.morpho.org/api/vaults-v2/list-v2-vaults/
- https://docs.morpho.org/api/vaults-v2/get-v2-vault/
- https://docs.morpho.org/api/vaults-v2/get-v2-vault-state/
- https://docs.morpho.org/api/vaults-v2/get-v2-vault-realized-average-apy/
