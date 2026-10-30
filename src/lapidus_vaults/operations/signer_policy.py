"""Explicit, keyless authorization for the bounded Base/USDC EOA executor."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from ..allocator import BASE_CHAIN, BASE_USDC, address_ok


@dataclass(frozen=True)
class SignerPolicy:
    execution_address: str
    protected_address: str
    chain_allowlist: tuple[int, ...]
    vault_allowlist: tuple[str, ...]
    asset_allowlist: tuple[str, ...]
    operation_allowlist: tuple[str, ...]
    max_action_units: int
    max_total_fee_wei: int
    min_eth_reserve_wei: int

    def __post_init__(self) -> None:
        for address in (self.execution_address, self.protected_address):
            if (not address_ok(address) or address != address.lower()
                    or int(address, 16) == 0):
                raise ValueError("Signer policy requires normalized nonzero addresses")
        if self.execution_address == self.protected_address:
            raise ValueError("Signer must be a separate dedicated wallet")
        for values in (self.chain_allowlist, self.vault_allowlist,
                       self.asset_allowlist, self.operation_allowlist):
            if type(values) is not tuple or not values or len(set(values)) != len(values):
                raise ValueError("Distinct explicit signer allowlists required")
        if any(type(c) is not int or c != BASE_CHAIN for c in self.chain_allowlist):
            raise ValueError("Signer supports Base only")
        if self.asset_allowlist != (BASE_USDC,):
            raise ValueError("Signer supports native Base USDC only")
        if any(not address_ok(v) or v != v.lower() or int(v, 16) == 0
               or v in (BASE_USDC, self.execution_address, self.protected_address)
               for v in self.vault_allowlist):
            raise ValueError("Invalid signer vault allowlist")
        if not set(self.operation_allowlist) <= {"approve", "deposit", "withdraw"}:
            raise ValueError("Unsupported signer operation")
        for value, low, high in (
            (self.max_action_units, 1, 1_000_000),
            (self.max_total_fee_wei, 1, 5_000_000_000_000_000),
            (self.min_eth_reserve_wei, 200_000_000_000_000, 2_000_000_000_000_000),
        ):
            if type(value) is not int or not low <= value <= high:
                raise ValueError("Signer policy limit outside supported bounds")


def load_signer_policy(path: str) -> SignerPolicy:
    """Load public configuration only; never load key material from this file."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError
        for name in ("chain_allowlist", "vault_allowlist", "asset_allowlist", "operation_allowlist"):
            if not isinstance(data.get(name), list):
                raise ValueError
            data[name] = tuple(data[name])
        return SignerPolicy(**data)
    except (OSError, ValueError, TypeError):
        # Do not echo untrusted configuration contents or paths.
        raise ValueError("Missing or malformed dedicated signer policy") from None
