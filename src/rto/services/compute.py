"""Process steps 5-7: onsite day tagging, compliance computation and materialisation of results.

Results are materialised in ``weekly_result`` / ``monthly_result`` so that dashboards and reports are
cheap reads, even with thousands of employees and hundreds of concurrent managers.
Re-computation is idempotent and runs after every import, approved request or policy change.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.orm import Session

from rto.domain.calendar import add_months, first_of_month, frame_week, week_start, weeks_of_month
from rto.domain.compliance import WeekInput, evaluate_month, evaluate_week
from rto.domain.tagging import Absence, Presence, TaggingRules, tag_day
from rto.models import (
    AbsenceRecord,
    AttendanceCredit,
    Commitment,
    DayRecord,
    Employee,
    EmployeeAssignment,
    MonthlyResult,
    MonthStatus,
    PresenceEvent,
    ReportMonth,
    WeeklyAttendance,
    WeeklyResult,
    WeeklySource,
)
from rto.services import Conflict
from rto.services.calendar_data import CalendarData
from rto.services.commitments import commitment_for_week, level_default
from rto.services.org import assignments_at
from rto.services.policy import PolicyResolver

log = logging.getLogger(__name__)


def _chunks(seq, n=500):
    seq = list(seq)
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def _latest_assignments(db: Session, employee_ids: list[int], as_of: dt.date) -> dict[int, EmployeeAssignment]:
    found = {k: v for k, v in assignments_at(db, as_of).items() if k in set(employee_ids)}
    missing = [e for e in employee_ids if e not in found]
    for chunk in _chunks(missing):
        for a in db.scalars(
            select(EmployeeAssignment)
            .where(EmployeeAssignment.employee_id.in_(chunk))
            .order_by(EmployeeAssignment.valid_from)
        ):
            found[a.employee_id] = a  # last one wins
    return found


# ---------------------------------------------------------------------------------------------------
# Step 5: tagging
# ---------------------------------------------------------------------------------------------------


@dataclass
class TaggingStats:
    employees: int = 0
    days: int = 0
    weeks: int = 0


def tag_range(
    db: Session,
    start: dt.date,
    end: dt.date,
    employee_ids: set[int] | None = None,
    rules: TaggingRules | None = None,
) -> TaggingStats:
    """Tag every day in [start, end] (expanded to whole weeks) for employees with raw data.

    Only employees with at least one raw record in the range are tagged, so weekly figures imported
    from the legacy report for other employees are never overwritten with zeros.
    """
    start = week_start(start)
    end = week_start(end) + dt.timedelta(days=6)

    presence: dict[int, dict[dt.date, list[Presence]]] = defaultdict(lambda: defaultdict(list))
    q = select(PresenceEvent).where(PresenceEvent.date >= start, PresenceEvent.date <= end)
    if employee_ids is not None:
        q = q.where(PresenceEvent.employee_id.in_(employee_ids))
    for ev in db.scalars(q):
        presence[ev.employee_id][ev.date].append(Presence(ev.source, ev.direction))

    absences: dict[int, dict[dt.date, list[Absence]]] = defaultdict(lambda: defaultdict(list))
    q2 = select(AbsenceRecord).where(AbsenceRecord.date >= start, AbsenceRecord.date <= end)
    if employee_ids is not None:
        q2 = q2.where(AbsenceRecord.employee_id.in_(employee_ids))
    for ab in db.scalars(q2):
        absences[ab.employee_id][ab.date].append(
            Absence(ab.kind, ab.portion, approved=ab.status.upper() == "APPROVED")
        )

    covered = sorted(set(presence) | set(absences))
    if not covered:
        return TaggingStats()

    cal = CalendarData(db, start, end)
    assignments = _latest_assignments(db, covered, end)
    stats = TaggingStats(employees=len(covered))

    for chunk in _chunks(covered):
        db.execute(
            delete(DayRecord).where(
                DayRecord.employee_id.in_(chunk), DayRecord.date >= start, DayRecord.date <= end
            )
        )
        db.execute(
            delete(WeeklyAttendance).where(
                WeeklyAttendance.employee_id.in_(chunk),
                WeeklyAttendance.week_start >= start,
                WeeklyAttendance.week_start <= end,
            )
        )

    day_rows = []
    week_rows = []
    for emp_id in covered:
        a = assignments.get(emp_id)
        loc = a.location_id if a else None
        unit = a.org_unit_id if a else None
        hol = cal.holidays_for(loc)
        excl = cal.excluded_days(unit, loc)
        weekly: dict[dt.date, float] = defaultdict(float)
        d = start
        while d <= end:
            t = tag_day(
                d,
                presence[emp_id].get(d, []),
                absences[emp_id].get(d, []),
                is_holiday=d in hol,
                is_excluded=d in excl,
                rules=rules,
            )
            day_rows.append(
                {
                    "employee_id": emp_id,
                    "date": d,
                    "status": t.status,
                    "valid_value": t.valid_value,
                    "flags": t.flags,
                    "sources": t.sources,
                }
            )
            weekly[week_start(d)] += t.valid_value
            d += dt.timedelta(days=1)
        for monday, value in weekly.items():
            week_rows.append(
                {
                    "employee_id": emp_id,
                    "week_start": monday,
                    "valid_days": value,
                    "source": WeeklySource.DAILY,
                }
            )
    for chunk in _chunks(day_rows, 2000):
        db.execute(DayRecord.__table__.insert(), chunk)
    for chunk in _chunks(week_rows, 2000):
        db.execute(WeeklyAttendance.__table__.insert(), chunk)
    stats.days = len(day_rows)
    stats.weeks = len(week_rows)
    db.flush()
    return stats


# ---------------------------------------------------------------------------------------------------
# Steps 6-7: computation
# ---------------------------------------------------------------------------------------------------


@dataclass
class ComputeStats:
    month: dt.date
    employees: int = 0
    evaluated: int = 0
    passed: int = 0


def month_status(db: Session, month: dt.date) -> MonthStatus:
    rm = db.get(ReportMonth, first_of_month(month))
    return rm.status if rm else MonthStatus.OPEN


def recompute_month(
    db: Session, month: dt.date, employee_ids: set[int] | None = None, force: bool = False
) -> ComputeStats:
    month = first_of_month(month)
    if not force and month_status(db, month) == MonthStatus.FINALIZED:
        raise Conflict(f"{month:%B %Y} is finalized; reopen it before recomputing")

    mondays = weeks_of_month(month)
    first, last = mondays[0], mondays[-1] + dt.timedelta(days=6)
    ref = mondays[-1] + dt.timedelta(days=4)

    q = select(Employee).where(
        Employee.in_scope.is_(True),
        or_(Employee.hired_date.is_(None), Employee.hired_date <= last),
        or_(Employee.termination_date.is_(None), Employee.termination_date >= first),
    )
    if employee_ids is not None:
        q = q.where(Employee.id.in_(employee_ids))
    employees = list(db.scalars(q))
    ids = [e.id for e in employees]
    stats = ComputeStats(month=month, employees=len(ids))

    # Remove stale results (also for employees who left scope) for the selection.
    del_w = delete(WeeklyResult).where(WeeklyResult.month == month)
    del_m = delete(MonthlyResult).where(MonthlyResult.month == month)
    if employee_ids is not None:
        del_w = del_w.where(WeeklyResult.employee_id.in_(employee_ids))
        del_m = del_m.where(MonthlyResult.employee_id.in_(employee_ids))
    db.execute(del_w)
    db.execute(del_m)
    if not ids:
        db.flush()
        return stats

    assignments = _latest_assignments(db, ids, ref)
    policy = PolicyResolver(db)
    cal = CalendarData(db, first, last)

    commitments: dict[int, list[Commitment]] = defaultdict(list)
    attendance: dict[tuple[int, dt.date], WeeklyAttendance] = {}
    credits: dict[tuple[int, dt.date], list[AttendanceCredit]] = defaultdict(list)
    day_values: dict[tuple[int, dt.date], float] = {}
    for chunk in _chunks(ids):
        for c in db.scalars(
            select(Commitment).where(
                Commitment.employee_id.in_(chunk),
                Commitment.valid_from <= last,
                or_(Commitment.valid_to.is_(None), Commitment.valid_to > first),
            )
        ):
            commitments[c.employee_id].append(c)
        for w in db.scalars(
            select(WeeklyAttendance).where(
                WeeklyAttendance.employee_id.in_(chunk),
                WeeklyAttendance.week_start >= first,
                WeeklyAttendance.week_start <= last,
            )
        ):
            attendance[(w.employee_id, w.week_start)] = w
        for cr in db.scalars(
            select(AttendanceCredit).where(
                AttendanceCredit.employee_id.in_(chunk),
                and_(AttendanceCredit.date >= first, AttendanceCredit.date <= last),
            )
        ):
            credits[(cr.employee_id, week_start(cr.date))].append(cr)
        for dr in db.scalars(
            select(DayRecord).where(
                DayRecord.employee_id.in_(chunk), DayRecord.date >= first, DayRecord.date <= last
            )
        ):
            day_values[(dr.employee_id, dr.date)] = dr.valid_value

    weekly_rows, monthly_rows = [], []
    for emp in employees:
        a = assignments.get(emp.id)
        unit = a.org_unit_id if a else None
        loc = a.location_id if a else None
        pol = policy.resolve(unit, ref)
        holidays = cal.holidays_for(loc)
        default_commit = level_default(emp.ceo_level) or pol.default_commitment_days
        rows = commitments.get(emp.id, [])
        outcomes = []
        for monday in mondays:
            frame = frame_week(
                monday,
                holidays,
                min_working_days=pol.min_working_days,
                excluded_whole_week=cal.week_excluded(monday, unit, loc),
            )
            considered, reason = frame.considered, frame.exclusion_reason
            if considered and emp.hired_date and emp.hired_date > monday:
                considered, reason = False, "Hired during or after this week"
            if considered and emp.termination_date and emp.termination_date < monday + dt.timedelta(days=4):
                considered, reason = False, "Separated during or before this week"

            init_row = commitment_for_week(rows, monday, adjusted=False)
            adj_row = commitment_for_week(rows, monday, adjusted=True)
            commit = init_row.days_per_week if init_row else default_commit
            adj_commit = adj_row.days_per_week if adj_row else default_commit

            att = attendance.get((emp.id, monday))
            valid = att.valid_days if att else None
            adj_valid = valid
            if att is not None and att.adj_valid_days_override is not None:
                adj_valid = att.adj_valid_days_override
            else:
                bonus = 0.0
                for cr in credits.get((emp.id, monday), []):
                    already = day_values.get((emp.id, cr.date), 0.0)
                    bonus += max(0.0, min(cr.value, 1.0 - already))
                if bonus:
                    adj_valid = min(float(len(frame.working_days)), (valid or 0.0) + bonus)

            out = evaluate_week(
                WeekInput(
                    monday=monday,
                    month=month,
                    considered=considered,
                    exclusion_reason=reason,
                    working_days=len(frame.working_days),
                    valid_days=valid,
                    commitment=commit,
                    adj_valid_days=adj_valid,
                    adj_commitment=adj_commit,
                )
            )
            outcomes.append(out)
            weekly_rows.append(
                {
                    "employee_id": emp.id,
                    "week_start": monday,
                    "month": month,
                    "considered": out.considered,
                    "exclusion_reason": out.exclusion_reason,
                    "working_days": out.working_days,
                    "valid_days": out.valid_days,
                    "commitment": out.commitment,
                    "met": out.met,
                    "adj_valid_days": out.adj_valid_days,
                    "adj_commitment": out.adj_commitment,
                    "adj_met": out.adj_met,
                }
            )
        m = evaluate_month(month, outcomes, pol.monthly_pass_pct)
        considered_commitments = [o.commitment for o in outcomes if o.considered and o.commitment is not None]
        monthly_rows.append(
            {
                "employee_id": emp.id,
                "month": month,
                "org_unit_id": unit,
                "manager_id": a.manager_id if a else None,
                "location_id": loc,
                "commitment_days": considered_commitments[0] if considered_commitments else default_commit,
                "considered_weeks": m.considered_weeks,
                "met_weeks": m.met_weeks,
                "adherence": m.adherence,
                "passed": m.passed,
                "adj_met_weeks": m.adj_met_weeks,
                "adj_adherence": m.adj_adherence,
                "adj_passed": m.adj_passed,
                "threshold_pct": m.threshold_pct,
                "total_valid_days": m.total_valid_days,
                "avg_valid_days": m.avg_valid_days,
            }
        )
        if m.considered_weeks:
            stats.evaluated += 1
            stats.passed += 1 if m.adj_passed else 0

    for chunk in _chunks(weekly_rows, 2000):
        db.execute(WeeklyResult.__table__.insert(), chunk)
    for chunk in _chunks(monthly_rows, 2000):
        db.execute(MonthlyResult.__table__.insert(), chunk)
    db.flush()
    log.info("recomputed %s: %s employees, %s evaluated", month, stats.employees, stats.evaluated)
    return stats


def months_between(start: dt.date, end: dt.date) -> list[dt.date]:
    """Reporting months touched by weeks between ``start`` and ``end``."""
    out: list[dt.date] = []
    m = add_months(first_of_month(start), -1)
    stop = add_months(first_of_month(end), 1)
    while m <= stop:
        mondays = weeks_of_month(m)
        if mondays and mondays[0] <= end and mondays[-1] + dt.timedelta(days=6) >= start:
            out.append(m)
        m = add_months(m, 1)
    return out


def recompute_range(
    db: Session,
    start: dt.date,
    end: dt.date,
    employee_ids: set[int] | None = None,
    skip_finalized: bool = True,
) -> list[ComputeStats]:
    out = []
    for m in months_between(start, end):
        if skip_finalized and month_status(db, m) == MonthStatus.FINALIZED:
            continue
        out.append(recompute_month(db, m, employee_ids))
    return out
