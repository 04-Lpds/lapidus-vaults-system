# Operator status and restart safety

The current single entry point is `python -m lapidus_vaults.operator`.
The **status** subcommand needs only a local executor SQLite database:

```powershell
.\.venv\Scripts\python.exe -m lapidus_vaults.operator status --db data/lvs_executor_test.sqlite3
```

This operation is **read-only** and uses an SQLite read-only connection. It
does not load a signing key, query a node, create an allocation campaign, or
broadcast a transaction. It displays the most recent saved economics, known
active campaigns, and unresolved submission hashes.

## What receipt recovery does

A signed transaction is recorded under a deterministic hash **before** RPC
broadcast. On later executor invocation, if there is an unresolved submission,
the executor tries to verify that *saved hash* against Base, and will not
construct another transaction while the earlier one is unresolved.
`SUBMISSION_UNKNOWN` with no hash fails closed. Completed transactions are
not continuously rechecked on every 5-minute economic cycle; they were checked
at confirmation time. Periodic historical audits may be added independently.

The tests `test_restart_fault_injection.py`, `test_auto_crash_windows.py`,
and `test_auto_lifecycle_fork.py` cover distinct failure windows. Passing
tests cannot simulate a physical laptop power loss or guarantee all network
failure modes.

## Operating model and future settings

The existing CLI provides a **master entry point**, though individual
subcommands still have many arguments. A future dedicated operator
configuration should hold the allowlist, max managed capital, per-action cap,
observation interval, economic horizon, and risk thresholds, with typed
validation. Private signing keys must remain *outside* that file; settings
changes affecting authority should require an explicit review/activation
boundary. Operator dashboards should not be used to bypass authorization,
capital caps, or receipt recovery.

Do not run a second live pilot while a previous one holds the lock. Following
a process crash, inspect active campaigns and unresolved submissions first.
Do not clear lock files or manually alter journal state without establishing
which process and transaction hashes actually exist.
