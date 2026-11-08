# ADR-0003: Microsoft Entra ID via OpenID Connect

**Status**: accepted (2026-11-08)

## Decision
- Browser sign-in uses the authorization-code flow with PKCE (confidential client) and a server-side session cookie.
- APIs use Entra access tokens (bearer), validated against the tenant JWKS.
- Users are matched to employees by e-mail/UPN. App roles in the token grant company-wide roles; scoped roles
  (People Partner of a unit) are stored in the application.
- The development impersonation login is refused in production.

## Consequences
- No passwords in the application. MFA and Conditional Access are enforced by Entra ID.
- The employee e-mail must equal the Entra UPN (or `RTO_OIDC_USER_CLAIM` must be changed).
