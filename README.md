# retirement-core

Reusable deterministic retirement calculation engine.

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

Current capabilities include reconciled annual projections, partial-year account-growth
proration, year-specific investment returns, recurring W-2 and VA income, withholding
settlements/refunds, annual federal AGI and IRMAA MAGI records, year-specific
fail-closed federal rules, multi-year Social Security, IRA QCD/RMDs, separate
Traditional 401(k) RMD distributions, 2026 Missouri retirement-income tax within its
documented scope, and Medicare Part B, Part D, and IRMAA cash-flow integration.

Medicare/IRMAA uses premium-year datasets and a two-year MAGI lookback. Federal and
Missouri tax calculations require an explicit matching dataset; real tax data is
currently available only for 2026. Traditional 401(k) RMDs intentionally fail closed
for Missouri until a versioned Missouri classification is added.

The account-growth convention is full configured annual return for complete calendar
years, inclusive actual-day proration for partial years, optional annual overrides,
and growth before transactions. Household cash reconciliation includes income, cash
withdrawals, tax refunds, spending, contributions, additional tax payments, and
Medicare costs.

Not yet implemented: self-employment/payroll/QBI mechanics, survivor or spousal
Social Security switching, Roth-conversion optimization, first-RMD deferral,
stochastic/Monte Carlo modeling, frontend applications, and reporting exports.

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
make pre  # formatting, lint, type checks, tests, and terminal coverage
```
