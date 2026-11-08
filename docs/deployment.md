# Deployment and operations

## Configuration reference

All settings are environment variables prefixed `RTO_` (`src/rto/config.py`). Secrets must come from a secret
manager, never from the Helm values or the image.

| Variable | Default | Notes |
|---|---|---|
| `RTO_ENVIRONMENT` | `development` | `production` enforces OIDC, a real secret key and HTTPS-only cookies |
| `RTO_DATABASE_URL` | – | **secret**. `postgresql+psycopg://user:pass@host:5432/rto?sslmode=require` |
| `RTO_SECRET_KEY` | dev value | **secret**. ≥ 32 random bytes; signs session cookies. Rotating it signs everyone out |
| `RTO_AUTH_MODE` | `dev` | `oidc` in staging/production |
| `RTO_OIDC_TENANT_ID` / `RTO_OIDC_CLIENT_ID` | – | Entra ID app registration |
| `RTO_OIDC_CLIENT_SECRET` | – | **secret** (or replace with a certificate / workload identity when hardening) |
| `RTO_OIDC_USER_CLAIM` | `preferred_username` | Claim matched to `employee.email` |
| `RTO_API_AUDIENCE` | – | For bearer tokens, for example `api://<client-id>` |
| `RTO_BASE_URL` | `http://localhost:8000` | Public URL. The redirect URI is `<base>/auth/callback` |
| `RTO_SESSION_HTTPS_ONLY` | `false` | `true` in production |
| `RTO_SESSION_MAX_AGE_SECONDS` | `28800` | |
| `RTO_APPROVAL_CHAIN` | `["DIRECT_MANAGER","PEOPLE_PARTNER"]` | Also `NEXT_LEVEL_MANAGER`, for example `["DIRECT_MANAGER","NEXT_LEVEL_MANAGER","PEOPLE_PARTNER"]` |
| `RTO_DISPUTE_WINDOW_DAYS` | `7` | Review window after a month is published |
| `RTO_DB_POOL_SIZE` / `RTO_DB_MAX_OVERFLOW` | `10` / `20` | **Per gunicorn worker** |
| `RTO_MAX_UPLOAD_MB` | `25` | |
| `RTO_LOG_LEVEL` | `INFO` | JSON logs outside development |
| `WEB_CONCURRENCY` | `4` | gunicorn workers per container |
| `PROMETHEUS_MULTIPROC_DIR` | `/tmp/prometheus` (image) | Aggregates metrics across workers |

## Microsoft Entra ID app registration

1. *Entra admin center → App registrations → New registration*. Name it "WOS Tracker". Supported account
   types: single tenant.
2. Redirect URI (Web): `https://<host>/auth/callback`. Front-channel logout URL: `https://<host>/`.
3. *Certificates & secrets*: create a client secret. Store it in the secret manager as `RTO_OIDC_CLIENT_SECRET`.
   For hardening, prefer a certificate or workload identity federation.
4. *Token configuration*: add the optional claims `email` and `preferred_username` to the ID token.
5. *Expose an API* (only for API clients): set the Application ID URI `api://<client-id>` and add a scope
   `Reports.Read`. Set `RTO_API_AUDIENCE=api://<client-id>`.
6. *App roles* (optional): `HR_ADMIN`, `HR_ANALYTICS`, `PEOPLE_PARTNER`, `EXECUTIVE`, `AUDITOR`.
   Assign them to security groups under *Enterprise applications → Users and groups*.
7. Set *Assignment required* = Yes, and assign the pilot population's group so that only they can sign in.

## Kubernetes (Helm)

```bash
kubectl create namespace wos
helm upgrade --install wos deploy/helm/rto -n wos -f values-production.yaml
```

Before installing, create the secret `rto-secrets` with the keys `RTO_DATABASE_URL`, `RTO_SECRET_KEY` and
`RTO_OIDC_CLIENT_SECRET`, preferably through External Secrets / Azure Key Vault CSI.

The chart contains:

