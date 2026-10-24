# LVS local verification — Windows PowerShell

This checklist is for the **read-only starter**. Do not configure a wallet, private key, seed phrase, or approval. The repository has no transaction-sending code.

## Step 0 — unzip and open PowerShell

Extract `Lapidus_Vault_Systems_Starter_Audited_v0.1.1.zip`. Open PowerShell in the resulting `lapidus-vault-systems` folder (the folder containing `pyproject.toml`).

## Step 1 — confirm Python and install into a local virtual environment

```powershell
py -3 --version
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

If `py` is unavailable, install Python 3.10+ and use `python` instead. No need to activate the environment; invoking its Python directly avoids PowerShell execution-policy issues. These install commands require normal package-download access.

## Step 2 — run all OFFLINE tests

```powershell
.\.venv\Scripts\python.exe -m pytest -v
```

Expected audited-starter result: **10 tests passed**. The tests mock HTTP responses and use a temporary *real SQLite database* to exercise `list → collect → report`; they do **not** access the live Morpho API.

## Step 3 — test the real network and API

```powershell
.\.venv\Scripts\python.exe -m lapidus_vaults --help
.\.venv\Scripts\python.exe -m lapidus_vaults list --chain 8453 --limit 3
.\.venv\Scripts\python.exe -m lapidus_vaults collect --chain 8453 --limit 3
.\.venv\Scripts\python.exe -m lapidus_vaults report
```

- `list` should print up to 3 **listed** Vault V2 entries on Base (chain ID 8453). These are NOT pre-approved or necessarily USDC vaults.
- `collect` should request each vault's state and persist rows in `data/lapidus.sqlite3`.
- `report` should print idle and reported-withdrawable percentages plus API indexing block numbers.
- Existing code does *not* check live RPC head height, current APY, liquidity execution feasibility, vault eligibility, or wallet balances. `withdrawable_assets` is not a guarantee you can redeem that amount.
- Public API calls may fail due to DNS, internet access, rate limits, TLS problems, API changes, or no listed results. If so, note the exact message and keep your test output: this does not invalidate the offline tests, but it means **live integration isn't verified**.

## Step 4 — optional Rust worker (after Python)

Install Rust from https://www.rust-lang.org/tools/install and reopen PowerShell. On Windows, the default MSVC toolchain may also need Microsoft's C++ build tools and Windows SDK.

```powershell
cargo --version
cd rust\chain-worker
cargo check
$env:RPC_URL = "https://mainnet.base.org"
$env:EXPECTED_CHAIN_ID = "8453"
cargo run
```

- `cargo check` downloads Rust dependencies, compiles/checks the Rust worker, and is the first real Rust compile verification.
- `cargo run` should print JSON such as `{"chain_id":8453,"latest_block":...}` if the public RPC responds. Block number is live and will differ. The worker has *no wallet or signing code*.
- The Base public RPC is rate-limited. It is suitable for a minimal development check, not production automation. For persistent operations, choose a reliable RPC provider.
- The Rust worker has **not** been compiled by the assistant in this environment because `cargo`/`rustc` are unavailable. Please report the compiler output if there are errors.

## Pass/fail checklist

| Check | What a PASS means | What it does not mean |
|---|---|---|
| 10 offline tests | Data-client, parser, SQLite, CLI integration behavior with representative fixtures works | Live API or real blockchain verified |
| `list` | Public API discovery works at test time | Selected vaults are safe/USDC |
| `collect` / `report` | State data persisted and summarized | Allocation models exist |
| `cargo check` | Rust source builds with locally resolved dependencies | RPC connectivity works |
| `cargo run` | Read-only chain ID/block RPC works | Transaction simulation, signing, or execution exist |

## Common pitfalls

- `ModuleNotFoundError`: run installation commands from the directory containing `pyproject.toml`. Then use `.\.venv\Scripts\python.exe`, not a global Python install.
- PowerShell blocks `Activate.ps1`: skip activation; commands above do not require it.
- Python `py` not found: Python launcher is missing; try `python --version` / `python -m venv .venv`.
- Rust linker errors on Windows: install the Visual Studio C++ build tools for the default MSVC toolchain.
- `ERROR: Morpho API failure`: copy the full error before troubleshooting; no writes or signing occur.

## Share when debugging

Python version, last 20 lines of `pytest -v`, one CLI `list` result/error, and (later) `cargo check`/`cargo run` output. **Never share a private key, seed phrase, or secret RPC key.**
