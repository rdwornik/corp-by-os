"""Smoke test: verify required API keys are available."""

import os

import pytest

# Keys this repo actually uses (found via audit of src/corp_by_os/):
#   GEMINI_API_KEY — audit.py, cleanup/classifier.py, llm_router.py,
#                    overnight/preflight.py, overnight/cke_client.py
REQUIRED_KEYS = [
    "GEMINI_API_KEY",
]


@pytest.mark.parametrize("key", REQUIRED_KEYS)
def test_api_key_available(key):
    """API key is set in environment (loaded by PS profile from global .env)."""
    value = os.environ.get(key)
    if value is None:
        pytest.skip(
            f"{key} not found. Run 'keys list' in PowerShell. "
            f"Keys should be in Documents/.secrets/.env"
        )
    assert len(value) > 10, f"{key} too short ({len(value)} chars)"
