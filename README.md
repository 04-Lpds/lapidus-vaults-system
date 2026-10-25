# Lapidus Vault Systems

**Read-only Morpho Vault V2 research system — v0.3-alpha2 exposure + portfolio look-through increment (24 October 2026)**

Prototype of a Python/Rust DeFi vault monitoring, risk research and eventual controlled-automation platform. This repository **does not trade, sign transactions, or hold any private keys**. It is deliberately not a production execution system.

Read `docs/Lapidus_Vault_Systems_Project_Blueprint_v0.2.docx` for the latest blueprint: **autonomous personal-capital allocation across existing Morpho vaults**, not a curator-facing product. For reproduction and code review, start with `docs/VERIFY_WINDOWS.md`, `docs/RUST_WALKTHROUGH.md`, and `docs/AUDIT_SUMMARY.md`.

## What actually works now

- Calls Morpho's public listed Vault V2 directory and Vault V2 state endpoint.
- Preserves token values as base-unit integers; validates response structure and vault identity.
- Persists append-only state snapshots with raw JSON in SQLite.
- Prints observed idle and reported withdrawable shares of total assets.
- Includes 55 passing offline tests in this development increment (mock HTTP, SQLite persistence, screening/plan, and exposures).
- Contains a separate **read-only Rust/Alloy RPC probe** (source provided; requires Cargo dependencies).

**Not implemented**: complete recursive adapter look-through, market credit-loss modeling, API-vs-RPC indexer lag reconciliation, permissioned operations, transaction simulation, wallet signing, production logging. A first-level, incomplete market exposure inventory is included; nested vaults are flagged, not silently ignored. The API-reported withdrawable amount is NOT a promise of an executable withdrawal.

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
python -m lapidus_vaults exposures --vault 0x050cE30b927Da55177A4914EC73480238BAD56f0
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

## CLI commands (read-only)

| Command | Function |
|---|---|
| `list` | Discover listed Morpho Vault V2s |
| `collect` | Collect current state and append SQLite snapshot |
| `report` | Display latest stored state snapshots |
| `screen` | Mechanical APY/configuration/liquidity pre-screening |
| `plan` | Hypothetical equal-weight allocations to locally allowlisted vaults; idle reserve |
| `exposures` | Inspect documented GraphQL adapter and underlying market positions for selected vaults; nested vaults marked unresolved |
| `portfolio-exposures` | Analyze saved paper allocations across previously recorded vault market positions (offline) |

For supported options use `python -m lapidus_vaults COMMAND --help`.

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


## v0.2 read-only MVP preview

New: `screen` fetches Morpho V2 configuration, state and historical APY and stores a preliminary screening in SQLite; `plan` generates a hypothetical, capped, equal-weight USDC allocation across explicitly allowlisted vaults, with an idle reserve. This does not trade or measure genuine risk-adjusted yield. See `docs/MVP_PREVIEW_v0.2.md` for setup, limitations and commands.

## v0.3-alpha: exposure inventory

`exposures --vault ADDRESS` queries a Vault V2's current adapters and its direct Morpho Blue market positions using the public Morpho GraphQL API. It stores immutable source observations and indicates where nested adapters or truncated results prevent full risk look-through. **This is not yet a risk score or investment decision.** See `docs/V0.3_EXPOSURES_ALPHA.md` for limitations and workflow.

## v0.3-alpha2: persisted paper portfolio look-through

`plan` now persists its hypothetical USDC allocations in SQLite and prints a paper plan ID.
If you already have exposure snapshots for every allocated vault, analyze them **offline**:

```powershell
python -m lapidus_vaults portfolio-exposures
# Or pick an older plan:
python -m lapidus_vaults portfolio-exposures --plan-id 1 --max-age-hours 48
```

This saves a portfolio analysis with source snapshot IDs; it reports market-level and
collateral-backed USDC lending concentrations, market HHI (deployed positions only),
wallet idle and vault-internal idle separately. It is not a trading or credit-risk model.
See `docs/V0.3_PORTFOLIO_LOOKTHROUGH.md`.