| Resource | Notes |
|---|---|
| Deployment | 3+ replicas, zone spread, rolling update with `maxUnavailable: 0`, startup/readiness/liveness probes, non-root, read-only root filesystem |
| HPA | 3–12 replicas at 65% CPU |
| PodDisruptionBudget | minAvailable 2 |
| Migration Job | Helm `pre-install` / `pre-upgrade` hook running `rto migrate`. A failed migration blocks the rollout |
| CronJob | Nightly `rto recompute` of open months (safety net) |
| Ingress | TLS; `/metrics` denied; 25 MB body limit |
| NetworkPolicy | Least privilege, as described in [security.md](security.md) |
| ServiceMonitor | Optional (Prometheus Operator) |

### PostgreSQL

Use a managed, highly available service (Azure Database for PostgreSQL Flexible Server, zone-redundant HA) with
PostgreSQL 16 or later:

- TLS required (`sslmode=require`);
- private endpoint;
- point-in-time restore (≥ 7 days);
- daily backups retained per policy;
- an application role with `CONNECT` plus DML only, and a separate migration role owning the schema.

**Connection budget**: `pods × WEB_CONCURRENCY × (pool_size + max_overflow)`. With 12 pods, 4 workers and 5+10,
that is 720 connections, too many for a small server. Either lower the pool, or add **PgBouncer** in transaction mode.

## Operations

| Task | How |
|---|---|
| First HR admin | `kubectl exec deploy/wos-rto -- rto grant first.admin@company.com HR_ADMIN` |
| Load data | *Admin → Imports*, or `rto import presence file.csv` |
| Recompute | *Admin → Months → Recompute*, or `rto recompute --from 2026-01` |
| Close a month | *Admin → Months → Publish*, then *Finalize* after the review window |
| Health | `/healthz` (liveness), `/readyz` (database) |
| Metrics | `/metrics`: `rto_http_requests_total`, `rto_http_request_seconds` |
| Logs | JSON on stdout with `request_id` (also returned as the `X-Request-ID` header) |

## Hardening checklist for IT

Items marked ✅ are already in the prototype. ☐ items are recommended before company-wide rollout.

**Identity and access**
- ✅ OIDC with PKCE against Entra ID; bearer-token validation for the API
- ✅ Dev login refused in production
- ☐ Replace the client secret with a certificate or workload identity federation
- ☐ Conditional Access policy for the app (MFA, compliant device)
- ☐ Decide whether roles come from Entra groups (app roles) or the in-app grant UI, or both
- ☐ Server-side session revocation (Redis) if forced logout is required

**Data**
- ✅ Migrations (Alembic) run as a gated Helm hook
- ✅ Audit log; no medical free text
- ☐ Retention and purge job (Q17); partition `presence_event` and `day_record` by month
- ☐ Encryption at rest (managed PostgreSQL default) and backup restore drill
- ☐ DPIA / privacy notice for the use of Wi-Fi data

**Integration**
- ☐ Adapters for the real DACS, HC Connect, timekeeping and Splunk extracts, then scheduled pulls from the DWH
- ☐ Masterlist sync from the HR system of record (nightly), with the admin UI for corrections
- ☐ E-mail or Teams notifications via Microsoft Graph (`services/notifications.py` is the integration point), sent through a queue

**Platform**
- ✅ Non-root, read-only root filesystem, dropped capabilities, NetworkPolicy, PDB, HPA, probes
- ✅ CI: lint, tests on PostgreSQL, migration-drift check, image build, Trivy scan (report-only), Helm lint
- ☐ Make Trivy blocking; sign images (cosign); pin base image digests; Dependabot / Renovate
- ☐ WAF / rate limiting at the ingress; restrict `--forwarded-allow-ips` to the ingress CIDR
- ☐ PgBouncer; read replica for exports if needed
- ☐ Background worker (queue) for large imports and recompute
- ☐ Penetration test; load test on staging with production-like volumes (see [testing.md](testing.md))
- ☐ Dashboards and alerts: error rate, p95 latency, failed imports, DB connections
