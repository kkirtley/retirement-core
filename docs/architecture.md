# Architecture

## Layers

- **Domain:** pure Pydantic and Python domain types.
- **Engine:** pure deterministic calculations.
- **Application:** use-case orchestration.
- **Infrastructure:** PostgreSQL, SQLAlchemy, Alembic and rule-data loaders.
- **API:** FastAPI adapter only.

## Persistence

Relational tables hold households, plans, immutable plan versions, runs and
ledger results. JSONB holds immutable input snapshots, scenario overrides,
optimizer settings, warnings, summaries and provenance.

## Precision

Use `Decimal` in Python and `NUMERIC` in PostgreSQL. Never use binary floating
point for money or tax calculations.

## Reproducibility

Every run records engine, plan-schema and rule-dataset versions.
