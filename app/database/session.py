from collections.abc import Generator

from sqlalchemy.orm import Session, sessionmaker

from app.database.engine import engine

session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db_session() -> Generator[Session, None, None]:
    with session_factory() as session:
        yield session
