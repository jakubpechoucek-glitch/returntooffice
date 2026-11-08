"""JSON API v1.

Authentication: Entra ID bearer token (``Authorization: Bearer <jwt>``, audience ``RTO_API_AUDIENCE``)
or the browser session cookie (then the ``X-CSRF-Token`` header is required for writes).
Authorization and data visibility are identical to the web UI (same service layer).
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from rto.auth.deps import csrf_protect, current_principal
from rto.config import get_settings
from rto.db import get_db
from rto.importers.daily import import_daily
from rto.importers.masterlist import import_masterlist
from rto.importers.onsite_report import import_onsite_report
from rto.models import Request as Req
from rto.models import RequestType, Role
from rto.services import ServiceError
from rto.services.access import Principal
from rto.services.compute import recompute_range
from rto.services.org import change_assignment, is_manager
from rto.services.policy import PolicyResolver, set_policy
from rto.services.reports import employee_year, month_rows, months_with_results, team
from rto.services.requests import create_request, decide, get_request, inbox, my_requests
from rto.web.common import parse_month

router = APIRouter(prefix="/api/v1", tags=["v1"])
write = [Depends(csrf_protect)]


# --- schemas --------------------------------------------------------------------------------------


class Me(BaseModel):
    id: int
    employee_number: str
    email: str
    name: str
    roles: dict[str, list[int | None]]
    is_manager: bool


class WeekOut(BaseModel):
    week_start: dt.date
    considered: bool
    exclusion_reason: str | None
    valid_days: float | None
    commitment: float | None
    met: bool | None
    adj_valid_days: float | None
    adj_commitment: float | None
    adj_met: bool | None


class MonthlyOut(BaseModel):
    employee_id: int
    employee_number: str
    name: str
    email: str
    department: str
    department_group: str
    manager: str
    month: dt.date
    commitment_days: float | None
    considered_weeks: int
    met_weeks: int
    adherence: float | None
    passed: bool | None
    adj_met_weeks: int
    adj_adherence: float | None
    adj_passed: bool | None
    threshold_pct: float
    weeks: list[WeekOut]


class RequestIn(BaseModel):
    employee_id: int
    type: RequestType
    start_date: dt.date
    end_date: dt.date | None = None
    days_per_week: float | None = Field(default=None, ge=0, le=5)
    credited_dates: list[dt.date] = []
    category: str
    reason: str = ""


class StepOut(BaseModel):
    order: int
    kind: str
    approver_id: int | None
    decision: str
    comment: str | None


class RequestOut(BaseModel):
    id: int
    type: str
    status: str
    employee_id: int
    submitted_by_id: int | None
    start_date: dt.date
    end_date: dt.date
    days_per_week: float | None
    credited_dates: list[str]
    category: str
    reason: str
    steps: list[StepOut]


class DecisionIn(BaseModel):
    decision: Literal["approve", "reject"]
    comment: str = ""


class ThresholdIn(BaseModel):
    org_unit_id: int | None = None
    effective_from: dt.date
    monthly_pass_pct: float | None = Field(default=None, ge=0, le=100)
    annual_pass_pct: float | None = Field(default=None, ge=0, le=100)
    min_working_days: int | None = Field(default=None, ge=1, le=5)
    default_commitment_days: float | None = Field(default=None, ge=0, le=5)
    note: str | None = None
    recompute: bool = True


class AssignmentIn(BaseModel):
    effective_from: dt.date
    manager_id: int | None = None
    org_unit_id: int | None = None
    location_id: int | None = None


def _req_out(r: Req) -> RequestOut:
    return RequestOut(
        id=r.id, type=r.type.value, status=r.status.value, employee_id=r.employee_id,
        submitted_by_id=r.submitted_by_id, start_date=r.start_date, end_date=r.end_date,
        days_per_week=r.days_per_week, credited_dates=r.credited_dates or [], category=r.category, reason=r.reason,
        steps=[StepOut(order=s.step_order, kind=s.kind.value, approver_id=s.approver_id, decision=s.decision.value,
                       comment=s.comment) for s in r.steps],
    )


# --- endpoints ------------------------------------------------------------------------------------


@router.get("/me", response_model=Me)
def me(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    roles = {r.value: sorted(v, key=lambda x: (x is not None, x or 0)) for r, v in p.roles.items()}
    for r in p.idp_roles:
        roles.setdefault(r.value, []).append(None)
    return Me(id=p.id, employee_number=p.employee.employee_number, email=p.employee.email,
              name=p.employee.full_name, roles=roles, is_manager=is_manager(db, p.id, dt.date.today()))


@router.get("/months", response_model=list[dt.date])
def months(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return months_with_results(db)


@router.get("/results/monthly", response_model=list[MonthlyOut])
def monthly_results(
    month: str | None = None, org_unit_id: int | None = None, manager_id: int | None = None,
    p: Principal = Depends(current_principal), db: Session = Depends(get_db),
):
    from rto.services.org import unit_descendants

    m = parse_month(month, db)
    units = unit_descendants(db, [org_unit_id]) if org_unit_id else None
    rows = month_rows(db, p, m, org_unit_ids=units, manager_id=manager_id)
    return [
        MonthlyOut(
            employee_id=r.employee.id, employee_number=r.employee.employee_number, name=r.employee.full_name,
            email=r.employee.email, department=r.unit_name, department_group=r.group_name, manager=r.manager_name,
            month=m, commitment_days=r.result.commitment_days, considered_weeks=r.result.considered_weeks,
            met_weeks=r.result.met_weeks, adherence=r.result.adherence, passed=r.result.passed,
            adj_met_weeks=r.result.adj_met_weeks, adj_adherence=r.result.adj_adherence,
            adj_passed=r.result.adj_passed, threshold_pct=r.result.threshold_pct,
            weeks=[WeekOut(week_start=w.monday, considered=w.considered, exclusion_reason=w.reason,
                           valid_days=w.valid, commitment=w.commitment, met=w.met, adj_valid_days=w.adj_valid,
                           adj_commitment=w.adj_commitment, adj_met=w.adj_met) for w in r.weeks],
        )
        for r in rows
    ]


@router.get("/employees/{employee_id}/years/{year}")
def employee_annual(employee_id: int, year: int, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    ey = employee_year(db, p, employee_id, year)
    return {
        "employee_id": employee_id,
        "year": year,
        "annual_rate": ey.annual_rate,
        "ytd_rate": ey.ytd_rate,
        "adhering_months": ey.adhering_months,
        "evaluated_months": ey.evaluated_months,
        "annual_threshold_pct": ey.annual_threshold,
        "good_standing": ey.annual_passed,
        "months": [
            {"month": m.month, "adherence": m.adherence, "passed": m.passed,
             "adj_adherence": m.adj_adherence, "adj_passed": m.adj_passed, "threshold_pct": m.threshold_pct}
            for m in ey.months
        ],
    }


@router.get("/team")
def team_api(month: str | None = None, root: int | None = None, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    m = parse_month(month, db)
    return [
        {
            "employee_id": n.employee.id, "name": n.employee.full_name, "is_manager": n.is_manager,
            "adj_adherence": n.result.adj_adherence if n.result else None,
            "adj_passed": n.result.adj_passed if n.result else None,
            "subtree": {"evaluated": n.subtree.evaluated, "passed": n.subtree.passed, "pass_rate": n.subtree.pass_rate},
        }
        for n in team(db, p, m, root or p.id)
    ]


@router.get("/requests", response_model=list[RequestOut])
def list_requests(scope: Literal["mine", "inbox"] = "mine", p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    reqs = my_requests(db, p) if scope == "mine" else inbox(db, p)
    return [_req_out(r) for r in reqs]


@router.post("/requests", response_model=RequestOut, status_code=201, dependencies=write)
def post_request(body: RequestIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    r = create_request(db, p, body.employee_id, body.type, body.start_date, body.end_date, body.category,
                       body.reason, body.days_per_week, body.credited_dates)
    db.commit()
    return _req_out(get_request(db, r.id))


@router.post("/requests/{request_id}/decision", response_model=RequestOut, dependencies=write)
def post_decision(request_id: int, body: DecisionIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    decide(db, p, request_id, body.decision == "approve", body.comment)
    db.commit()
    return _req_out(get_request(db, request_id))


# --- admin ----------------------------------------------------------------------------------------


@router.get("/admin/thresholds")
def get_thresholds(as_of: dt.date | None = None, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    p.require(Role.HR_ADMIN, Role.HR_ANALYTICS, Role.AUDITOR)
    from sqlalchemy import select

    from rto.models import OrgUnit

    resolver = PolicyResolver(db)
    as_of = as_of or dt.date.today()
    out = []
    for u in db.scalars(select(OrgUnit).order_by(OrgUnit.name)):
        e = resolver.resolve(u.id, as_of)
        out.append({"org_unit_id": u.id, "name": u.name, "kind": u.kind.value, "monthly_pass_pct": e.monthly_pass_pct,
                    "annual_pass_pct": e.annual_pass_pct, "min_working_days": e.min_working_days,
                    "default_commitment_days": e.default_commitment_days, "origin": e.origin})
    return out


@router.put("/admin/thresholds", dependencies=write)
def put_threshold(body: ThresholdIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    p.require(Role.HR_ADMIN)
    values = body.model_dump(include={"monthly_pass_pct", "annual_pass_pct", "min_working_days", "default_commitment_days"})
    row = set_policy(db, p.employee, body.org_unit_id, body.effective_from, values, body.note)
    if body.recompute:
        recompute_range(db, body.effective_from, dt.date.today())
    db.commit()
    return {"id": row.id}


@router.post("/admin/employees/{employee_id}/assignment", dependencies=write)
def post_assignment(employee_id: int, body: AssignmentIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    p.require(Role.HR_ADMIN, Role.HR_ANALYTICS)
    row = change_assignment(db, p.employee, employee_id, body.effective_from, manager_id=body.manager_id,
                            org_unit_id=body.org_unit_id, location_id=body.location_id)
    recompute_range(db, body.effective_from, max(body.effective_from, dt.date.today()), {employee_id})
    db.commit()
    return {"assignment_id": row.id}


@router.post("/admin/imports/{kind}", dependencies=write)
async def post_import(kind: Literal["onsite_report", "masterlist", "presence", "absences"], file: UploadFile = File(...),
                      p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    p.require(Role.HR_ADMIN, Role.HR_ANALYTICS)
    limit = get_settings().max_upload_mb * 1024 * 1024
    content = await file.read(limit + 1)
    if len(content) > limit:
        raise ServiceError("File too large")
    name = (file.filename or "upload")[:255]
    if kind == "onsite_report":
        out = import_onsite_report(db, content, name, p.employee).as_dict()
    elif kind == "masterlist":
        out = import_masterlist(db, content, name, p.employee).as_dict()
    else:
        out = import_daily(db, kind, content, name, p.employee).as_dict()
    db.commit()
    return out
