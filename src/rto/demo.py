"""Synthetic pilot data and demo seeding.

``generate_synthetic`` writes three CSV files in the canonical import layouts for a fictional,
structurally deep department ("Risk Management": 4 management levels, ~30 people). They exercise the
daily pipeline (door taps, timekeeping, Wi-Fi, leave, OB, half days, incomplete taps) end to end.
All names are fictional; e-mails use the reserved ``example.com`` domain.

``seed_demo`` loads the anonymized legacy report, the synthetic department, holidays, thresholds,
roles and a few requests in different workflow states, so every screen has something to show.
"""

from __future__ import annotations

import csv
import datetime as dt
import pathlib
import random

from sqlalchemy import select
from sqlalchemy.orm import Session

from rto.domain.calendar import iter_days
from rto.importers.daily import import_daily
from rto.importers.masterlist import import_masterlist
from rto.importers.onsite_report import import_onsite_report
from rto.models import Employee, Holiday, OrgUnit, OrgUnitKind, RequestType, Role, RoleAssignment
from rto.services.access import load_principal
from rto.services.org import company
from rto.services.policy import set_policy
from rto.services.requests import create_request, decide

REPO = pathlib.Path(__file__).resolve().parents[2]
SAMPLE_DIR = REPO / "sample_data"

# Philippine holidays used by the demo. Verify against the official proclamation before production use;
# HR maintains the real calendar in Admin -> Calendar.
HOLIDAYS_2026 = [
    ("2026-01-01", "New Year's Day", "LEGAL"),
    ("2026-02-17", "Chinese New Year", "SPECIAL"),
    ("2026-04-02", "Maundy Thursday", "LEGAL"),
    ("2026-04-03", "Good Friday", "LEGAL"),
    ("2026-04-04", "Black Saturday", "SPECIAL"),
    ("2026-04-09", "Araw ng Kagitingan", "LEGAL"),
    ("2026-05-01", "Labor Day", "LEGAL"),
    ("2026-06-12", "Independence Day", "LEGAL"),
    ("2026-08-21", "Ninoy Aquino Day", "SPECIAL"),
    ("2026-08-31", "National Heroes Day", "LEGAL"),
    ("2026-11-01", "All Saints' Day", "SPECIAL"),
    ("2026-11-30", "Bonifacio Day", "LEGAL"),
    ("2026-12-08", "Feast of the Immaculate Conception", "SPECIAL"),
    ("2026-12-24", "Christmas Eve", "SPECIAL"),
    ("2026-12-25", "Christmas Day", "LEGAL"),
    ("2026-12-30", "Rizal Day", "LEGAL"),
    ("2026-12-31", "Last Day of the Year", "SPECIAL"),
]

FIRST = [
    "Andrea", "Benjo", "Camille", "Dennis", "Elaine", "Francis", "Grace", "Hector", "Isabel", "Jerome",
    "Katrina", "Leo", "Mika", "Noel", "Olivia", "Paulo", "Queenie", "Rafael", "Sofia", "Tristan",
    "Ursula", "Vince", "Wendy", "Xavier", "Yvonne", "Zoren", "Alma", "Bryan", "Cora", "Dino",
]
LAST = [
    "Agustin", "Bernal", "Cruz", "Dizon", "Estrada", "Flores", "Garcia", "Herrera", "Ignacio", "Jimenez",
    "Lopez", "Morales", "Navarro", "Ocampo", "Pascual", "Quiambao", "Reyes", "Salazar", "Tan", "Uy",
    "Velasco", "Yap", "Zamora", "Aquino", "Bautista", "Castro", "Domingo", "Enriquez", "Fajardo", "Gomez",
]

PERIOD = (dt.date(2026, 1, 5), dt.date(2026, 3, 29))


def _people():
    """(number, first, last, position, people_level, ceo_level, department, manager_idx, location, commit)."""
    out = []
    n = 0

    def add(position, level, ceo, dept, mgr, loc, commit):
        nonlocal n
        out.append((910001 + n, FIRST[n % 30], LAST[(n * 7) % 30], position, level, ceo, dept, mgr, loc, commit))
        n += 1
        return n - 1

    head = add("Chief Risk Officer", "People Manager", 2, "Risk Office", None, "Three NEO", 4)
    for dept, loc in (("Credit Risk", "Three NEO"), ("Fraud Risk", "Ore Central")):
        mgr = add(f"{dept} Head", "People Manager", 3, dept, head, loc, 3)
        for t in range(2):
            lead = add(f"{dept} Team Lead {t + 1}", "People Manager", 4, dept, mgr, loc, 3)
            for i in range(5):
                commit = 2 if (t == 1 and i == 4) else 3
                add(f"{dept} Analyst", "Individual Contributor", 5, dept, lead, loc if i % 3 else "Spark", commit)
    return out


