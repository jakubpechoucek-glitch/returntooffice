"""Login / logout.

* ``RTO_AUTH_MODE=oidc``: OpenID Connect authorization-code flow (with PKCE) against Microsoft Entra ID.
  The signed-in user is matched to an employee by e-mail / UPN. Entra ID *app roles* in the ``roles``
  claim (HR_ADMIN, HR_ANALYTICS, PEOPLE_PARTNER, EXECUTIVE, AUDITOR) are honoured company-wide in
  addition to roles assigned in the application.
* ``RTO_AUTH_MODE=dev``: a page to impersonate any employee. Refused at start-up in production.
"""

from __future__ import annotations

from urllib.parse import quote

from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from rto.auth.deps import csrf_protect, employee_by_login, roles_from_claims
from rto.config import get_settings
from rto.db import get_db
from rto.models import Employee, RoleAssignment
from rto.services.audit import audit
from rto.web.templating import render

router = APIRouter()
_oauth: OAuth | None = None


def oauth() -> OAuth:
    global _oauth
    if _oauth is None:
        s = get_settings()
        _oauth = OAuth()
        _oauth.register(
            name="entra",
            client_id=s.oidc_client_id,
            client_secret=s.oidc_client_secret,
            server_metadata_url=s.oidc_metadata_url,
            client_kwargs={"scope": "openid email profile", "code_challenge_method": "S256"},
        )
    return _oauth


def _safe_next(nxt: str | None) -> str:
    return nxt if nxt and nxt.startswith("/") and not nxt.startswith("//") else "/"


def _start_session(request: Request, emp: Employee, idp_roles: list[str]) -> None:
    request.session.clear()  # prevent session fixation
    request.session["uid"] = emp.id
    request.session["idp_roles"] = idp_roles


@router.get("/login", response_class=HTMLResponse)
async def login(request: Request, next: str | None = None, q: str = "", db: Session = Depends(get_db)):
    s = get_settings()
    if s.auth_mode == "oidc":
        request.session["next"] = _safe_next(next)
        return await oauth().entra.authorize_redirect(request, f"{s.base_url}/auth/callback")
    # dev impersonation page
    stmt = select(Employee).order_by(Employee.in_scope, Employee.last_name).limit(200)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(Employee.email.ilike(like), Employee.last_name.ilike(like), Employee.first_name.ilike(like)))
    people = list(db.scalars(stmt))
    roles: dict[int, list[str]] = {}
    for ra in db.scalars(select(RoleAssignment)):
        roles.setdefault(ra.employee_id, []).append(ra.role.value)
    return render(request, "login_dev.html", people=people, roles=roles, next=_safe_next(next), q=q, user=None)


@router.post("/login/dev", dependencies=[Depends(csrf_protect)])
def dev_login(request: Request, email: str = Form(...), next: str = Form("/"), db: Session = Depends(get_db)):
    if get_settings().auth_mode != "dev":
        return RedirectResponse("/login", status_code=303)
    emp = employee_by_login(db, email)
    if emp is None:
        return RedirectResponse("/login", status_code=303)
    _start_session(request, emp, [])
    audit(db, emp, "auth.login", "employee", emp.id, {"mode": "dev"})
    db.commit()
    return RedirectResponse(_safe_next(next), status_code=303)


@router.get("/auth/callback")
async def callback(request: Request, db: Session = Depends(get_db)):
    s = get_settings()
    try:
        token = await oauth().entra.authorize_access_token(request)
    except OAuthError as exc:
        return render(request, "error.html", status_code=401, user=None, title="Sign-in failed", message=exc.description or exc.error)
    claims = token.get("userinfo") or {}
    login_name = claims.get(s.oidc_user_claim) or claims.get("email") or claims.get("upn") or ""
    emp = employee_by_login(db, login_name)
    if emp is None:
        return render(
            request, "error.html", status_code=403, user=None, title="No access",
            message=f"The account {login_name} is not linked to an employee record. Please contact HR.",
        )
    nxt = request.session.get("next", "/")
    idp_roles = sorted(r.value for r in roles_from_claims(claims.get(s.oidc_roles_claim)))
    _start_session(request, emp, idp_roles)
    audit(db, emp, "auth.login", "employee", emp.id, {"mode": "oidc", "idp_roles": idp_roles})
    db.commit()
    return RedirectResponse(_safe_next(nxt), status_code=303)


@router.post("/logout", dependencies=[Depends(csrf_protect)])
def logout(request: Request):
    request.session.clear()
    s = get_settings()
    if s.auth_mode == "oidc":
        url = (
            f"{s.oidc_authority}/{s.oidc_tenant_id}/oauth2/v2.0/logout"
            f"?post_logout_redirect_uri={quote(s.base_url + '/', safe='')}"
        )
        return RedirectResponse(url, status_code=303)
    return RedirectResponse("/login", status_code=303)
