"""Strict normalization; money is stored in base-unit integers, never floats."""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
from datetime import datetime, timezone
from typing import Any

@dataclass(frozen=True)
class VaultSnapshot:
    chain_id: int
    vault_address: str
    observed_at: str
    source_block: int
    total_assets: int
    total_supply: int
    withdrawable_assets: int
    allocated_assets: int
    idle_assets: int
    name: str = ""
    asset_address: str = ""

    @classmethod
    def from_api(cls, data: dict[str, Any], *, name: str="", asset_address: str="", observed_at: str | None=None) -> "VaultSnapshot":
        try:
            ints = {key: int(data[key]) for key in (
                "chain_id", "last_indexed_block", "total_assets", "total_supply",
                "withdrawable_assets", "allocated_assets", "idle_assets"
            )}
            address = str(data["address"])
            if not address.startswith("0x") or len(address) != 42:
                raise ValueError("invalid EVM vault address")
            if any(val < 0 for val in ints.values()):
                raise ValueError("negative state field")
            if ints["withdrawable_assets"] > ints["total_assets"]:
                raise ValueError("withdrawable exceeds total assets")
            if ints["idle_assets"] > ints["total_assets"]:
                raise ValueError("idle exceeds total assets")
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid vault state data: {exc}") from exc
        return cls(
            chain_id=ints["chain_id"], vault_address=address,
            observed_at=observed_at or datetime.now(timezone.utc).isoformat(),
            source_block=ints["last_indexed_block"],
            total_assets=ints["total_assets"], total_supply=ints["total_supply"],
            withdrawable_assets=ints["withdrawable_assets"],
            allocated_assets=ints["allocated_assets"],idle_assets=ints["idle_assets"],
            name=name,asset_address=asset_address,
        )

    def ratio(self, numerator: int) -> Decimal | None:
        if self.total_assets == 0: return None
        return Decimal(numerator) / Decimal(self.total_assets)

    @property
    def withdrawable_fraction(self) -> Decimal | None:
        return self.ratio(self.withdrawable_assets)

    @property
    def idle_fraction(self) -> Decimal | None:
        return self.ratio(self.idle_assets)
