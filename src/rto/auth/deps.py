"""FastAPI dependencies resolving the current user (:class:`Principal`) and CSRF protection."""

from __future__ import annotations

import secrets

from fastapi import Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from rto.auth.tokens import TokenError, validate_bearer
from rto.config import get_settings
from rto.db import get_db
from rto.models import Employee, Role
from rto.services import PermissionDenied
from rto.services.access import Principal, load_principal


class NotAuthenticated(Exception):
    pass


class CsrfError(Exception):
    pass


def roles_from_claims(values: list[str] | None) -> set[Role]:
    out = set()
    for v in values or []:
        try:
            out.add(Role(v.upper().replace(".", "_").replace("-", "_")))
        except ValueError:
            continue
    return out


def employee_by_login(db: Session, login: str) -> Employee | None:
    return db.scalar(select(Employee).where(func.lower(Employee.email) == login.strip().lower()))


def _from_bearer(db: Session, request: Request) -> Principal | None:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        return None
    token = header[7:].strip()
    s = get_settings()
    if s.auth_mode == "dev" and token.startswith("dev:"):
        emp = employee_by_login(db, token[4:])
        if emp is None:
            raise NotAuthenticated()
        return load_principal(db, emp)
    try:
        claims = validate_bearer(token)
    except TokenError as exc:
        raise NotAuthenticated() from exc
    login = claims.get(s.oidc_user_claim) or claims.get("upn") or claims.get("email")
    emp = employee_by_login(db, login or "")
    if emp is None:
        raise PermissionDenied("Your account is not linked to an employee record")
    return load_principal(db, emp, roles_from_claims(claims.get(s.oidc_roles_claim)))


def current_principal(request: Request, db: Session = Depends(get_db)) -> Principal:
    p = _from_bearer(db, request)
    if p is not None:
        return p
    uid = request.session.get("uid")
    if not uid:
        raise NotAuthenticated()
    emp = db.get(Employee, uid)
    if emp is None:
        request.session.clear()
        raise NotAuthenticated()
    idp = {Role(r) for r in request.session.get("idp_roles", []) if r in Role.__members__}
    return load_principal(db, emp, idp)


def csrf_token(request: Request) -> str:
    tok = request.session.get("csrf")
    if not tok:
        tok = secrets.token_urlsafe(32)
        request.session["csrf"] = tok
    return tok


async def csrf_protect(request: Request) -> None:
    """Double-submit check for cookie-authenticated, state-changing requests."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    if request.headers.get("authorization", "").lower().startswith("bearer "):
        return  # bearer tokens are not sent automatically by browsers
    expected = request.session.get("csrf")
    sent = request.headers.get("x-csrf-token")
    if sent is None:
        form = await request.form()
        sent = form.get("csrf_token")
    if not expected or not sent or not secrets.compare_digest(str(expected), str(sent)):
        raise CsrfError()
