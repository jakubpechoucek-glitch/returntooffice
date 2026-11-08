"""Importer for the legacy *Monthly Onsite Report* workbook (sheet ``DATA`` + ``COLLATED_DISPUTES``).

The workbook is the current hand-made output of HR Analytics: one row per employee and month with the
masterlist attributes, the reporting line, the weekly commitment and the number of valid onsite days
per week (``W1``..``W5``), plus "after dispute" columns.

What the importer does
----------------------
1. Upserts org units (DEPARTMENT GROUP -> DEPARTMENT NAME), office locations and employees.
2. Creates placeholder employees for managers / leaders that are not rows themselves (``in_scope=False``).
3. Records the reporting line and the baseline commitment, effective from the month (effective-dated).
4. Stores the weekly valid-day counts (``WeeklySource.REPORT_IMPORT``), never overwriting weeks that
   already have daily data.
5. Imports collated disputes / exemptions as already-approved requests.
6. Recomputes the months and **reconciles** the computed monthly adherence and "Met Commitment" flag
   with the values in the workbook; mismatches are listed in the import summary.

Column matching is case/whitespace-insensitive; weekly columns are detected by the ``W<n>`` prefix.
"""

from __future__ import annotations

import datetime as dt
import io
import re
from dataclasses import dataclass, field
from typing import Any

from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from rto.domain.calendar import first_of_month, spans_year_boundary, weeks_of_month
from rto.models import (
    CommitmentKind,
    Employee,
    ImportBatch,
    MonthlyResult,
    OrgUnitKind,
    Request,
    RequestStatus,
    RequestType,
    WeeklyAttendance,
    WeeklySource,
)
from rto.services import ServiceError
from rto.services.audit import audit
from rto.services.commitments import add_override, set_baseline
from rto.models import MonthStatus
from rto.services.compute import month_status, recompute_month
from rto.services.org import change_assignment, company, get_or_create_location, get_or_create_unit
from rto.services.requests import CATEGORIES, apply_effects

REQUIRED = [
    "MONTH",
    "EMPLOYEE NUMBER",
    "EMPLOYEE EMAIL",
    "DEPARTMENT NAME",
    "COMMITTED DAYS PER WEEK",
]


def _norm(h: Any) -> str:
    return re.sub(r"\s+", " ", str(h or "")).strip().upper()


def _blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and v.strip() in ("", "#N/A", "N/A", "NA", "-"))


def _num(v: Any) -> float | None:
    if _blank(v):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _date(v: Any) -> dt.date | None:
    if _blank(v):
        return None
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%b %Y", "%B %Y"):
        try:
            return dt.datetime.strptime(str(v).strip(), fmt).date()
        except ValueError:
            continue
    raise ServiceError(f"Unrecognised date: {v!r}")


def _str(v: Any) -> str | None:
    return None if _blank(v) else str(v).strip()


def _ceo_level(v: Any) -> int | None:
    m = re.search(r"(\d+)", str(v or ""))
    return int(m.group(1)) if m else None


def _emp_number(v: Any) -> str:
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


@dataclass
class ImportResult:
    batch_id: int | None = None
    rows: int = 0
    employees_created: int = 0
    employees_updated: int = 0
    placeholders_created: int = 0
    weeks_imported: int = 0
    weeks_skipped_daily: int = 0
    requests_imported: int = 0
    months: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    reconciliation: list[dict[str, Any]] = field(default_factory=list)
    reconciled_rows: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


class _Ctx:
    def __init__(self, db: Session, result: ImportResult):
        self.db = db
        self.result = result
        self.by_number: dict[str, Employee] = {}
        self.by_email: dict[str, Employee] = {}
        for e in db.scalars(select(Employee)):
            self.by_number[e.employee_number] = e
            self.by_email[e.email.lower()] = e

    def placeholder(self, name: str | None, email: str | None) -> Employee | None:
        if not email:
            return None
        key = email.lower()
        if key in self.by_email:
            return self.by_email[key]
        first, _, last = (name or email.split("@")[0]).partition(" ")
        emp = Employee(
            employee_number=f"EXT:{key}",
            email=key,
            first_name=first,
            last_name=last,
            in_scope=False,
            status="Active",
        )
        self.db.add(emp)
        self.db.flush()
        self.by_email[key] = emp
        self.by_number[emp.employee_number] = emp
        self.result.placeholders_created += 1
        return emp


