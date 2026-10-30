# Reproducible Anvil setup for this Codex environment

## Exact current blocker

The selected environment is **Linux x86_64**. `command -v anvil`,
`command -v foundryup`, and `command -v cargo` each return exit status 1 with no
path. The missing executable is **Foundry's `anvil`**; a Rust source-build toolchain
and Foundry installer are also absent. The Python `.[dev]` dependencies are
installed, but cannot substitute for a running fork node. No real signed fork
rehearsal has been completed.

Downloads and loopback listeners require Codex sandbox escalation in this
session. Do not unset the inherited HTTP(S) proxy or CA settings and do not
change system-wide networking. An environment publication is not required for
a workspace-local installation; persistence in a reusable image is a separate
configuration task.

## Pinned workspace-local binary installation

The following is a procedure for a future authorized setup, **not commands run
in this audit**. GitHub release metadata was read to verify the exact artifact
and its digest. It does not require `cargo`, shell installer scripts or root.
Run network commands with Codex `require_escalated` when necessary.

```bash
mkdir -p /workspace/tools/foundry-v1.3.1
cd /workspace/tools/foundry-v1.3.1
gh release download v1.3.1 --repo foundry-rs/foundry \
  --pattern foundry_v1.3.1_linux_amd64.tar.gz
python - <<'PY'
from pathlib import Path
import hashlib
archive = Path('foundry_v1.3.1_linux_amd64.tar.gz')
expected = 'baad3e1b06d6f310d210c93e95258a03d923fe610f8d0742138f2245f94abd7c'
assert hashlib.sha256(archive.read_bytes()).hexdigest() == expected, 'checksum mismatch'
PY
tar -xzf foundry_v1.3.1_linux_amd64.tar.gz anvil
./anvil --version
```

The pinned binary makes setup repeatable; it is not a claim this historical
Anvil version supports every current Base upgrade. If it rejects the selected
fork's opcode or RPC behavior, stop and review a newer pinned Foundry release,
verify its published digest and repeat validation. Do not weaken application
checks or silently switch to mocked chain state.

## Real local fork rehearsal: separate authorization required

The audit request prohibits broadcasting, so **do not execute this section as
part of the audit**. It sends signed transactions only to an ephemeral loopback
Anvil fork. It requires a separately authorized local transaction rehearsal.

Prerequisites:

- An accessible Base RPC with historical state for a reviewed, pinned block at
  which the allowlisted vaults and Base gas oracle are deployed.
- A public USDC-holder address with sufficient USDC **at that fork block**;
  no private key is needed. Never use a real wallet signing key.
- Pick and record the fork block and vault/holder addresses before the run.
  Public `https://mainnet.base.org` is shown below; it may rate-limit or lack the
  required historical state. If it fails, use an approved archival RPC without
  printing credential-bearing URLs or storing them in Git.

Start the node in a separate Codex terminal, with loopback socket permission:

```bash
export BASE_FORK_BLOCK='<reviewed block number>'
/workspace/tools/foundry-v1.3.1/anvil \
  --host 127.0.0.1 --port 8545 --chain-id 8453 \
  --fork-url https://mainnet.base.org --fork-block-number "$BASE_FORK_BLOCK" \
  --silent
```

In another terminal, install the repository's declared Python dependencies if
needed, then run the real signed and lifecycle helpers:

```bash
cd /workspace/lapidus-vaults-system
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m lapidus_vaults.operator signed-fork \
  --fork-rpc http://127.0.0.1:8545 \
  --usdc-holder '<public funded holder at the fork block>' \
  --vault '<allowlisted Base USDC vault>'
.venv/bin/python -m lapidus_vaults.operator auto-lifecycle-fork \
  --fork-rpc http://127.0.0.1:8545 \
  --usdc-holder '<public funded holder at the fork block>' \
  --vault '<allowlisted Base USDC vault>'
```

These helpers generate disposable keys and use fork-only funding/impersonation.
The direct transaction transport is `ForkRPC`, which refuses non-loopback URLs.
Never replace it with a mainnet endpoint or use `--execute-live`.
Record the Anvil version, fork block, command output, hashes/receipts and exit
codes. Require exact approvals, deposits, receipt/event/balance reconciliation,
nonce/oracle checks, restart/no-resend behavior and final campaign completion.
The lifecycle CLI also supports cross-vault/crash-injection scenarios: inspect
`auto-lifecycle-fork --help` and run the withdrawal → approval → deposit path
against a second allowlisted vault before calling fork validation complete.
If anything fails, retain the journal, stop and investigate. A green mocked test
suite is not a replacement for these transaction-bearing local checks.
