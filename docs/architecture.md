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

## Transaction timing

The current projection engine uses a temporary deterministic annual convention:

1. Apply annual growth to each beginning-of-year account balance.
2. Apply generated income transactions.
3. Apply plan transactions in their declared order.
4. Calculate supported federal ordinary-income tax and apply its cash payment.

This convention is intentionally simple and must be replaced or made configurable before
modeling intra-year transaction dates or sequence-of-returns effects.

Cash accounts default to a zero annual return. They receive spendable income and fund
spending, contributions, and cash charitable gifts. Negative cash balances are rejected
unless a plan explicitly enables them. Roth conversions and internal transfers move value
between accounts without affecting household spendable cash.

For 2026 married-filing-jointly projections, federally taxable pension income and
Roth conversions form the supported ordinary-income subtotal. Roth conversions are
taxable but not spendable. Federal tax is paid from the explicitly configured cash
account and participates in household cash-flow reconciliation.

## Reproducibility

Every run records engine, plan-schema and rule-dataset versions.
