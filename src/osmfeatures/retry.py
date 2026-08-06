"""Retry logic with exponential backoff and jitter."""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .models import OSMFeaturesRateLimitError


@dataclass
class RetryConfig:
    """Configuration for retry behaviour.

    Attributes:
        max_retries: Maximum number of retry attempts after the first failure.
        backoff_base: Base delay in seconds between retries (doubled each time).
        backoff_max: Maximum delay cap in seconds.
        jitter: Add +/-25% random jitter to the backoff delay.
        retry_on_status: HTTP status codes that trigger a retry.
    """

    max_retries: int = 3
    backoff_base: float = 1.0
    backoff_max: float = 60.0
    jitter: bool = True
    retry_on_status: frozenset[int] = field(
        default_factory=lambda: frozenset({429, 500, 502, 503, 504})
    )


def _backoff_delay(attempt: int, config: RetryConfig) -> float:
    delay = min(config.backoff_base * (2 ** attempt), config.backoff_max)
    if config.jitter:
        delay *= 1 + random.uniform(-0.25, 0.25)
    return max(delay, 0.0)


def _parse_retry_after(headers: dict[str, Any]) -> float | None:
    """Return seconds to wait from a Retry-After header, if present."""
    value = headers.get("Retry-After") or headers.get("retry-after")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def retry(
    fn: Callable[[], Any],
    config: RetryConfig,
    *,
    get_status: Callable[[Any], int],
    get_headers: Callable[[Any], dict[str, Any]],
    is_rate_limit_error: Callable[[Any], bool],
    build_rate_limit_error: Callable[[Any], OSMFeaturesRateLimitError],
) -> Any:
    """Execute *fn* with retry/backoff.  Returns the response on success.

    Parameters
    ----------
    fn:
        Callable that performs the HTTP request and returns a response object.
    config:
        Retry configuration.
    get_status:
        Extracts the HTTP status code from the response.
    get_headers:
        Extracts response headers as a dict.
    is_rate_limit_error:
        Returns True when the 429 response body is a retryable rate-limit error
        (as opposed to a hard monthly limit).
    build_rate_limit_error:
        Builds an ``OSMFeaturesRateLimitError`` from the response (used when
        retries are exhausted).
    """
    last_response: Any = None

    for attempt in range(config.max_retries + 1):
        last_response = fn()
        status = get_status(last_response)

        if status not in config.retry_on_status:
            return last_response  # success or non-retryable error

        if attempt == config.max_retries:
            break  # exhausted - fall through to raise

        if status == 429:
            if not is_rate_limit_error(last_response):
                break  # non-retryable 429 (e.g. monthly cap) — surface immediately
            retry_after = _parse_retry_after(get_headers(last_response))
            delay = retry_after if retry_after is not None else _backoff_delay(attempt, config)
        else:
            delay = _backoff_delay(attempt, config)

        time.sleep(delay)

    # Retries exhausted - raise the appropriate exception
    if get_status(last_response) == 429 and is_rate_limit_error(last_response):
        raise build_rate_limit_error(last_response)
    return last_response


async def retry_async(
    fn: Callable[[], Any],
    config: RetryConfig,
    *,
    get_status: Callable[[Any], int],
    get_headers: Callable[[Any], dict[str, Any]],
    is_rate_limit_error: Callable[[Any], bool],
    build_rate_limit_error: Callable[[Any], OSMFeaturesRateLimitError],
) -> Any:
    """Async counterpart to :func:`retry`."""
    import asyncio

    last_response: Any = None

    for attempt in range(config.max_retries + 1):
        last_response = await fn()
        status = get_status(last_response)

        if status not in config.retry_on_status:
            return last_response

        if attempt == config.max_retries:
            break

        if status == 429:
            if not is_rate_limit_error(last_response):
                break  # non-retryable 429 (e.g. monthly cap) — surface immediately
            retry_after = _parse_retry_after(get_headers(last_response))
            delay = retry_after if retry_after is not None else _backoff_delay(attempt, config)
        else:
            delay = _backoff_delay(attempt, config)

        await asyncio.sleep(delay)

    if get_status(last_response) == 429 and is_rate_limit_error(last_response):
        raise build_rate_limit_error(last_response)
    return last_response
