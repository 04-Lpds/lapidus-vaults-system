"""Read-only hypothetical Vault V2 interface diagnostic.

The contract observations and fee model are reusable for live preflight.
Hypothetical amounts never modify wallet state and NEVER produce approval.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
import sys

from .allocator import BASE_CHAIN, BASE_USDC, address_ok, display_usdc
from .rpc_verify import ReadOnlyRPC, RPCError, _address_word, _word
from .preflight import (GAS_PRICE_ORACLE, L1_FEE_UPPER_BOUND, PREVIEW_DEPOSIT,
                        PREVIEW_WITHDRAW, DEPOSIT, WITHDRAW)


@dataclass(frozen=True)
class VaultDiagnostic:
    vault: str
    wallet: str
    block: int
    amount_units: int
    preview_deposit_shares: int | None
    preview_withdraw_shares: int | None
    held_shares: int
    usdc_balance: int
    usdc_allowance: int
    eth_balance: int
    gas_price_wei: int | None
    l1_fee_upper_bound_wei: int | None
    simulation: str
    notes: tuple[str, ...]


def inspect_vault(rpc: ReadOnlyRPC, vault: str, wallet: str, amount_units: int,
                  block: int) -> VaultDiagnostic:
    """Observe a hypothetical amount against *real* state, without state overrides.

    Neither gas estimation nor simulated transactions are attempted with fictional
    allowances or funds. Returns UNKNOWN where an execution check cannot run.
    """
    if not address_ok(vault) or not address_ok(wallet):
        raise ValueError('Invalid wallet/vault address')
    if type(amount_units) is not int or amount_units <= 0 or amount_units > 1_000_000_000:
        raise ValueError('Use a positive amount at most 1000 USDC for diagnostics')
    if type(block) is not int or block < 0 or rpc.chain_id() != BASE_CHAIN:
        raise ValueError('Requires Base mainnet and pinned block')

    from .rpc_verify import BALANCE_OF
    from .preflight import ALLOWANCE
    if not rpc.contract_exists(vault, block):
        raise RPCError('Vault contract not found at pinned block')
    shares = rpc.call_uint(vault, BALANCE_OF, block, _address_word(wallet))
    cash = rpc.call_uint(BASE_USDC, BALANCE_OF, block, _address_word(wallet))
    allowance = rpc.call_uint(BASE_USDC, ALLOWANCE, block,
                              _address_word(wallet), _address_word(vault))
    eth = rpc.eth_balance(wallet, block)
    notes = [
        'Hypothetical analysis only: no balance/allowance overrides, signing, or broadcast',
        'Morpho V2 maxDeposit/maxWithdraw intentionally return zero; capacity remains unverified',
    ]
    dep = wd = None
    for label, selector in (('previewDeposit', PREVIEW_DEPOSIT),
                            ('previewWithdraw', PREVIEW_WITHDRAW)):
        try:
            result = rpc.call_uint(vault, selector, block, _word(amount_units))
            if label == 'previewDeposit':
                dep = result
            else:
                wd = result
        except (RPCError, ValueError):
            notes.append(f'{label} unavailable or reverted at pinned block')
    quote = l1 = None
    try:
        quote = rpc.gas_price_wei()
    except (RPCError, ValueError):
        notes.append('Current gas price quote unavailable')
    try:
        # Conservative length assumption is NOT a proven bound for signed tx.
        l1 = rpc.call_uint(GAS_PRICE_ORACLE, L1_FEE_UPPER_BOUND, block, _word(512))
    except (RPCError, ValueError):
        notes.append('L1 data fee oracle quote unavailable')
    if cash < amount_units or allowance < amount_units:
        notes.append('Deposit cannot be simulated successfully from this unfunded/unapproved wallet')
    if wd is None or shares < wd:
        notes.append('Withdrawal cannot be simulated successfully with current vault shares')
    notes.append('Transaction gas needs funded/approved live state or isolated local fork; no imaginary gas estimate')
    return VaultDiagnostic(vault.lower(), wallet.lower(), block, amount_units,
                           dep, wd, shares, cash, allowance, eth, quote, l1,
                           'NOT_ATTEMPTED_NO_STATE_OVERRIDE', tuple(notes))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description='Read-only hypothetical Vault V2 diagnostics; no execution')
    p.add_argument('--wallet', required=True)
    p.add_argument('--vault', required=True)
    p.add_argument('--usdc', type=str, default='10')
    args = p.parse_args(argv)
    try:
        from decimal import Decimal
        amount = Decimal(args.usdc)
        if not amount.is_finite() or amount <= 0 or amount > 1000 or amount * 1_000_000 != (amount * 1_000_000).to_integral_value():
            raise ValueError('Provide 0 < USDC <= 1000 with at most six decimal places')
        url = os.getenv('BASE_RPC_URL', '')
        if not url:
            raise ValueError('BASE_RPC_URL required')
        with ReadOnlyRPC(url) as rpc:
            result = inspect_vault(rpc, args.vault, args.wallet, int(amount * 1_000_000), rpc.block_number())
        print(f'LVS READ-ONLY HYPOTHETICAL DIAGNOSTIC | Base block {result.block}')
        print(f'Vault: {result.vault}; hypothetical assets: {display_usdc(result.amount_units)} USDC')
        print(f'previewDeposit shares: {result.preview_deposit_shares}; previewWithdraw shares: {result.preview_withdraw_shares}')
        print(f'Observed USDC: {display_usdc(result.usdc_balance)}; allowance (units): {result.usdc_allowance}; shares: {result.held_shares}')
        print(f'ETH balance wei: {result.eth_balance}; gas price wei/gas: {result.gas_price_wei}')
        print(f'L1 data fee bound for assumed 512-byte tx (wei): {result.l1_fee_upper_bound_wei}')
        print(f'Nonzero simulation: {result.simulation}')
        for note in result.notes:
            print(f'  - {note}')
        print('NO execution authorization. L1 bound is for assumed size only; no real gas units estimated.')
        return 0
    except (RPCError, ValueError, OSError) as exc:
        print(f'DIAGNOSTIC ERROR: {type(exc).__name__}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
