.PHONY: install lint format typecheck test run worker up down migrate

install:
	python -m pip install -e ".[dev]"

lint:
	ruff check .

format:
	ruff format .

typecheck:
	mypy app evals

test:
	pytest

run:
	uvicorn app.main:app --reload

worker:
	celery -A app.workers.celery_app:celery_app worker --loglevel=INFO --pool=solo

up:
	docker compose up --build

down:
	docker compose down

migrate:
	alembic upgrade head
