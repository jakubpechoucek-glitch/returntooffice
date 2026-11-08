"""Read models for dashboards, team views and the Monthly Onsite Report (process steps 7 and 9)."""

from __future__ import annotations

import csv
import datetime as dt
import io
from collections import defaultdict
from dataclasses import dataclass, field

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from rto.domain.calendar import first_of_month, weeks_of_month
from rto.domain.compliance import evaluate_year
from rto.models import (
    Commitment,
    DayRecord,
    Employee,
    EmployeeAssignment,
    Location,
    MonthlyResult,
    OrgUnit,
    WeeklyResult,
)
from rto.services.access import Principal, require_view, visible_employee_ids
from rto.services.org import assignment_at, assignments_at, subordinate_ids
from rto.services.policy import PolicyResolver


def month_ref_date(month: dt.date) -> dt.date:
    mondays = weeks_of_month(first_of_month(month))
    return mondays[-1] + dt.timedelta(days=4)


def latest_month_with_results(db: Session) -> dt.date | None:
    return db.scalar(select(func.max(MonthlyResult.month)))


def months_with_results(db: Session) -> list[dt.date]:
    return list(db.scalars(select(MonthlyResult.month).distinct().order_by(MonthlyResult.month.desc())))


@dataclass
class WeekCell:
    monday: dt.date
    considered: bool
    reason: str | None
    valid: float | None
    commitment: float | None
    met: bool | None
    adj_valid: float | None
    adj_commitment: float | None
    adj_met: bool | None

    @property
    def changed_by_dispute(self) -> bool:
        return self.considered and (self.adj_valid != self.valid or self.adj_commitment != self.commitment)


@dataclass
class EmployeeMonthRow:
    employee: Employee
    result: MonthlyResult
    unit_name: str
    group_name: str
    manager_name: str
    location: str
    weeks: list[WeekCell] = field(default_factory=list)


def _units(db: Session) -> dict[int, OrgUnit]:
    return {u.id: u for u in db.scalars(select(OrgUnit))}


def month_rows(
    db: Session,
    p: Principal,
    month: dt.date,
    org_unit_ids: set[int] | None = None,
    manager_id: int | None = None,
    employee_ids: set[int] | None = None,
    only_failed: bool = False,
) -> list[EmployeeMonthRow]:
    month = first_of_month(month)
    vis = visible_employee_ids(db, p, month_ref_date(month))
    q = (
        select(MonthlyResult)
        .options(joinedload(MonthlyResult.employee))
        .where(MonthlyResult.month == month)
    )
    if vis is not None:
        q = q.where(MonthlyResult.employee_id.in_(vis))
    if employee_ids is not None:
        q = q.where(MonthlyResult.employee_id.in_(employee_ids))
    if org_unit_ids is not None:
        q = q.where(MonthlyResult.org_unit_id.in_(org_unit_ids))
    if manager_id is not None:
        q = q.where(MonthlyResult.manager_id == manager_id)
    if only_failed:
        q = q.where(MonthlyResult.adj_passed.is_(False))
    results = list(db.scalars(q))
    if not results:
        return []
    ids = [r.employee_id for r in results]
    weeks: dict[int, list[WeekCell]] = defaultdict(list)
    for i in range(0, len(ids), 500):
        for w in db.scalars(
            select(WeeklyResult)
            .where(WeeklyResult.month == month, WeeklyResult.employee_id.in_(ids[i : i + 500]))
            .order_by(WeeklyResult.week_start)
        ):
            weeks[w.employee_id].append(
                WeekCell(w.week_start, w.considered, w.exclusion_reason, w.valid_days, w.commitment, w.met,
                         w.adj_valid_days, w.adj_commitment, w.adj_met)
            )
    units = _units(db)
    managers = {e.id: e for e in db.scalars(select(Employee).where(Employee.id.in_({r.manager_id for r in results if r.manager_id})))}
    locations = {loc.id: loc.name for loc in db.scalars(select(Location))}
    rows = []
    for r in results:
        unit = units.get(r.org_unit_id) if r.org_unit_id else None
        group = units.get(unit.parent_id) if unit and unit.parent_id else None
        mgr = managers.get(r.manager_id) if r.manager_id else None
        rows.append(
            EmployeeMonthRow(
                employee=r.employee,
                result=r,
                unit_name=unit.name if unit else "-",
                group_name=group.name if group and group.kind.value != "COMPANY" else (unit.name if unit else "-"),
                manager_name=mgr.full_name if mgr else "-",
                location=locations.get(r.location_id, "-") if r.location_id else "-",
                weeks=weeks.get(r.employee_id, []),
            )
        )
    rows.sort(key=lambda x: (x.group_name, x.unit_name, x.employee.last_name, x.employee.first_name))
    return rows


