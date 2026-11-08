"""Self-service requests and their approval workflow.

Request types
-------------
* ``EXEMPTION``          - reduced commitment for specific weeks (weekly filing instead of monthly).
* ``COMMITMENT_CHANGE``  - new baseline commitment from a given week onward.
* ``DISPUTE``            - correction after the initial report: credit specific days as valid
                           and/or correct the commitment for a date range (after-dispute figures).

Approval chain (configurable, ``RTO_APPROVAL_CHAIN``; default: direct manager -> People Partner).
If the submitter *is* the direct manager, the direct-manager step is routed to the next-level manager
(so nobody approves their own submission). Steps without a resolvable approver are skipped.
"""

from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from rto.config import get_settings
from rto.domain.calendar import week_start
from rto.models import (
    AttendanceCredit,
    CommitmentKind,
    Employee,
    Request,
    RequestStatus,
    RequestStep,
    RequestType,
    Role,
    StepDecision,
    StepKind,
)
from rto.services import NotFound, PermissionDenied, ServiceError
from rto.services.access import Principal, can_view, is_people_partner_for
from rto.services.audit import audit
from rto.services.commitments import add_override, set_baseline
from rto.services.compute import month_status, months_between, recompute_range
from rto.services.notifications import notify
from rto.services.org import assignment_at, manager_chain

log = logging.getLogger(__name__)

CATEGORIES = [
    "Medical",
    "Family / personal",
    "Business requirement",
    "Office closure / weather",
    "Door access / system issue",
    "Unfiled leave or OB",
    "Commitment not updated",
    "Other",
]


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _normalise(
    rtype: RequestType, start: dt.date, end: dt.date | None, credited: list[dt.date]
) -> tuple[dt.date, dt.date]:
    if rtype == RequestType.EXEMPTION:
        end = end or start
        return week_start(start), week_start(end) + dt.timedelta(days=6)
    if rtype == RequestType.COMMITMENT_CHANGE:
        return week_start(start), week_start(start)
    # dispute
    if credited:
        start = min([start, *credited])
        end = max([end or start, *credited])
    return start, end or start


def _build_steps(db: Session, employee: Employee, submitter: Employee, as_of: dt.date) -> list[RequestStep]:
    chain = manager_chain(db, employee.id, as_of)  # direct manager first
    excluded = {submitter.id, employee.id}  # nobody approves their own submission
    steps: list[RequestStep] = []
    order = 0
    last_index = -1

    def pick(min_index: int) -> tuple[int, Employee | None]:
        for i, m in enumerate(chain):
            if i >= min_index and m.id not in excluded:
                return i, m
        return last_index, None

    for kind_name in get_settings().approval_chain:
        kind = StepKind(kind_name)
        approver: Employee | None = None
        if kind == StepKind.DIRECT_MANAGER:
            last_index, approver = pick(max(0, last_index + 1))
        elif kind == StepKind.NEXT_LEVEL_MANAGER:
            last_index, approver = pick(max(1, last_index + 1))
        order += 1
        if kind == StepKind.PEOPLE_PARTNER:
            steps.append(RequestStep(step_order=order, kind=kind, decision=StepDecision.WAITING))
            continue
        if approver is None:
            steps.append(
                RequestStep(
                    step_order=order,
                    kind=kind,
                    decision=StepDecision.SKIPPED,
                    comment="No approver in reporting line",
                )
            )
            continue
        steps.append(
            RequestStep(step_order=order, kind=kind, approver_id=approver.id, decision=StepDecision.WAITING)
        )
    return steps


def _activate_next(req: Request) -> RequestStep | None:
    for s in req.steps:
        if s.decision == StepDecision.WAITING:
            s.decision = StepDecision.PENDING
            return s
    return None


def current_step(req: Request) -> RequestStep | None:
    return next((s for s in req.steps if s.decision == StepDecision.PENDING), None)


