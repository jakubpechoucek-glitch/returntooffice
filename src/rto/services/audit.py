from __future__ import annotations

import datetime as dt
import enum
from typing import Any

from sqlalchemy.orm import Session

from rto.models import AuditLog, Employee


def _jsonable(v: Any) -> Any:
    if isinstance(v, dt.date | dt.datetime):
        return v.isoformat()
    if isinstance(v, enum.Enum):
        return v.value
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, list | tuple | set):
        return [_jsonable(x) for x in v]
    return v


def audit(
    db: Session,
    actor: Employee | None,
    action: str,
    entity: str,
    entity_id: Any = None,
    details: dict[str, Any] | None = None,
    actor_label: str | None = None,
) -> None:
    db.add(
        AuditLog(
            actor_id=actor.id if actor else None,
            actor_label=actor_label or (actor.email if actor else "system"),
            action=action,
            entity=entity,
            entity_id=None if entity_id is None else str(entity_id),
            details=_jsonable(details or {}),
        )
    )
