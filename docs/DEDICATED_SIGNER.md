# Dedicated execution signer: authorization boundary

## Repository audit and scope

Base: `feature/v0.7-dedicated-signer` at
`727c6736dc1623602d9849814a63fe079f76b53c`. Changes belong on
`codex/v0.7-dedicated-signer-work`.

The base already includes dedicated EOA signing (`operations/dedicated_signer.py`),
quote/consume/reconciliation (`live_deposit.py`, `live_withdraw.py`), wallet-aware
RPC/API planning (`live_readonly_plan.py`), exact campaign quote binding,
nonce/pending checks and locking (`operations/auto_once.py`), and a durable
no-resend journal (`operations/auto_journal.py`). CLI entry points are `operator.py`
and `cli.py`; fork helpers live in `testing/`. These components were extended,
not replaced. Supervised browser execution remains separate.

The authority visible in the code is **self-custody by a dedicated EOA**: the EOA
owns the USDC and vault shares, pays gas, receives deposited shares and withdrawn
assets, and is the owner argument on withdrawals. The protected/personal address
is an exclusion, not a delegated strategy owner. No contract authorization to
manage someone else's position has been established. Do not substitute an owner,
receiver, relayer or smart-account address. A delegated owner model requires a
separate design and review; the signer rejects alternative recipients/owners.

## Required public policy

All production paths through `execute_once` now require `LVS_SIGNER_POLICY` to
name a public JSON file, or an explicitly constructed `SignerPolicy` through the
Python API. There is no inferred policy, default signer, or alternate key.
Missing policy blocks execution before key parsing and network access. Existing
live-canary, live-pilot and campaign entry points inherit this requirement.
Keyless preflight reports `PREFLIGHT_SIGNER_POLICY_BLOCKED` for an eligible action
when configuration is missing or inconsistent. A no-active-campaign report is
only an observation, not permission to sign.

Example schema (dummy addresses; **not an operational authorization**):

```json
{
  "execution_address": "0x1111111111111111111111111111111111111111",
  "protected_address": "0x3333333333333333333333333333333333333333",
  "chain_allowlist": [8453],
  "vault_allowlist": ["0x2222222222222222222222222222222222222222"],
  "asset_allowlist": ["0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"],
  "operation_allowlist": ["approve", "deposit", "withdraw"],
  "max_action_units": 1000000,
  "max_total_fee_wei": 50000000000000,
  "min_eth_reserve_wei": 200000000000000
}
```

Addresses must be normalized lowercase and nonzero. Signer and protected address
must differ. Vaults must be explicit, distinct, and cannot be the token or either
wallet. Chain and asset support remain restricted to Base 8453 / native USDC.
Only the three named operations are implemented; omit operations to deny them.
The configured wallet/protected address and ordered vault list must match the
executor configuration exactly. Unknown fields (including any key field), missing
fields, empty allowlists, and invalid types fail closed.

Amounts use USDC raw units (1,000,000 = 1 USDC). Maximum action is 1 USDC; the
existing portfolio policy still caps capital at 10 USDC. Reserve bounds are
0.0002–0.002 ETH. Use the previous 0.00005 ETH transaction fee ceiling or a lower
one for conservative configuration. An explicit fee ceiling may be at most
0.005 ETH, matching the pre-existing signer override bound; local fork harnesses
use that upper ceiling for synthetic fork gas prices. This is an L2 execution
fee bound, not a complete Base L1-data-fee estimate. No operational policy file,
key, live opt-in, scheduler, or broadcasting configuration is created by this
change. Protect policy integrity alongside campaign DB and process access.

## Construction, signing and broadcasting

- `build_unsigned_transaction` is pure: policy, campaign bytes, nonce and fee
  inputs produce a type-2 transaction without key access or network calls.
- `prepare_signed_transaction` validates the dedicated runtime key identity,
  rechecks fresh read-only RPC state, and signs without broadcasting.
- `reserve_and_broadcast` first checks the exact persisted campaign binding,
  invokes signing against the same RPC endpoint used for broadcast, reserves
  the journal, saves the deterministic hash, then attempts one raw send.
  Unknown responses/timeouts retain that hash and block retry. Receipt/event/
  block reconciliation must complete before the campaign advances.

