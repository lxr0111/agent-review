from fastapi import APIRouter, Response, status
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.database.session import session_factory
from app.schemas.common import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health/live", response_model=HealthResponse)
def liveness() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get(
    "/health/ready",
    response_model=HealthResponse,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": HealthResponse}},
)
def readiness(response: Response) -> HealthResponse:
    checks: dict[str, str] = {}
    try:
        with session_factory() as session:
            session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except SQLAlchemyError:
        checks["database"] = "unavailable"

    redis_client: Redis = Redis.from_url(get_settings().redis_url)
    try:
        redis_client.ping()
        checks["redis"] = "ok"
    except RedisError:
        checks["redis"] = "unavailable"
    finally:
        redis_client.close()

    ready = all(value == "ok" for value in checks.values())
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(status="ok" if ready else "unavailable", checks=checks)
