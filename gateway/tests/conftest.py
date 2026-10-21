import pytest


@pytest.fixture(autouse=True)
def standalone_legacy_contract(monkeypatch):
    """Existing queue/unit tests use a JSON double; real protocol tests override it."""
    monkeypatch.delenv("BACKEND_URL", raising=False)
    monkeypatch.setenv("SANDBOX_PROTOCOL", "json")
