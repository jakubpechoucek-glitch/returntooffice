"""Masterlist / hierarchy CSV importer (canonical layout, see ``docs/data-import.md``).

Columns::

    employee_number,email,first_name,last_name,position,people_level,ceo_level,
    department_group,department,manager_email,location,hired_date,termination_date,
    status,committed_days,effective_from

* ``effective_from`` (optional, default today) is used for reporting-line and commitment changes.
* Managers may appear anywhere in the file; rows are loaded in two passes.
* Employees missing from the file are **not** deleted (use ``termination_date``).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from rto.importers.daily import _reader
from rto.models import Employee, ImportBatch, OrgUnitKind
from rto.services import ServiceError
from rto.services.audit import audit
from rto.services.commitments import set_baseline
from rto.services.org import change_assignment, company, get_or_create_location, get_or_create_unit

REQUIRED = ["employee_number", "email", "department", "manager_email"]


@dataclass
class MasterlistResult:
    batch_id: int | None = None
    rows: int = 0
    created: int = 0
    updated: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def _d(v: str | None) -> dt.date | None:
    v = (v or "").strip()
    return dt.date.fromisoformat(v) if v else None


def import_masterlist(
    db: Session, content: bytes, filename: str, actor: Employee | None = None
) -> MasterlistResult:
    rows = list(_reader(content, REQUIRED))
    res = MasterlistResult(rows=len(rows))
    batch = ImportBatch(kind="MASTERLIST_CSV", filename=filename, created_by_id=actor.id if actor else None)
    db.add(batch)
    db.flush()
    res.batch_id = batch.id
    root = company(db)
    by_email = {e.email.lower(): e for e in db.scalars(select(Employee))}
    by_number = {e.employee_number: e for e in by_email.values()}

    # pass 1: people
    for line, r in enumerate(rows, start=2):
        try:
            number = r["employee_number"].strip()
            email = r["email"].strip().lower()
            if not number or "@" not in email:
                raise ValueError("employee_number and a valid email are required")
            emp = by_number.get(number) or by_email.get(email)
            if emp is None:
                emp = Employee(employee_number=number, email=email)
                db.add(emp)
                res.created += 1
            else:
                res.updated += 1
            emp.employee_number, emp.email = number, email
            emp.first_name = (r.get("first_name") or emp.first_name or "").strip()
            emp.last_name = (r.get("last_name") or emp.last_name or "").strip()
            emp.position = (r.get("position") or "").strip() or emp.position
            emp.people_level = (r.get("people_level") or "").strip() or emp.people_level
            lvl = (r.get("ceo_level") or "").strip().upper().replace("CEO-", "")
            emp.ceo_level = int(lvl) if lvl.isdigit() else emp.ceo_level
            emp.status = (r.get("status") or "").strip() or emp.status or "Active"
            emp.hired_date = _d(r.get("hired_date")) or emp.hired_date
            emp.termination_date = _d(r.get("termination_date")) or emp.termination_date
            emp.in_scope = True
            db.flush()
            by_email[email], by_number[number] = emp, emp
        except (ValueError, KeyError) as exc:
            res.errors.append(f"Line {line}: {exc}")
    if res.errors:
        raise ServiceError("Masterlist rejected:\n" + "\n".join(res.errors[:50]))

    # pass 2: placement and commitment (managers now exist)
    for line, r in enumerate(rows, start=2):
        emp = by_number[r["employee_number"].strip()]
        eff = _d(r.get("effective_from")) or dt.date.today()
        mgr_email = (r.get("manager_email") or "").strip().lower()
        mgr = by_email.get(mgr_email) if mgr_email else None
        if mgr_email and mgr is None:
            res.errors.append(f"Line {line}: manager {mgr_email} not found")
            continue
        group = (r.get("department_group") or "").strip()
        parent = get_or_create_unit(db, OrgUnitKind.DEPARTMENT_GROUP, group, root) if group else root
        dept = get_or_create_unit(db, OrgUnitKind.DEPARTMENT, r["department"].strip() or "Unassigned", parent)
        loc = get_or_create_location(db, (r.get("location") or "").strip())
        try:
            change_assignment(
                db, actor, emp.id, eff, manager_id=mgr.id if mgr else None, org_unit_id=dept.id,
                location_id=loc.id if loc else None, source="IMPORT",
            )
            days = (r.get("committed_days") or "").strip()
            if days:
                set_baseline(db, actor, emp.id, float(days), eff, source="IMPORT")
        except (ServiceError, ValueError) as exc:
            res.errors.append(f"Line {line}: {exc}")
    if res.errors:
        raise ServiceError("Masterlist rejected:\n" + "\n".join(res.errors[:50]))
    batch.row_count = res.rows
    batch.summary = res.as_dict()
    audit(db, actor, "import.masterlist", "import_batch", batch.id, {"filename": filename, "rows": res.rows})
    db.flush()
    return res
