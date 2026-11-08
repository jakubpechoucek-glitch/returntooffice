from __future__ import annotations

import datetime as dt

from fastapi import Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from rto.domain.calendar import first_of_month
from rto.services import ServiceError
from rto.services.access import Principal
from rto.services.org import is_manager
from rto.services.reports import latest_month_with_results


def flash(request: Request, message: str, kind: str = "info") -> None:
    request.session.setdefault("flash", []).append({"kind": kind, "message": message})


def pop_flashes(request: Request) -> list[dict]:
    return request.session.pop("flash", [])


def back(request: Request, fallback: str = "/") -> RedirectResponse:
    ref = request.headers.get("referer") or fallback
    base = str(request.base_url).rstrip("/")
    if ref.startswith(base):
        ref = ref[len(base):] or "/"
    if not ref.startswith("/") or ref.startswith("//"):
        ref = fallback
    return RedirectResponse(ref, status_code=303)


def parse_month(value: str | None, db: Session) -> dt.date:
    if value:
        try:
            return dt.date.fromisoformat(value + "-01") if len(value) == 7 else first_of_month(dt.date.fromisoformat(value))
        except ValueError as exc:
            raise ServiceError("Invalid month, expected YYYY-MM") from exc
    return latest_month_with_results(db) or first_of_month(dt.date.today())


def parse_date(value: str | None) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise ServiceError(f"Invalid date {value!r}") from exc


def nav_ctx(request: Request, db: Session, p: Principal) -> dict:
    from rto.services.requests import inbox

    today = dt.date.today()
    ref = latest_month_with_results(db) or today
    return {
        "user": p,
        "is_manager": is_manager(db, p.id, ref) or is_manager(db, p.id, today),
        "inbox_count": len(inbox(db, p)),
        "flashes": pop_flashes(request),
    }
