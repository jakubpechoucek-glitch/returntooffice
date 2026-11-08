"""Importers for daily raw data in the canonical CSV layouts (see ``docs/data-import.md``).

* ``presence``  - door access (DACS), timekeeping and Splunk Wi-Fi events
  ``employee_number,date,time,source,direction,site``
* ``absences``  - approved leave and Official Business from HC Connect
  ``employee_number,date,kind,subtype,status,portion``

Imports are idempotent per (employee, date, source/kind): re-loading a corrected extract replaces
the previous rows for the dates it contains. After loading, the affected weeks are tagged and the
affected (non-finalized) months recomputed.

When the real extract layouts are known, add a thin adapter that maps them to these columns.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
from dataclasses import dataclass, field

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.orm import Session

from rto.models import (
    AbsenceKind,
    AbsenceRecord,
    Employee,
    ImportBatch,
    PresenceDirection,
    PresenceEvent,
    PresenceSource,
)
from rto.services import ServiceError
from rto.services.audit import audit
from rto.services.compute import recompute_range, tag_range

PRESENCE_COLUMNS = ["employee_number", "date", "time", "source", "direction", "site"]
ABSENCE_COLUMNS = ["employee_number", "date", "kind", "subtype", "status", "portion"]
MAX_ERRORS = 50


@dataclass
class DailyImportResult:
    batch_id: int | None = None
    rows: int = 0
    employees: int = 0
    date_from: str | None = None
    date_to: str | None = None
    errors: list[str] = field(default_factory=list)
    months_recomputed: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def _reader(content: bytes, required: list[str]) -> csv.DictReader:
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    fields = [f.strip().lower() for f in (reader.fieldnames or [])]
    reader.fieldnames = fields
    missing = [c for c in required if c not in fields]
    if missing:
        raise ServiceError(f"Missing columns: {', '.join(missing)}")
    return reader


def _employees(db: Session) -> dict[str, int]:
    return {e.employee_number: e.id for e in db.scalars(select(Employee))}


def import_daily(
    db: Session, kind: str, content: bytes, filename: str, actor: Employee | None = None
) -> DailyImportResult:
    if kind not in ("presence", "absences"):
        raise ServiceError("Unknown daily import kind")
    required = PRESENCE_COLUMNS[:5] if kind == "presence" else ABSENCE_COLUMNS[:3]
    reader = _reader(content, required)
    emp_ids = _employees(db)
    res = DailyImportResult()
    batch = ImportBatch(kind=f"DAILY_{kind.upper()}", filename=filename, created_by_id=actor.id if actor else None)
    db.add(batch)
    db.flush()
    res.batch_id = batch.id

    rows: list[dict] = []
    keys: set[tuple[int, dt.date, str]] = set()
    for line, raw in enumerate(reader, start=2):
        try:
            number = (raw.get("employee_number") or "").strip()
            if number not in emp_ids:
                raise ValueError(f"unknown employee_number {number!r}")
            emp_id = emp_ids[number]
            day = dt.date.fromisoformat((raw.get("date") or "").strip())
            if kind == "presence":
                source = PresenceSource((raw.get("source") or "").strip().upper())
                direction = PresenceDirection((raw.get("direction") or "").strip().upper())
                t = (raw.get("time") or "").strip()
                ts = dt.datetime.combine(day, dt.time.fromisoformat(t)) if t else None
                rows.append(
                    {
                        "employee_id": emp_id,
                        "date": day,
                        "ts": ts,
                        "source": source,
                        "direction": direction,
                        "site": (raw.get("site") or "").strip() or None,
                        "import_batch_id": batch.id,
                    }
                )
                keys.add((emp_id, day, source.value))
            else:
                absence_kind = AbsenceKind((raw.get("kind") or "").strip().upper())
                portion = float((raw.get("portion") or "1").strip() or 1)
                if portion not in (0.5, 1.0):
                    raise ValueError("portion must be 1 or 0.5")
                rows.append(
                    {
                        "employee_id": emp_id,
                        "date": day,
                        "kind": absence_kind,
                        "subtype": (raw.get("subtype") or "").strip() or None,
                        "status": (raw.get("status") or "APPROVED").strip().upper(),
                        "portion": portion,
                        "import_batch_id": batch.id,
                    }
                )
                keys.add((emp_id, day, absence_kind.value))
        except ValueError as exc:
            if len(res.errors) < MAX_ERRORS:
                res.errors.append(f"Line {line}: {exc}")
    if res.errors:
        batch.status = "FAILED"
        batch.summary = res.as_dict()
        db.flush()
        raise ServiceError("Import rejected, no rows were loaded:\n" + "\n".join(res.errors))
    if not rows:
        raise ServiceError("The file contains no data rows")

    # Replace previously loaded rows for the same (employee, date, source/kind).
    model = PresenceEvent if kind == "presence" else AbsenceRecord
    discr = PresenceEvent.source if kind == "presence" else AbsenceRecord.kind
    keys_list = sorted(keys)
    for i in range(0, len(keys_list), 300):
        chunk = keys_list[i : i + 300]
        db.execute(
            delete(model).where(
                or_(*[and_(model.employee_id == e, model.date == d, discr == s) for e, d, s in chunk])
            )
        )
    for i in range(0, len(rows), 2000):
        db.execute(model.__table__.insert(), rows[i : i + 2000])

    employees = {r["employee_id"] for r in rows}
    start = min(r["date"] for r in rows)
    end = max(r["date"] for r in rows)
    res.rows = len(rows)
    res.employees = len(employees)
    res.date_from, res.date_to = start.isoformat(), end.isoformat()
    tag_range(db, start, end, employees)
    res.months_recomputed = [s.month.isoformat() for s in recompute_range(db, start, end, employees)]
    batch.row_count = res.rows
    batch.summary = res.as_dict()
    audit(db, actor, f"import.daily_{kind}", "import_batch", batch.id, {"filename": filename, "rows": res.rows})
    db.flush()
    return res
