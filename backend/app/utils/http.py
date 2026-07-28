"""Shared httpx client with retry/backoff for outbound calls."""

from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from app.core.logging_config import get_logger

logger = get_logger(__name__)

_client: httpx.AsyncClient | None = None

DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=10.0, pool=5.0)
USER_AGENT = "Booktunes/1.0 (+https://github.com/booktunes; contact: ops@booktunes.app)"


class RetryableStatus(Exception):
    """Raised for 5xx/429 so tenacity retries; 4xx is not retried."""


async def get_http_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=DEFAULT_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            # Cap concurrency so a burst of ingestion can't exhaust the free
            # dyno's file descriptors.
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _client


async def close_http_client() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential_jitter(initial=1, max=20),
    retry=retry_if_exception_type((RetryableStatus, httpx.TransportError)),
    reraise=True,
)
async def fetch_json(
    url: str,
    params: dict | None = None,
    headers: dict | None = None,
) -> Any | None:
    """GET a JSON document, retrying transient failures.

    Returns ``None`` for 404 (a genuinely missing resource is not an error for
    any of our callers) and raises for anything else that survives retries.
    """
    client = await get_http_client()
    response = await client.get(url, params=params, headers=headers)

    if response.status_code == 404:
        return None
    if response.status_code == 429 or response.status_code >= 500:
        retry_after = response.headers.get("Retry-After")
        logger.warning(
            "Upstream %s returned %s (Retry-After=%s)",
            url,
            response.status_code,
            retry_after,
        )
        raise RetryableStatus(f"{response.status_code} from {url}")

    response.raise_for_status()
    try:
        return response.json()
    except ValueError:
        logger.warning("Non-JSON response from %s", url)
        return None