def generate_synthetic(out_dir: pathlib.Path, seed: int = 42) -> list[pathlib.Path]:
    rng = random.Random(seed)  # noqa: S311 - synthetic data, not security relevant
    out_dir.mkdir(parents=True, exist_ok=True)
    people = _people()
    hol = {dt.date.fromisoformat(d) for d, _, _ in HOLIDAYS_2026}

    master = out_dir / "masterlist_risk.csv"
    with master.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            ["employee_number", "email", "first_name", "last_name", "position", "people_level", "ceo_level",
             "department_group", "department", "manager_email", "location", "hired_date", "termination_date",
             "status", "committed_days", "effective_from"]
        )
        for num, fn, ln, pos, lvl, ceo, dept, mgr, loc, commit in people:
            mgr_email = "paolo.reyes@example.com" if mgr is None else _email(people[mgr])
            hired = "2026-02-09" if num == 910027 else f"20{10 + num % 14}-0{1 + num % 9}-15"
            w.writerow(
                [num, _email((num, fn, ln)), fn, ln, pos, lvl, f"CEO-{ceo}", "Risk Management", dept, mgr_email,
                 loc, hired, "", "Active", commit, "2026-01-05"]
            )
        # ExCo member the department head reports to (not tracked: no commitment, placed at company level)
        w.writerow(
            ["800001", "paolo.reyes@example.com", "Paolo", "Reyes", "Chief Operating Officer", "People Manager",
             "CEO-1", "Risk Management", "Risk Office", "", "Three NEO", "2012-05-01", "", "Active", 5, "2026-01-05"]
        )

    presence_path = out_dir / "presence_risk_2026q1.csv"
    absence_path = out_dir / "absences_risk_2026q1.csv"
    with presence_path.open("w", newline="") as pf, absence_path.open("w", newline="") as af:
        pw, aw = csv.writer(pf), csv.writer(af)
        pw.writerow(["employee_number", "date", "time", "source", "direction", "site"])
        aw.writerow(["employee_number", "date", "kind", "subtype", "status", "portion"])
        for idx, (num, *_rest) in enumerate(people):
            loc = people[idx][8]
            ceo = people[idx][5]
            # propensity profiles: most comply, some borderline, a few chronic
            propensity = [0.85, 0.85, 0.7, 0.55, 0.3][idx % 5] if ceo >= 4 else 0.9
            for d in iter_days(*PERIOD):
                if d.weekday() >= 5 or d in hol:
                    continue
                if num == 910027 and d < dt.date(2026, 2, 9):
                    continue  # new hire
                roll = rng.random()
                if roll < 0.04:
                    portion = 0.5 if rng.random() < 0.25 else 1
                    aw.writerow([num, d, "LEAVE", rng.choice(["Wellness", "Birthday", "Wellness"]), "APPROVED", portion])
                    if portion == 1:
                        continue
                elif roll < 0.06:
                    aw.writerow([num, d, "OB", "Client visit", "APPROVED", 1])
                    continue
                elif roll < 0.065:
                    aw.writerow([num, d, "LEAVE", "Wellness", "PENDING", 1])
                if rng.random() < propensity * 0.75:
                    t_in = dt.time(8 + rng.randint(0, 1), rng.randint(0, 59))
                    t_out = dt.time(17 + rng.randint(0, 2), rng.randint(0, 59))
                    q = rng.random()
                    if q < 0.03:
                        pw.writerow([num, d, t_in, "DOOR", "IN", loc])  # forgot to tap out
                    elif q < 0.05:
                        pw.writerow([num, d, t_in, "TIMEKEEPING", "IN", loc])
                        pw.writerow([num, d, t_out, "TIMEKEEPING", "OUT", loc])
                    else:
                        pw.writerow([num, d, t_in, "DOOR", "IN", loc])
                        pw.writerow([num, d, t_out, "DOOR", "OUT", loc])
                    pw.writerow([num, d, "", "WIFI", "PRESENT", loc])
                elif rng.random() < 0.03:
                    pw.writerow([num, d, "", "WIFI", "PRESENT", loc])  # Wi-Fi only -> flagged
    return [master, presence_path, absence_path]


def _email(p) -> str:
    return f"{p[1].lower()}.{p[2].lower()}@example.com"


def seed_holidays(db: Session) -> None:
    for d, name, kind in HOLIDAYS_2026:
        day = dt.date.fromisoformat(d)
        if db.scalar(select(Holiday).where(Holiday.date == day, Holiday.location_id.is_(None))) is None:
            db.add(Holiday(date=day, name=name, kind=kind))
    db.flush()


def _grant(db: Session, emp: Employee, role: Role, unit: OrgUnit | None = None) -> None:
    exists = db.scalar(
        select(RoleAssignment).where(
            RoleAssignment.employee_id == emp.id,
            RoleAssignment.role == role,
            RoleAssignment.org_unit_id.is_(None) if unit is None else RoleAssignment.org_unit_id == unit.id,
        )
    )
    if exists is None:
        db.add(RoleAssignment(employee_id=emp.id, role=role, org_unit_id=unit.id if unit else None))
        db.flush()


