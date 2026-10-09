"""Shared fixtures and constants for osmgeojson example-app integration tests.

These tests run against a live MapLark API.  Fill in ``tests/.env`` (copy
from ``tests/.env.example``) with your ``MAPLARK_API_KEY``, then run:

    pytest tests/example_apps/ -v

All tests are automatically skipped when the env var is absent.
"""

import os
import time
from pathlib import Path

import pytest
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from osmfeatures import OSMFeaturesClient

# ---------------------------------------------------------------------------
# Bounding boxes (Stockholm area used throughout the examples)
# ---------------------------------------------------------------------------

# Djurgården - parkland, lakes, and recreational facilities east of the city
DJURGARDEN_BBOX = "18.090,59.320,18.170,59.345"

# Gamla Stan + Södermalm tip - dense medieval street grid, restaurants
GAMLA_STAN_BBOX = "18.063,59.322,18.082,59.332"

# Södermalm - urban cycling infrastructure
SODERMALM_BBOX = "18.045,59.308,18.100,59.330"

# Broader central-east area to maximise lake + amenity overlap
CENTRAL_EAST_BBOX = "18.020,59.310,18.180,59.365"

# Corridor tiles for the Liljeholmen -> Djurgården bike-route Dijkstra test.
# The ~9 km west-east stretch is split into three overlapping tiles so
# the backend is queried separately for each segment and the results are
# merged client-side before graph construction.
CORRIDOR_TILES = [
    "18.000,59.305,18.070,59.340",  # Liljeholmen / Södermalm west
    "18.060,59.308,18.130,59.340",  # Södermalm central / Slussen
    "18.120,59.308,18.180,59.340",  # Djurgården
]


# ---------------------------------------------------------------------------
# Client fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client() -> OSMFeaturesClient:
    """Live OSMFeaturesClient pointed at the MapLark API.

    Reads ``MAPLARK_API_KEY`` from the environment.  The whole module is
    skipped if the variable is absent so the unit-test suite (which uses a
    separate mocked client) can run unaffected.
    """
    api_key = os.environ.get("MAPLARK_API_KEY", "")
    if not api_key:
        pytest.skip("MAPLARK_API_KEY not set — skipping live API example tests")
    base_url = os.environ.get("MAPLARK_BASE_URL", "https://api.maplark.com")
    return OSMFeaturesClient(api_key=api_key, base_url=base_url)


@pytest.fixture(autouse=True)
def rate_limit_sleep():
    """Pause between tests to stay within per-second rate limits."""
    time.sleep(0.75)


@pytest.fixture(autouse=True)
def usage_probe(client: OSMFeaturesClient):
    """Print unit-budget snapshots before and after each test.

    Calls ``GET /v1/usage`` (API key auth) at the start and end of every test
    so you can see exactly how many credits each example consumes.
    """
    def _snapshot() -> dict | None:
        try:
            return client.usage()
        except Exception:
            return None

    before = _snapshot()
    if before:
        print(
            f"\n[usage:before] tier={before['tier']}"
            f"  used={before['usage_this_month']}"
            f"  remaining={before['remaining_this_month']}"
        )

    yield

    after = _snapshot()
    if after and before:
        delta = after["usage_this_month"] - before["usage_this_month"]
        print(
            f"[usage:after]  tier={after['tier']}"
            f"  used={after['usage_this_month']}"
            f"  remaining={after['remaining_this_month']}"
            f"  charged={delta:+d}"
        )