def import_onsite_report(
    db: Session, content: bytes, filename: str, actor: Employee | None = None
) -> ImportResult:
    try:
        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception as exc:  # corrupted / not an xlsx
        raise ServiceError(f"Cannot read workbook: {exc}") from exc
    sheet = next((ws for ws in wb.worksheets if _norm(ws.title) == "DATA"), None)
    if sheet is None:
        raise ServiceError("Sheet 'DATA' not found")

    rows_iter = sheet.iter_rows(values_only=True)
    header = [_norm(h) for h in next(rows_iter, [])]
    missing = [c for c in REQUIRED if c not in header]
    if missing:
        raise ServiceError(f"Missing columns: {', '.join(missing)}")
    col = {h: i for i, h in enumerate(header) if h}
    week_cols = [i for i, h in enumerate(header) if re.match(r"^W\d+\b", h) and "DISPUTE" not in h]
    adj_week_cols = [i for i, h in enumerate(header) if re.match(r"^W\d+\b", h) and "DISPUTE" in h]

    result = ImportResult()
    ctx = _Ctx(db, result)
    batch = ImportBatch(kind="ONSITE_REPORT_XLSX", filename=filename, created_by_id=actor.id if actor else None)
    db.add(batch)
    db.flush()
    result.batch_id = batch.id
    root = company(db)

    def g(row: tuple, name: str) -> Any:
        i = col.get(name)
        return row[i] if i is not None and i < len(row) else None

    data_rows = [r for r in rows_iter if r and not all(_blank(v) for v in r)]
    parsed = []
    for idx, r in enumerate(data_rows, start=2):
        month = _date(g(r, "MONTH"))
        if month is None or _blank(g(r, "EMPLOYEE NUMBER")):
            result.warnings.append(f"Row {idx}: missing month or employee number, skipped")
            continue
        parsed.append((idx, first_of_month(month), r))
    # Effective-dated changes must be applied in chronological order.
    parsed.sort(key=lambda t: (t[1], t[0]))

    expected: dict[tuple[int, dt.date], tuple[float | None, str | None, float | None, str | None]] = {}
    months: set[dt.date] = set()

    for idx, month, r in parsed:
        result.rows += 1
        months.add(month)
        number = _emp_number(g(r, "EMPLOYEE NUMBER"))
        email = (_str(g(r, "EMPLOYEE EMAIL")) or f"{number}@unknown.invalid").lower()

        emp = ctx.by_number.get(number) or ctx.by_email.get(email)
        if emp is None:
            emp = Employee(employee_number=number, email=email)
            db.add(emp)
            result.employees_created += 1
        else:
            result.employees_updated += 1
            if emp.employee_number.startswith("EXT:"):
                emp.employee_number = number
        emp.email = email
        emp.first_name = _str(g(r, "FIRST NAME")) or emp.first_name or ""
        emp.last_name = _str(g(r, "LAST NAME")) or emp.last_name or ""
        emp.position = _str(g(r, "POSITION")) or emp.position
        emp.people_level = _str(g(r, "PEOPLE LEVEL")) or emp.people_level
        emp.ceo_level = _ceo_level(g(r, "CEO LEVEL")) or emp.ceo_level
        emp.status = _str(g(r, "STATUS")) or emp.status or "Active"
        emp.site = _str(g(r, "SITE")) or emp.site
        emp.arrangement = _str(g(r, "ARRANGEMENT")) or emp.arrangement
        emp.hired_date = _date(g(r, "HIRED DATE")) or emp.hired_date
        emp.in_scope = True
        db.flush()
        ctx.by_number[number] = emp
        ctx.by_email[email] = emp

        group_name = _str(g(r, "DEPARTMENT GROUP"))
        dept_name = _str(g(r, "DEPARTMENT NAME")) or "Unassigned"
        parent = get_or_create_unit(db, OrgUnitKind.DEPARTMENT_GROUP, group_name, root) if group_name else root
        dept = get_or_create_unit(db, OrgUnitKind.DEPARTMENT, dept_name, parent)
        loc = get_or_create_location(db, _str(g(r, "LOCATION")))
        mgr = ctx.placeholder(_str(g(r, "MANAGER")), _str(g(r, "MANAGER EMAIL")))
        ctx.placeholder(_str(g(r, "CEO 2")), _str(g(r, "CEO 2 EMAIL")))
        if mgr is not None and mgr.id == emp.id:
            mgr = None

        mondays = [m for m in weeks_of_month(month) if not spans_year_boundary(m)]
        effective = mondays[0] if mondays else month
        try:
            change_assignment(
                db, actor, emp.id, effective, manager_id=mgr.id if mgr else None,
                org_unit_id=dept.id, location_id=loc.id if loc else None, source="IMPORT",
            )
        except ServiceError as exc:
            result.warnings.append(f"Row {idx} ({number}): reporting line not updated: {exc}")

        commit = _num(g(r, "COMMITTED DAYS PER WEEK"))
        if commit is not None:
            set_baseline(db, actor, emp.id, commit, effective, source="IMPORT")
        commit_disp = _num(g(r, "COMMITMENT PER WEEK WITH DISPUTE"))
        if commit_disp is not None and commit is not None and commit_disp != commit and mondays:
            add_override(
                db, emp.id, CommitmentKind.DISPUTE, commit_disp, mondays[0], mondays[-1] + dt.timedelta(days=6),
                "Imported dispute", None, source="IMPORT",
            )

        if len(week_cols) < len(mondays):
            # Legacy layout has a fixed number of W columns; a 5-week month cannot be represented fully.
            result.warnings.append(
                f"Row {idx} ({number}): {month:%b %Y} has {len(mondays)} weeks but the file has "
                f"{len(week_cols)} weekly columns; the last week(s) have no data"
            )
        for n, monday in enumerate(mondays):
            if n >= len(week_cols):
                break
            val = _num(r[week_cols[n]] if week_cols[n] < len(r) else None)
            if val is None:
                continue
            adj = None
            if n < len(adj_week_cols) and adj_week_cols[n] < len(r):
                adj = _num(r[adj_week_cols[n]])
            existing = db.get(WeeklyAttendance, (emp.id, monday))
            if existing is not None and existing.source == WeeklySource.DAILY:
                result.weeks_skipped_daily += 1
                continue
            if existing is None:
                existing = WeeklyAttendance(employee_id=emp.id, week_start=monday)
                db.add(existing)
            existing.valid_days = val
            existing.source = WeeklySource.REPORT_IMPORT
            existing.adj_valid_days_override = adj
            existing.import_batch_id = batch.id
            result.weeks_imported += 1

        expected[(emp.id, month)] = (
            _num(g(r, "MONTHLY ADHERENCE")),
            _str(g(r, "MET COMMITMENT")),
            _num(g(r, "MONTHLY ADHERENCE AFTER DISPUTE")),
            _str(g(r, "MET_COMMITMENT WITH DISPUTE")),
        )
    db.flush()

    disputes = next((ws for ws in wb.worksheets if _norm(ws.title) == "COLLATED_DISPUTES"), None)
    if disputes is not None:
        _import_disputes(db, ctx, disputes, result, actor)

    for m in sorted(months):
        if month_status(db, m) == MonthStatus.FINALIZED:
            result.warnings.append(f"{m:%B %Y} is finalized: data stored but results not recomputed")
            continue
        recompute_month(db, m)
        result.months.append(m.isoformat())
    _reconcile(db, expected, result, ctx)

    batch.row_count = result.rows
    batch.summary = {
        k: v for k, v in result.as_dict().items() if k not in ("reconciliation",)
    } | {"mismatches": len(result.reconciliation)}
    batch.status = "SUCCEEDED_WITH_WARNINGS" if result.warnings or result.reconciliation else "SUCCEEDED"
    audit(db, actor, "import.onsite_report", "import_batch", batch.id, {"filename": filename, "rows": result.rows})
    db.flush()
    return result


