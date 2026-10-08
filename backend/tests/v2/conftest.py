"""Protocol/unit tests have no Redis state to reset."""
import pytest

@pytest.fixture(autouse=True)
def reset_verification_send_rate_limits():
    # Override the legacy suite's network reset fixture for isolated V2 tests.
    yield


@pytest.fixture(autouse=True)
def unlimited_designs(monkeypatch):
    # Most suites create many conversations for one account; the per-account limit has its own tests.
    from app.core.config import get_settings
    monkeypatch.setenv("V2_DESIGN_LIMIT_PER_USER", "0")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
