# Base mainnet cross-vault canary — 29 October 2026

## Scope and result

**LVS operator output: `CANARY_COMPLETED`.** An explicitly authorized,
bounded three-step transaction campaign transferred **0.500000 USDC** of
intended allocation from the first allowlisted Morpho vault to the second on
**Base (chain ID 8453)**.

- Campaign ID: `live-canary-500k`
- Dedicated signer / vault share owner: `0x3E62eEC622a1db01324A32e7837fb2eaE33fB0E3`
- Source: `0x050ce30b927da55177a4914ec73480238bad56f0`
- Destination: `0x1e9e47583f15d45a10df48c0b1846e0492c795d7`
- Requested amount: `500000` USDC base units (0.5 USDC)
- Preparation observed block: `53262915`
- Campaign outcome as reported by the user's live executor: `CANARY_COMPLETED`

| Seq | Action | Transaction | Operator status |
| --- | --- | --- | --- |
| 1 | Withdraw 0.5 USDC from source vault | [0x9909…85d2](https://basescan.org/tx/0x9909bbcc835c33b62233c08294d615d9ca0b4837e94e8e08283428d139da85d2) | `SUBMITTED` → `RECEIPT_AND_BLOCK_MATCH` |
| 2 | Approve destination vault for exact 0.5 USDC | [0x7a52…5532](https://basescan.org/tx/0x7a529f54743b5b9d28053550075c55ee523e9d62d83c9186f58cc8b918315532) | `SUBMITTED` → `RECEIPT_AND_BLOCK_MATCH` |
| 3 | Deposit 0.5 USDC into destination vault | [0x69b2…c82](https://basescan.org/tx/0x69b27cb6fd264f24bb52c67f2ce1a87b854597ea3ed63b13402b9afaa1b0ac82) | `SUBMITTED` → `RECEIPT_AND_BLOCK_MATCH` |

The terminal displayed a submission and a matching receipt/reconciliation
status for each action, followed by `CANARY_COMPLETED`. The operator did not
manually sign each step. The saved local SQLite journal is the authoritative
campaign state; the explorer links make the on-chain transactions separately
inspectable.

## What this demonstrates

- Bounded multi-step unattended **execution of an operator-authorized canary**
  with live signing on Base, not merely a local Anvil simulation.
- Ordered withdraw → approve → deposit with per-step receipt confirmation and
  reconciliation reports before moving to successor actions.
- Exact-vault allowlist and capital/action bounds applied to this small pilot.

## What this does **not** demonstrate

- A rebalance autonomously **originated by the yield/risk optimizer**. This
  campaign was authorized as an engineering canary irrespective of profitability.
- Positive net yield, robust automated vault selection, or independently
  validated strategy performance.
- Comprehensive failure recovery, code audit, or protection against all Morpho
  / Base / RPC / signer failure modes.
- The *post-campaign* portfolio snapshot is **not yet recorded here**. Run a
  fresh read-only observation against the dedicated wallet and attach its
  observation ID, block and measured per-vault positions.

## Post-campaign check (operator-run, read-only)

With `BASE_RPC_URL` configured, from the repo root:

```powershell
.\.venv\Scripts\python.exe -m lapidus_vaults.operator monitor `
  --wallet 0x3E62eEC622a1db01324A32e7837fb2eaE33fB0E3 `
  --allowlist data/paper_test_allowlist.json `
  --db data/lvs_post_canary_monitor.sqlite3
```

This command does **not** load a signing key or broadcast transactions.

## Operational cautions

Do not push private keys, secret RPC URLs, local SQLite databases, or
`data/STOP_AUTOMATION` controls. The `data/` folder is gitignored apart
from `.gitkeep`. To resume a prior or paused signing campaign, review the
local submission journal and on-chain receipts before any new execution.
