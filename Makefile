.PHONY: install test lint typecheck run migrate
install:
	pip install -e ".[dev]"
test:
	pytest
lint:
	ruff check .
typecheck:
	mypy src
run:
	uvicorn retirement_core.api.app:create_app --factory --reload
migrate:
	alembic upgrade head
