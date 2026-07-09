"""HTTP client for Statuspage.io public API v2 (async, httpx)."""

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 10.0


async def fetch_status(api_base: str) -> dict[str, Any]:
    return await _get(f"{api_base}/status.json")


async def fetch_incidents_unresolved(api_base: str) -> dict[str, Any]:
    return await _get(f"{api_base}/incidents/unresolved.json")


async def fetch_maintenances_active(api_base: str) -> dict[str, Any]:
    return await _get(f"{api_base}/scheduled-maintenances/active.json")


async def _get(url: str) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.json()
    except httpx.TimeoutException:
        logger.warning("Timeout fetching %s", url)
        raise
    except httpx.HTTPStatusError as e:
        logger.warning("HTTP %d from %s", e.response.status_code, url)
        raise
    except httpx.RequestError as e:
        logger.warning("Request error fetching %s: %s", url, e)
        raise
