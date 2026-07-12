.PHONY: install test coverage format-check lint typecheck pre run migrate
install:
	pip install -e ".[dev]"
test:
	pytest
coverage:
	pytest --cov=retirement_core --cov-report=term
format-check:
	ruff format --check .
lint:
	ruff check .
typecheck:
	mypy src
pre: format-check lint typecheck coverage
run:
	uvicorn retirement_core.api.app:create_app --factory --reload
migrate:
	alembic upgrade head