@dataclass
class Aggregate:
    key: str
    label: str
    headcount: int = 0
    evaluated: int = 0
    passed_initial: int = 0
    passed: int = 0  # after dispute
    met_weeks: int = 0
    considered_weeks: int = 0
    total_valid: float = 0.0

    @property
    def pass_rate(self) -> float | None:
        return self.passed / self.evaluated if self.evaluated else None

    @property
    def avg_days(self) -> float | None:
        return self.total_valid / self.considered_weeks if self.considered_weeks else None

    def add(self, r: MonthlyResult) -> None:
        self.headcount += 1
        if r.considered_weeks:
            self.evaluated += 1
            self.passed += 1 if r.adj_passed else 0
            self.passed_initial += 1 if r.passed else 0
            self.met_weeks += r.adj_met_weeks
            self.considered_weeks += r.considered_weeks
            self.total_valid += r.total_valid_days


def aggregate(rows: list[EmployeeMonthRow], by: str) -> list[Aggregate]:
    out: dict[str, Aggregate] = {}
    for row in rows:
        if by == "unit":
            key, label = f"{row.group_name}/{row.unit_name}", f"{row.group_name} / {row.unit_name}" if row.group_name != row.unit_name else row.unit_name
        elif by == "manager":
            key, label = str(row.result.manager_id), row.manager_name
        else:
            key, label = "all", "Total"
        out.setdefault(key, Aggregate(key, label)).add(row.result)
    return sorted(out.values(), key=lambda a: a.label)


# --- team tree ------------------------------------------------------------------------------------


@dataclass
class TeamNode:
    employee: Employee
    result: MonthlyResult | None
    is_manager: bool
    subtree: Aggregate


def team(db: Session, p: Principal, month: dt.date, root_id: int) -> list[TeamNode]:
    """Direct reports of ``root_id`` with their own result and the aggregate of everyone below them."""
    month = first_of_month(month)
    ref = month_ref_date(month)
    require_view(db, p, root_id, ref)
    directs = subordinate_ids(db, root_id, ref, direct_only=True)
    if not directs:
        return []
    all_assign = assignments_at(db, ref)
    children: dict[int, list[int]] = defaultdict(list)
    for eid, a in all_assign.items():
        if a.manager_id:
            children[a.manager_id].append(eid)
    results = {
        r.employee_id: r for r in db.scalars(select(MonthlyResult).where(MonthlyResult.month == month))
    }
    emps = {e.id: e for e in db.scalars(select(Employee).where(Employee.id.in_(directs)))}
    nodes = []
    for eid in directs:
        agg = Aggregate(str(eid), "subtree")
        stack, seen = list(children.get(eid, [])), set()
        while stack:
            x = stack.pop()
            if x in seen:
                continue
            seen.add(x)
            if x in results:
                agg.add(results[x])
            stack.extend(children.get(x, []))
        nodes.append(TeamNode(emps[eid], results.get(eid), bool(children.get(eid)), agg))
    nodes.sort(key=lambda n: (not n.is_manager, n.employee.last_name))
    return nodes


# --- organisation tree ----------------------------------------------------------------------------


@dataclass
class OrgNode:
    employee: Employee
    result: MonthlyResult | None
    unit_name: str
    children: list[OrgNode] = field(default_factory=list)
    subtree: Aggregate = field(default_factory=lambda: Aggregate("subtree", "subtree"))
    size: int = 0  # number of people below (all levels)


