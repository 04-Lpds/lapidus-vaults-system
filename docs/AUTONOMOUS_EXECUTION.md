# Experimental dedicated-wallet unattended execution (v0.7)

**Do not run `--execute-live` until CI, Anvil signed-transaction testing,
Windows restart testing, and an independent security review are complete.**
The code is on a development branch, not the tested supervised v0.6 release.

## Architecture

`monitor_once --create-campaign --max-action-usdc 1` can save a bounded
read-only campaign when drift triggers. An operator must validate the campaign
generation process separately before scheduling this option.

`operator auto-once` is a **one-step** executor. Each invocation either:

1. Checks the journal for an unresolved earlier submission and only performs
   an RPC receipt check; **never sends a second copy**.
2. When all previous steps are verified, fetches a fresh RPC/API snapshot and
   quote, consumes the quote, saves and binds the authorization, reserves a
   transaction in SQLite, signs an EIP-1559 transaction with the dedicated
   wallet, persists the signed hash, then sends that exact raw transaction.
3. Returns without automatically advancing any later step. A subsequent
   scheduled invocation must first reconcile the receipt.

## Mandatory execution controls

- Use a **new dedicated Base wallet**, not the personal MetaMask wallet.
- The specified protected wallet must be different from the signer.
- Per-step bound: at most 1 USDC; portfolio capital bound: at most 10 USDC.
- Explicit allowlisted vaults and exact ERC20/ERC4626 calldata.
- Strict gas cap and zero ETH transfer value.
- `data/STOP_AUTOMATION` immediately blocks new actions.
- `data/lapidus.sqlite3.auto.lock` serializes invocations; investigate a stale
  lock rather than deleting it while an executor could still be active.
- `SUBMISSION_UNKNOWN` or `SUBMITTED` entries block duplicate sending.
- The signing key must be injected locally through
  `LVS_AUTO_PRIVATE_KEY`. Do not write it in a script, source file, shell
  history, GitHub, conversation, or logs. This environment-variable prototype
  is **not a hardened key-custody design**.

## Dry/disabled entry point

```powershell
.\.venv\Scripts\python.exe -m lapidus_vaults.operator auto-once --wallet <DEDICATED_WALLET> --protected-wallet <PERSONAL_WALLET> --allowlist data/paper_test_allowlist.json
```

Without `--execute-live`, nothing is signed or broadcast. **Do not add that
flag for a live wallet yet.** The `auto-fork` command remains the supported
local-only automatic integration test.

## Known limitations before live use

- Verify a raw signed transaction on a Base Anvil fork and reconcile it against
  the campaign journal using production receipt checks.
- Confirm safe handling of network timeouts, gas pricing, allowance changes,
  nonce conflicts, pending receipts and process crashes at each checkpoint.
- Ensure an automatic campaign is never generated from stale snapshots or
  while another campaign is active.
- Add trusted encrypted key storage or external signer; environment variables
  do not meet a serious production custody standard.
- Add a persistent spend budget, max-loss/risk constraints, alerting, health
  monitoring, and operational runbooks before recurring live execution.

**Unattended execution being implemented does not imply unattended execution
is safe or enabled.**
