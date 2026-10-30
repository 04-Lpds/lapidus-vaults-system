# Controller-originated Anvil execution test

Use this test to exercise the **actual keyless controller_once function**,
which must create the saved campaign before the existing autonomous executor
signs and reconciles its steps on a local Anvil Base fork. This is stronger
than supplying a precreated campaign directly to the executor.

The test uses a fresh disposable fork wallet and on-fork USDC balances. It
patches Morpho API/observation plumbing with deterministic observations whose
wallet cash is read from the fork; target weights and economic approval remain
**explicit synthetic fixtures**. The test is not proof that live APY-based
economics would choose to trade, and cannot broadcast to Base mainnet.

## Command (Windows PowerShell, Anvil running in WSL)

```powershell
.\.venv\Scripts\python.exe -m lapidus_vaults.operator auto-lifecycle-fork `
  --fork-rpc http://127.0.0.1:8545 `
  --usdc-holder 0x51c72848c68a965f66fa7a88855f9f7784502a7f `
  --vault 0x050ce30b927da55177a4914ec73480238bad56f0 `
  --scenario profitable `
  --controller-originated `
  --inject-post-broadcast-crash
```

For a three-step cross-vault test, append `--destination
0x1e9e47583f15d45a10df48c0b1846e0492c795d7`.
For the simplest **idle USDC deployment** use the command as written without
a destination. That tests approve → deposit and the recovery path.

Expected JSON contains:
- `status: AUTO_LIFECYCLE_FORK_PASSED`
- `controller_originated: true`
- `campaign_state: COMPLETED`
- `post_broadcast_crash_injected: true`
- `pending_without_resend: true`
- `mainnet_broadcast: false`

All transactions are confined to the local fork. The real mainnet signer,
capital limits, and live wallet remain unchanged.

## Remaining milestone before $20 live pilot

Replace the controlled economic approval with an actual fresh snapshot,
seven-day Morpho APYs, realistic execution gas costs and a separate idle
capital policy. Verify this can originate deposits at the proposed limits,
and then perform a specifically authorized small live trial. Do not use
this fixture to admit real-money campaigns.
