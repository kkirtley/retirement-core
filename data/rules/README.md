# Rule datasets

The engine does not hard-code tax, Medicare, RMD, Social Security, or IRMAA values.
Each dataset records its ID, type, jurisdiction, applicable year, version, legal
status, values, source provenance, and any projection assumptions.

RMD/QCD datasets use `effective_from` and `effective_to`. Projections select the latest
applicable version for each year, so an open-ended dataset remains usable without a
duplicate file for every future calendar year.

The 2026 federal-tax dataset contains verified married-filing-jointly standard-deduction
and ordinary-income bracket values from IRS Revenue Procedure 2025-32, plus Social
Security taxation thresholds and rates from 26 U.S.C. Section 86. Other filing statuses
and federal-tax features are not yet implemented.
