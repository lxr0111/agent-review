from fastapi import FastAPI

from app.api.routes import health, reviews, webhooks
from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    app = FastAPI(title=settings.app_name, version=settings.app_version)
    register_exception_handlers(app)
    app.include_router(health.router)
    app.include_router(reviews.router, prefix=settings.api_prefix)
    app.include_router(webhooks.router, prefix=settings.api_prefix)
    return app


app = create_app()
