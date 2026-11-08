# ADR-0002: Server-rendered UI plus a JSON API

**Status**: accepted (2026-11-08)

## Context
The users (employees, managers, HR) need simple tables and forms. The company network may block CDNs, and a
separate SPA would add a Node toolchain, a second deployable and token handling in the browser.

## Decision
- The UI is Jinja2 templates with one CSS file and minimal JavaScript, served by the same app (BFF pattern:
  OIDC session cookie, no tokens in the browser).
- The same use cases are exposed as `/api/v1` (OpenAPI) for integrations (Power BI, Teams, a future SPA).

## Consequences
- A strict CSP (`script-src 'self'`) and CSRF tokens are possible.
- Rich client interactivity is limited. A SPA can be added later on top of the API.