def _import_disputes(db: Session, ctx: _Ctx, ws, result: ImportResult, actor: Employee | None) -> None:
    rows = ws.iter_rows(values_only=True)
    header = [_norm(h) for h in next(rows, [])]
    col = {h: i for i, h in enumerate(header)}
    for idx, r in enumerate(rows, start=2):
        if not r or all(_blank(v) for v in r):
            continue

        def g(name: str, r=r) -> Any:
            i = col.get(name)
            return r[i] if i is not None and i < len(r) else None

        emp = ctx.by_number.get(_emp_number(g("EMP_NO"))) if not _blank(g("EMP_NO")) else None
        start, end = _date(g("START_DATE")), _date(g("END_DATE"))
        if emp is None or start is None:
            result.warnings.append(f"Disputes row {idx}: unknown employee or missing start date, skipped")
            continue
        type_txt = (_str(g("TYPE")) or "").upper()
        rtype = RequestType.EXEMPTION if "EXEMPT" in type_txt or "EXCEPT" in type_txt else RequestType.DISPUTE
        category_txt = _str(g("CATEGORY")) or _str(g("SUBTYPE")) or "Other"
        category = next((c for c in CATEGORIES if c.lower() == category_txt.lower()), None)
        if category is None:
            category = "Medical" if "MEDIC" in category_txt.upper() else "Other"
        req = Request(
            type=rtype,
            employee_id=emp.id,
            submitted_by_id=actor.id if actor else None,
            status=RequestStatus.APPROVED,
            start_date=start,
            end_date=end or start,
            days_per_week=_num(g("COUNT_COMMIT")),
            credited_dates=[],
            category=category,
            reason=f"Imported: {_str(g('TYPE')) or ''} / {_str(g('SUBTYPE')) or ''}".strip(),
            source="IMPORT",
            decided_at=dt.datetime.now(dt.UTC),
        )
        if req.days_per_week is None:
            result.warnings.append(f"Disputes row {idx}: COUNT_COMMIT missing, skipped")
            continue
        db.add(req)
        db.flush()
        apply_effects(db, req, actor)
        result.requests_imported += 1


