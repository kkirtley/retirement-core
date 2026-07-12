# retirement-core

Reusable retirement calculation and reporting engine.

## Stack

- Python 3.14 or later
- FastAPI
- Pydantic v2
- PostgreSQL
- SQLAlchemy 2.x
- Alembic
- Pytest, Ruff, mypy

## Architectural rule

The calculation engine does not depend on FastAPI, PostgreSQL, or SQLAlchemy.

```text
Frontend / CLI / Notebook
          |
          v
FastAPI adapter
          |
          v
Application services
          |
          v
Domain + calculation engine
          |
          v
Versioned rule providers
```

The engine contains algorithms. Tax brackets, deductions, RMD tables, IRMAA
thresholds, Medicare premiums, and similar regulatory values live in versioned
data files or database datasets.

## Quick start

```bash
cp .env.example .env
docker compose up --build
```

- API docs: http://localhost:8000/docs
- Health: http://localhost:8000/api/v1/health

## Local development

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
alembic upgrade head
uvicorn retirement_core.api.app:create_app --factory --reload
```

## Tests

```bash
pytest
ruff check .
mypy src
```
