# Risk report v0.1 (offline, read-only)

This is the first **research-only** concentration risk gate. It is not wired
into the live executor and must **not** be used to authorize capital movement.

It assesses two distinct concentration dimensions for USDC-valued holdings:

- **Vault HHI**: sum of squared portfolio weights across vaults
- **Underlying market HHI**: sum of squared underlying lending-market weights,
  aggregated across vaults sharing the same Morpho market ID

It also checks configured maximum vault and underlying market weight, and
refuses to claim a clean report for incomplete/reconciliation-failed exposure
snapshots.

## Input contract

Create a local JSON snapshot containing `holdings_units` keyed by vault
address and `exposures` keyed by the same addresses.

Each exposure is an explicitly validated observation with:
`vault_address`, `total_assets_units`, `idle_assets_units`,
`issues` (empty only if fully reconciled), and `markets`, containing
`market_id` and `supply_assets_units` in USDC base units. No floats.
Example structures are intentionally illustrative, not sourced from Base.

Run:

```powershell
.\.venv\Scripts\python.exe -m lapidus_vaults.operator risk-report --snapshot data/my_reviewed_exposures.json
```

The command returns a JSON report. Default **research** thresholds:
- maximum 40% of total portfolio value in one vault
- maximum 25% of total portfolio value through one underlying Morpho market
- zero unresolved exposure

Exceeding a limit returns `REVIEW_LIMIT_BREACH` and exit status 2; incomplete
look-through also blocks. Neither a passed nor failed report is trade
authorization.

## Known limitations

The inputs are **offline**, manually prepared and are **not independently
authenticated** or freshness checked by this module. No price/oracle
uncertainty, utilization, liquidation, withdrawal capacity, adapter
permissions, correlation, bad debt, market liquidity, or strategy stress
simulation is yet modeled. The current cap is a static research heuristic,
not a calibrated optimal limit. The existing production controller and live
pilot retain their current conservative rules, unaffected by this report.

Next integration work: source exposure snapshots from the existing ingestion
store with timestamps, independently compare vault totals and wallet shares
to pinned Base RPC, introduce conservative liquidity/withdrawal penalties,
and only then consider an explicit risk authorization integration.
