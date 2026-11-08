import datetime as dt

import pytest
from sqlalchemy import select

from rto.importers.daily import import_daily
from rto.importers.masterlist import import_masterlist
from rto.importers.onsite_report import import_onsite_report
from rto.models import (
    DayRecord,
    DayStatus,
    Employee,
    MonthlyResult,
    OrgUnit,
    RequestStatus,
    RequestType,
    Role,
    RoleAssignment,
    StepDecision,
    WeeklyResult,
)
from rto.services import PermissionDenied, ServiceError
from rto.services.access import can_view, load_principal, visible_employee_ids
from rto.services.compute import recompute_month
from rto.services.months import finalize
from rto.services.org import change_assignment
from rto.services.policy import resolve_policy, set_policy
from rto.services.requests import create_request, decide, inbox, withdraw

MAR = dt.date(2026, 3, 1)
JAN = dt.date(2026, 1, 1)


def emp(db, number):
    return db.scalar(select(Employee).where(Employee.employee_number == number))


@pytest.fixture()
def loaded(db, sample_bytes):
    import_onsite_report(db, sample_bytes, "sample.xlsx")
    pp = Employee(employee_number="PP1", email="pp@example.com", first_name="Pat", last_name="Partner", in_scope=False)
    db.add(pp)
    db.flush()
    group = db.scalar(select(OrgUnit).where(OrgUnit.name == "Collections", OrgUnit.kind == "DEPARTMENT_GROUP"))
    db.add(RoleAssignment(employee_id=pp.id, role=Role.PEOPLE_PARTNER, org_unit_id=group.id))
    db.flush()
    return db


def P(db, number_or_emp):
    e = number_or_emp if isinstance(number_or_emp, Employee) else emp(db, number_or_emp)
    return load_principal(db, e)


# --- visibility -----------------------------------------------------------------------------------


def test_manager_sees_only_own_subtree(loaded):
    db = loaded
    daphne = P(db, "900004")
    vis = visible_employee_ids(db, daphne, dt.date(2026, 3, 20))
    numbers = {db.get(Employee, i).employee_number for i in vis}
    assert numbers == {"900004", "900008", "900009", "900010", "900011", "900012"}
    assert not can_view(db, daphne, emp(db, "900013").id, dt.date(2026, 3, 20))  # her own manager


def test_individual_contributor_sees_only_self(loaded):
    db = loaded
    hazel = P(db, "900008")
    assert visible_employee_ids(db, hazel, dt.date(2026, 3, 20)) == {hazel.id}


def test_people_partner_scope(loaded):
    db = loaded
    pp = P(db, db.scalar(select(Employee).where(Employee.email == "pp@example.com")))
    vis = visible_employee_ids(db, pp, dt.date(2026, 3, 20))
    assert len([i for i in vis if db.get(Employee, i).in_scope]) == 13


# --- hierarchy admin ------------------------------------------------------------------------------


def test_reassignment_is_effective_dated_and_rejects_cycles(loaded):
    db = loaded
    hazel, marco, daphne = emp(db, "900008"), emp(db, "900013"), emp(db, "900004")
    change_assignment(db, None, hazel.id, dt.date(2026, 3, 16), manager_id=marco.id)
    assert hazel.id in visible_employee_ids(db, P(db, marco), dt.date(2026, 3, 20))
    # history preserved: in January Hazel still reported to Daphne
    assert can_view(db, P(db, daphne), hazel.id, dt.date(2026, 1, 20))
    assert not can_view(db, P(db, daphne), hazel.id, dt.date(2026, 3, 20))
    with pytest.raises(ServiceError, match="cycle"):
        change_assignment(db, None, marco.id, dt.date(2026, 3, 16), manager_id=daphne.id)


# --- thresholds -----------------------------------------------------------------------------------


def test_department_threshold_overrides_company_default(loaded):
    db = loaded
    dept = db.scalar(select(OrgUnit).where(OrgUnit.name == "Collections", OrgUnit.kind == "DEPARTMENT"))
    set_policy(db, None, None, dt.date(2026, 1, 1), {"monthly_pass_pct": 75})
    set_policy(db, None, dept.id, dt.date(2026, 3, 1), {"monthly_pass_pct": 100})
    assert resolve_policy(db, dept.id, dt.date(2026, 2, 15)).monthly_pass_pct == 75
    pol = resolve_policy(db, dept.id, dt.date(2026, 3, 15))
    assert pol.monthly_pass_pct == 100 and pol.origin["monthly_pass_pct"] == "Collections"
    assert pol.annual_pass_pct == 75  # inherited
    recompute_month(db, MAR)
    kevin = db.get(MonthlyResult, (emp(db, "900011").id, MAR))  # 3 of 4 weeks met in March
    assert kevin.adherence == 0.75 and kevin.passed is False and kevin.threshold_pct == 100


