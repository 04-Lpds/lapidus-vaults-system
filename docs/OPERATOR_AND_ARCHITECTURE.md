# Lapidus Vault Systems — operator guide (v0.6 development)

## One CLI; distinct modules

Use `python -m lapidus_vaults.operator <command>` (or the `lvs` console script
after `pip install -e .`):

| Command | Purpose | May send a live transaction? |
| --- | --- | --- |
| `monitor` | One read-only observation to SQLite | No |
| `console` | Supervised local browser / MetaMask authorization | Only with explicit user approval |
| `campaign-create` | Persist immutable campaign intents | No |
| `fork-rehearsal` | Exercise production quote and receipt bridge on local Anvil | No live transactions |
| `auto-fork` | Unattended local fork campaign rehearsal | No live transactions |

### Module responsibilities

- `allocator.py`, `wallet_plan.py`, `safety_plan.py`: portfolio targets and constraints.
- `rpc_verify.py`, `live_deposit.py`, `live_withdraw.py`: chain reads and per-step preflight.
- `operations/campaigns.py`, `next_action.py`, `campaign_quote.py`: durable campaign state and sequencing.
- `operations/receipt_bridge.py`, `receipt_events.py`: independent receipt reconciliation.
- `monitor_once.py`: operational SQLite observation journal.
- `local_console.py` + `operations/campaign_ui.py`: manual operator interface.
- `fork_roundtrip.py` + `fork_campaign_rehearsal.py`: loopback-only fork test foundation.

Earlier `fork_cross_rebalance`, `fork_candidate_rebalance`,
`fork_exit_scenarios`, and `fork_journaled_rebalance` have been moved to
`lapidus_vaults.testing` and their offline tests updated. `fork_two_vaults`
remains as a compatibility diagnostic and `fork_roundtrip` as a shared fork
transport. These are **not production execution entry points**.

## Current execution boundary

The **only proven live execution** is supervised MetaMask approval, deposit
and withdrawal with receipt reconciliation. It completed
`supervised-test-001` (4 confirmed steps), followed by monitoring observation
65 showing no material drift.

Unattended transaction broadcasting, credential custody, fault-tolerant
signing, key rotation, gas and liquidity controls, and crash-safe transaction
nonce management are **not production-ready**. `auto-fork` is deliberately
restricted to an Anvil loopback endpoint. The new `AutoPolicy` does not sign
or broadcast and rejects `live_enabled=True`.

## Safety and crash recovery

- For a submitted transaction, **never submit another copy while the receipt
  is pending or unknown**.
- The browser now performs bounded receipt polls after receiving a hash and
  shows that hash and quote ID for later recovery.
- An interrupted browser can recover a journaled quote and a known tx hash via
  `/api/campaign/recovery-info` and the receipt checker.
- Wallet popups and MetaMask confirmations remain manual.
- Campaign creation is not transaction authorization.
- Monitor and fork-test SQLite databases must not be confused with the
  production journal at `data/lapidus.sqlite3`.

## Developer process

Keep `main` stable. Work on `feature/v0.6-execution-hardening` until
offline tests, Windows checkout tests, local Anvil tests, and independent code
review pass. Do not hardcode private keys or RPC secrets, and never attempt
unattended mainnet signing with the personal wallet.
