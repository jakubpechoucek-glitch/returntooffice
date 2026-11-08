"""Organisation structure and reporting lines (effective-dated)."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, joinedload

from rto.models import Employee, EmployeeAssignment, Location, OrgUnit, OrgUnitKind
from rto.services import NotFound, ServiceError
from rto.services.audit import audit

COMPANY_CODE = "COMPANY"


def valid_at(model, as_of: dt.date):
    return and_(model.valid_from <= as_of, or_(model.valid_to.is_(None), model.valid_to > as_of))


# --- org units ------------------------------------------------------------------------------------


def company(db: Session) -> OrgUnit:
    unit = db.scalar(select(OrgUnit).where(OrgUnit.code == COMPANY_CODE))
    if unit is None:
        unit = OrgUnit(code=COMPANY_CODE, name="Company", kind=OrgUnitKind.COMPANY)
        db.add(unit)
        db.flush()
    return unit


def get_or_create_unit(db: Session, kind: OrgUnitKind, name: str, parent: OrgUnit) -> OrgUnit:
    code = f"{kind.value}:{name.strip().upper()}"
    if kind == OrgUnitKind.DEPARTMENT:
        code = f"{parent.code}/{name.strip().upper()}"
    unit = db.scalar(select(OrgUnit).where(OrgUnit.code == code))
    if unit is None:
        unit = OrgUnit(code=code, name=name.strip(), kind=kind, parent_id=parent.id)
        db.add(unit)
        db.flush()
    return unit


def get_or_create_location(db: Session, name: str | None) -> Location | None:
    if not name:
        return None
    loc = db.scalar(select(Location).where(Location.name == name.strip()))
    if loc is None:
        loc = Location(name=name.strip(), region="NCR")
        db.add(loc)
        db.flush()
    return loc


def all_units(db: Session) -> list[OrgUnit]:
    return list(db.scalars(select(OrgUnit).order_by(OrgUnit.kind, OrgUnit.name)))


def unit_ancestors(units_by_id: dict[int, OrgUnit], unit_id: int | None) -> list[int]:
    """Unit itself first, then parents up to the root."""
    chain = []
    seen = set()
    while unit_id is not None and unit_id not in seen:
        seen.add(unit_id)
        chain.append(unit_id)
        unit_id = units_by_id[unit_id].parent_id if unit_id in units_by_id else None
    return chain


def unit_descendants(db: Session, unit_ids: Iterable[int]) -> set[int]:
    units = all_units(db)
    children: dict[int | None, list[int]] = {}
    for u in units:
        children.setdefault(u.parent_id, []).append(u.id)
    out: set[int] = set()
    stack = list(unit_ids)
    while stack:
        uid = stack.pop()
        if uid in out:
            continue
        out.add(uid)
        stack.extend(children.get(uid, []))
    return out


# --- assignments ----------------------------------------------------------------------------------


def assignment_at(db: Session, employee_id: int, as_of: dt.date) -> EmployeeAssignment | None:
    return db.scalar(
        select(EmployeeAssignment)
        .options(joinedload(EmployeeAssignment.manager), joinedload(EmployeeAssignment.org_unit))
        .where(EmployeeAssignment.employee_id == employee_id, valid_at(EmployeeAssignment, as_of))
        .order_by(EmployeeAssignment.valid_from.desc())
        .limit(1)
    )


def assignments_at(db: Session, as_of: dt.date) -> dict[int, EmployeeAssignment]:
    rows = db.scalars(select(EmployeeAssignment).where(valid_at(EmployeeAssignment, as_of)))
    return {a.employee_id: a for a in rows}


def subordinate_ids(
    db: Session, manager_id: int, as_of: dt.date, direct_only: bool = False
) -> set[int]:
    """All employees reporting (directly or indirectly) to ``manager_id`` on ``as_of``.

    Implemented as a recursive CTE so the database does the walk (works on PostgreSQL and SQLite).
    """
    ea = EmployeeAssignment
    base = select(ea.employee_id.label("eid")).where(ea.manager_id == manager_id, valid_at(ea, as_of))
    if direct_only:
        return set(db.scalars(base))
    tree = base.cte("tree", recursive=True)
    ea2 = EmployeeAssignment.__table__.alias("ea2")
    tree = tree.union(
        select(ea2.c.employee_id).where(
            ea2.c.manager_id == tree.c.eid,
            ea2.c.valid_from <= as_of,
            or_(ea2.c.valid_to.is_(None), ea2.c.valid_to > as_of),
        )
    )
    ids = set(db.scalars(select(tree.c.eid)))
    ids.discard(manager_id)
    return ids


def manager_chain(db: Session, employee_id: int, as_of: dt.date, max_depth: int = 20) -> list[Employee]:
    chain: list[Employee] = []
    current = employee_id
    seen = {employee_id}
    for _ in range(max_depth):
        a = assignment_at(db, current, as_of)
        if a is None or a.manager_id is None or a.manager_id in seen:
            break
        chain.append(a.manager)
        seen.add(a.manager_id)
        current = a.manager_id
    return chain


def is_manager(db: Session, employee_id: int, as_of: dt.date) -> bool:
    return bool(subordinate_ids(db, employee_id, as_of, direct_only=True))


_UNSET = object()


def change_assignment(
    db: Session,
    actor: Employee | None,
    employee_id: int,
    effective_from: dt.date,
    manager_id: int | None | object = _UNSET,
    org_unit_id: int | None | object = _UNSET,
    location_id: int | None | object = _UNSET,
    source: str = "ADMIN",
) -> EmployeeAssignment:
    """Effective-dated change of manager / department / location.

    The current row is closed the day the change takes effect and a new row is opened, so history
    (and therefore historical report visibility) is preserved.
    """
    emp = db.get(Employee, employee_id)
    if emp is None:
        raise NotFound("Employee not found")

    if manager_id is not _UNSET and manager_id is not None:
        if manager_id == employee_id:
            raise ServiceError("An employee cannot be their own manager")
        if db.get(Employee, manager_id) is None:
            raise NotFound("Manager not found")
        if manager_id in subordinate_ids(db, employee_id, effective_from):
            raise ServiceError("This change would create a reporting cycle")

    current = assignment_at(db, employee_id, effective_from)
    later = db.scalars(
        select(EmployeeAssignment).where(
            EmployeeAssignment.employee_id == employee_id, EmployeeAssignment.valid_from > effective_from
        )
    ).first()
    if later is not None:
        raise ServiceError(
            f"A later change already exists (from {later.valid_from}); changes must be entered in order"
        )

    new_values = {
        "manager_id": current.manager_id if current else None,
        "org_unit_id": current.org_unit_id if current else None,
        "location_id": current.location_id if current else None,
    }
    if manager_id is not _UNSET:
        new_values["manager_id"] = manager_id
    if org_unit_id is not _UNSET:
        new_values["org_unit_id"] = org_unit_id
    if location_id is not _UNSET:
        new_values["location_id"] = location_id

    before = None
    if current is not None:
        before = {k: getattr(current, k) for k in new_values}
        if before == new_values:
            return current
        if current.valid_from == effective_from:
            for k, v in new_values.items():
                setattr(current, k, v)
            current.source = source
            row = current
        else:
            current.valid_to = effective_from
            row = EmployeeAssignment(employee_id=employee_id, valid_from=effective_from, source=source, **new_values)
            db.add(row)
    else:
        row = EmployeeAssignment(employee_id=employee_id, valid_from=effective_from, source=source, **new_values)
        db.add(row)
    db.flush()
    if source == "ADMIN":
        audit(
            db,
            actor,
            "assignment.change",
            "employee",
            employee_id,
            {"effective_from": effective_from, "before": before, "after": new_values},
        )
    return row
