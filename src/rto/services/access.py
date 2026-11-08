"""Authorization: who may see whose data and who may administer what.

Visibility replaces Power BI row-level security:

* every employee sees their own data;
* managers see their direct and indirect reports (reporting lines as of the period viewed);
* People Partners and Executives see the org units they are assigned to (including sub-units);
* HR Admin, HR Analytics and Auditors see everything.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from rto.models import Employee, Role, RoleAssignment
from rto.services import PermissionDenied
from rto.services.org import assignments_at, subordinate_ids, unit_descendants

GLOBAL_VIEW_ROLES = {Role.HR_ADMIN, Role.HR_ANALYTICS, Role.AUDITOR}
SCOPED_VIEW_ROLES = {Role.PEOPLE_PARTNER, Role.EXECUTIVE}


@dataclass
class Principal:
    employee: Employee
    # role -> org unit ids (None inside the set = company-wide)
    roles: dict[Role, set[int | None]] = field(default_factory=dict)
    # roles granted by the identity provider (Entra ID app roles); company-wide
    idp_roles: set[Role] = field(default_factory=set)

    @property
    def id(self) -> int:
        return self.employee.id

    def has(self, *roles: Role) -> bool:
        return any(r in self.roles or r in self.idp_roles for r in roles)

    def require(self, *roles: Role) -> None:
        if not self.has(*roles):
            raise PermissionDenied("You do not have permission for this action")

    @property
    def is_hr_admin(self) -> bool:
        return self.has(Role.HR_ADMIN)

    @property
    def can_manage_data(self) -> bool:
        return self.has(Role.HR_ADMIN, Role.HR_ANALYTICS)

    @property
    def sees_everything(self) -> bool:
        return any(self.has(r) for r in GLOBAL_VIEW_ROLES)

    def scoped_units(self, *roles: Role) -> set[int | None]:
        out: set[int | None] = set()
        for r in roles:
            out |= self.roles.get(r, set())
            if r in self.idp_roles:
                out.add(None)
        return out


def load_principal(db: Session, employee: Employee, idp_roles: set[Role] | None = None) -> Principal:
    roles: dict[Role, set[int | None]] = {}
    for ra in db.scalars(select(RoleAssignment).where(RoleAssignment.employee_id == employee.id)):
        roles.setdefault(ra.role, set()).add(ra.org_unit_id)
    return Principal(employee=employee, roles=roles, idp_roles=idp_roles or set())


def visible_employee_ids(db: Session, p: Principal, as_of: dt.date) -> set[int] | None:
    """``None`` means unrestricted."""
    if p.sees_everything:
        return None
    units = p.scoped_units(*SCOPED_VIEW_ROLES)
    if None in units:
        return None
    ids: set[int] = {p.id}
    ids |= subordinate_ids(db, p.id, as_of)
    if units:
        all_units = unit_descendants(db, [u for u in units if u is not None])
        ids |= {eid for eid, a in assignments_at(db, as_of).items() if a.org_unit_id in all_units}
    return ids


def can_view(db: Session, p: Principal, employee_id: int, as_of: dt.date) -> bool:
    if employee_id == p.id:
        return True
    vis = visible_employee_ids(db, p, as_of)
    return vis is None or employee_id in vis


def require_view(db: Session, p: Principal, employee_id: int, as_of: dt.date) -> None:
    if not can_view(db, p, employee_id, as_of):
        raise PermissionDenied("You are not allowed to view this employee")


def is_people_partner_for(db: Session, p: Principal, org_unit_id: int | None) -> bool:
    units = p.scoped_units(Role.PEOPLE_PARTNER)
    if not units:
        return False
    if None in units:
        return True
    return org_unit_id is not None and org_unit_id in unit_descendants(db, [u for u in units if u is not None])
