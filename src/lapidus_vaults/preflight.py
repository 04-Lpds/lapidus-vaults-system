"""Conservative, read-only ERC-4626 preflight for an existing LVS rebalance preview.

This deliberately does not construct executable transactions, estimate actual gas,
approve tokens, sign, or broadcast. Fail closed whenever chain state is uncertain.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
import sys

from .allocator import BASE_CHAIN, BASE_USDC, display_usdc, address_ok
from .client import MorphoClient, MorphoAPIError
from .rpc_verify import ReadOnlyRPC, RPCError, _address_word, _word
from .wallet_plan import read_policy, read_wallet_snapshot, build_rebalance

# ERC-20 / ERC-4626 standard method selectors.
ALLOWANCE = "0xdd62ed3e"
MAX_DEPOSIT = "0x402d267d"
MAX_WITHDRAW = "0xce96cb77"
PREVIEW_DEPOSIT = "0xef8b30f7"
PREVIEW_WITHDRAW = "0x0a28a477"
DEPOSIT = "0x6e553f65"
WITHDRAW = "0xb460af94"
MIN_ETH_RESERVE_WEI = 2_000_000_000_000_000  # 0.002 ETH; configured minimum reserve
GAS_MULTIPLIER = 2  # conservative budget buffer; not a guarantee


@dataclass(frozen=True)
class Check:
    vault: str
    action: str
    requested_assets: int
    status: str
    observations: tuple[str, ...]


def _returned_uint(value: str) -> int:
    if not isinstance(value, str) or len(value) != 66 or not value.startswith('0x'):
        raise RPCError('Malformed eth_call return')
    return int(value[2:], 16)


def preflight(preview, rpc: ReadOnlyRPC, *, min_eth_reserve_wei: int = MIN_ETH_RESERVE_WEI) -> tuple[Check, ...]:
    """Checks feasibility independently per action at the preview's pinned block.

    A PASS only means individual static checks succeeded at the historical snapshot:
    never equivalent to a complete, sequential rebalance simulation or trade approval.
    """
    snap = preview.snapshot
    if snap.source != 'rpc' or snap.chain_id != BASE_CHAIN or not address_ok(snap.wallet_address):
        raise ValueError('Live RPC wallet snapshot required; demo is never preflightable')
    if type(min_eth_reserve_wei) is not int or min_eth_reserve_wei < 0:
        raise ValueError('Invalid ETH reserve policy')
    if rpc.chain_id() != BASE_CHAIN:
        raise RPCError('Wrong chain')
    block, wallet = snap.rpc_block, snap.wallet_address
    eth = rpc.eth_balance(wallet, block)
    results = []
    # Gas quotations use latest-node state and must not be presented as pinned.
    gas_price = None
    if any(c.delta_units for c in preview.changes):
        try:
            gas_price = rpc.gas_price_wei()
            if gas_price <= 0:
                gas_price = None
        except (RPCError, ValueError):
            gas_price = None
    for change in preview.changes:
        action = 'DEPOSIT' if change.delta_units > 0 else 'WITHDRAW' if change.delta_units < 0 else 'HOLD'
        amount = abs(change.delta_units)
        vault = change.vault_address
        notes = []
        if action == 'HOLD':
            results.append(Check(vault, action, 0, 'NO_ACTION', ('No change requested',)))
            continue
        if eth < min_eth_reserve_wei:
            notes.append('ETH below configured reserve')
        if gas_price is None:
            notes.append('RPC CHECK FAILED: gas price unavailable')
        if action == 'DEPOSIT':
            # Standalone feasibility, not a multi-step simulation. Existing USDC only.
            if snap.wallet_usdc_units < amount:
                notes.append('Insufficient current wallet USDC (anticipated withdrawals not credited)')
            try:
                allowance = rpc.call_uint(BASE_USDC, ALLOWANCE, block, _address_word(wallet), _address_word(vault))
                maximum = rpc.call_uint(vault, MAX_DEPOSIT, block, _address_word(wallet))
                shares = rpc.call_uint(vault, PREVIEW_DEPOSIT, block, _word(amount))
                notes.append(f'Allowance: {allowance}; maxDeposit: {maximum}; previewDeposit shares: {shares}')
                if allowance < amount:
                    notes.append('Insufficient USDC allowance; approval NOT performed')
                if maximum < amount:
                    notes.append('maxDeposit below requested amount')
                if shares == 0:
                    notes.append('previewDeposit returned zero shares')
                if not any(('Insufficient' in x or 'below' in x or 'zero shares' in x or 'FAILED' in x) for x in notes):
                    data = DEPOSIT + _word(amount) + _address_word(wallet)
                    _returned_uint(rpc.simulate_call(wallet, vault, data, block))
                    notes.append('Individual deposit eth_call returned successfully')
                    estimate = rpc.estimate_gas(wallet, vault, data)
                    budget = estimate * gas_price * GAS_MULTIPLIER
                    notes.append(f'Latest-state gas estimate {estimate}, fee quote {gas_price} wei/gas; 2x budget {budget} wei')
                    if eth < min_eth_reserve_wei + budget:
                        notes.append('Insufficient ETH for reserved gas budget')
            except (RPCError, ValueError) as exc:
                notes.append(f'RPC CHECK FAILED: {type(exc).__name__}')
        else:
            try:
                maximum = rpc.call_uint(vault, MAX_WITHDRAW, block, _address_word(wallet))
                shares = rpc.call_uint(vault, PREVIEW_WITHDRAW, block, _word(amount))
                held = next(h.share_balance for h in snap.holdings if h.vault_address == vault)
                notes.append(f'maxWithdraw: {maximum}; previewWithdraw shares: {shares}; owned shares: {held}')
                if maximum < amount:
                    notes.append('maxWithdraw below requested amount (liquidity or limits)')
                if shares > held:
                    notes.append('Insufficient vault shares for requested withdrawal')
                if not any(('below' in x or 'Insufficient' in x or 'FAILED' in x) for x in notes):
                    data = WITHDRAW + _word(amount) + _address_word(wallet) + _address_word(wallet)
                    _returned_uint(rpc.simulate_call(wallet, vault, data, block))
                    notes.append('Individual withdraw eth_call returned successfully')
                    estimate = rpc.estimate_gas(wallet, vault, data)
                    budget = estimate * gas_price * GAS_MULTIPLIER
                    notes.append(f'Latest-state gas estimate {estimate}, fee quote {gas_price} wei/gas; 2x budget {budget} wei')
                    if eth < min_eth_reserve_wei + budget:
                        notes.append('Insufficient ETH for reserved gas budget')
            except (RPCError, ValueError, StopIteration) as exc:
                notes.append(f'RPC CHECK FAILED: {type(exc).__name__}')
        blockers = ('below', 'Insufficient', 'FAILED', 'zero shares')
        status = 'BLOCKED' if any(any(b in line for b in blockers) for line in notes) else 'STATIC_PASS'
        results.append(Check(vault, action, amount, status, tuple(notes)))
    return tuple(results)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description='Read-only static preflight; no transaction execution')
    p.add_argument('--wallet', required=True)
    p.add_argument('--allowlist', required=True)
    p.add_argument('--min-eth-reserve-wei', type=int, default=MIN_ETH_RESERVE_WEI)
    args = p.parse_args(argv)
    try:
        policy = read_policy(args.allowlist)
        if not address_ok(args.wallet):
            raise ValueError('Invalid wallet')
        url = os.getenv('BASE_RPC_URL', '')
        if not url:
            raise ValueError('BASE_RPC_URL required')
        with ReadOnlyRPC(url) as rpc, MorphoClient() as morpho:
            snap = read_wallet_snapshot(args.wallet, policy, rpc, morpho)
            preview = build_rebalance(snap, policy)
            checks = preflight(preview, rpc, min_eth_reserve_wei=args.min_eth_reserve_wei)
        print(f'LVS STATIC PREFLIGHT | Base block {snap.rpc_block} | READ ONLY')
        for check in checks:
            print(f'{check.vault} {check.action} {display_usdc(check.requested_assets)} USDC: {check.status}')
            for note in check.observations:
                print(f'  - {note}')
        print('NO SIGNING OR BROADCAST. Individual historical eth_call only; no sequential gas budgeting,')
        print('no sequential state simulation, no slippage limits, no investment approval.')
        return 2 if any(c.status == 'BLOCKED' for c in checks) else 0
    except (OSError, ValueError, RPCError, MorphoAPIError) as exc:
        print(f'PREFLIGHT ERROR: {type(exc).__name__}: {exc if isinstance(exc, ValueError) else "read failed"}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