def create_request(
    db: Session,
    actor: Principal,
    employee_id: int,
    rtype: RequestType,
    start: dt.date,
    end: dt.date | None,
    category: str,
    reason: str,
    days_per_week: float | None = None,
    credited_dates: list[dt.date] | None = None,
) -> Request:
    employee = db.get(Employee, employee_id)
    if employee is None:
        raise NotFound("Employee not found")
    credited = sorted(set(credited_dates or []))
    start, end = _normalise(rtype, start, end, credited)
    if end < start:
        raise ServiceError("End date must not be before start date")

    if employee_id != actor.id:
        is_line_manager = any(m.id == actor.id for m in manager_chain(db, employee_id, start))
        if not (is_line_manager or actor.can_manage_data):
            raise PermissionDenied("You can only file requests for yourself or for your reports")
        if not can_view(db, actor, employee_id, start):
            raise PermissionDenied("You can only file requests for employees you can see")

    if category not in CATEGORIES:
        raise ServiceError("Unknown category")
    if rtype in (RequestType.EXEMPTION, RequestType.COMMITMENT_CHANGE):
        if days_per_week is None:
            raise ServiceError("Days per week is required")
    if rtype == RequestType.DISPUTE and not credited and days_per_week is None:
        raise ServiceError("A dispute must credit at least one day or correct the commitment")
    if days_per_week is not None and not (0 <= days_per_week <= 5):
        raise ServiceError("Days per week must be between 0 and 5")
    for d in credited:
        if d.weekday() >= 5:
            raise ServiceError(f"{d} is a weekend day")
    if (end - start).days > 366:
        raise ServiceError("A request may cover at most one year")

    if not actor.can_manage_data:
        from rto.models import MonthStatus

        last = end if rtype != RequestType.COMMITMENT_CHANGE else start
        for m in months_between(start, last):
            if month_status(db, m) == MonthStatus.FINALIZED:
                raise ServiceError(f"{m:%B %Y} is already finalized; contact HR for an override")

    req = Request(
        type=rtype,
        employee_id=employee_id,
        submitted_by_id=actor.id,
        status=RequestStatus.PENDING,
        start_date=start,
        end_date=end,
        days_per_week=days_per_week,
        credited_dates=[d.isoformat() for d in credited],
        category=category,
        reason=reason.strip(),
    )
    req.steps = _build_steps(db, employee, actor.employee, start)
    db.add(req)
    db.flush()
    audit(db, actor.employee, "request.create", "request", req.id, {"type": rtype, "employee_id": employee_id})
    if _activate_next(req) is None:
        _finalise(db, req, actor.employee, approved=True, note="No approval steps configured")
    else:
        _notify_current(db, req)
    return req


def _notify_current(db: Session, req: Request) -> None:
    step = current_step(req)
    if step is None:
        return
    if step.approver_id:
        approver = db.get(Employee, step.approver_id)
        notify(approver.email if approver else None, f"Request #{req.id} waits for your approval")
    else:
        notify("people-partners", f"Request #{req.id} waits for People Partner review")


def can_decide(db: Session, actor: Principal, req: Request) -> bool:
    step = current_step(req)
    if step is None or req.status != RequestStatus.PENDING:
        return False
    if req.employee_id == actor.id and not actor.is_hr_admin:
        return False
    if step.kind == StepKind.PEOPLE_PARTNER:
        a = assignment_at(db, req.employee_id, req.start_date) or assignment_at(db, req.employee_id, dt.date.today())
        unit = a.org_unit_id if a else None
        return is_people_partner_for(db, actor, unit) or actor.is_hr_admin
    return step.approver_id == actor.id or actor.is_hr_admin


def decide(db: Session, actor: Principal, request_id: int, approve: bool, comment: str = "") -> Request:
    req = get_request(db, request_id)
    if not can_decide(db, actor, req):
        raise PermissionDenied("You cannot decide on this request")
    if not approve and not comment.strip():
        raise ServiceError("Please give a reason when rejecting")
    step = current_step(req)
    assert step is not None
    step.decision = StepDecision.APPROVED if approve else StepDecision.REJECTED
    step.decided_by_id = actor.id
    step.decided_at = _now()
    step.comment = comment.strip() or None
    audit(
        db,
        actor.employee,
        "request.approve_step" if approve else "request.reject",
        "request",
        req.id,
        {"step": step.kind, "override": step.approver_id not in (None, actor.id)},
    )
    if not approve:
        _finalise(db, req, actor.employee, approved=False)
        return req
    if _activate_next(req) is None:
        _finalise(db, req, actor.employee, approved=True)
    else:
        _notify_current(db, req)
    db.flush()
    return req


