"""Smoke tests for the public, LLM-free endpoints.

TestClient is used without a context manager, so the lifespan (provider
validation + SQLite init) does not run and no API keys are needed.
"""

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.providers import PROVIDERS

client = TestClient(app)


@pytest.fixture
def only_groq(monkeypatch):
    for config in PROVIDERS.values():
        monkeypatch.delenv(config["env_var"], raising=False)
    monkeypatch.setenv(PROVIDERS["groq"]["env_var"], "test-key")


def test_health(only_groq):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "providers": [PROVIDERS["groq"]["label"]]}


def test_providers_reflect_env(only_groq):
    resp = client.get("/providers")
    assert resp.status_code == 200
    body = resp.json()
    assert [p["name"] for p in body] == sorted(PROVIDERS, key=lambda n: PROVIDERS[n]["order"])
    assert {p["name"]: p["available"] for p in body} == {n: n == "groq" for n in PROVIDERS}


@pytest.mark.parametrize("api_key, expected", [("", False), ("secret", True)])
def test_config_require_api_key(monkeypatch, api_key, expected):
    monkeypatch.setenv("API_KEY", api_key)
    resp = client.get("/config")
    assert resp.status_code == 200
    assert resp.json() == {"require_api_key": expected}
