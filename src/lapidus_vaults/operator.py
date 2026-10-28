"""Single, discoverable entry point for Lapidus Vault Systems.

Keep implementation modules importable and backwards compatible, but route
operator workflows through one CLI rather than dozens of module invocations.
No private key handling or autonomous live broadcasting.
"""
from __future__ import annotations

import argparse
import sys


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="lvs", description="Lapidus Vault Systems operator CLI"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("monitor", "Collect and persist a read-only live portfolio observation"),
        ("shadow-decision", "Record read-only allocation drift and fee-vs-yield decision"),
        ("console", "Start the local supervised MetaMask console"),
        ("campaign-create", "Persist a read-only supervised rebalance campaign"),
        ("fork-rehearsal", "Run local Anvil campaign and receipt-bridge integration"),
        ("auto-fork", "Run an unattended campaign ONLY on a local Anvil Base fork"),
        ("auto-once", "Experimental dedicated-wallet live executor; disabled unless explicitly enabled"),
        ("auto-preflight", "Keyless read-only funded-wallet and campaign preflight"),
        ("auto-campaign", "Execute existing limited trial campaign with receipt confirmation"),
        ("auto-trial", "Create an exact 0.8 USDC non-executable executor campaign"),
        ("signed-fork", "EIP-1559 signer test with disposable key on local Anvil"),
        ("auto-lifecycle-fork", "Full signed auto campaign and restart recovery on local Anvil"),
    ):
        commands.add_parser(name, help=help_text, add_help=False)
    args, rest = parser.parse_known_args(argv)
    from . import monitor_once, local_console, fork_campaign_rehearsal
    from .operations import create_campaign, auto_once, auto_preflight, auto_trial_campaign, auto_campaign, shadow_decision
    from .testing import signed_fork_rehearsal, auto_lifecycle_fork

    runners = {
        "monitor": monitor_once.main,
        "shadow-decision": shadow_decision.main,
        "console": local_console.main,
        "campaign-create": create_campaign.main,
        "fork-rehearsal": fork_campaign_rehearsal.main,
        "auto-fork": fork_campaign_rehearsal.main,
        "auto-once": auto_once.main,
        "auto-preflight": auto_preflight.main,
        "auto-campaign": auto_campaign.main,
        "auto-trial": auto_trial_campaign.main,
        "signed-fork": signed_fork_rehearsal.main,
        "auto-lifecycle-fork": auto_lifecycle_fork.main,
    }
    return int(runners[args.command](rest))


if __name__ == "__main__":
    raise SystemExit(main())
