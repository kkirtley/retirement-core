# ADR-0001: Python, FastAPI and PostgreSQL

## Status
Accepted

## Decision
Use Python 3.13 for calculations, FastAPI for the HTTP adapter and PostgreSQL
for persistence.

## Rationale
Python supports financial analysis, FastAPI exposes typed OpenAPI contracts to
multiple frontends, and PostgreSQL provides integrity, transactions, NUMERIC
precision, analytical queries and selective JSONB flexibility.
