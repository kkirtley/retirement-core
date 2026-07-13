# AGENTS.md

## Project

This repository contains `retirement_core`, a deterministic retirement calculation
engine and API foundation written in Python 3.14+.

## Current capabilities

The project currently provides:

- Pydantic models for plans, accounts, income, and annual transactions
- Deterministic annual account and household cash-flow reconciliation
- Full-year account growth and simple actual-calendar-day proration for partial
  years, with growth applied before annual transactions
- Negative investment returns and optional year-specific return overrides
- Explicit income, spending, contribution, withdrawal, transfer, Roth conversion,
  and charitable-giving transactions
- Cash accounts with negative-balance protection
- Typed recurring W-2 wages and VA disability income, including annual overrides
- Withholding-aware federal and Missouri settlements and refunds
- Annual federal AGI and IRMAA MAGI records with component provenance
- Federal tax-rule selection by tax year; federally relevant unsupported years
  fail closed rather than being treated as zero tax
- Multi-year Social Security benefit generation and 2026 MFJ taxable-benefit
  calculation, including tax-exempt interest in provisional income
- IRA RMD and configurable QCD projections, plus separate Traditional 401(k)
  obligations and account-specific 401(k) RMD distributions
- 2026 married-filing-jointly federal tax within the supported ordinary-income scope
- Projected 2026 Missouri married-filing-combined retirement-income tax within its
  documented scope
- Medicare Part B, Part D, and IRMAA premium cash-flow projections using
  premium-year rules and a two-year MAGI lookback
- Immutable transaction inputs and generated ledger results
- A FastAPI adapter
- PostgreSQL models and Alembic migration scaffolding
- Versioned external rule-dataset interfaces
- Unit and API tests with terminal coverage reporting through `make pre`

Account balances are measured at `plan.start_date`. Full calendar years use the
configured annual return; partial years use simple inclusive actual-day proration.
An account may override its return for a specific year. Growth is applied before
generated and declared transactions.

Only years with explicit federal and Missouri datasets are supported: there are no
real federal or Missouri tax datasets after 2026. Medicare premium years are limited
to the datasets present in the repository.

## Repository structure

```text
src/retirement_core/
  api/              FastAPI adapter
  application/      Use-case services
  domain/           Pydantic models and enums
  engine/           Projection, transaction, and reconciliation logic
  infrastructure/   Database and rule-provider adapters
  reporting/        Reporting scaffold
  rules/            Versioned rule-dataset contracts

tests/              Unit and API tests
data/rules/         Versioned regulatory data and placeholders
examples/           Example plan inputs
alembic/            Database migrations
docs/               Architecture documentation and ADRs
```

## Permanent engineering rules

1. Financial calculations must be deterministic unless a scenario explicitly
   requests stochastic behavior.
2. Use `Decimal` for financial values; never use binary floating point.
3. Every account must conceptually reconcile annually:
   `beginning + growth + income + contributions + transfers in - spending - withdrawals
   - transfers out = ending`.
   The current aggregate account result records income in `contributions` and spending
   in `withdrawals`; transaction ledger entries preserve their explicit types.
4. Household cash flow must reconcile annually:
   `spendable income + cash withdrawals + federal tax refunds + Missouri tax refunds
   - spending - contributions - federal tax payments - Missouri tax payments
   - Medicare costs = surplus or deficit`.
   Withholding is already reflected in configured spendable W-2 cash and is not
   subtracted again.
5. A reconciliation difference greater than `$0.01` must fail the projection.
6. Transfers must be explicit. Roth conversions are account transfers, not
   spendable household cash.
7. Never silently change a planning assumption. Document intentional assumption
   changes in the relevant input, test, ADR, or architecture document.
8. Authoritative financial logic belongs in the domain or engine, not in API,
   database, reporting, or frontend code.
9. Regulatory values must come from versioned, attributable datasets rather than
   being hard-coded in calculation functions.
10. Every financial rule or bug fix requires tests, including boundary and
    reconciliation cases.
11. Preserve auditability: material results must be traceable to inputs,
    transactions, calculation rules, and annual ledger entries.
12. Treat retirement and household data as private. Do not add external telemetry,
    analytics, or data transmission without explicit approval.
13. Preserve backward compatibility for plan inputs when practical. Clearly
    document intentional schema changes.

## Planned behavior

Future work may add:

- Additional federal and state income-tax rules
- Additional Social Security benefit and taxation rules
- Additional RMD account types, first-RMD deferral, and inherited-IRA rules
- Medicare appeals, late-enrollment penalties, hold-harmless, Extra Help, survivor
  behavior, and other advanced Medicare behavior
- Survivor and long-term-care scenarios
- Withdrawal and Roth-conversion optimization
- Self-employment tax, QBI, payroll taxes, and payroll-contribution mechanics
- Traditional 401(k) Missouri retirement-subtraction classification
- Monte Carlo or other stochastic market modeling
- Versioned JSON, CSV, and Excel reports
- Additional input validation and schema formats
- Frontend applications that consume engine results without duplicating logic

Planned behavior must not be treated as implemented until corresponding code,
versioned data, and tests exist.

## Verification

Run the complete preflight suite:

```bash
make pre
```

Run tests directly:

```bash
pytest
```

Run the local API:

```bash
uvicorn retirement_core.api.app:create_app --factory --reload
```

Before completing a change, ensure formatting, linting, type checking, and tests pass.
Do not commit generated environments, caches, secrets, or local output artifacts.
