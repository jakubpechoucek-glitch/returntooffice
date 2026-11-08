"""Relational data model.

Conventions
-----------
* Effective-dated rows use ``valid_from`` (inclusive) and ``valid_to`` (exclusive, ``NULL`` = open ended).
* Day counts are stored as floats; the only fractional values the domain produces are halves (0.5),
  which are exact in binary floating point.
* Every manual change is mirrored into :class:`AuditLog` by the service layer.
"""

from __future__ import annotations

import datetime as dt
import enum
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from rto.db import Base


def _enum(e: type[enum.Enum], name: str) -> Enum:
    return Enum(e, name=name, native_enum=False, length=32, validate_strings=True)


# ---------------------------------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------------------------------


class OrgUnitKind(str, enum.Enum):
    COMPANY = "COMPANY"
    DEPARTMENT_GROUP = "DEPARTMENT_GROUP"
    DEPARTMENT = "DEPARTMENT"


class CommitmentKind(str, enum.Enum):
    BASELINE = "BASELINE"  # the agreed weekly commitment
    EXEMPTION = "EXEMPTION"  # approved exemption for specific weeks (part of the initial report)
    DISPUTE = "DISPUTE"  # commitment corrected through an approved dispute (after-dispute figures)


class PresenceSource(str, enum.Enum):
    DOOR = "DOOR"
    TIMEKEEPING = "TIMEKEEPING"
    WIFI = "WIFI"


class PresenceDirection(str, enum.Enum):
    IN = "IN"
    OUT = "OUT"
    PRESENT = "PRESENT"


class AbsenceKind(str, enum.Enum):
    LEAVE = "LEAVE"
    OB = "OB"


class DayStatus(str, enum.Enum):
    ONSITE = "ONSITE"
    LEAVE = "LEAVE"
    OB = "OB"
    HOLIDAY = "HOLIDAY"
    EXCLUDED = "EXCLUDED"
    NOT_ONSITE = "NOT_ONSITE"
    WEEKEND = "WEEKEND"


class WeeklySource(str, enum.Enum):
    DAILY = "DAILY"  # derived from daily tagging of raw events
    REPORT_IMPORT = "REPORT_IMPORT"  # weekly counts imported from the legacy Monthly Onsite Report


class ExclusionMode(str, enum.Enum):
    WEEK = "WEEK"  # whole overlapping weeks are not considered
    DAYS = "DAYS"  # affected days are treated like holidays


class MonthStatus(str, enum.Enum):
    OPEN = "OPEN"
    PUBLISHED = "PUBLISHED"  # initial report released, review / dispute window running
    FINALIZED = "FINALIZED"  # locked; only HR overrides possible


class RequestType(str, enum.Enum):
    EXEMPTION = "EXEMPTION"
    DISPUTE = "DISPUTE"
    COMMITMENT_CHANGE = "COMMITMENT_CHANGE"


class RequestStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    WITHDRAWN = "WITHDRAWN"


class StepKind(str, enum.Enum):
    DIRECT_MANAGER = "DIRECT_MANAGER"
    NEXT_LEVEL_MANAGER = "NEXT_LEVEL_MANAGER"
    PEOPLE_PARTNER = "PEOPLE_PARTNER"


class StepDecision(str, enum.Enum):
    WAITING = "WAITING"  # not yet reached
    PENDING = "PENDING"  # current step
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SKIPPED = "SKIPPED"


class Role(str, enum.Enum):
    """Explicitly granted roles. EMPLOYEE and MANAGER are implicit (derived from the hierarchy)."""

    HR_ADMIN = "HR_ADMIN"
    HR_ANALYTICS = "HR_ANALYTICS"
    PEOPLE_PARTNER = "PEOPLE_PARTNER"
    EXECUTIVE = "EXECUTIVE"
    AUDITOR = "AUDITOR"


# ---------------------------------------------------------------------------------------------------
# Organisation
# ---------------------------------------------------------------------------------------------------


class OrgUnit(Base):
    __tablename__ = "org_unit"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(128), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    kind: Mapped[OrgUnitKind] = mapped_column(_enum(OrgUnitKind, "org_unit_kind"))
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("org_unit.id"), index=True)

    parent: Mapped[OrgUnit | None] = relationship(remote_side="OrgUnit.id")


class Location(Base):
    __tablename__ = "location"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    region: Mapped[str] = mapped_column(String(32), default="NCR")