# --- workflow -------------------------------------------------------------------------------------


def test_exemption_flow_manager_then_people_partner_updates_results(loaded):
    db = loaded
    hazel, daphne = emp(db, "900008"), emp(db, "900004")
    pp = P(db, db.scalar(select(Employee).where(Employee.email == "pp@example.com")))
    before = db.get(MonthlyResult, (hazel.id, JAN))
    assert before.met_weeks == 0
    req = create_request(db, P(db, hazel), hazel.id, RequestType.EXEMPTION, dt.date(2026, 1, 7),
                         dt.date(2026, 1, 25), "Medical", "", days_per_week=1)
    assert (req.start_date, req.end_date) == (dt.date(2026, 1, 5), dt.date(2026, 1, 25))  # snapped to weeks
    assert [s.approver_id for s in req.steps] == [daphne.id, None]
    assert req in inbox(db, P(db, daphne)) and req not in inbox(db, pp)
    with pytest.raises(PermissionDenied):
        decide(db, pp, req.id, True)
    decide(db, P(db, daphne), req.id, True, "ok")
    assert req.status == RequestStatus.PENDING and req in inbox(db, pp)
    decide(db, pp, req.id, True)
    assert req.status == RequestStatus.APPROVED
    after = db.get(MonthlyResult, (hazel.id, JAN))
    # Jan weeks: 0,1,1,1 valid days. Exemption (1 day) covers weeks 1-3 -> weeks 2 and 3 met;
    # week 4 keeps the baseline commitment of 2. Exemptions count in the initial figure.
    assert (after.met_weeks, after.adj_met_weeks) == (2, 2)


def test_manager_submission_goes_to_next_level(loaded):
    db = loaded
    hazel, daphne, marco = emp(db, "900008"), emp(db, "900004"), emp(db, "900013")
    req = create_request(db, P(db, daphne), hazel.id, RequestType.EXEMPTION, dt.date(2026, 3, 2), None,
                         "Family / personal", "", days_per_week=1)
    assert req.steps[0].approver_id == marco.id


def test_dispute_credits_day_only_in_after_dispute_figures(loaded):
    db = loaded
    ivan, daphne = emp(db, "900009"), emp(db, "900004")
    pp = P(db, db.scalar(select(Employee).where(Employee.email == "pp@example.com")))
    # Ivan, March: weeks 1,0,0,1 vs commitment 2
    req = create_request(db, P(db, ivan), ivan.id, RequestType.DISPUTE, dt.date(2026, 3, 3), None,
                         "Door access / system issue", "turnstile down",
                         credited_dates=[dt.date(2026, 3, 3)])
    decide(db, P(db, daphne), req.id, True)
    decide(db, pp, req.id, True)
    week = db.get(WeeklyResult, (ivan.id, dt.date(2026, 3, 2)))
    assert (week.valid_days, week.met, week.adj_valid_days, week.adj_met) == (1, False, 2, True)
    m = db.get(MonthlyResult, (ivan.id, MAR))
    assert (m.met_weeks, m.adj_met_weeks) == (0, 1)


def test_reject_requires_comment_and_withdraw(loaded):
    db = loaded
    hazel, daphne = emp(db, "900008"), emp(db, "900004")
    req = create_request(db, P(db, hazel), hazel.id, RequestType.COMMITMENT_CHANGE, dt.date(2026, 3, 9), None,
                         "Business requirement", "", days_per_week=3)
    with pytest.raises(ServiceError):
        decide(db, P(db, daphne), req.id, False, "")
    withdraw(db, P(db, hazel), req.id)
    assert req.status == RequestStatus.WITHDRAWN
    assert all(s.decision in (StepDecision.SKIPPED,) for s in req.steps)


def test_cannot_file_for_unrelated_employee(loaded):
    db = loaded
    with pytest.raises(PermissionDenied):
        create_request(db, P(db, "900008"), emp(db, "900009").id, RequestType.EXEMPTION, dt.date(2026, 3, 2),
                       None, "Other", "", days_per_week=1)


def test_finalized_month_is_locked(loaded):
    db = loaded
    hr = Employee(employee_number="HR1", email="hr@example.com", in_scope=False)
    db.add(hr)
    db.flush()
    finalize(db, hr, MAR)
    hazel = emp(db, "900008")
    with pytest.raises(ServiceError, match="finalized"):
        create_request(db, P(db, hazel), hazel.id, RequestType.EXEMPTION, dt.date(2026, 3, 2), None,
                       "Other", "", days_per_week=1)
    with pytest.raises(ServiceError):
        recompute_month(db, MAR)


