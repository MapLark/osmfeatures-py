"""Wall-clock deadline for tiled ``query`` and ``places_search`` walks."""

from __future__ import annotations

import time

from ._http import DEFAULT_TIMEOUT

# Default ``timeout`` for query/places tiled walks (re-exported as osmfeatures.DEFAULT_QUERY_ALL_TIMEOUT_S).
DEFAULT_QUERY_ALL_TIMEOUT_S = DEFAULT_TIMEOUT


def query_all_deadline(timeout: float | None) -> float | None:
    """Absolute monotonic deadline, or None if uncapped."""
    if timeout is None:
        return None
    return time.monotonic() + timeout
