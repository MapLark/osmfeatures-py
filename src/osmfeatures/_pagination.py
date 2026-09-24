"""Wall-clock deadline for a tiled ``query`` call."""

from __future__ import annotations

import time


DEFAULT_QUERY_ALL_TIMEOUT_S = 60.0


def query_all_deadline(timeout: float | None) -> float | None:
    """Absolute monotonic deadline, or None if uncapped."""
    if timeout is None:
        return None
    return time.monotonic() + timeout
