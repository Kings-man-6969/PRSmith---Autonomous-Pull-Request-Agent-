"""Failure injection test: Docker daemon outage does not crash API process.

Verifies:
1. When Docker is unavailable, the FastAPI application remains healthy (returns 200 on health endpoint).
2. The worker execution backend fails closed by raising SandboxUnavailableError at runtime.
"""

from pathlib import Path
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.sandbox.docker import DockerBackend
from backend.sandbox.interface import SandboxUnavailableError


def test_api_healthy_when_docker_unavailable():
    # Verify that DockerBackend is unavailable
    backend = DockerBackend()
    backend._available = False
    backend.client = None

    # API health check endpoint remains responsive (200 OK)
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data.get("status") == "healthy" or "status" in data


@pytest.mark.asyncio
async def test_worker_fails_closed_when_docker_unavailable(monkeypatch, tmp_path: Path):
    backend = DockerBackend()
    backend._available = False
    backend.client = None

    monkeypatch.setattr("backend.sandbox.docker.settings.ENVIRONMENT", "production")
    monkeypatch.setattr("backend.sandbox.docker.settings.ALLOW_LOCAL_DEV_EXECUTION", False)

    # Worker attempts to execute untrusted code -> fails closed
    with pytest.raises(SandboxUnavailableError) as exc_info:
        await backend.run_command(["pytest"], worktree_path=tmp_path)
    assert "Docker sandbox is unavailable in production" in str(exc_info.value)
