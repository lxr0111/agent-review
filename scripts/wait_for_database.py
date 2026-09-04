import time

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings


def wait_for_database(max_attempts: int = 30, delay_seconds: float = 2.0) -> None:
    engine = create_engine(get_settings().database_url, pool_pre_ping=True)
    try:
        for attempt in range(1, max_attempts + 1):
            try:
                with engine.connect() as connection:
                    connection.execute(text("SELECT 1"))
                return
            except SQLAlchemyError:
                if attempt == max_attempts:
                    raise
                time.sleep(delay_seconds)
    finally:
        engine.dispose()


if __name__ == "__main__":
    wait_for_database()
