from unittest.mock import MagicMock, patch

import pytest

# `client`/`app` always provision a real db_session (see conftest.py), and
# the first test below deliberately exercises the real database connection.
pytestmark = pytest.mark.integration


def test_ready_endpoint_all_dependencies_available(client):
    with (
        patch("app.api.health.get_redis_client") as mock_redis,
        patch("app.api.health.get_qdrant_client") as mock_qdrant,
    ):
        mock_redis.return_value.ping.return_value = True
        mock_qdrant.return_value.get_collections.return_value = []

        response = client.get("/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["checks"]["redis"] == "ok"
    assert body["checks"]["qdrant"] == "ok"


def test_ready_endpoint_reports_degraded_when_redis_unavailable(client):
    with (
        patch("app.api.health.get_redis_client") as mock_redis,
        patch("app.api.health.get_qdrant_client") as mock_qdrant,
    ):
        mock_redis.return_value.ping.side_effect = ConnectionError("refused")
        mock_qdrant.return_value.get_collections.return_value = []

        response = client.get("/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["redis"] == "unavailable"


def test_ready_endpoint_reports_degraded_when_database_unavailable(client, app):
    broken_db = MagicMock()
    broken_db.execute.side_effect = ConnectionError("refused")

    from app.db.session import get_db

    def override_get_db():
        yield broken_db

    app.dependency_overrides[get_db] = override_get_db

    with (
        patch("app.api.health.get_redis_client") as mock_redis,
        patch("app.api.health.get_qdrant_client") as mock_qdrant,
    ):
        mock_redis.return_value.ping.return_value = True
        mock_qdrant.return_value.get_collections.return_value = []

        from fastapi.testclient import TestClient

        response = TestClient(app).get("/ready")

    assert response.status_code == 200
    assert response.json()["checks"]["database"] == "unavailable"