def _staff(db: Session, number: str, email: str, first: str, last: str, position: str) -> Employee:
    emp = db.scalar(select(Employee).where(Employee.email == email))
    if emp is None:
        emp = Employee(employee_number=number, email=email, first_name=first, last_name=last,
                       position=position, in_scope=False)
        db.add(emp)
        db.flush()
    return emp


def seed_demo(db: Session, synthetic_dir: pathlib.Path | None = None) -> dict:
    synthetic_dir = synthetic_dir or SAMPLE_DIR / "synthetic"
    if not (synthetic_dir / "masterlist_risk.csv").exists():
        generate_synthetic(synthetic_dir)
    company(db)
    seed_holidays(db)
    set_policy(db, None, None, dt.date(2026, 1, 1),
               {"monthly_pass_pct": 75, "annual_pass_pct": 75, "min_working_days": 3, "default_commitment_days": 3},
               note="Company default (Working Arrangements Manual v1)")

    report = import_onsite_report(
        db, (SAMPLE_DIR / "onsite_report_2026_jan_mar_anonymized.xlsx").read_bytes(), "onsite_report_2026_jan_mar_anonymized.xlsx"
    )
    import_masterlist(db, (synthetic_dir / "masterlist_risk.csv").read_bytes(), "masterlist_risk.csv")
    import_daily(db, "absences", (synthetic_dir / "absences_risk_2026q1.csv").read_bytes(), "absences_risk_2026q1.csv")
    import_daily(db, "presence", (synthetic_dir / "presence_risk_2026q1.csv").read_bytes(), "presence_risk_2026q1.csv")

    # Per-department threshold example: Fraud Risk requires 100 % monthly adherence.
    fraud = db.scalar(select(OrgUnit).where(OrgUnit.name == "Fraud Risk"))
    if fraud is not None:
        set_policy(db, None, fraud.id, dt.date(2026, 1, 1), {"monthly_pass_pct": 100},
                   note="Demo: stricter monthly threshold for Fraud Risk")
        from rto.services.compute import recompute_range

        recompute_range(db, PERIOD[0], PERIOD[1])

    hr_admin = _staff(db, "HR-001", "hr.admin@example.com", "Helena", "Ramirez", "HR Business Systems Lead")
    analyst = _staff(db, "HR-002", "hr.analytics@example.com", "Rina", "Evangelista", "HR Analytics Specialist")
    pp_coll = _staff(db, "HR-003", "pp.collections@example.com", "Karla", "Fuentes", "People Partner")
    pp_risk = _staff(db, "HR-004", "pp.risk@example.com", "Mateo", "Santiago", "People Partner")
    auditor = _staff(db, "HR-005", "auditor@example.com", "Alex", "Uy", "Internal Audit")
    _grant(db, hr_admin, Role.HR_ADMIN)
    _grant(db, analyst, Role.HR_ANALYTICS)
    _grant(db, auditor, Role.AUDITOR)
    coll = db.scalar(select(OrgUnit).where(OrgUnit.name == "Collections", OrgUnit.kind == OrgUnitKind.DEPARTMENT_GROUP))
    risk = db.scalar(select(OrgUnit).where(OrgUnit.name == "Risk Management"))
    if coll:
        _grant(db, pp_coll, Role.PEOPLE_PARTNER, coll)
    if risk:
        _grant(db, pp_risk, Role.PEOPLE_PARTNER, risk)
        paolo = db.scalar(select(Employee).where(Employee.email == "paolo.reyes@example.com"))
        if paolo:
            paolo.in_scope = False  # ExCo member: shown in hierarchy, not tracked in this demo
            _grant(db, paolo, Role.EXECUTIVE, risk)

    # Workflow examples
    def emp(number: str) -> Employee:
        return db.scalar(select(Employee).where(Employee.employee_number == number))

    analyst_p = load_principal(db, emp("910008"))
    r1 = create_request(db, analyst_p, emp("910008").id, RequestType.EXEMPTION, dt.date(2026, 3, 16),
                        dt.date(2026, 3, 22), "Medical", "Recovering at home after a minor procedure.", days_per_week=1)
    lead_p = load_principal(db, emp("910003"))
    decide(db, lead_p, r1.id, True, "OK, take care.")
    create_request(db, load_principal(db, emp("910010")), emp("910010").id, RequestType.DISPUTE,
                   dt.date(2026, 3, 10), None, "Door access / system issue",
                   "I was in the office but the turnstile at Three NEO was down.",
                   credited_dates=[dt.date(2026, 3, 10)])
    create_request(db, load_principal(db, emp("900008")), emp("900008").id, RequestType.EXEMPTION,
                   dt.date(2026, 1, 5), dt.date(2026, 1, 18), "Family / personal",
                   "Caring for a family member (details shared with my manager).", days_per_week=0)
    db.flush()
    return {"report": report.as_dict(), "employees": len(list(db.scalars(select(Employee.id))))}
