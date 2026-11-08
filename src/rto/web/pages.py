"""End-user pages: dashboard, team, employee detail, reports and requests."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from rto.auth.deps import csrf_protect, current_principal
from rto.db import get_db
from rto.domain.calendar import weeks_of_month
from rto.models import Employee, OrgUnit, RequestType
from rto.services import ServiceError
from rto.services.access import Principal, can_view, visible_employee_ids
from rto.services.org import manager_chain, subordinate_ids, unit_descendants
from rto.services.reports import (
    aggregate,
    employee_days,
    employee_weeks,
    employee_year,
    export_csv,
    export_xlsx,
    latest_month_with_results,
    month_ref_date,
    month_rows,
    months_with_results,
    team,
    to_cells,
)
from rto.services.requests import (
    CATEGORIES,
    can_decide,
    can_view_request,
    create_request,
    current_step,
    decide,
    get_request,
    inbox,
    my_requests,
    withdraw,
)
from rto.web.common import back, flash, nav_ctx, parse_date, parse_month
from rto.web.templating import render

router = APIRouter()


@router.get("/")
def dashboard(request: Request, year: int | None = None, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    latest = latest_month_with_results(db)
    year = year or (latest.year if latest else dt.date.today().year)
    me = employee_year(db, p, p.id, year) if p.employee.in_scope else None
    weeks = to_cells(employee_weeks(db, p.id, latest)) if (latest and me) else []
    ctx = nav_ctx(request, db, p)
    team_nodes = team(db, p, latest, p.id) if (latest and ctx["is_manager"]) else []
    team_total = None
    if team_nodes:
        rows = month_rows(db, p, latest, employee_ids=subordinate_ids(db, p.id, month_ref_date(latest)))
        team_total = aggregate(rows, "all")[0] if rows else None
    return render(
        request, "dashboard.html", **ctx, me=me, year=year, latest=latest, weeks=weeks,
        requests=my_requests(db, p, limit=5), team_nodes=team_nodes, team_total=team_total,
    )


@router.get("/team")
def team_view(
    request: Request, month: str | None = None, root: int | None = None,
    p: Principal = Depends(current_principal), db: Session = Depends(get_db),
):
    m = parse_month(month, db)
    root_id = root or p.id
    nodes = team(db, p, m, root_id)
    root_emp = db.get(Employee, root_id)
    ref = month_ref_date(m)
    trail = []
    if root_id != p.id:
        for mgr in manager_chain(db, root_id, ref):
            if not can_view(db, p, mgr.id, ref) and mgr.id != p.id:
                break
            trail.insert(0, mgr)
            if mgr.id == p.id:
                break
    rows = month_rows(db, p, m, employee_ids={n.employee.id for n in nodes}) if nodes else []
    by_emp = {r.employee.id: r for r in rows}
    return render(
        request, "team/index.html", **nav_ctx(request, db, p), month=m, months=months_with_results(db),
        nodes=nodes, root=root_emp, trail=trail, rows=by_emp, week_mondays=weeks_of_month(m),
    )


@router.get("/employees/{employee_id}")
def employee_detail(
    request: Request, employee_id: int, year: int | None = None, month: str | None = None,
    p: Principal = Depends(current_principal), db: Session = Depends(get_db),
):
    m = parse_month(month, db)
    ey = employee_year(db, p, employee_id, year or m.year)
    weeks = to_cells(employee_weeks(db, employee_id, m))
    days = {}
    if weeks:
        days = employee_days(db, employee_id, weeks[0].monday, weeks[-1].monday + dt.timedelta(days=6))
    from rto.models import Request as Req

    reqs = list(db.scalars(select(Req).where(Req.employee_id == employee_id).order_by(Req.created_at.desc()).limit(20)))
    chain = manager_chain(db, employee_id, month_ref_date(m))
    return render(
        request, "employees/detail.html", **nav_ctx(request, db, p), ey=ey, month=m, weeks=weeks, days=days,
        requests=reqs, chain=chain, can_file=employee_id == p.id or any(c.id == p.id for c in chain) or p.can_manage_data,
    )


def _report_rows(db: Session, p: Principal, m: dt.date, unit: int | None, failed: bool):
    units = unit_descendants(db, [unit]) if unit else None
    return month_rows(db, p, m, org_unit_ids=units, only_failed=failed)


@router.get("/reports")
def reports(
    request: Request, month: str | None = None, unit: str = "", failed: str = "",
    p: Principal = Depends(current_principal), db: Session = Depends(get_db),
):
    m = parse_month(month, db)
    unit = int(unit) if unit.isdigit() else None
    failed = failed == "1"
    rows = _report_rows(db, p, m, unit, failed)
    all_rows = rows if not failed else _report_rows(db, p, m, unit, False)
    vis = visible_employee_ids(db, p, month_ref_date(m))
    units = list(db.scalars(select(OrgUnit).order_by(OrgUnit.name)))
    return render(
        request, "reports/index.html", **nav_ctx(request, db, p), month=m, months=months_with_results(db),
        rows=rows, total=(aggregate(all_rows, "all") or [None])[0], by_unit=aggregate(all_rows, "unit"),
        by_manager=aggregate(all_rows, "manager"), units=units, unit=unit, failed=failed,
        restricted=vis is not None, week_mondays=weeks_of_month(m),
    )


@router.get("/reports/export.{fmt}")
def reports_export(
    fmt: str, month: str | None = None, unit: str = "", failed: str = "",
    p: Principal = Depends(current_principal), db: Session = Depends(get_db),
):
    m = parse_month(month, db)
    unit = int(unit) if unit.isdigit() else None
    failed = failed == "1"
    rows = _report_rows(db, p, m, unit, failed)
    from rto.services.audit import audit

    audit(db, p.employee, "report.export", "report_month", m, {"format": fmt, "rows": len(rows), "unit": unit})
    db.commit()
    name = f"onsite_report_{m:%Y_%m}"
    if fmt == "xlsx":
        return Response(
            export_xlsx(rows, m),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{name}.xlsx"'},
        )
    if fmt == "csv":
        return Response(export_csv(rows, m), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})
    raise ServiceError("Unsupported export format")


# --- requests ------------------------------------------------------------------------------------


@router.get("/requests")
def requests_index(request: Request, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return render(request, "requests/index.html", **nav_ctx(request, db, p), mine=my_requests(db, p), inbox=inbox(db, p))


@router.get("/requests/new")
def request_new(
    request: Request, employee: int | None = None, type: str = "EXEMPTION",
    p: Principal = Depends(current_principal), db: Session = Depends(get_db),
):
    today = dt.date.today()
    ref = latest_month_with_results(db) or today
    reports_ids = subordinate_ids(db, p.id, ref) | subordinate_ids(db, p.id, today)
    people = [p.employee]
    if reports_ids:
        people += list(db.scalars(select(Employee).where(Employee.id.in_(reports_ids)).order_by(Employee.last_name)))
    selected = employee if employee in {e.id for e in people} else p.id
    return render(
        request, "requests/new.html", **nav_ctx(request, db, p), people=people, selected=selected,
        rtype=type if type in RequestType.__members__ else "EXEMPTION", categories=CATEGORIES,
    )


@router.post("/requests", dependencies=[Depends(csrf_protect)])
def request_create(
    request: Request,
    employee_id: int = Form(...),
    type: str = Form(...),
    start_date: str = Form(...),
    end_date: str = Form(""),
    days_per_week: str = Form(""),
    credited_dates: str = Form(""),
    category: str = Form(...),
    reason: str = Form(""),
    p: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
):
    try:
        credited = [parse_date(x.strip()) for x in credited_dates.replace(";", ",").split(",") if x.strip()]
        req = create_request(
            db, p, employee_id, RequestType(type), parse_date(start_date), parse_date(end_date),
            category, reason, days_per_week=float(days_per_week) if days_per_week.strip() else None,
            credited_dates=credited,
        )
        db.commit()
    except (ServiceError, ValueError) as exc:
        db.rollback()
        flash(request, str(exc), "error")
        return back(request, "/requests/new")
    flash(request, f"Request #{req.id} submitted.", "success")
    return RedirectResponse(f"/requests/{req.id}", status_code=303)


@router.get("/requests/{request_id}")
def request_detail(request: Request, request_id: int, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    req = get_request(db, request_id)
    if not can_view_request(db, p, req):
        raise ServiceError("Request not found")
    return render(
        request, "requests/detail.html", **nav_ctx(request, db, p), req=req, step=current_step(req),
        can_decide=can_decide(db, p, req),
        can_withdraw=req.status.value == "PENDING" and p.id in (req.employee_id, req.submitted_by_id),
    )


@router.post("/requests/{request_id}/decision", dependencies=[Depends(csrf_protect)])
def request_decision(
    request: Request, request_id: int, decision: str = Form(...), comment: str = Form(""),
    p: Principal = Depends(current_principal), db: Session = Depends(get_db),
):
    try:
        decide(db, p, request_id, decision == "approve", comment)
        db.commit()
        flash(request, "Decision recorded.", "success")
    except ServiceError as exc:
        db.rollback()
        flash(request, str(exc), "error")
    return RedirectResponse(f"/requests/{request_id}", status_code=303)


@router.post("/requests/{request_id}/withdraw", dependencies=[Depends(csrf_protect)])
def request_withdraw(request: Request, request_id: int, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    try:
        withdraw(db, p, request_id)
        db.commit()
        flash(request, "Request withdrawn.", "success")
    except ServiceError as exc:
        db.rollback()
        flash(request, str(exc), "error")
    return RedirectResponse(f"/requests/{request_id}", status_code=303)
