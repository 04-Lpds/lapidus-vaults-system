# Verification and architecture baseline — 22 October 2026

## Exactly what is present

- `client.py`: GET-only public Morpho REST client; listed V2 vault discovery plus per-vault live state.
- `models.py`: vault state normalization and ratios; underlying units are integers.
- `storage.py`: SQLite history of snapshots, preserving raw API JSON.
- `cli.py`: `list`, `collect`, `report`; read-only and no own-wallet awareness.
- `tests/`: 7 original unit tests plus 3 new offline integration tests.
- `rust/chain-worker`: 1 tiny read-only Alloy/Tokio RPC probe (uncompiled here).

## Verification actually performed

- All 10 Python tests pass (mocked HTTP; real SQLite for integration test).
- Python CLI startup and parsing work.
- The public Morpho endpoint routes and advertised fields agree with current Morpho docs.
- Attempted `list` against the actual Morpho domain failed with temporary DNS resolution failure in this runtime, so live API success cannot be claimed.
- `rustc` and `cargo` are unavailable here; Rust compilation and RPC call remain unverified.
- Package install succeeded in the pre-equipped project Python environment. A separate isolated no-network Python venv initially lacked build tools/dependencies; that was an environment provisioning issue, not evidence of an application failure.

## Boundaries; not yet implemented

- No user wallet address or balances; no USDC token-decimal lookups.
- No adapter, market, cap, fee, or oracle look-through; no APY selection.
- No target weights, risk engine, candidate strategy, or rebalance conditions.
- No transaction calldata, wallet integration, signature, simulation, or executor.
- No long-running scheduler, logging, recovery, alerts, indexer freshness gate, or vault-security verification.

## Next engineering milestone

**Portfolio observability before optimization.** Select actual chain/token/vault addresses, add own-wallet USDC and share balances, vault configuration and allocations, APY and withdrawal-options calls, RPC-freshness validation, and a deterministic candidate-filter report. Keep everything read-only. Once this is tested on the user's computer, build a transparent target-weight engine and a paper-only rebalance planner.

The product's end goal is **automatic allocation of the user's own capital among existing Morpho vaults**, not commercialization or curator permissions. Autonomous live execution is a later required milestone with independent security gates.
