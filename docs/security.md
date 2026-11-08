# Security and privacy

## Authentication

| Mode | Use | How |
|---|---|---|
| `RTO_AUTH_MODE=oidc` | staging, production | OpenID Connect authorization-code flow with **PKCE** against **Microsoft Entra ID** (Authlib). The ID token is validated (issuer, audience, signature, nonce). The signed-in account is matched to an employee by `preferred_username` / e-mail. |
| Bearer token | API clients, Power BI, integrations | `Authorization: Bearer <Entra access token>`. Validated against the tenant JWKS: RS256, `aud = RTO_API_AUDIENCE`, `iss`, `exp`. |
| `RTO_AUTH_MODE=dev` | local, test | Impersonation page, plus `Bearer dev:<email>` for load tests. **The app refuses to start** with `RTO_ENVIRONMENT=production` and dev auth. |

Sessions are signed (itsdangerous) `HttpOnly`, `SameSite=Lax` cookies, `Secure` in production, with an 8-hour lifetime.
The session is cleared and re-issued at sign-in to prevent session fixation. Sign-out also ends the Entra ID session.

### Entra ID app roles (optional)

Define app roles `HR_ADMIN`, `HR_ANALYTICS`, `PEOPLE_PARTNER`, `EXECUTIVE` and `AUDITOR` on the app
registration and assign them to security groups. Roles in the token's `roles` claim apply **company-wide**.
Scoped roles (for example a People Partner for one department group) are granted in *Admin → Roles*.

## Authorization

Implemented once in `services/access.py` and used by the UI and the API.

| Role | Sees | Can do |
|---|---|---|
| Employee (implicit) | Own data | File exemptions, disputes, commitment changes for self; withdraw own requests |
| Manager (implicit: has reports) | Direct **and indirect** reports, using reporting lines as of the period viewed | File for reports; approve the manager step |
| PEOPLE_PARTNER (scoped) | Everyone in the scoped unit(s) and sub-units | Review step of requests in scope |
| EXECUTIVE (scoped) | Scoped unit(s) | Read only |
| HR_ANALYTICS | Everyone | Imports, people and hierarchy edits, recompute, publish and finalize months |
| HR_ADMIN | Everyone | Everything, including thresholds, roles, calendar, reopening months, and acting on any approval step (audited as an override) |
| AUDITOR | Everyone, plus the audit log | Read only |

Additional rules:

- Nobody approves their own submission. When the submitter is the direct manager, the step routes to the next-level manager.
- Employees and managers cannot file requests touching a **finalized** month.
- Exports apply the same visibility as the screens, and each export is audited.

## Web security controls

| Control | Implementation |
|---|---|
| CSRF | Per-session token in a hidden form field or the `X-CSRF-Token` header for every cookie-authenticated write; constant-time compare |
| XSS | Jinja2 autoescaping; **CSP** `default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'`; no inline scripts or styles |
| Clickjacking | `X-Frame-Options: DENY`, `frame-ancestors 'none'` |
| Transport | HSTS when `RTO_SESSION_HTTPS_ONLY=true`; TLS terminated at the ingress |
| Injection | SQLAlchemy bound parameters only; CSV export neutralises spreadsheet formulas (`=`, `+`, `-`, `@`) |
| Uploads | Size limit (`RTO_MAX_UPLOAD_MB`); parsed in memory, never written to disk or executed |
| Open redirect | `next` parameter restricted to relative paths |
| Container | Non-root UID 10001, read-only root filesystem, all capabilities dropped, seccomp `RuntimeDefault` |
| Network | NetworkPolicy: ingress only from the ingress controller; egress only to PostgreSQL, 443 (Entra ID) and DNS; `/metrics` blocked at the ingress |

## Privacy (Philippine Data Privacy Act)

- **Data minimisation.** No medical details are stored. Requests use a category ("Medical"), and the form warns users not to
  write medical information. Leave subtypes should not carry diagnoses.
- **Wi-Fi data** is used only as a presence signal. Employees see the day status and source type, not raw logs (Q25).
- **Purpose limitation.** The data is used only for the onsite compliance report.
- **Retention.** Proposed: raw events 13 months, results 5 years (Q17). Implement as a scheduled purge job when confirmed.
- **Audit trail.** Every change, approval, import, export and login is recorded with actor and timestamp.
- **Sample data** in the repository is anonymized (fictional names, `example.com`). Never commit real extracts.
- **Pilot data.** Real or masked pilot data only goes to a company-approved environment (see `docs/deployment.md`).

## Reporting vulnerabilities

Report internally to the IT security team. Do not open public issues.
