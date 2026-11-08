"""Per-department compliance thresholds with inheritance (department -> department group -> company)."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from rto.models import Employee, OrgUnit, PolicySetting
from rto.services import ServiceError
from rto.services.audit import audit
from rto.services.org import unit_ancestors

FIELDS = ("monthly_pass_pct", "annual_pass_pct", "min_working_days", "default_commitment_days")

# Built-in fallbacks when nothing is configured. Monthly 75 % reflects the legacy report
# ("Met Commitment" = Y at 75 % adherence); see docs/open-questions-hr.md Q1.
BUILTIN = {
    "monthly_pass_pct": 75.0,
    "annual_pass_pct": 75.0,
    "min_working_days": 3,
    "default_commitment_days": 3.0,
}


@dataclass(frozen=True)
class EffectivePolicy:
    monthly_pass_pct: float
    annual_pass_pct: float
    min_working_days: int
    default_commitment_days: float
    # field -> name of the org unit the value comes from ("Company", "Built-in default", ...)
    origin: dict[str, str]


class PolicyResolver:
    """Loads all settings once; cheap to query for thousands of employees during a recompute."""

    def __init__(self, db: Session):
        self.units = {u.id: u for u in db.scalars(select(OrgUnit))}
        self.settings: dict[int | None, list[PolicySetting]] = {}
        for s in db.scalars(select(PolicySetting).order_by(PolicySetting.effective_from.desc())):
            self.settings.setdefault(s.org_unit_id, []).append(s)
        self._cache: dict[tuple[int | None, dt.date], EffectivePolicy] = {}

    def _setting_at(self, unit_id: int | None, as_of: dt.date) -> PolicySetting | None:
        for s in self.settings.get(unit_id, []):
            if s.effective_from <= as_of:
                return s
        return None

    def resolve(self, org_unit_id: int | None, as_of: dt.date) -> EffectivePolicy:
        key = (org_unit_id, as_of)
        if key in self._cache:
            return self._cache[key]
        chain: list[int | None] = list(unit_ancestors(self.units, org_unit_id))
        # Company-level settings may be stored on the COMPANY unit or with org_unit_id NULL.
        chain.append(None)
        values: dict[str, object] = {}
        origin: dict[str, str] = {}
        for uid in chain:
            s = self._setting_at(uid, as_of)
            if s is None:
                continue
            for f in FIELDS:
                v = getattr(s, f)
                if f not in values and v is not None:
                    values[f] = v
                    origin[f] = self.units[uid].name if uid is not None else "Company default"
        for f in FIELDS:
            if f not in values:
                values[f] = BUILTIN[f]
                origin[f] = "Built-in default"
        pol = EffectivePolicy(
            monthly_pass_pct=float(values["monthly_pass_pct"]),
            annual_pass_pct=float(values["annual_pass_pct"]),
            min_working_days=int(values["min_working_days"]),
            default_commitment_days=float(values["default_commitment_days"]),
            origin=origin,
        )
        self._cache[key] = pol
        return pol


def resolve_policy(db: Session, org_unit_id: int | None, as_of: dt.date) -> EffectivePolicy:
    return PolicyResolver(db).resolve(org_unit_id, as_of)


def set_policy(
    db: Session,
    actor: Employee | None,
    org_unit_id: int | None,
    effective_from: dt.date,
    values: dict[str, float | int | None],
    note: str | None = None,
) -> PolicySetting:
    for f, v in values.items():
        if f not in FIELDS:
            raise ServiceError(f"Unknown setting {f}")
        if v is None:
            continue
        if f.endswith("_pct") and not (0 <= float(v) <= 100):
            raise ServiceError(f"{f} must be between 0 and 100")
        if f == "min_working_days" and not (1 <= int(v) <= 5):
            raise ServiceError("min_working_days must be between 1 and 5")
        if f == "default_commitment_days" and not (0 <= float(v) <= 5):
            raise ServiceError("default_commitment_days must be between 0 and 5")
    row = db.scalar(
        select(PolicySetting).where(
            PolicySetting.org_unit_id.is_(None) if org_unit_id is None else PolicySetting.org_unit_id == org_unit_id,
            PolicySetting.effective_from == effective_from,
        )
    )
    before = {f: getattr(row, f) for f in FIELDS} if row else None
    if row is None:
        row = PolicySetting(org_unit_id=org_unit_id, effective_from=effective_from)
        db.add(row)
    for f in FIELDS:
        if f in values:
            setattr(row, f, values[f])
    row.note = note
    db.flush()
    audit(
        db,
        actor,
        "policy.set",
        "policy_setting",
        row.id,
        {"org_unit_id": org_unit_id, "effective_from": effective_from, "before": before, "after": values},
    )
    return row
