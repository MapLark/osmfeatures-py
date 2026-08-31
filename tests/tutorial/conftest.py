"""Load tests/.env and skip this folder when MAPLARK_API_KEY is missing."""

import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")


@pytest.fixture(autouse=True)
def require_api_key():
    if not os.environ.get("MAPLARK_API_KEY"):
        pytest.skip("MAPLARK_API_KEY not set")
