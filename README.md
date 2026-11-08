# WOS Tracker: Work Onsite Compliance

A web application that automates the **Monthly Onsite Report**. It tracks whether employees meet their
weekly *work onsite* (WOS) commitment under the Working Arrangements Manual. It also replaces e-mail and
spreadsheet handling of exemptions and disputes with an approval workflow, and gives every manager a view
of their own reporting line.

> **Status: prototype / MVP.** It is functionally complete for a pilot with 1–2 departments and is designed to
> be hardened by IT: PostgreSQL, Microsoft Entra ID (OIDC / OAuth 2.0), containers, Kubernetes and Helm.
> See the [hardening checklist](docs/deployment.md#hardening-checklist-for-it).

## What it does

| Process-map step | Feature |
|---|---|
| 1. Policy & scope | Per-department thresholds with inheritance; holiday calendars per office; waivers (excluded periods) |
| 2–3. Data sources & extraction | Importers: legacy report (.xlsx), masterlist/hierarchy (.csv), door/timekeeping/Wi-Fi presence (.csv), leave & OB (.csv) |
| 4. Consolidation | Employees, org units and reporting lines are effective-dated; raw events are linked by employee number |
| 5. Onsite day tagging | Each day is tagged Onsite / Leave / OB / Holiday / Not onsite, with review flags (single tap, Wi-Fi only, …) |
| 6. Computation | Weekly met / not met, monthly adherence, annual compliance, as initial and after-dispute figures |
| 7. Aggregation | Team tree (every manager sees their whole subtree), department and manager roll-ups, Excel/CSV export in the legacy layout |
| 8. Validation & exceptions | Self-service **weekly** exemptions, disputes and commitment changes: employee → manager → People Partner |
| 9. Finalization | Month lifecycle: Open → Published (review window) → Finalized (locked) |
| 10. Discussion | Audit log, per-person history, reconciliation reports |

Importing the anonymized sample report (`sample_data/`) reproduces the hand-made figures exactly: **26 of 26 rows reconcile**.

## Quick start (local)

Requirements: Python 3.12+ and PostgreSQL 14+ (or Docker).

```bash
docker compose up --build
```

Then open http://localhost:8000 and pick a persona on the development sign-in page.

Without Docker:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
export RTO_DATABASE_URL=postgresql+psycopg://postgres@localhost:5432/rto
rto migrate
rto seed-demo
uvicorn rto.main:app --reload
```

`rto seed-demo` loads two kinds of data:

- **Collections**: the anonymized legacy report for Jan and Mar 2026, with weekly totals only.
- **Risk Management**: a fictional pilot department with 27 people and 4 management levels. It has daily door, timekeeping, Wi-Fi, leave and OB data for Jan–Mar 2026. Fraud Risk has a stricter 100% monthly threshold to demonstrate per-department settings.

| Persona (dev sign-in) | Shows |
|---|---|
| `hr.admin@example.com` | Everything, including administration |
| `hr.analytics@example.com` | Data operations: imports, months |
| `pp.risk@example.com`, `pp.collections@example.com` | People Partner scope and review inbox |
| `nina.cabrera@example.com`, `andrea.agustin@example.com` | Senior managers, multi-level team tree |
| `camille.pascual@example.com`, `daphne.lim@example.com` | Team leads with pending approvals |
| `hazel.ramos@example.com`, `francis.flores@example.com` | Individual employees |

## Tests

```bash
pytest
RTO_TEST_DATABASE_URL=postgresql+psycopg://... pytest
ruff check src tests
```

The first command runs on SQLite. The second runs the same tests on PostgreSQL, as CI does.

The test suite covers:

- the policy examples from the manual;
- the 2026 weeks-per-month table;
- reconciliation against the sample report;
- the approval workflow;
- data visibility;
- per-department thresholds;
- the daily pipeline;
- the HTTP layer (CSRF, roles, security headers).

## Documentation

| Document | Content |
|---|---|
| [docs/compliance-rules.md](docs/compliance-rules.md) | Every policy rule as implemented, with worked examples and configurable values |
| [docs/architecture.md](docs/architecture.md) | Components, data flow, scaling model, code layout |
| [docs/data-model.md](docs/data-model.md) | Tables, effective dating, derived results |
| [docs/data-import.md](docs/data-import.md) | File layouts, reconciliation, findings on the sample data |
| [docs/security.md](docs/security.md) | Entra ID, roles and visibility matrix, CSRF / headers, privacy, audit |
| [docs/deployment.md](docs/deployment.md) | Configuration reference, Kubernetes / Helm, Entra app registration, operations, **hardening checklist** |
| [docs/user-guide.md](docs/user-guide.md) | How employees, managers, People Partners and HR use the tool |
| [docs/testing.md](docs/testing.md) | Test strategy and load-test results |
| [docs/adr/](docs/adr/) | Architecture decision records |
| [docs/00-discovery.md](docs/00-discovery.md) | Requirements discovery and decision log |
| [docs/open-questions-hr.md](docs/open-questions-hr.md) | Open policy questions for HR, with the defaults currently implemented |

## Repository layout

```
src/rto/
  domain/        pure policy logic: calendar, tagging, compliance (no I/O)
  services/      use cases: compute, requests workflow, access control, org/hierarchy, policy, reports
  importers/     legacy report, masterlist, daily presence/absence
  auth/          Entra ID OIDC, bearer tokens, CSRF
  api/           JSON API v1 (OpenAPI at /api/docs)
  web/           server-rendered UI (Jinja2) and static assets
  cli.py         rto migrate | seed-demo | import | recompute | grant
migrations/      Alembic
deploy/helm/rto  Kubernetes Helm chart
tests/           unit, integration, HTTP and load (locust) tests
sample_data/     anonymized legacy report and synthetic pilot CSVs (fictional people, example.com)
```