def withdraw(db: Session, actor: Principal, request_id: int) -> Request:
    req = get_request(db, request_id)
    if req.status != RequestStatus.PENDING:
        raise ServiceError("Only pending requests can be withdrawn")
    if actor.id not in (req.employee_id, req.submitted_by_id):
        raise PermissionDenied("Only the employee or the submitter can withdraw a request")
    req.status = RequestStatus.WITHDRAWN
    req.decided_at = _now()
    for s in req.steps:
        if s.decision in (StepDecision.PENDING, StepDecision.WAITING):
            s.decision = StepDecision.SKIPPED
    audit(db, actor.employee, "request.withdraw", "request", req.id)
    return req


def _finalise(db: Session, req: Request, actor: Employee, approved: bool, note: str | None = None) -> None:
    req.status = RequestStatus.APPROVED if approved else RequestStatus.REJECTED
    req.decided_at = _now()
    for s in req.steps:
        if s.decision in (StepDecision.PENDING, StepDecision.WAITING):
            s.decision = StepDecision.SKIPPED
    if approved:
        apply_effects(db, req, actor)
    submitter = db.get(Employee, req.submitted_by_id) if req.submitted_by_id else None
    notify(submitter.email if submitter else None, f"Request #{req.id} was {req.status.value.lower()}")
    if note:
        audit(db, actor, "request.auto_approve", "request", req.id, {"note": note})


def apply_effects(db: Session, req: Request, actor: Employee | None) -> None:
    """Turn an approved request into data and recompute affected (non-finalized) months."""
    if req.type == RequestType.EXEMPTION:
        add_override(
            db, req.employee_id, CommitmentKind.EXEMPTION, req.days_per_week or 0, req.start_date,
            req.end_date, req.category, req.id, source=req.source,
        )
        recompute_range(db, req.start_date, req.end_date, {req.employee_id})
    elif req.type == RequestType.COMMITMENT_CHANGE:
        set_baseline(db, actor, req.employee_id, req.days_per_week or 0, req.start_date, source="REQUEST", request_id=req.id)
        recompute_range(db, req.start_date, max(req.start_date, dt.date.today()), {req.employee_id})
    elif req.type == RequestType.DISPUTE:
        for iso in req.credited_dates or []:
            db.add(AttendanceCredit(employee_id=req.employee_id, date=dt.date.fromisoformat(iso), request_id=req.id))
        if req.days_per_week is not None:
            add_override(
                db, req.employee_id, CommitmentKind.DISPUTE, req.days_per_week, req.start_date,
                req.end_date, req.category, req.id, source=req.source,
            )
        db.flush()
        recompute_range(db, req.start_date, req.end_date, {req.employee_id})


def get_request(db: Session, request_id: int) -> Request:
    req = db.scalar(
        select(Request)
        .options(selectinload(Request.steps), selectinload(Request.employee))
        .where(Request.id == request_id)
    )
    if req is None:
        raise NotFound("Request not found")
    return req


def can_view_request(db: Session, actor: Principal, req: Request) -> bool:
    if actor.id in (req.employee_id, req.submitted_by_id):
        return True
    if any(s.approver_id == actor.id for s in req.steps):
        return True
    return can_view(db, actor, req.employee_id, req.start_date) or can_decide(db, actor, req)


def inbox(db: Session, actor: Principal) -> list[Request]:
    """Pending requests where the actor can act on the current step."""
    q = (
        select(Request)
        .join(RequestStep, RequestStep.request_id == Request.id)
        .options(selectinload(Request.steps), selectinload(Request.employee))
        .where(Request.status == RequestStatus.PENDING, RequestStep.decision == StepDecision.PENDING)
    )
    conds = [RequestStep.approver_id == actor.id]
    if actor.has(Role.PEOPLE_PARTNER, Role.HR_ADMIN):
        conds.append(RequestStep.kind == StepKind.PEOPLE_PARTNER)
    if actor.is_hr_admin:
        conds.append(RequestStep.kind.in_([StepKind.DIRECT_MANAGER, StepKind.NEXT_LEVEL_MANAGER]))
    q = q.where(or_(*conds)).order_by(Request.created_at)
    return [r for r in db.scalars(q).unique() if can_decide(db, actor, r)]


def my_requests(db: Session, actor: Principal, limit: int = 100) -> list[Request]:
    q = (
        select(Request)
        .options(selectinload(Request.steps), selectinload(Request.employee))
        .where(or_(Request.employee_id == actor.id, Request.submitted_by_id == actor.id))
        .order_by(Request.created_at.desc())
        .limit(limit)
    )
    return list(db.scalars(q))
