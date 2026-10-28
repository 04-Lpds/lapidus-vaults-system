"""Public, read-only Morpho Vault V2 API client. No wallet functionality."""
from __future__ import annotations
from typing import Any
import httpx

API_BASE = "https://api.morpho.org"

class MorphoAPIError(RuntimeError):
    pass


class MorphoClient:
    def __init__(self, base_url: str = API_BASE, *, client: httpx.Client | None = None) -> None:
        self._owns_client = client is None
        self.http = client or httpx.Client(
            base_url=base_url, timeout=httpx.Timeout(12.0),
            headers={"Accept": "application/json", "User-Agent": "LapidusVaultSystems/0.1"},
            follow_redirects=False,
        )

    def _get(self, path: str, **params: Any) -> Any:
        try:
            response = self.http.get(path, params={k: v for k,v in params.items() if v is not None})
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise MorphoAPIError(f"Morpho API failure at {path}: {exc}") from exc
        if not isinstance(payload, dict) or "data" not in payload:
            raise MorphoAPIError(f"Unexpected response shape at {path}")
        return payload

    def listed_v2_vaults(self, chain_id: int, *, limit: int=100, asset_address: str | None=None) -> list[dict[str, Any]]:
        """Fetch listed only; limit is maximum returned, with cursor-pagination support."""
        if not (1 <= limit <= 1000):
            raise ValueError("limit must be in [1,1000]")
        results: list[dict[str, Any]] = []
        cursor: str | None = None
        while len(results) < limit:
            batch_size = min(100, limit - len(results))
            response = self._get(
                "/v0/vaults-v2", chain_ids=chain_id,
                limit=batch_size, asset_addresses=asset_address, cursor=cursor,
            )
            rows = response["data"]
            if not isinstance(rows, list):
                raise MorphoAPIError("Vault list returned a non-list 'data' field")
            for item in rows:
                if not isinstance(item, dict) or not item.get("vault_address"):
                    raise MorphoAPIError("Vault list item has no vault_address")
            results.extend(rows)
            cursor = response.get("cursor")
            if not cursor or not rows:
                break
        return results[:limit]

    def v2_state(self, chain_id: int, vault_address: str) -> dict[str, Any]:
        selector = f"{chain_id}:{vault_address}"
        response = self._get(f"/v1/vaults-v2/{selector}/state")
        if not isinstance(response["data"], dict):
            raise MorphoAPIError(f"Unexpected vault state for {selector}")
        return response["data"]

    def v2_config(self, chain_id: int, vault_address: str) -> dict[str, Any]:
        """Detailed identity, token decimals, fees and gates of a single V2 vault."""
        selector = f"{chain_id}:{vault_address}"
        response = self._get(f"/v0/vaults-v2/{selector}")
        if not isinstance(response["data"], dict):
            raise MorphoAPIError(f"Unexpected vault configuration for {selector}")
        return response["data"]

    def v2_apy(self, chain_id: int, vault_address: str, *, lookback: str = "seven_days") -> dict[str, Any]:
        """Historical realized share-price APY, NOT a forecast or guaranteed yield."""
        allowed = {"one_hour", "six_hours", "one_day", "seven_days", "thirty_days", "ninety_days", "one_year", "inception"}
        if lookback not in allowed:
            raise ValueError(f"Unsupported APY lookback: {lookback}")
        selector = f"{chain_id}:{vault_address}"
        response = self._get(f"/v1/vaults-v2/{selector}/apy-averages", lookback=lookback)
        if not isinstance(response["data"], dict):
            raise MorphoAPIError(f"Unexpected vault APY for {selector}")
        return response["data"]

    def v2_exposures(self, chain_id: int, vault_address: str) -> dict[str, Any]:
        """GraphQL market positions and adapter balances; read only.

        Query limits are currently 100 adapters / 100 positions per adapter.
        The normalization layer flags possible truncation and nested vaults.
        """
        from .exposures import MAX_API_ITEMS
        if chain_id <= 0 or not isinstance(vault_address, str) or len(vault_address) != 42:
            raise ValueError("Valid chain ID and vault address required")
        query = f"""query VaultV2Exposure($address: String!, $chainId: Int!) {{
          vaultV2ByAddress(address: $address, chainId: $chainId) {{
            address name totalAssets idleAssets
            adapters(first: {MAX_API_ITEMS}) {{ items {{
              __typename address assets type
              ... on MorphoMarketV1Adapter {{
                positions(first: {MAX_API_ITEMS}) {{ items {{
                  market {{ marketId collateralAsset {{ symbol }} loanAsset {{ symbol }} }}
                  state {{ supplyAssets }}
                }} }}
              }}
              ... on MetaMorphoAdapter {{ metaMorpho {{ address name }} }}
              ... on MorphoVaultV2Adapter {{ innerVault {{ address name }} }}
            }} }}
          }}
        }}"""
        try:
            response = self.http.post('/graphql', json={
                'query': query,
                'variables': {'address': vault_address, 'chainId': chain_id},
            })
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise MorphoAPIError(f"Morpho GraphQL failure: {exc}") from exc
        if not isinstance(payload, dict):
            raise MorphoAPIError('Unexpected GraphQL response format')
        if payload.get('errors'):
            raise MorphoAPIError(f"Morpho GraphQL errors: {payload['errors']}")
        result = payload.get('data')
        if not isinstance(result, dict) or not isinstance(result.get('vaultV2ByAddress'), dict):
            raise MorphoAPIError('GraphQL missing vaultV2ByAddress')
        return result['vaultV2ByAddress']

    def close(self) -> None:
        if self._owns_client:
            self.http.close()

    def __enter__(self) -> "MorphoClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