def org_tree(db: Session, p: Principal, month: dt.date) -> list[OrgNode]:
    """Reporting-line tree as of ``month``, limited to the people the principal may see.

    Roots are visible people whose manager is not visible (or who have no manager). Managers who are
    not tracked themselves (e.g. ExCo members known only from the masterlist) appear as tree nodes.
    """
    month = first_of_month(month)
    ref = month_ref_date(month)
    vis = visible_employee_ids(db, p, ref)
    assign = assignments_at(db, ref)
    everyone = set(assign) | {a.manager_id for a in assign.values() if a.manager_id}
    ids = everyone if vis is None else set(vis)
    children: dict[int, list[int]] = defaultdict(list)
    for eid in ids:
        a = assign.get(eid)
        if a and a.manager_id in ids and a.manager_id != eid:
            children[a.manager_id].append(eid)
    roots = [
        eid for eid in ids
        if not (assign.get(eid) and assign[eid].manager_id in ids)
        # people outside the structure (e.g. HR staff with no placement and no reports) are not shown
        and (eid in assign or children.get(eid))
    ]

    emps = {}
    id_list = list(ids)
    for i in range(0, len(id_list), 500):
        for e in db.scalars(select(Employee).where(Employee.id.in_(id_list[i : i + 500]))):
            emps[e.id] = e
    results = {
        r.employee_id: r
        for r in db.scalars(select(MonthlyResult).where(MonthlyResult.month == month))
        if r.employee_id in ids
    }
    units = _units(db)

    def build(eid: int, seen: frozenset[int]) -> OrgNode | None:
        e = emps.get(eid)
        if e is None:
            return None
        a = assign.get(eid)
        unit = units.get(a.org_unit_id).name if a and a.org_unit_id in units else ""
        node = OrgNode(e, results.get(eid), unit)
        for cid in children.get(eid, []):
            if cid in seen:  # defensive: never loop on bad data
                continue
            child = build(cid, seen | {cid})
            if child is None:
                continue
            node.children.append(child)
            node.size += 1 + child.size
            if child.result is not None:
                node.subtree.add(child.result)
            for k in ("headcount", "evaluated", "passed", "passed_initial", "met_weeks", "considered_weeks"):
                setattr(node.subtree, k, getattr(node.subtree, k) + getattr(child.subtree, k))
            node.subtree.total_valid += child.subtree.total_valid
        # managers first, then alphabetical
        node.children.sort(key=lambda n: (not n.children, n.employee.last_name, n.employee.first_name))
        return node

    out = [n for n in (build(r, frozenset({r})) for r in roots) if n is not None]
    out.sort(key=lambda n: (-n.size, n.employee.last_name))
    return out


# --- employee detail ------------------------------------------------------------------------------


@dataclass
class EmployeeYear:
    employee: Employee
    assignment: EmployeeAssignment | None
    months: list[MonthlyResult]
    annual_passed: bool
    annual_rate: float
    ytd_rate: float | None
    adhering_months: int
    evaluated_months: int
    annual_threshold: float
    monthly_threshold: float
    threshold_origin: dict[str, str]
    commitments: list[Commitment]


def employee_year(db: Session, p: Principal, employee_id: int, year: int) -> EmployeeYear:
    today = dt.date.today()
    ref = min(today, dt.date(year, 12, 31))
    require_view(db, p, employee_id, ref)
    emp = db.get(Employee, employee_id)
    a = assignment_at(db, employee_id, ref)
    if a is None:
        a = db.scalar(
            select(EmployeeAssignment)
            .where(EmployeeAssignment.employee_id == employee_id)
            .order_by(EmployeeAssignment.valid_from.desc())
        )
    months = list(
        db.scalars(
            select(MonthlyResult)
            .where(
                MonthlyResult.employee_id == employee_id,
                MonthlyResult.month >= dt.date(year, 1, 1),
                MonthlyResult.month <= dt.date(year, 12, 1),
            )
            .order_by(MonthlyResult.month)
        )
    )
    pol = PolicyResolver(db).resolve(a.org_unit_id if a else None, ref)
    ann = evaluate_year(year, [m.adj_passed if m.considered_weeks else None for m in months], pol.annual_pass_pct)
    commitments = list(
        db.scalars(
            select(Commitment).where(Commitment.employee_id == employee_id).order_by(Commitment.valid_from.desc())
        )
    )
    return EmployeeYear(
        emp, a, months, ann.passed, ann.rate, ann.ytd_rate, ann.adhering_months, ann.evaluated_months,
        pol.annual_pass_pct, pol.monthly_pass_pct, pol.origin, commitments,
    )


