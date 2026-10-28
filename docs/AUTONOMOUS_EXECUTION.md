## Keyless mainnet preflight (no signing or broadcast)

After a dedicated campaign has been created in its own SQLite database, run:

```powershell
.\\.venv\\Scripts\\python.exe -m lapidus_vaults.operator auto-preflight \`
  --wallet 0x3E62eEC622a1db01324A32e7837fb2eaE33fB0E3 \`
  --protected-wallet 0x8685F85E3afcE79310658FB09d85BB98ecFc7EdE \`
  --allowlist data/paper_test_allowlist.json \`
  --db data/lvs_executor_test.sqlite3
```

The command does not read any signing key or send transactions. It reports live
wallet capital, Base ETH for gas, campaign status, and (only when a campaign
step exists) a fresh read-only quote simulation with fee-cap checks.
An empty/no-active-campaign DB produces PREFLIGHT_READ_ONLY with
NO_ACTIVE_CAMPAIGN; it does not create a campaign.

If a quote binding exists without a matching autonomous journal, or a journal
is confirmed without a matching bridged campaign receipt, auto-once and
preflight halt rather than creating another quote. These require manual
investigation; do not delete records to resume blindly.

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

## Signed Anvil rehearsal (required before live testing)

After installing the optional automation dependency, start a local Base
Anvil fork, then run:

```powershell
.\\.venv\\Scripts\\python.exe -m lapidus_vaults.operator signed-fork --fork-rpc http://127.0.0.1:8545 --usdc-holder <BASE_USDC_HOLDER> --vault <ALLOWLISTED_USDC_VAULT>
```

The tool creates a fresh disposable signing key for local transactions only,
funds its actor through an impersonated **forked** USDC holder, and signs
approval and deposit calls using the same EIP-1559 signer code. It validates
production quote/consume and receipt checks. It never uses a real wallet
private key or an external transaction endpoint.

## Full automatic lifecycle Anvil test

Run against the local Base fork after fetching this branch:

```powershell
.\.venv\Scripts\python.exe -m lapidus_vaults.operator auto-lifecycle-fork `
  --fork-rpc http://127.0.0.1:8545 `
  --usdc-holder 0x8685F85E3afcE79310658FB09d85BB98ecFc7EdE `
  --vault 0x050ce30b927da55177a4914ec73480238bad56f0
```

Expected: AUTO_LIFECYCLE_FORK_PASSED, two signed approval/deposit
transactions, an intermediate PENDING_NOT_FOUND without rebroadcast,
subsequent receipt recovery, both journal states CONFIRMED, and campaign
COMPLETED. The test creates a disposable wallet and SQLite file; its
RPC writes pass only through the loopback-only ForkRPC transport. Restart
simulation re-enters the real auto_once controller on separate calls.

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
