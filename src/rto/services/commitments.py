"""Weekly onsite commitments: baseline (effective-dated), weekly exemptions and dispute corrections."""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from rto.domain.calendar import week_start
from rto.models import Commitment, CommitmentKind, Employee
from rto.services import ServiceError
from rto.services.audit import audit

PRIORITY_INITIAL = (CommitmentKind.EXEMPTION, CommitmentKind.BASELINE)
PRIORITY_ADJUSTED = (CommitmentKind.DISPUTE, CommitmentKind.EXEMPTION, CommitmentKind.BASELINE)


def level_default(ceo_level: int | None) -> float | None:
    """Policy 6.2: ExCo (CEO-1) 5 days, ExCo-1 (CEO-2) 4 days. Others fall back to the policy default."""
    if ceo_level == 1:
        return 5.0
    if ceo_level == 2:
        return 4.0
    return None


def commitment_for_week(
    rows: Sequence[Commitment], monday: dt.date, adjusted: bool
) -> Commitment | None:
    """The commitment row that applies to the week starting ``monday``.

    The value in effect on the Monday applies to the whole week (open question Q10 default).
    """
    priority = PRIORITY_ADJUSTED if adjusted else PRIORITY_INITIAL
    covering = [r for r in rows if r.valid_from <= monday and (r.valid_to is None or r.valid_to > monday)]
    for kind in priority:
        candidates = [r for r in covering if r.kind == kind]
        if candidates:
            return max(candidates, key=lambda r: (r.valid_from, r.id or 0))
    return None


def set_baseline(
    db: Session,
    actor: Employee | None,
    employee_id: int,
    days_per_week: float,
    effective_from: dt.date,
    source: str = "ADMIN",
    request_id: int | None = None,
) -> Commitment:
    if not (0 <= days_per_week <= 5):
        raise ServiceError("Commitment must be between 0 and 5 days per week")
    effective_from = week_start(effective_from)
    rows = list(
        db.scalars(
            select(Commitment)
            .where(Commitment.employee_id == employee_id, Commitment.kind == CommitmentKind.BASELINE)
            .order_by(Commitment.valid_from)
        )
    )
    current = commitment_for_week(rows, effective_from, adjusted=False)
    if current is not None and current.days_per_week == days_per_week:
        return current
    later = [r for r in rows if r.valid_from > effective_from]
    if current is not None and current.valid_from == effective_from:
        before = current.days_per_week
        current.days_per_week = days_per_week
        current.source = source
        current.request_id = request_id
        row = current
    else:
        before = current.days_per_week if current else None
        if current is not None:
            current.valid_to = effective_from
        row = Commitment(
            employee_id=employee_id,
            kind=CommitmentKind.BASELINE,
            days_per_week=days_per_week,
            valid_from=effective_from,
            valid_to=later[0].valid_from if later else None,
            source=source,
            request_id=request_id,
        )
        db.add(row)
    db.flush()
    if source != "IMPORT":
        audit(
            db,
            actor,
            "commitment.baseline",
            "employee",
            employee_id,
            {"effective_from": effective_from, "before": before, "after": days_per_week, "request_id": request_id},
        )
    return row


def add_override(
    db: Session,
    employee_id: int,
    kind: CommitmentKind,
    days_per_week: float,
    start: dt.date,
    end_inclusive: dt.date,
    category: str | None,
    request_id: int | None,
    source: str = "REQUEST",
) -> Commitment:
    row = Commitment(
        employee_id=employee_id,
        kind=kind,
        days_per_week=days_per_week,
        valid_from=week_start(start),
        valid_to=week_start(end_inclusive) + dt.timedelta(days=7),
        category=category,
        request_id=request_id,
        source=source,
    )
    db.add(row)
    db.flush()
    return row
