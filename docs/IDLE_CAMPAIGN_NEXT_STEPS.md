# Idle capital campaign — first admission test

The new `idle-campaign-rehearsal` command takes an **offline synthetic**
wallet observation containing 5 USDC, with an eligible 1 USDC idle deposit
target. It invokes the same `propose_candidates` and `assess_admission`
modules that the live controller uses, then creates a disposable journaled
campaign. It does **not** execute it, call Base, use actual APYs, or modify
the live executor.

This is an incremental test of the proposal/admission part of the pipeline,
**not** a verified real-data automated decision-to-execution lifecycle.

Run from PowerShell:

```powershell
.\.venv\Scripts\python.exe -m lapidus_vaults.operator idle-campaign-rehearsal `
  --wallet 0x3E62eEC622a1db01324A32e7837fb2eaE33fB0E3 `
  --source-vault 0x050ce30b927da55177a4914ec73480238bad56f0 `
  --other-vault 0x1e9e47583f15d45a10df48c0b1846e0492c795d7
```

Expected: `OFFLINE_IDLE_CAMPAIGN_ADMITTED`, `approve_intent`,
`deposit_intent`, and `submitted: false`.

Next work is to replace the synthetic observation/economics with an actual
read-only pinned snapshot and genuine APYs/fee estimates, then feed that
decision into an Anvil-only execution test. Separately, before activating
`docs/proposed_pilot_20_usdc.json` in a signer, rework and re-test policy
limits; the live pilot still caps capital near 5 USDC and each step at 1 USDC.

Overnight: a read-only `shadow-service` is appropriate to collect RPC/API
reliability evidence. Signed live pilot may run at the existing tiny limit
if operators understand its failure/restart behavior; neither is evidence
of higher-capital production readiness.
