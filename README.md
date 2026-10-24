# Lapidus Vault Systems

**Read-only Morpho Vault V2 data collector — read-only milestone M1 starter, audited 22 October 2026**

Prototype of a Python/Rust DeFi vault monitoring, risk research and eventual controlled-automation platform. This repository **does not trade, sign transactions, or hold any private keys**. It is deliberately not a production execution system.

Read `docs/Lapidus_Vault_Systems_Project_Blueprint_v0.2.docx` for the latest blueprint: **autonomous personal-capital allocation across existing Morpho vaults**, not a curator-facing product. For reproduction and code review, start with `docs/VERIFY_WINDOWS.md`, `docs/RUST_WALKTHROUGH.md`, and `docs/AUDIT_SUMMARY.md`.

## What actually works now

- Calls Morpho's public listed Vault V2 directory and Vault V2 state endpoint.
- Preserves token values as base-unit integers; validates response structure and vault identity.
- Persists append-only state snapshots with raw JSON in SQLite.
- Prints observed idle and reported withdrawable shares of total assets.
- Includes 10 offline tests: mocked HTTP, real temporary SQLite persistence, and end-to-end CLI integration.
- Contains a separate **read-only Rust/Alloy RPC probe** (source provided; requires Cargo dependencies).

**Not implemented**: adapter look-through, portfolio aggregation, market credit-loss modeling, RPC source/indexer lag reconciliation, permissioned operations, transaction simulation, wallet signing, production logging. The API-reported withdrawable amount is NOT a promise of an executable withdrawal.

## Quick start: Python

**Windows PowerShell users:** prefer the commands in `docs/VERIFY_WINDOWS.md`; the examples below are shell-neutral except for virtual-environment activation.

```bash
python -m venv .venv
# activate .venv in your shell
python -m pip install -e '.[dev]'
pytest -q
python -m lapidus_vaults list --chain 8453 --limit 10
python -m lapidus_vaults collect --chain 8453 --limit 3
python -m lapidus_vaults report
```

To constrain discovery to a specific stablecoin, use `--asset-address 0x...` with the correct token address on the selected chain; verify token contracts independently. The directory covers **listed** V2 vaults only, not all deployed vaults. Do not assume that the first N vaults are liquid, mature, or safe.

The CLI defaults to Base chain ID 8453 only to reduce setup friction; the project has **not** preselected real vaults or verified their security. Data lives under `data/lapidus.sqlite3` and is gitignored. No .env or secret key is necessary for read-only Morpho API collection.

## Quick start: Rust RPC probe

Requires Rust/Cargo and an actual reliable JSON-RPC endpoint:

```bash
cd rust/chain-worker
export RPC_URL='https://YOUR_RPC_PROVIDER_URL'
export EXPECTED_CHAIN_ID=8453
cargo run
```

The worker outputs `{"chain_id":8453,"latest_block":...}` after connecting, and refuses an unexpected chain ID. **It cannot sign anything.** The Rust source follows current [Alloy 2.x](https://alloy.rs/) provider examples; it has not been compiled in the document authoring environment because Cargo is unavailable there.

## Next build tasks

1. Select 3–5 one-chain, same-underlying Morpho V2 vaults after checking available exposure and liquidity data.
2. Add state/allocations/withdrawal-options API adapters and relational tables for exposures.
3. Compare API `last_indexed_block` against actual RPC chain head, with a strict stale-data policy.
4. Build scenario-based concentration and withdrawal-liquidity reports; label model assumptions.
5. Add structured logs, metrics, backoffs and worker health tests.
6. Only after dry runs: design a separate wallet-execution threat model and preflight simulator.

## Sources

- [Morpho Vaults API](https://docs.morpho.org/developers/api/morpho-vaults/)
- [List V2 vaults](https://docs.morpho.org/api/vaults-v2/list-v2-vaults/)
- [Get V2 vault state](https://docs.morpho.org/api/vaults-v2/get-v2-vault-state/)
- [Vault V2 roles](https://docs.morpho.org/curate/concepts/roles/)
- [Alloy Rust EVM tooling](https://alloy.rs/)