class Employee(Base):
    __tablename__ = "employee"

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_number: Mapped[str] = mapped_column(String(64), unique=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    first_name: Mapped[str] = mapped_column(String(128), default="")
    last_name: Mapped[str] = mapped_column(String(128), default="")
    position: Mapped[str | None] = mapped_column(String(255))
    people_level: Mapped[str | None] = mapped_column(String(64))  # Individual Contributor / People Manager
    ceo_level: Mapped[int | None] = mapped_column(Integer)  # distance from CEO: CEO-1 = ExCo, CEO-2 = ExCo-1
    status: Mapped[str] = mapped_column(String(32), default="Active")
    site: Mapped[str | None] = mapped_column(String(64))
    arrangement: Mapped[str | None] = mapped_column(String(64))
    hired_date: Mapped[dt.date | None] = mapped_column(Date)
    termination_date: Mapped[dt.date | None] = mapped_column(Date)
    # False for people that only appear as managers / leaders but are not tracked by the report.
    in_scope: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip() or self.email


class EmployeeAssignment(Base):
    """Effective-dated placement of an employee: department, manager and office location."""

    __tablename__ = "employee_assignment"
    __table_args__ = (Index("ix_assignment_emp_from", "employee_id", "valid_from"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employee.id", ondelete="CASCADE"))
    org_unit_id: Mapped[int | None] = mapped_column(ForeignKey("org_unit.id"), index=True)
    manager_id: Mapped[int | None] = mapped_column(ForeignKey("employee.id"), index=True)
    location_id: Mapped[int | None] = mapped_column(ForeignKey("location.id"))
    valid_from: Mapped[dt.date] = mapped_column(Date)
    valid_to: Mapped[dt.date | None] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String(32), default="ADMIN")

    employee: Mapped[Employee] = relationship(foreign_keys=[employee_id])
    manager: Mapped[Employee | None] = relationship(foreign_keys=[manager_id])
    org_unit: Mapped[OrgUnit | None] = relationship()
    location: Mapped[Location | None] = relationship()


class RoleAssignment(Base):
    __tablename__ = "role_assignment"
    __table_args__ = (UniqueConstraint("employee_id", "role", "org_unit_id", name="uq_role_assignment"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employee.id", ondelete="CASCADE"), index=True)
    role: Mapped[Role] = mapped_column(_enum(Role, "role"))
    # Scope for PEOPLE_PARTNER / EXECUTIVE. NULL = whole company.
    org_unit_id: Mapped[int | None] = mapped_column(ForeignKey("org_unit.id"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    employee: Mapped[Employee] = relationship()
    org_unit: Mapped[OrgUnit | None] = relationship()


# ---------------------------------------------------------------------------------------------------
# Policy configuration
# ---------------------------------------------------------------------------------------------------


class PolicySetting(Base):
    """Compliance thresholds. ``org_unit_id`` NULL = company default; NULL fields inherit from parent."""

    __tablename__ = "policy_setting"
    __table_args__ = (UniqueConstraint("org_unit_id", "effective_from", name="uq_policy_scope_from"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    org_unit_id: Mapped[int | None] = mapped_column(ForeignKey("org_unit.id"))
    effective_from: Mapped[dt.date] = mapped_column(Date)
    monthly_pass_pct: Mapped[float | None] = mapped_column(Float)
    annual_pass_pct: Mapped[float | None] = mapped_column(Float)
    min_working_days: Mapped[int | None] = mapped_column(Integer)
    default_commitment_days: Mapped[float | None] = mapped_column(Float)
    note: Mapped[str | None] = mapped_column(Text)

    org_unit: Mapped[OrgUnit | None] = relationship()


class Commitment(Base):
    __tablename__ = "commitment"
    __table_args__ = (Index("ix_commitment_emp_from", "employee_id", "valid_from"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employee.id", ondelete="CASCADE"))
    kind: Mapped[CommitmentKind] = mapped_column(_enum(CommitmentKind, "commitment_kind"))
    days_per_week: Mapped[float] = mapped_column(Float)
    valid_from: Mapped[dt.date] = mapped_column(Date)
    valid_to: Mapped[dt.date | None] = mapped_column(Date)
    category: Mapped[str | None] = mapped_column(String(64))
    request_id: Mapped[int | None] = mapped_column(ForeignKey("request.id"))
    source: Mapped[str] = mapped_column(String(32), default="ADMIN")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Holiday(Base):
    __tablename__ = "holiday"
    __table_args__ = (UniqueConstraint("date", "location_id", name="uq_holiday_date_location"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, index=True)
    name: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(32), default="LEGAL")  # LEGAL / SPECIAL / REGIONAL
    location_id: Mapped[int | None] = mapped_column(ForeignKey("location.id"))  # NULL = all offices

    location: Mapped[Location | None] = relationship()


class ExcludedPeriod(Base):
    """One-off waivers (typhoon, management decisions) removing weeks or days from the computation."""

    __tablename__ = "excluded_period"

    id: Mapped[int] = mapped_column(primary_key=True)
    start_date: Mapped[dt.date] = mapped_column(Date)
    end_date: Mapped[dt.date] = mapped_column(Date)  # inclusive
    mode: Mapped[ExclusionMode] = mapped_column(_enum(ExclusionMode, "exclusion_mode"))
    org_unit_id: Mapped[int | None] = mapped_column(ForeignKey("org_unit.id"))
    location_id: Mapped[int | None] = mapped_column(ForeignKey("location.id"))
    reason: Mapped[str] = mapped_column(Text)

    org_unit: Mapped[OrgUnit | None] = relationship()
    location: Mapped[Location | None] = relationship()


# ---------------------------------------------------------------------------------------------------
# Raw and derived attendance
# ---------------------------------------------------------------------------------------------------


class ImportBatch(Base):
    __tablename__ = "import_batch"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(64))
    filename: Mapped[str] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(32), default="SUCCEEDED")
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("employee.id"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PresenceEvent(Base):
    __tablename__ = "presence_event"
    __table_args__ = (Index("ix_presence_emp_date", "employee_id", "date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employee.id", ondelete="CASCADE"))
    date: Mapped[dt.date] = mapped_column(Date)
    ts: Mapped[dt.datetime | None] = mapped_column(DateTime)
    source: Mapped[PresenceSource] = mapped_column(_enum(PresenceSource, "presence_source"))
    direction: Mapped[PresenceDirection] = mapped_column(_enum(PresenceDirection, "presence_direction"))
    site: Mapped[str | None] = mapped_column(String(128))
    import_batch_id: Mapped[int | None] = mapped_column(ForeignKey("import_batch.id", ondelete="CASCADE"))


class AbsenceRecord(Base):
    __tablename__ = "absence_record"
    __table_args__ = (Index("ix_absence_emp_date", "employee_id", "date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employee.id", ondelete="CASCADE"))
    date: Mapped[dt.date] = mapped_column(Date)
    kind: Mapped[AbsenceKind] = mapped_column(_enum(AbsenceKind, "absence_kind"))
    subtype: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="APPROVED")
    portion: Mapped[float] = mapped_column(Float, default=1.0)  # 1.0 full day, 0.5 half day
    import_batch_id: Mapped[int | None] = mapped_column(ForeignKey("import_batch.id", ondelete="CASCADE"))


class DayRecord(Base):
    """Result of onsite-day tagging (process step 5) for one employee and one calendar day."""

    __tablename__ = "day_record"

    employee_id: Mapped[int] = mapped_column(ForeignKey("employee.id", ondelete="CASCADE"), primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    status: Mapped[DayStatus] = mapped_column(_enum(DayStatus, "day_status"))
    valid_value: Mapped[float] = mapped_column(Float, default=0.0)
    flags: Mapped[list[str]] = mapped_column(JSON, default=list)
    sources: Mapped[str | None] = mapped_column(String(128))


class WeeklyAttendance(Base):
    """Valid onsite days per employee-week: the input of the compliance computation."""

    __tablename__ = "weekly_attendance"

    employee_id: Mapped[int] = mapped_column(ForeignKey("employee.id", ondelete="CASCADE"), primary_key=True)
    week_start: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    valid_days: Mapped[float] = mapped_column(Float)
    source: Mapped[WeeklySource] = mapped_column(_enum(WeeklySource, "weekly_source"))
    # Legacy reports carry an "after dispute" weekly count without day-level detail.
    adj_valid_days_override: Mapped[float | None] = mapped_column(Float)
    import_batch_id: Mapped[int | None] = mapped_column(ForeignKey("import_batch.id", ondelete="SET NULL"))


class AttendanceCredit(Base):
    """A day credited as valid through an approved dispute (after-dispute figures only)."""

    __tablename__ = "attendance_credit"
    __table_args__ = (UniqueConstraint("employee_id", "date", "request_id", name="uq_credit"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employee.id", ondelete="CASCADE"), index=True)
    date: Mapped[dt.date] = mapped_column(Date)
    value: Mapped[float] = mapped_column(Float, default=1.0)
    request_id: Mapped[int | None] = mapped_column(ForeignKey("request.id"))


class WeeklyResult(Base):
    __tablename__ = "weekly_result"
    __table_args__ = (Index("ix_weekly_result_month", "month"),)

    employee_id: Mapped[int] = mapped_column(ForeignKey("employee.id", ondelete="CASCADE"), primary_key=True)
    week_start: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    month: Mapped[dt.date] = mapped_column(Date)  # first day of the reporting month
    considered: Mapped[bool] = mapped_column(Boolean)
    exclusion_reason: Mapped[str | None] = mapped_column(String(128))
    working_days: Mapped[int] = mapped_column(Integer)
    valid_days: Mapped[float | None] = mapped_column(Float)
    commitment: Mapped[float | None] = mapped_column(Float)
    met: Mapped[bool | None] = mapped_column(Boolean)
    adj_valid_days: Mapped[float | None] = mapped_column(Float)
    adj_commitment: Mapped[float | None] = mapped_column(Float)
    adj_met: Mapped[bool | None] = mapped_column(Boolean)


class MonthlyResult(Base):
    __tablename__ = "monthly_result"
    __table_args__ = (
        Index("ix_monthly_result_month_unit", "month", "org_unit_id"),
        Index("ix_monthly_result_month_mgr", "month", "manager_id"),
    )

    employee_id: Mapped[int] = mapped_column(ForeignKey("employee.id", ondelete="CASCADE"), primary_key=True)
    month: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    org_unit_id: Mapped[int | None] = mapped_column(ForeignKey("org_unit.id"))
    manager_id: Mapped[int | None] = mapped_column(ForeignKey("employee.id"))
    location_id: Mapped[int | None] = mapped_column(ForeignKey("location.id"))
    commitment_days: Mapped[float | None] = mapped_column(Float)
    considered_weeks: Mapped[int] = mapped_column(Integer)
    met_weeks: Mapped[int] = mapped_column(Integer)
    adherence: Mapped[float | None] = mapped_column(Float)
    passed: Mapped[bool | None] = mapped_column(Boolean)
    adj_met_weeks: Mapped[int] = mapped_column(Integer)
    adj_adherence: Mapped[float | None] = mapped_column(Float)
    adj_passed: Mapped[bool | None] = mapped_column(Boolean)
    threshold_pct: Mapped[float] = mapped_column(Float)
    total_valid_days: Mapped[float] = mapped_column(Float, default=0)
    avg_valid_days: Mapped[float | None] = mapped_column(Float)
    computed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    employee: Mapped[Employee] = relationship(foreign_keys=[employee_id])
    org_unit: Mapped[OrgUnit | None] = relationship()


class ReportMonth(Base):
    __tablename__ = "report_month"

    month: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    status: Mapped[MonthStatus] = mapped_column(_enum(MonthStatus, "month_status"), default=MonthStatus.OPEN)
    published_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    review_deadline: Mapped[dt.date | None] = mapped_column(Date)
    finalized_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    finalized_by_id: Mapped[int | None] = mapped_column(ForeignKey("employee.id"))


# ---------------------------------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------------------------------


class Request(Base):
    __tablename__ = "request"
    __table_args__ = (Index("ix_request_emp_status", "employee_id", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[RequestType] = mapped_column(_enum(RequestType, "request_type"))
    employee_id: Mapped[int] = mapped_column(ForeignKey("employee.id", ondelete="CASCADE"))
    submitted_by_id: Mapped[int | None] = mapped_column(ForeignKey("employee.id"))
    status: Mapped[RequestStatus] = mapped_column(
        _enum(RequestStatus, "request_status"), default=RequestStatus.PENDING
    )
    start_date: Mapped[dt.date] = mapped_column(Date)
    end_date: Mapped[dt.date] = mapped_column(Date)
    days_per_week: Mapped[float | None] = mapped_column(Float)
    credited_dates: Mapped[list[str]] = mapped_column(JSON, default=list)
    category: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(32), default="APP")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    decided_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    employee: Mapped[Employee] = relationship(foreign_keys=[employee_id])
    submitted_by: Mapped[Employee | None] = relationship(foreign_keys=[submitted_by_id])
    steps: Mapped[list[RequestStep]] = relationship(
        back_populates="request", order_by="RequestStep.step_order", cascade="all, delete-orphan"
    )


class RequestStep(Base):
    __tablename__ = "request_step"
    __table_args__ = (Index("ix_step_approver_decision", "approver_id", "decision"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("request.id", ondelete="CASCADE"))
    step_order: Mapped[int] = mapped_column(Integer)
    kind: Mapped[StepKind] = mapped_column(_enum(StepKind, "step_kind"))
    # Named approver for manager steps; NULL for People Partner steps (any PP in scope may act).
    approver_id: Mapped[int | None] = mapped_column(ForeignKey("employee.id"))
    decision: Mapped[StepDecision] = mapped_column(_enum(StepDecision, "step_decision"))
    decided_by_id: Mapped[int | None] = mapped_column(ForeignKey("employee.id"))
    comment: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    request: Mapped[Request] = relationship(back_populates="steps")
    approver: Mapped[Employee | None] = relationship(foreign_keys=[approver_id])
    decided_by: Mapped[Employee | None] = relationship(foreign_keys=[decided_by_id])


# ---------------------------------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------------------------------


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_entity", "entity", "entity_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("employee.id"))
    actor_label: Mapped[str] = mapped_column(String(320))
    action: Mapped[str] = mapped_column(String(64))
    entity: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