def employee_weeks(db: Session, employee_id: int, month: dt.date) -> list[WeeklyResult]:
    return list(
        db.scalars(
            select(WeeklyResult)
            .where(WeeklyResult.employee_id == employee_id, WeeklyResult.month == first_of_month(month))
            .order_by(WeeklyResult.week_start)
        )
    )


def to_cells(rows: list[WeeklyResult]) -> list[WeekCell]:
    return [
        WeekCell(w.week_start, w.considered, w.exclusion_reason, w.valid_days, w.commitment, w.met,
                 w.adj_valid_days, w.adj_commitment, w.adj_met)
        for w in rows
    ]


def employee_days(db: Session, employee_id: int, start: dt.date, end: dt.date) -> dict[dt.date, DayRecord]:
    return {
        d.date: d
        for d in db.scalars(
            select(DayRecord).where(
                DayRecord.employee_id == employee_id, DayRecord.date >= start, DayRecord.date <= end
            )
        )
    }


# --- export ---------------------------------------------------------------------------------------

LEGACY_HEADER = [
    "MONTH", "DEPARTMENT GROUP", "STATUS", "EMPLOYEE NUMBER", "EMPLOYEE EMAIL", "FIRST NAME", "LAST NAME",
    "DEPARTMENT NAME", "POSITION", "MANAGER", "PEOPLE LEVEL", "CEO LEVEL", "HIRED DATE", "LOCATION",
    "ARRANGEMENT", "COMMITTED DAYS PER WEEK",
]


def export_rows(rows: list[EmployeeMonthRow], month: dt.date) -> tuple[list[str], list[list]]:
    n_weeks = max([len([w for w in r.weeks if w.considered]) for r in rows] + [len(weeks_of_month(month))])
    header = list(LEGACY_HEADER)
    header += [f"W{i + 1}" for i in range(n_weeks)]
    header += ["Monthly Adherence", "Met Commitment", "Average Onsite Days/Wk", "Count of Onsite + OB + Leave Days"]
    header += [f"W{i + 1} + dispute" for i in range(n_weeks)]
    header += ["Monthly Adherence AFTER DISPUTE", "MET_COMMITMENT WITH DISPUTE", "Monthly threshold %"]
    out = []
    for r in rows:
        e, m = r.employee, r.result
        considered = [w for w in r.weeks if w.considered]
        line = [
            month.strftime("%Y-%m"), r.group_name, e.status, e.employee_number, e.email, e.first_name, e.last_name,
            r.unit_name, e.position, r.manager_name, e.people_level, f"CEO-{e.ceo_level}" if e.ceo_level else "",
            e.hired_date.isoformat() if e.hired_date else "", r.location, e.arrangement or "", m.commitment_days,
        ]
        line += [w.valid for w in considered] + [None] * (n_weeks - len(considered))
        line += [
            m.adherence, "" if m.passed is None else ("Y" if m.passed else "N"),
            None if m.avg_valid_days is None else round(m.avg_valid_days, 2), m.total_valid_days,
        ]
        line += [w.adj_valid for w in considered] + [None] * (n_weeks - len(considered))
        line += [m.adj_adherence, "" if m.adj_passed is None else ("Y" if m.adj_passed else "N"), m.threshold_pct]
        out.append(line)
    return header, out


def export_xlsx(rows: list[EmployeeMonthRow], month: dt.date) -> bytes:
    header, data = export_rows(rows, month)
    wb = Workbook()
    ws = wb.active
    ws.title = "DATA"
    ws.append(header)
    for c in ws[1]:
        c.font = Font(bold=True)
        c.fill = PatternFill("solid", fgColor="DDDDDD")
    for line in data:
        ws.append(line)
    pct_cols = [i + 1 for i, h in enumerate(header) if h.startswith("Monthly Adherence")]
    for col in pct_cols:
        for row in ws.iter_rows(min_row=2, min_col=col, max_col=col):
            row[0].number_format = "0%"
    ws.freeze_panes = "E2"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def export_csv(rows: list[EmployeeMonthRow], month: dt.date) -> bytes:
    header, data = export_rows(rows, month)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for line in data:
        # neutralise spreadsheet formula injection
        w.writerow([("'" + v) if isinstance(v, str) and v[:1] in ("=", "+", "-", "@") else v for v in line])
    return buf.getvalue().encode("utf-8-sig")
