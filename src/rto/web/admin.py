"""Administration: thresholds, people & hierarchy, org units, roles, calendar, imports, months, audit."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from rto.auth.deps import csrf_protect, current_principal
from rto.config import get_settings
from rto.db import get_db
from rto.importers.daily import import_daily
from rto.importers.masterlist import import_masterlist
from rto.importers.onsite_report import import_onsite_report
from rto.models import (
    AuditLog,
    Employee,
    EmployeeAssignment,
    ExcludedPeriod,
    ExclusionMode,
    Holiday,
    ImportBatch,
    Location,
    OrgUnit,
    OrgUnitKind,
    PolicySetting,
    Role,
    RoleAssignment,
)
from rto.services import PermissionDenied, ServiceError
from rto.services.access import Principal
from rto.services.audit import audit
from rto.services.commitments import set_baseline
from rto.services.compute import recompute_month, recompute_range
from rto.services.months import finalize, list_months, publish, reopen
from rto.services.org import assignment_at, change_assignment, company
from rto.services.policy import FIELDS, PolicyResolver, set_policy
from rto.services.reports import months_with_results
from rto.web.common import back, flash, nav_ctx, parse_date, parse_month
from rto.web.templating import render

router = APIRouter(prefix="/admin")


def admin_reader(p: Principal = Depends(current_principal)) -> Principal:
    p.require(Role.HR_ADMIN, Role.HR_ANALYTICS, Role.AUDITOR)
    return p


def data_manager(p: Principal = Depends(current_principal)) -> Principal:
    p.require(Role.HR_ADMIN, Role.HR_ANALYTICS)
    return p


def hr_admin(p: Principal = Depends(current_principal)) -> Principal:
    p.require(Role.HR_ADMIN)
    return p


def _run(request: Request, db: Session, fn, success: str, fallback: str):
    try:
        out = fn()
        db.commit()
        flash(request, success, "success")
        return out
    except (ServiceError, ValueError) as exc:
        db.rollback()
        flash(request, str(exc), "error")
        return None
    finally:
        pass


@router.get("")
def index(request: Request, p: Principal = Depends(admin_reader), db: Session = Depends(get_db)):
    counts = {
        "employees": db.scalar(select(func.count()).select_from(Employee).where(Employee.in_scope.is_(True))),
        "units": db.scalar(select(func.count()).select_from(OrgUnit)),
        "imports": db.scalar(select(func.count()).select_from(ImportBatch)),
        "audit": db.scalar(select(func.count()).select_from(AuditLog)),
    }
    return render(request, "admin/index.html", **nav_ctx(request, db, p), counts=counts)


# --- thresholds -----------------------------------------------------------------------------------


@router.get("/thresholds")
def thresholds(request: Request, p: Principal = Depends(admin_reader), db: Session = Depends(get_db)):
    today = dt.date.today()
    resolver = PolicyResolver(db)
    units = list(db.scalars(select(OrgUnit)))
    by_parent: dict[int | None, list[OrgUnit]] = {}
    for u in units:
        by_parent.setdefault(u.parent_id, []).append(u)
    ordered: list[tuple[int, OrgUnit]] = []

    def walk(parent: int | None, depth: int) -> None:
        for u in sorted(by_parent.get(parent, []), key=lambda x: x.name):
            ordered.append((depth, u))
            walk(u.id, depth + 1)

    walk(None, 0)
    effective = {u.id: resolver.resolve(u.id, today) for u in units}
    settings = list(db.scalars(select(PolicySetting).order_by(PolicySetting.effective_from.desc())))
    return render(
        request, "admin/thresholds.html", **nav_ctx(request, db, p), ordered=ordered, effective=effective,
        settings=settings, units={u.id: u for u in units}, today=today,
    )


@router.post("/thresholds", dependencies=[Depends(csrf_protect)])
def thresholds_save(
    request: Request,
    org_unit_id: str = Form(""),
    effective_from: str = Form(...),
    monthly_pass_pct: str = Form(""),
    annual_pass_pct: str = Form(""),
    min_working_days: str = Form(""),
    default_commitment_days: str = Form(""),
    note: str = Form(""),
    recompute: bool = Form(False),
    p: Principal = Depends(hr_admin),
    db: Session = Depends(get_db),
):
    raw = {
        "monthly_pass_pct": monthly_pass_pct, "annual_pass_pct": annual_pass_pct,
        "min_working_days": min_working_days, "default_commitment_days": default_commitment_days,
    }
    values = {k: (None if not v.strip() else (int(v) if k == "min_working_days" else float(v))) for k, v in raw.items()}
    unit = int(org_unit_id) if org_unit_id else None
    eff = parse_date(effective_from)

    def go():
        set_policy(db, p.employee, unit, eff, values, note or None)
        if recompute:
            recompute_range(db, eff, dt.date.today())

    _run(request, db, go, "Thresholds saved" + (" and results recomputed." if recompute else "."), "/admin/thresholds")
    return RedirectResponse("/admin/thresholds", status_code=303)


# --- people & hierarchy ---------------------------------------------------------------------------


@router.get("/people")
def people(request: Request, q: str = "", unit: str = "", p: Principal = Depends(data_manager), db: Session = Depends(get_db)):
    today = dt.date.today()
    unit = int(unit) if unit.isdigit() else None
    stmt = select(Employee).order_by(Employee.last_name, Employee.first_name).limit(300)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(Employee.email.ilike(like), Employee.last_name.ilike(like),
                              Employee.first_name.ilike(like), Employee.employee_number.ilike(like)))
    emps = list(db.scalars(stmt))
    assign = {e.id: assignment_at(db, e.id, today) for e in emps}
    if unit:
        emps = [e for e in emps if assign[e.id] and assign[e.id].org_unit_id == unit]
    return render(request, "admin/people.html", **nav_ctx(request, db, p), emps=emps, assign=assign, q=q,
                  units=list(db.scalars(select(OrgUnit).order_by(OrgUnit.name))), unit=unit)


@router.get("/people/{employee_id}")
def person(request: Request, employee_id: int, p: Principal = Depends(data_manager), db: Session = Depends(get_db)):
    emp = db.get(Employee, employee_id)
    if emp is None:
        raise ServiceError("Employee not found")
    history = list(db.scalars(select(EmployeeAssignment).where(EmployeeAssignment.employee_id == employee_id)
                              .order_by(EmployeeAssignment.valid_from.desc())))
    from rto.models import Commitment

    commitments = list(db.scalars(select(Commitment).where(Commitment.employee_id == employee_id)
                                  .order_by(Commitment.valid_from.desc())))
    current = assignment_at(db, employee_id, dt.date.today()) or (history[0] if history else None)
    return render(
        request, "admin/person.html", **nav_ctx(request, db, p), emp=emp, history=history, current=current,
        commitments=commitments, units=list(db.scalars(select(OrgUnit).order_by(OrgUnit.name))),
        locations=list(db.scalars(select(Location).order_by(Location.name))),
        managers=list(db.scalars(select(Employee).where(Employee.id != employee_id).order_by(Employee.last_name))),
        today=dt.date.today(), months=months_with_results(db),
    )


@router.post("/people/{employee_id}/assignment", dependencies=[Depends(csrf_protect)])
def person_assignment(
    request: Request, employee_id: int, effective_from: str = Form(...), manager_id: str = Form(""),
    org_unit_id: str = Form(""), location_id: str = Form(""), p: Principal = Depends(data_manager),
    db: Session = Depends(get_db),
):
    eff = parse_date(effective_from)

    def go():
        change_assignment(
            db, p.employee, employee_id, eff,
            manager_id=int(manager_id) if manager_id else None,
            org_unit_id=int(org_unit_id) if org_unit_id else None,
            location_id=int(location_id) if location_id else None,
        )
        recompute_range(db, eff, max(eff, dt.date.today()), {employee_id})

    _run(request, db, go, "Reporting line updated.", f"/admin/people/{employee_id}")
    return RedirectResponse(f"/admin/people/{employee_id}", status_code=303)


@router.post("/people/{employee_id}/commitment", dependencies=[Depends(csrf_protect)])
def person_commitment(
    request: Request, employee_id: int, effective_from: str = Form(...), days_per_week: float = Form(...),
    p: Principal = Depends(data_manager), db: Session = Depends(get_db),
):
    eff = parse_date(effective_from)

    def go():
        set_baseline(db, p.employee, employee_id, days_per_week, eff)
        recompute_range(db, eff, max(eff, dt.date.today()), {employee_id})

    _run(request, db, go, "Commitment updated.", f"/admin/people/{employee_id}")
    return RedirectResponse(f"/admin/people/{employee_id}", status_code=303)


@router.post("/people/{employee_id}/profile", dependencies=[Depends(csrf_protect)])
def person_profile(
    request: Request, employee_id: int, in_scope: bool = Form(False), termination_date: str = Form(""),
    p: Principal = Depends(data_manager), db: Session = Depends(get_db),
):
    def go():
        emp = db.get(Employee, employee_id)
        before = {"in_scope": emp.in_scope, "termination_date": emp.termination_date}
        emp.in_scope = in_scope
        emp.termination_date = parse_date(termination_date)
        audit(db, p.employee, "employee.profile", "employee", employee_id,
              {"before": before, "after": {"in_scope": in_scope, "termination_date": emp.termination_date}})
        for m in months_with_results(db)[:3]:
            recompute_range(db, m, m, {employee_id})

    _run(request, db, go, "Employee updated.", f"/admin/people/{employee_id}")
    return RedirectResponse(f"/admin/people/{employee_id}", status_code=303)


@router.get("/org")
def org(request: Request, p: Principal = Depends(data_manager), db: Session = Depends(get_db)):
    units = list(db.scalars(select(OrgUnit).order_by(OrgUnit.name)))
    counts = dict(db.execute(
        select(EmployeeAssignment.org_unit_id, func.count())
        .where(EmployeeAssignment.valid_to.is_(None)).group_by(EmployeeAssignment.org_unit_id)
    ).all())
    return render(request, "admin/org.html", **nav_ctx(request, db, p), units=units, counts=counts,
                  kinds=[k.value for k in OrgUnitKind if k != OrgUnitKind.COMPANY])


@router.post("/org", dependencies=[Depends(csrf_protect)])
def org_save(
    request: Request, unit_id: str = Form(""), name: str = Form(...), kind: str = Form("DEPARTMENT"),
    parent_id: str = Form(""), p: Principal = Depends(hr_admin), db: Session = Depends(get_db),
):
    def go():
        if not name.strip():
            raise ServiceError("Name is required")
        parent = int(parent_id) if parent_id else company(db).id
        if unit_id:
            u = db.get(OrgUnit, int(unit_id))
            if u is None or u.kind == OrgUnitKind.COMPANY:
                raise ServiceError("This unit cannot be edited")
            # prevent cycles
            x = db.get(OrgUnit, parent)
            while x is not None:
                if x.id == u.id:
                    raise ServiceError("A unit cannot be moved under itself")
                x = db.get(OrgUnit, x.parent_id) if x.parent_id else None
            before = {"name": u.name, "parent_id": u.parent_id}
            u.name, u.parent_id = name.strip(), parent
            audit(db, p.employee, "org_unit.update", "org_unit", u.id, {"before": before, "after": {"name": u.name, "parent_id": parent}})
        else:
            parent_unit = db.get(OrgUnit, parent)
            u = OrgUnit(code=f"{parent_unit.code}/{name.strip().upper()}", name=name.strip(), kind=OrgUnitKind(kind), parent_id=parent)
            db.add(u)
            db.flush()
            audit(db, p.employee, "org_unit.create", "org_unit", u.id, {"name": u.name, "parent_id": parent})

    _run(request, db, go, "Organisation unit saved.", "/admin/org")
    return RedirectResponse("/admin/org", status_code=303)


# --- roles ----------------------------------------------------------------------------------------


@router.get("/roles")
def roles(request: Request, p: Principal = Depends(hr_admin), db: Session = Depends(get_db)):
    grants = list(db.scalars(select(RoleAssignment).order_by(RoleAssignment.role)))
    return render(
        request, "admin/roles.html", **nav_ctx(request, db, p), grants=grants, roles=[r.value for r in Role],
        units=list(db.scalars(select(OrgUnit).order_by(OrgUnit.name))),
    )


@router.post("/roles", dependencies=[Depends(csrf_protect)])
def roles_grant(
    request: Request, email: str = Form(...), role: str = Form(...), org_unit_id: str = Form(""),
    p: Principal = Depends(hr_admin), db: Session = Depends(get_db),
):
    def go():
        emp = db.scalar(select(Employee).where(func.lower(Employee.email) == email.strip().lower()))
        if emp is None:
            raise ServiceError(f"No employee with e-mail {email}")
        ra = RoleAssignment(employee_id=emp.id, role=Role(role), org_unit_id=int(org_unit_id) if org_unit_id else None)
        db.add(ra)
        db.flush()
        audit(db, p.employee, "role.grant", "employee", emp.id, {"role": role, "org_unit_id": ra.org_unit_id})

    _run(request, db, go, "Role granted.", "/admin/roles")
    return RedirectResponse("/admin/roles", status_code=303)


@router.post("/roles/{grant_id}/revoke", dependencies=[Depends(csrf_protect)])
def roles_revoke(request: Request, grant_id: int, p: Principal = Depends(hr_admin), db: Session = Depends(get_db)):
    def go():
        ra = db.get(RoleAssignment, grant_id)
        if ra is None:
            raise ServiceError("Grant not found")
        if ra.employee_id == p.id and ra.role == Role.HR_ADMIN:
            raise ServiceError("You cannot revoke your own HR Admin role")
        audit(db, p.employee, "role.revoke", "employee", ra.employee_id, {"role": ra.role, "org_unit_id": ra.org_unit_id})
        db.delete(ra)

    _run(request, db, go, "Role revoked.", "/admin/roles")
    return RedirectResponse("/admin/roles", status_code=303)


# --- calendar -------------------------------------------------------------------------------------


@router.get("/calendar")
def calendar(request: Request, year: int | None = None, p: Principal = Depends(admin_reader), db: Session = Depends(get_db)):
    year = year or dt.date.today().year
    hols = list(db.scalars(select(Holiday).where(Holiday.date >= dt.date(year, 1, 1), Holiday.date <= dt.date(year, 12, 31)).order_by(Holiday.date)))
    periods = list(db.scalars(select(ExcludedPeriod).order_by(ExcludedPeriod.start_date.desc())))
    return render(
        request, "admin/calendar.html", **nav_ctx(request, db, p), year=year, holidays=hols, periods=periods,
        locations=list(db.scalars(select(Location).order_by(Location.name))),
        units=list(db.scalars(select(OrgUnit).order_by(OrgUnit.name))),
    )


@router.post("/calendar/holidays", dependencies=[Depends(csrf_protect)])
def holiday_add(
    request: Request, date: str = Form(...), name: str = Form(...), kind: str = Form("LEGAL"),
    location_id: str = Form(""), p: Principal = Depends(hr_admin), db: Session = Depends(get_db),
):
    d = parse_date(date)

    def go():
        h = Holiday(date=d, name=name.strip(), kind=kind, location_id=int(location_id) if location_id else None)
        db.add(h)
        db.flush()
        audit(db, p.employee, "holiday.add", "holiday", h.id, {"date": d, "name": h.name, "location_id": h.location_id})
        recompute_range(db, d, d)

    _run(request, db, go, "Holiday added and affected month recomputed.", "/admin/calendar")
    return RedirectResponse(f"/admin/calendar?year={d.year}", status_code=303)


@router.post("/calendar/holidays/{hid}/delete", dependencies=[Depends(csrf_protect)])
def holiday_delete(request: Request, hid: int, p: Principal = Depends(hr_admin), db: Session = Depends(get_db)):
    def go():
        h = db.get(Holiday, hid)
        if h is None:
            raise ServiceError("Holiday not found")
        audit(db, p.employee, "holiday.delete", "holiday", hid, {"date": h.date, "name": h.name})
        d = h.date
        db.delete(h)
        db.flush()
        recompute_range(db, d, d)

    _run(request, db, go, "Holiday removed.", "/admin/calendar")
    return back(request, "/admin/calendar")


@router.post("/calendar/periods", dependencies=[Depends(csrf_protect)])
def period_add(
    request: Request, start_date: str = Form(...), end_date: str = Form(...), mode: str = Form("WEEK"),
    reason: str = Form(...), org_unit_id: str = Form(""), location_id: str = Form(""),
    p: Principal = Depends(hr_admin), db: Session = Depends(get_db),
):
    s, e = parse_date(start_date), parse_date(end_date)

    def go():
        if e < s:
            raise ServiceError("End date must not be before start date")
        if not reason.strip():
            raise ServiceError("A reason is required")
        ep = ExcludedPeriod(start_date=s, end_date=e, mode=ExclusionMode(mode), reason=reason.strip(),
                            org_unit_id=int(org_unit_id) if org_unit_id else None,
                            location_id=int(location_id) if location_id else None)
        db.add(ep)
        db.flush()
        audit(db, p.employee, "excluded_period.add", "excluded_period", ep.id,
              {"start": s, "end": e, "mode": mode, "reason": ep.reason, "org_unit_id": ep.org_unit_id, "location_id": ep.location_id})
        _retag_and_recompute(db, s, e)

    _run(request, db, go, "Excluded period saved and results recomputed.", "/admin/calendar")
    return RedirectResponse("/admin/calendar", status_code=303)


@router.post("/calendar/periods/{pid}/delete", dependencies=[Depends(csrf_protect)])
def period_delete(request: Request, pid: int, p: Principal = Depends(hr_admin), db: Session = Depends(get_db)):
    def go():
        ep = db.get(ExcludedPeriod, pid)
        if ep is None:
            raise ServiceError("Period not found")
        s, e = ep.start_date, ep.end_date
        audit(db, p.employee, "excluded_period.delete", "excluded_period", pid, {"start": s, "end": e, "reason": ep.reason})
        db.delete(ep)
        db.flush()
        _retag_and_recompute(db, s, e)

    _run(request, db, go, "Excluded period removed.", "/admin/calendar")
    return RedirectResponse("/admin/calendar", status_code=303)


def _retag_and_recompute(db: Session, s: dt.date, e: dt.date) -> None:
    from rto.services.compute import tag_range

    tag_range(db, s, e)
    recompute_range(db, s, e)


# --- imports --------------------------------------------------------------------------------------

IMPORT_KINDS = {
    "onsite_report": "Monthly Onsite Report (.xlsx, legacy layout)",
    "masterlist": "Masterlist / hierarchy (.csv)",
    "presence": "Presence events: door access, timekeeping, Wi-Fi (.csv)",
    "absences": "Leave and Official Business (.csv)",
}


SAMPLE_FILES = {
    "onsite_report_2026_jan_mar_anonymized.xlsx": ("onsite_report", "Legacy onsite report (Collections, Jan + Mar 2026)"),
    "synthetic/masterlist_risk.csv": ("masterlist", "Masterlist / hierarchy (Risk Management)"),
    "synthetic/absences_risk_2026q1.csv": ("absences", "Leave and OB, Q1 2026"),
    "synthetic/presence_risk_2026q1.csv": ("presence", "Door / timekeeping / Wi-Fi events, Q1 2026"),
}


@router.get("/imports/samples/{name:path}")
def sample_file(name: str, p: Principal = Depends(data_manager)):
    from fastapi.responses import FileResponse

    from rto.config import project_root

    if name not in SAMPLE_FILES:  # whitelist: no arbitrary file access
        raise ServiceError("Unknown sample file")
    path = project_root() / "sample_data" / name
    if not path.is_file():
        raise ServiceError("Sample file is not available in this deployment")
    return FileResponse(path, filename=path.name)


@router.get("/imports")
def imports(request: Request, p: Principal = Depends(data_manager), db: Session = Depends(get_db)):
    batches = list(db.scalars(select(ImportBatch).order_by(ImportBatch.created_at.desc()).limit(50)))
    last = request.session.pop("last_import", None)
    return render(request, "admin/imports.html", **nav_ctx(request, db, p), batches=batches, kinds=IMPORT_KINDS, last=last,
                  samples=SAMPLE_FILES)


@router.post("/imports", dependencies=[Depends(csrf_protect)])
async def imports_upload(
    request: Request, kind: str = Form(...), file: UploadFile = File(...),
    p: Principal = Depends(data_manager), db: Session = Depends(get_db),
):
    limit = get_settings().max_upload_mb * 1024 * 1024
    content = await file.read(limit + 1)
    if len(content) > limit:
        flash(request, f"File is larger than {get_settings().max_upload_mb} MB", "error")
        return RedirectResponse("/admin/imports", status_code=303)
    name = (file.filename or "upload")[:255]

    def go():
        if kind == "onsite_report":
            return import_onsite_report(db, content, name, p.employee).as_dict()
        if kind == "masterlist":
            return import_masterlist(db, content, name, p.employee).as_dict()
        if kind in ("presence", "absences"):
            return import_daily(db, kind, content, name, p.employee).as_dict()
        raise ServiceError("Unknown import kind")

    out = _run(request, db, go, f"{IMPORT_KINDS.get(kind, kind)} imported.", "/admin/imports")
    if out:
        request.session["last_import"] = {
            k: v for k, v in out.items() if k in ("rows", "warnings", "reconciliation", "reconciled_rows", "months",
                                                   "months_recomputed", "created", "updated", "employees")
        }
    return RedirectResponse("/admin/imports", status_code=303)


# --- months ---------------------------------------------------------------------------------------


@router.get("/months")
def months(request: Request, p: Principal = Depends(data_manager), db: Session = Depends(get_db)):
    from rto.services.months import get_month

    for m in months_with_results(db):
        get_month(db, m)
    db.commit()
    return render(request, "admin/months.html", **nav_ctx(request, db, p), months=list_months(db))


@router.post("/months/{month}/{action}", dependencies=[Depends(csrf_protect)])
def months_action(
    request: Request, month: str, action: str, reason: str = Form(""),
    p: Principal = Depends(data_manager), db: Session = Depends(get_db),
):
    m = parse_month(month, db)
    actions = {
        "publish": lambda: publish(db, p.employee, m),
        "finalize": lambda: finalize(db, p.employee, m),
        "reopen": lambda: reopen(db, p.employee, m, reason),
        "recompute": lambda: recompute_month(db, m),
    }
    if action not in actions:
        raise PermissionDenied("Unknown action")
    if action == "reopen":
        p.require(Role.HR_ADMIN)
    _run(request, db, actions[action], f"{m:%B %Y}: {action} done.", "/admin/months")
    return RedirectResponse("/admin/months", status_code=303)


# --- audit ----------------------------------------------------------------------------------------


@router.get("/audit")
def audit_log(
    request: Request, page: int = 1, entity: str = "", entity_id: str = "",
    p: Principal = Depends(current_principal), db: Session = Depends(get_db),
):
    p.require(Role.HR_ADMIN, Role.AUDITOR)
    per = 100
    q = select(AuditLog).order_by(AuditLog.id.desc())
    if entity:
        q = q.where(AuditLog.entity == entity)
    if entity_id:
        q = q.where(AuditLog.entity_id == entity_id)
    rows = list(db.scalars(q.offset((max(page, 1) - 1) * per).limit(per + 1)))
    return render(request, "admin/audit.html", **nav_ctx(request, db, p), rows=rows[:per], page=page,
                  has_next=len(rows) > per, entity=entity, entity_id=entity_id)


__all__ = ["router", "FIELDS", "parse_month"]
