# Lapidus Vault Systems

**Morpho Vault V2 portfolio monitoring, bounded capital allocation, and journaled execution on Base.**

LVS is a Python system for observing vault positions, computing bounded USDC
allocation plans, journaling transaction intentions, and reconciling supervised
approvals, deposits and withdrawals against on-chain receipts. It persists
monitoring observations and execution campaigns to SQLite.

**Earlier supervised milestone:** on 27 October 2026 a four-step supervised Base
campaign (`supervised-test-001`) completed with matching receipts for two
exact-amount approvals and two deposits. The following read-only observation
(#65) recorded a ~12 USDC portfolio, roughly 40% in each selected vault and
20% idle, with `NO_REBALANCE_NEEDED`. These are small-scale functional tests,
not evidence of profitable strategies or comprehensive protocol risk control.

**Mainnet unattended execution milestone (29 October 2026):** an explicitly
operator-authorized 0.5 USDC three-step Gauntlet → Juno canary completed on
Base, with signed withdrawal, ERC-20 approval and deposit, each reporting a
matching receipt and reconciliation. See the [transaction-linked execution
report](docs/MAINNET_CANARY_2026-10-29.md). This did **not** originate from an
automatic profitability-triggered allocation decision.

## Status and execution boundaries

| Capability | Status |
| --- | --- |
| Morpho V2 discovery and read-only snapshots | Implemented |
| Wallet-aware allocation planning and drift monitoring | Implemented |
| SQLite monitoring and transaction authorization journal | Implemented |
| Supervised MetaMask approval, deposit and withdrawal | Live-tested |
| Persisted multi-step campaign with dependency gating | Live-tested |
| RPC receipt/event/balance checks and restart recovery | Live-tested |
| Bounded receipt polling without automatic resubmission | v0.6 development |
| Autonomous local-fork campaign execution | Available for Anvil testing |
| Dedicated-wallet unattended mainnet signing and broadcast | **Live-tested for one operator-authorized 3-step canary** |
| Bounded five-minute mainnet economic pilot | Implemented and observed making live HOLD decisions; economic-triggered live rebalance not yet demonstrated |

The system is **not an audited vault allocator**. Vault liquidity, adapter
risk, pricing/oracle behavior, and execution fee economics require independent
assessment. It never guarantees successful withdrawals or financial outcomes.

## Installation

Python 3.10+:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q
```

### Operator CLI

The new unified entry point keeps operator commands separate from internal
modules. Run `python -m lapidus_vaults.operator --help`, or use `lvs` after
editable installation.

```powershell
$env:BASE_RPC_URL = "https://mainnet.base.org"
.\.venv\Scripts\python.exe -m lapidus_vaults.operator monitor --wallet <PUBLIC_WALLET> --allowlist data/paper_test_allowlist.json
.\.venv\Scripts\python.exe -m lapidus_vaults.operator console --wallet <PUBLIC_WALLET> --allowlist data/paper_test_allowlist.json
.\.venv\Scripts\python.exe -m lapidus_vaults.operator campaign-create --wallet <PUBLIC_WALLET> --allowlist data/paper_test_allowlist.json --campaign-id <NEW_ID> --max-action-usdc 1
```

The **supervised console** runs on `http://127.0.0.1:8765/` and requires explicit MetaMask
confirmation for each transaction. Separately, the dedicated-wallet `live-pilot`
and `live-canary` commands support explicitly opted-in bounded unattended signing.
Do not run them concurrently on the same execution database.
Do not put private keys, mnemonic phrases, or secret RPC URLs in Git.

### Local autonomous fork rehearsal

With Anvil already running a **Base fork on loopback**:

```powershell
.\.venv\Scripts\python.exe -m lapidus_vaults.operator auto-fork --fork-rpc http://127.0.0.1:8545 --wallet <PUBLIC_WALLET> --source <ALLOWLISTED_SOURCE> --destination <ALLOWLISTED_DESTINATION> --amount-usdc 1
```

This executes the three-step local-fork campaign without manual wallet clicks,
using Anvil impersonation only. It tests production quote/receipt components,
persistent authorization recovery, and a crash-safe submission reservation
journal. It **does not sign or broadcast to real Base**.

## Explicit dedicated signer policy

Dedicated execution requires a separate public `LVS_SIGNER_POLICY` JSON file
with explicit execution/protected addresses and chain, asset, vault and operation
allowlists. Missing or mismatched policy blocks signing; no alternate wallet is
used. See [signer configuration, architecture audit and local testing](docs/DEDICATED_SIGNER.md).
See also the [adversarial audit and unresolved risks](docs/V0.7_SIGNER_SECURITY_AUDIT.md)
and [Codex Anvil setup procedure](docs/CODEX_ANVIL_SETUP.md).
This change does not enable live execution or configure a mainnet scheduler.

## Project structure

- `src/lapidus_vaults/allocator.py`, `wallet_plan.py`, `safety_plan.py`:
  portfolio observation, allocation and bounded planning.
- `src/lapidus_vaults/rpc_verify.py`, `live_deposit.py`, `live_withdraw.py`:
  pinned network reads, simulation and preflight.
- `src/lapidus_vaults/operations/`: campaign persistence, quote binding,
  receipt reconciliation, autonomous policy/reservation state.
- `src/lapidus_vaults/monitor_once.py`: periodic read-only monitoring journal.
- `src/lapidus_vaults/local_console.py`: supervised browser operator interface.
- `src/lapidus_vaults/testing/`: experimental historical fork scenarios.
- `tests/`: offline contract mocks, state-machine and regression tests.
- `rust/chain-worker/`: separate read-only Rust/Alloy RPC experiment.
- `docs/OPERATOR_AND_ARCHITECTURE.md`: operational details and safety model.

SQLite defaults to `data/lapidus.sqlite3` and must remain untracked. Prior
v0.3–v0.5 design notes in `docs/` are historical development records rather
than instructions for the current operator interface.

## Safety and recovery

A quote ID identifies a cryptographically random, locally journaled
authorization. A transaction hash identifies an on-chain submission.
After a known submission, **never retry sending solely because the RPC has not
yet returned a receipt**. The console performs bounded polling and offers
read-only receipt recovery by quote ID and transaction hash.

The per-action autonomous policy caps actions at 1 USDC, and the current
live-pilot manages at most 5 USDC plus a small accounting tolerance. The
reservation journal commits `SUBMISSION_UNKNOWN` before a send attempt so
an unknown broadcast cannot be blindly resubmitted. A dedicated-wallet signer
has executed a mainnet canary; this is **experimental**, not audited, and not
proof of profitable or fully risk-aware allocation.

No claim of third-party security audit, custody protection or fully automated
production allocation is made.
