# Testing

## Automated tests (`pytest`)

The suite has 62 tests. They run on SQLite locally and on PostgreSQL in CI (`RTO_TEST_DATABASE_URL`).

| File | Covers |
|---|---|
| `test_calendar.py` | Manual's 2026 weeks-per-month table, cross-month assignment (Holy Week 2026), ≥ 3 working days rule, year-boundary week |
| `test_compliance.py` | The manual's weekly and monthly examples, per-department threshold, after-dispute figures, annual rate, day-tagging rules |
| `test_import_report.py` | Import of the anonymized sample: 26/26 rows reconcile with the hand-made figures; hierarchy; idempotent re-import |
| `test_services.py` | Visibility (manager subtree, individual contributor, People Partner scope), effective-dated reassignment and cycle prevention, threshold inheritance, full approval workflow, disputes, rejection and withdrawal, finalized-month lock, daily pipeline (taps, Wi-Fi, timekeeping, half days, pending leave), atomic rejection of bad files |
| `test_http.py` | Redirects and 401, every admin page for HR, 403 for employees, API visibility, CSRF enforcement, form and API request creation, dev bearer token, security headers |

In CI, `alembic check` also verifies that the migrations match the models.

## Load test (`tests/load/locustfile.py`)

Scenario: managers browse their team, the report and their inbox. Employees (4× more) open their annual view and
file exemptions.

Prototype measurement:

- **Setup**: 400 simulated users, 90 seconds.
- **Server**: one gunicorn instance with 4 uvicorn workers.
- **Database**: local PostgreSQL 16.
- **Machine**: a single 4-vCPU machine that also ran the load generator.
- **Data**: the demo dataset (47 people).

| Metric | Result |
|---|---|
| Requests | 10,093 (112 req/s) |
| Failures | 1 (0.01%, one connection reset) |
| Median latency | 18 ms |
| p95 / p99 | 96 ms / 170 ms |
| Slowest endpoint | `/api/v1/me` p95 410 ms (first call of each user) |

This shows the request path is cheap and that 400 concurrent users are realistic for one small deployment. It is
**not** a capacity proof. Before rollout, repeat the test on staging:

- production-like data, for example 10,000 synthetic employees (extend `rto.demo.generate_synthetic`, which currently produces one department);
- several pods;
- the managed database;
- the test runner on a separate machine.

```bash
locust -f tests/load/locustfile.py --host https://wos-staging.example.com -u 1000 -r 50 -t 10m --headless
```
