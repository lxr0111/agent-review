from sqlalchemy import Engine, create_engine

from app.core.config import get_settings


def create_database_engine() -> Engine:
    settings = get_settings()
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_recycle=1800,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
    )


engine = create_database_engine()