Before signing, the boundary enforces explicit chain/vault/asset/operation
permissions, exact selectors and ABI arguments, bounded exact approvals to the
vault, amount caps, zero ETH value, nonce/gas bounds and maximum EIP-1559 fee.
Fresh verification requires a recent persisted authorization (90 seconds,
10 blocks, no future observations), contract bytecode, asset identity, balances,
allowance safety, current simulation and gas estimates. Withdrawals retain the
reviewed share-burn ceiling. The final balance check preserves the configured
ETH reserve even at the actual transaction maximum fee. No RPC result means no
signature. The existing RPC/API verification and wallet reconciliation in the
runner remain prerequisites; a signature alone does not authorize execution.

Morpho V2 `maxWithdraw` is still read and recorded as a diagnostic: the existing
code documents that it returns zero by design. Positive owned shares,
`previewWithdraw`, pinned simulation and the share-burn ceiling remain required;
this change does not invent an executable liquidity limit from that diagnostic.

New persisted authorizations carry `authorized_at_unix`. Old authorizations
cannot be newly signed without it, but recovery of an already journaled hash
continues through the existing receipt path without a new signature. Never edit
old records or refresh timestamps to authorize a retry.

## Safe local validation

No real key, RPC URL, or wallet funding is needed for the offline suite:

```bash
python -m pip install -e '.[dev]'
python -m pytest -q
python -m compileall -q src tests
git diff --check
```

The tests generate disposable keys, mock all blockchain transport, use temporary
SQLite journals, and exercise approval/deposit/withdrawal signatures, policy
failures, state changes, expired plans, reserve/gas checks, and uncertain-send
recovery. HTTP console tests need permission to bind loopback sockets.

With Anvil already running a Base fork on loopback, the existing `signed-fork`
and `auto-lifecycle-fork` commands use explicit test policies and fresh disposable
keys. They keep the production signing checks and send only through `ForkRPC`,
which refuses non-loopback endpoints. See `AUTONOMOUS_EXECUTION.md` for command
arguments. Do not use a real wallet key, add `--execute-live`, or configure a
mainnet scheduler for local validation.

## Remaining production blockers

This remains an unaudited in-process environment-key signer, not a remote signer
or hardened custody service. An attacker controlling the process can bypass
Python policy checks or read its key. Policy enforcement is not an on-chain
permission boundary. Review external signer/HSM integration and tamper-resistant
policy storage before production custody.

RPC reads are pinned by height, not block hash; reorgs, provider dishonesty and
changes after simulation remain possible. ERC-4626 deposit/withdraw do not carry
on-chain slippage bounds here. L1 data fees, persistent spend budgets, strategy
loss limits, independent vault risk approval, monitoring/alerts, and deployment
health controls need further work. API/RPC consistency is not an economic risk
assessment. The read-only RPC abstraction has no transaction-count method;
the existing runner's separate nonce transport remains in place.

Local signed Anvil runs, multi-version Python CI, operational crash/restart
rehearsals and independent security review remain required before any live
rollout. No production-readiness claim is made.

## Validation in this workspace

Python 3.12, with the repository's declared dev dependencies installed:

- `python -m pip install -e '.[dev]'`: passed (provided missing `eth-account`).
- Initial `python -m pytest -q`: collection failed because `eth-account` was
  absent. After dependency installation, the sandboxed suite had 378 passes and
  four loopback socket permission failures in `test_local_console.py`.
- Final `.venv/bin/python -m pytest -q`, with sandbox escalation for loopback
  sockets: **397 passed**. Blockchain transports were mocked throughout.
- `.venv/bin/python -m compileall -q src tests`: passed.
- `.venv/bin/python -m pip check`: passed; no broken requirements.
- `git diff --check`: passed.

No Python linter/type-checker configuration is provided by this repository.
Anvil is not installed, so a real signed Base-fork rehearsal was not run. Only
Python 3.12 was available for this validation; the existing CI matrix covers
3.10 and 3.12. No real wallet key was accessed and no live transaction was sent.
