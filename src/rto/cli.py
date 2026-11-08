"""Operations CLI (also used by Kubernetes Jobs / CronJobs).

    rto migrate                      # alembic upgrade head
    rto seed-demo                    # load anonymized sample + synthetic pilot data (non-production only)
    rto generate-sample [DIR]        # (re)generate synthetic CSVs
    rto import KIND FILE             # onsite_report | masterlist | presence | absences
    rto recompute [--from YYYY-MM] [--to YYYY-MM]
    rto grant EMAIL ROLE [UNIT_NAME] # bootstrap the first HR admin
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys

from sqlalchemy import func, select

from rto.config import get_settings, project_root
from rto.db import session_scope


def _month(s: str) -> dt.date:
    return dt.date.fromisoformat(s + "-01")


def cmd_migrate(_: argparse.Namespace) -> None:
    from alembic import command
    from alembic.config import Config

    root = project_root()
    ini = root / "alembic.ini"
    cfg = Config(str(ini)) if ini.exists() else Config()
    cfg.set_main_option("script_location", str(root / "migrations"))
    command.upgrade(cfg, "head")


def cmd_seed_demo(a: argparse.Namespace) -> None:
    if get_settings().environment == "production":
        sys.exit("seed-demo is not allowed in production")
    from rto.demo import seed_demo
    from rto.models import Employee

    with session_scope() as db:
        if a.if_empty and db.scalar(select(func.count()).select_from(Employee)):
            print("database already has data; seed skipped")
            return
        out = seed_demo(db)
    print(json.dumps({"employees": out["employees"], "reconciliation_issues": len(out["report"]["reconciliation"])}))


def cmd_generate(a: argparse.Namespace) -> None:
    from rto.demo import SAMPLE_DIR, generate_synthetic

    for p in generate_synthetic(pathlib.Path(a.dir) if a.dir else SAMPLE_DIR / "synthetic"):
        print(p)


def cmd_import(a: argparse.Namespace) -> None:
    from rto.importers.daily import import_daily
    from rto.importers.masterlist import import_masterlist
    from rto.importers.onsite_report import import_onsite_report

    path = pathlib.Path(a.file)
    content = path.read_bytes()
    with session_scope() as db:
        if a.kind == "onsite_report":
            out = import_onsite_report(db, content, path.name).as_dict()
        elif a.kind == "masterlist":
            out = import_masterlist(db, content, path.name).as_dict()
        else:
            out = import_daily(db, a.kind, content, path.name).as_dict()
    print(json.dumps(out, indent=2, default=str))


def cmd_recompute(a: argparse.Namespace) -> None:
    from rto.domain.calendar import add_months
    from rto.models import MonthlyResult
    from rto.services.compute import month_status, recompute_month

    with session_scope() as db:
        start = _month(a.from_) if a.from_ else db.scalar(select(func.min(MonthlyResult.month)))
        end = _month(a.to) if a.to else dt.date.today().replace(day=1)
        if start is None:
            print("nothing to recompute")
            return
        m = start
        while m <= end:
            if month_status(db, m).value == "FINALIZED":
                print(f"{m:%Y-%m}: finalized, skipped")
            else:
                s = recompute_month(db, m)
                print(f"{m:%Y-%m}: {s.employees} employees, {s.evaluated} evaluated, {s.passed} passed")
            m = add_months(m, 1)


def cmd_grant(a: argparse.Namespace) -> None:
    from rto.models import Employee, OrgUnit, Role, RoleAssignment
    from rto.services.audit import audit

    with session_scope() as db:
        emp = db.scalar(select(Employee).where(func.lower(Employee.email) == a.email.lower()))
        if emp is None:
            emp = Employee(employee_number=f"EXT:{a.email.lower()}", email=a.email.lower(), in_scope=False)
            db.add(emp)
            db.flush()
        unit = None
        if a.unit:
            unit = db.scalar(select(OrgUnit).where(OrgUnit.name == a.unit))
            if unit is None:
                sys.exit(f"unit {a.unit!r} not found")
        db.add(RoleAssignment(employee_id=emp.id, role=Role(a.role), org_unit_id=unit.id if unit else None))
        audit(db, None, "role.grant", "employee", emp.id, {"role": a.role, "via": "cli"}, actor_label="cli")
    print(f"granted {a.role} to {a.email}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="rto")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate").set_defaults(fn=cmd_migrate)
    sd = sub.add_parser("seed-demo")
    sd.add_argument("--if-empty", action="store_true", help="only seed an empty database")
    sd.set_defaults(fn=cmd_seed_demo)
    g = sub.add_parser("generate-sample")
    g.add_argument("dir", nargs="?")
    g.set_defaults(fn=cmd_generate)
    i = sub.add_parser("import")
    i.add_argument("kind", choices=["onsite_report", "masterlist", "presence", "absences"])
    i.add_argument("file")
    i.set_defaults(fn=cmd_import)
    r = sub.add_parser("recompute")
    r.add_argument("--from", dest="from_")
    r.add_argument("--to")
    r.set_defaults(fn=cmd_recompute)
    gr = sub.add_parser("grant")
    gr.add_argument("email")
    gr.add_argument("role", choices=["HR_ADMIN", "HR_ANALYTICS", "PEOPLE_PARTNER", "EXECUTIVE", "AUDITOR"])
    gr.add_argument("unit", nargs="?")
    gr.set_defaults(fn=cmd_grant)
    args = parser.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
