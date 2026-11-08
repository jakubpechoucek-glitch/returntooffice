# ADR-0004: Effective-dated hierarchy and materialised results

**Status**: accepted (2026-11-08)

## Context
Visibility must follow the reporting line (replacing Power BI row-level security), and managers change.
Hundreds of managers open reports at the same time.

## Decision
- Reporting lines, departments, offices and commitments are effective-dated rows. Admin changes close the
  current row and open a new one.
- Visibility for a month uses the reporting lines **as of that month**.
- Weekly and monthly results are materialised and recomputed idempotently on every input change. Monthly
  results keep a snapshot of department, manager and threshold.

## Consequences
- Historical reports are reproducible and cheap to read.
- Every write path must trigger a recompute of the affected employees and months; this is centralised in
  `services/compute.py`. A nightly CronJob is the safety net.
