.PHONY: install test format-check lint typecheck pre run migrate
install:
	pip install -e ".[dev]"
test:
	pytest
format-check:
	ruff format --check .
lint:
	ruff check .
typecheck:
	mypy src
pre: format-check lint typecheck test
run:
	uvicorn retirement_core.api.app:create_app --factory --reload
migrate:
	alembic upgrade head
