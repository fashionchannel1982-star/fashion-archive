"""
Pytest configuration.

Integration tests require a live backend at localhost:8000.
If the backend is not up, all integration tests are skipped automatically
so `pytest -m unit` always works offline.
"""

import pytest
import httpx


def pytest_collection_modifyitems(config, items):
    """Skip integration tests if the backend is not reachable."""
    backend_up = _check_backend()
    skip_integration = pytest.mark.skip(reason="backend not running — start uvicorn first")
    for item in items:
        if "integration" in item.keywords and not backend_up:
            item.add_marker(skip_integration)


def _check_backend() -> bool:
    try:
        r = httpx.get("http://localhost:8000/health", timeout=2)
        return r.status_code == 200
    except Exception:
        return False