def _yn(v: str | None) -> bool | None:
    if v is None:
        return None
    return v.strip().upper().startswith("Y")


def _reconcile(db: Session, expected: dict, result: ImportResult, ctx: _Ctx) -> None:
    for (emp_id, month), (adh, met, adh_d, met_d) in expected.items():
        res = db.get(MonthlyResult, (emp_id, month))
        if res is None:
            continue
        issues = []
        if adh is not None and (res.adherence is None or abs(res.adherence - adh) > 0.005):
            issues.append(f"adherence file={adh:.2%} computed={res.adherence if res.adherence is None else f'{res.adherence:.2%}'}")
        if _yn(met) is not None and _yn(met) != res.passed:
            issues.append(f"met commitment file={met} computed={'Y' if res.passed else 'N'}")
        if adh_d is not None and (res.adj_adherence is None or abs(res.adj_adherence - adh_d) > 0.005):
            issues.append(f"adherence after dispute file={adh_d:.2%} computed={res.adj_adherence}")
        if _yn(met_d) is not None and _yn(met_d) != res.adj_passed:
            issues.append(f"met after dispute file={met_d} computed={'Y' if res.adj_passed else 'N'}")
        result.reconciled_rows += 1
        if issues:
            emp = db.get(Employee, emp_id)
            result.reconciliation.append(
                {"employee_number": emp.employee_number if emp else emp_id, "month": month.isoformat(), "issues": issues}
            )
