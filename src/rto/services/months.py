"""Report month lifecycle (process steps 8-9): OPEN -> PUBLISHED (review window) -> FINALIZED."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from rto.config import get_settings
from rto.domain.calendar import first_of_month
from rto.models import Employee, MonthStatus, ReportMonth
from rto.services import ServiceError
from rto.services.audit import audit
from rto.services.compute import recompute_month
from rto.services.notifications import notify


def get_month(db: Session, month: dt.date) -> ReportMonth:
    month = first_of_month(month)
    rm = db.get(ReportMonth, month)
    if rm is None:
        rm = ReportMonth(month=month, status=MonthStatus.OPEN)
        db.add(rm)
        db.flush()
    return rm


def list_months(db: Session) -> list[ReportMonth]:
    return list(db.scalars(select(ReportMonth).order_by(ReportMonth.month.desc())))


def publish(db: Session, actor: Employee, month: dt.date) -> ReportMonth:
    rm = get_month(db, month)
    if rm.status == MonthStatus.FINALIZED:
        raise ServiceError("Month is finalized; reopen it first")
    recompute_month(db, rm.month)
    rm.status = MonthStatus.PUBLISHED
    rm.published_at = dt.datetime.now(dt.UTC)
    rm.review_deadline = dt.date.today() + dt.timedelta(days=get_settings().dispute_window_days)
    audit(db, actor, "month.publish", "report_month", rm.month, {"review_deadline": rm.review_deadline})
    notify("all-managers", f"Onsite report for {rm.month:%B %Y} is published; review until {rm.review_deadline}")
    return rm


def finalize(db: Session, actor: Employee, month: dt.date) -> ReportMonth:
    rm = get_month(db, month)
    if rm.status == MonthStatus.FINALIZED:
        raise ServiceError("Month is already finalized")
    recompute_month(db, rm.month)
    rm.status = MonthStatus.FINALIZED
    rm.finalized_at = dt.datetime.now(dt.UTC)
    rm.finalized_by_id = actor.id
    audit(db, actor, "month.finalize", "report_month", rm.month)
    return rm


def reopen(db: Session, actor: Employee, month: dt.date, reason: str) -> ReportMonth:
    if not reason.strip():
        raise ServiceError("A reason is required to reopen a finalized month")
    rm = get_month(db, month)
    rm.status = MonthStatus.PUBLISHED
    rm.finalized_at = None
    rm.finalized_by_id = None
    audit(db, actor, "month.reopen", "report_month", rm.month, {"reason": reason})
    return rm
