FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY app ./app
COPY alembic ./alembic
COPY scripts ./scripts
COPY alembic.ini ./
RUN python -m pip install --upgrade pip && python -m pip install .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
