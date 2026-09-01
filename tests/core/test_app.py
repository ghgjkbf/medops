"""Tests for the FastAPI skeleton app (Task 10)."""

from fastapi.testclient import TestClient
from medops_core.app import app


def test_health_endpoint_ok() -> None:
    client = TestClient(app)
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "medops-core"
    assert body["mcp_servers"] == []  # no registry configured in P0 skeleton