# --- daily pipeline -------------------------------------------------------------------------------

MASTER = b"""employee_number,email,first_name,last_name,department_group,department,manager_email,location,committed_days,effective_from
100,boss@example.com,Bo,Ss,Ops,Ops Core,,Spark,3,2026-01-05
101,ann@example.com,Ann,Lee,Ops,Ops Core,boss@example.com,Spark,3,2026-01-05
"""

PRESENCE = b"""employee_number,date,time,source,direction,site
101,2026-03-02,08:30,DOOR,IN,Spark
101,2026-03-02,17:30,DOOR,OUT,Spark
101,2026-03-03,08:30,DOOR,IN,Spark
101,2026-03-04,,WIFI,PRESENT,Spark
101,2026-03-05,09:00,TIMEKEEPING,IN,Spark
101,2026-03-05,18:00,TIMEKEEPING,OUT,Spark
"""

ABSENCES = b"""employee_number,date,kind,subtype,status,portion
101,2026-03-06,LEAVE,Wellness,APPROVED,0.5
101,2026-03-09,OB,Client,APPROVED,1
101,2026-03-10,LEAVE,Wellness,PENDING,1
"""


def test_daily_pipeline_tags_and_computes(db):
    import_masterlist(db, MASTER, "m.csv")
    import_daily(db, "absences", ABSENCES, "a.csv")
    import_daily(db, "presence", PRESENCE, "p.csv")
    ann = emp(db, "101")
    days = {d.date: d for d in db.scalars(select(DayRecord).where(DayRecord.employee_id == ann.id))}
    assert days[dt.date(2026, 3, 2)].status == DayStatus.ONSITE
    assert "INCOMPLETE_TAP" in days[dt.date(2026, 3, 3)].flags
    assert days[dt.date(2026, 3, 4)].valid_value == 0 and "WIFI_ONLY" in days[dt.date(2026, 3, 4)].flags
    assert "NO_DOOR_TAP" in days[dt.date(2026, 3, 5)].flags
    assert days[dt.date(2026, 3, 6)].valid_value == 0.5
    assert days[dt.date(2026, 3, 10)].status == DayStatus.NOT_ONSITE  # pending leave does not count
    w1 = db.get(WeeklyResult, (ann.id, dt.date(2026, 3, 2)))
    assert (w1.valid_days, w1.commitment, w1.met) == (3.5, 3, True)
    w2 = db.get(WeeklyResult, (ann.id, dt.date(2026, 3, 9)))
    assert (w2.valid_days, w2.met) == (1, False)


def test_daily_import_rejects_bad_rows_atomically(db):
    import_masterlist(db, MASTER, "m.csv")
    bad = PRESENCE + b"999,2026-03-02,08:00,DOOR,IN,Spark\n"
    with pytest.raises(ServiceError, match="unknown employee_number"):
        import_daily(db, "presence", bad, "p.csv")


# --- org chart ------------------------------------------------------------------------------------


def _flatten(nodes):
    for n in nodes:
        yield n
        yield from _flatten(n.children)


def test_org_tree_scopes_and_counts(loaded):
    from rto.services.reports import org_tree

    db = loaded
    # HR-like global view (People Partner is scoped to Collections group -> whole sample)
    pp = P(db, db.scalar(select(Employee).where(Employee.email == "pp@example.com")))
    roots = org_tree(db, pp, MAR)
    assert [r.employee.email for r in roots] == ["marco.valdez@example.com"]  # Nina is outside the PP scope
    marco = roots[0]
    assert marco.size == 12
    assert {n.employee.employee_number for n in _flatten(roots)} == {f"9000{i:02d}" for i in range(1, 14)}

    # a team lead sees only her own subtree, rooted at herself
    daphne_roots = org_tree(db, P(db, "900004"), MAR)
    assert [r.employee.employee_number for r in daphne_roots] == ["900004"]
    assert daphne_roots[0].size == 5 and daphne_roots[0].subtree.evaluated == 5

    # an individual contributor sees just herself
    assert [n.employee.employee_number for n in _flatten(org_tree(db, P(db, "900008"), MAR))] == ["900008"]

    # the CEO-2 leader (not tracked) is the root for the global view
    nina = db.scalar(select(Employee).where(Employee.email == "nina.cabrera@example.com"))
    db.add(RoleAssignment(employee_id=nina.id, role=Role.HR_ADMIN))
    db.flush()
    top = org_tree(db, P(db, nina), MAR)
    assert top[0].employee.id == nina.id and top[0].size == 13 and top[0].result is None
