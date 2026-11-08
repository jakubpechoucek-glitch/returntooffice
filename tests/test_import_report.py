import datetime as dt

from sqlalchemy import func, select

from rto.importers.onsite_report import import_onsite_report
from rto.models import Employee, MonthlyResult, WeeklyResult
from rto.services.org import assignment_at, subordinate_ids


def by_number(db, number):
    return db.scalar(select(Employee).where(Employee.employee_number == number))


def test_sample_report_reconciles_exactly(db, sample_bytes):
    res = import_onsite_report(db, sample_bytes, "sample.xlsx")
    db.commit()
    assert res.rows == 26
    in_scope = db.scalar(select(func.count()).select_from(Employee).where(Employee.in_scope.is_(True)))
    assert in_scope == 13
    assert res.reconciled_rows == 26
    # Every computed monthly adherence and "Met Commitment" flag equals the hand-made report.
    assert res.reconciliation == []
    assert res.months == ["2026-01-01", "2026-03-01"]


def test_sample_report_builds_hierarchy(db, sample_bytes):
    import_onsite_report(db, sample_bytes, "sample.xlsx")
    marco = by_number(db, "900013")
    nina = db.scalar(select(Employee).where(Employee.email == "nina.cabrera@example.com"))
    daphne = by_number(db, "900004")
    assert nina is not None and nina.in_scope is False  # leader known only as a manager
    as_of = dt.date(2026, 3, 15)
    assert assignment_at(db, marco.id, as_of).manager_id == nina.id
    assert len(subordinate_ids(db, daphne.id, as_of)) == 5
    # Nina sees everyone: 13 employees in the file
    assert len(subordinate_ids(db, nina.id, as_of)) == 13


def test_sample_values(db, sample_bytes):
    import_onsite_report(db, sample_bytes, "sample.xlsx")
    hazel = by_number(db, "900008")
    jan = db.get(MonthlyResult, (hazel.id, dt.date(2026, 1, 1)))
    assert (jan.considered_weeks, jan.met_weeks, jan.passed) == (4, 0, False)
    mar = db.get(MonthlyResult, (hazel.id, dt.date(2026, 3, 1)))
    assert (mar.met_weeks, mar.passed) == (4, True)
    weeks = db.scalars(
        select(WeeklyResult).where(WeeklyResult.employee_id == hazel.id).order_by(WeeklyResult.week_start)
    ).all()
    assert [w.valid_days for w in weeks if w.month == dt.date(2026, 1, 1) and w.considered] == [0, 1, 1, 1]


def test_reimport_is_idempotent(db, sample_bytes):
    import_onsite_report(db, sample_bytes, "a.xlsx")
    db.commit()
    res = import_onsite_report(db, sample_bytes, "b.xlsx")
    db.commit()
    assert res.employees_created == 0 and res.reconciliation == []
    assert db.scalar(select(func.count()).select_from(MonthlyResult)) == 26
