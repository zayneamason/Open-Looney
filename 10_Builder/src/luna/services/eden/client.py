"""
Eden HTTP client.

Async httpx client with auth, retry, and error handling.
All Eden API calls go through this layer.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

import httpx

from .config import EdenConfig

logger = logging.getLogger("luna.eden")


class EdenAPIError(Exception):
    """Eden API returned an error."""
    def __init__(self, status_code: int, message: str, response_body: Optional[str] = None):
        self.status_code = status_code
        self.response_body = response_body
        super().__init__(f"Eden API {status_code}: {message}")


class EdenClient:
    """
    Low-level async HTTP client for Eden API.

    Usage:
        config = EdenConfig.load()
        async with EdenClient(config) as client:
            data = await client.post("/v2/tasks/create", json=payload)
    """

    def __init__(self, config: EdenConfig):
        self.config = config
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "EdenClient":
        self._client = httpx.AsyncClient(
            base_url=self.config.api_base,
            headers={
                "Content-Type": "application/json",
                **({"X-Api-Key": self.config.api_key} if self.config.api_key else {}),
            },
            timeout=httpx.Timeout(self.config.timeout_seconds),
        )
        return self

    async def __aexit__(self, *exc) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("EdenClient not initialized. Use 'async with EdenClient(config) as client:'")
        return self._client

    async def get(self, path: str, params: Optional[dict] = None) -> dict[str, Any]:
        """GET request with retry."""
        return await self._request("GET", path, params=params)

    async def post(self, path: str, json: Optional[dict] = None) -> dict[str, Any]:
        """POST request with retry."""
        return await self._request("POST", path, json=json)

    async def _request(
        self,
        method: str,
        path: str,
        json: Optional[dict] = None,
        params: Optional[dict] = None,
    ) -> dict[str, Any]:
        """Execute HTTP request with retry logic."""
        last_error = None

        for attempt in range(self.config.max_retries):
            try:
                response = await self.client.request(
                    method, path, json=json, params=params
                )

                if response.status_code == 429:
                    # Rate limited — back off and retry
                    wait = (attempt + 1) * 2
                    logger.warning(f"Eden rate limited, waiting {wait}s (attempt {attempt + 1})")
                    await asyncio.sleep(wait)
                    continue

                if response.status_code >= 500:
                    # Server error — retry
                    wait = (attempt + 1) * self.config.poll_backoff_factor
                    logger.warning(f"Eden server error {response.status_code}, retrying in {wait}s")
                    await asyncio.sleep(wait)
                    continue

                if not response.is_success:
                    body = response.text
                    raise EdenAPIError(response.status_code, body, body)

                return response.json()

            except httpx.TimeoutException as e:
                last_error = e
                logger.warning(f"Eden request timeout (attempt {attempt + 1})")
                await asyncio.sleep((attempt + 1) * 2)
            except httpx.ConnectError as e:
                last_error = e
                logger.warning(f"Eden connection error (attempt {attempt + 1})")
                await asyncio.sleep((attempt + 1) * 2)

        raise EdenAPIError(0, f"Failed after {self.config.max_retries} attempts: {last_error}")
