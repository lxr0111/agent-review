from unittest.mock import MagicMock

from fastapi.testclient import TestClient
from redis.exceptions import RedisError

from app.api.routes import health
from app.main import app


def test_liveness() -> None:
    response = TestClient(app).get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {}}


def test_api_remains_ready_to_accept_durable_work_when_redis_is_down(monkeypatch) -> None:
    database_session = MagicMock()
    redis_client = MagicMock()
    redis_client.ping.side_effect = RedisError("redis unavailable")
    monkeypatch.setattr(health, "session_factory", lambda: database_session)
    monkeypatch.setattr(health.Redis, "from_url", lambda _url: redis_client)

    response = TestClient(app).get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "checks": {"database": "ok", "redis": "unavailable"},
    }
