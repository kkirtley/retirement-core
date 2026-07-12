# ADR-0003: Immutable plan versions and runs

## Status
Accepted

## Decision
Separate plan identity, immutable plan versions and immutable scenario runs.

```text
Household -> Plan -> PlanVersion -> ScenarioRun -> Results
```

A later edit must not change the meaning of a historical result.
