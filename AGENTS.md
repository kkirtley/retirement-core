# AGENTS.md

## Project

This repository contains `retirement_core`, a deterministic retirement calculation,
reporting, and API foundation written in Python 3.14+.

## Current capabilities

The project currently provides:

- Pydantic models for plans, accounts, income, and annual transactions
- Annual account growth using `Decimal`
- Explicit income, spending, contribution, withdrawal, transfer, Roth conversion,
  and charitable-giving transactions
- Cash accounts with negative-balance protection
- Annual account and household cash-flow reconciliation
- 2026 married-filing-jointly federal tax for ordinary pension income, Roth conversions,
  and taxable Social Security benefits
- Owner-specific RMD and configurable QCD projection processing
- Projected 2026 Missouri married-filing-combined retirement-income tax
- Immutable transaction inputs and generated ledger results
- A FastAPI adapter
- PostgreSQL models and Alembic migration scaffolding
- Versioned external rule-dataset interfaces
- Unit and API tests

The current deterministic timing convention applies annual growth to
beginning-of-year balances, followed by generated income and declared transactions.

Federal tax outside the supported 2026 MFJ scope, state tax outside the projected 2026
Missouri scope, Medicare IRMAA, survivor logic, optimization, reporting exports, and
frontend behavior are not implemented.

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
   `spendable income + cash withdrawals - spending - contributions - taxes
   = surplus or deficit`.
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
- Additional RMD account and inherited-IRA rules
- Medicare premiums and IRMAA
- Survivor and long-term-care scenarios
- Withdrawal and Roth-conversion optimization
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
