"""Holiday calendars and excluded periods, resolved per employee (office location, department)."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from rto.models import ExcludedPeriod, ExclusionMode, Holiday
from rto.services.org import unit_ancestors


class CalendarData:
    def __init__(self, db: Session, start: dt.date, end: dt.date):
        from rto.models import OrgUnit

        self.units = {u.id: u for u in db.scalars(select(OrgUnit))}
        self.holidays: dict[int | None, set[dt.date]] = {}
        for h in db.scalars(select(Holiday).where(Holiday.date >= start, Holiday.date <= end)):
            self.holidays.setdefault(h.location_id, set()).add(h.date)
        self.periods = list(
            db.scalars(
                select(ExcludedPeriod).where(ExcludedPeriod.end_date >= start, ExcludedPeriod.start_date <= end)
            )
        )

    def holidays_for(self, location_id: int | None) -> set[dt.date]:
        out = set(self.holidays.get(None, set()))
        if location_id is not None:
            out |= self.holidays.get(location_id, set())
        return out

    def _applies(self, p: ExcludedPeriod, org_unit_id: int | None, location_id: int | None) -> bool:
        if p.location_id is not None and p.location_id != location_id:
            return False
        return not (p.org_unit_id is not None and p.org_unit_id not in unit_ancestors(self.units, org_unit_id))

    def excluded_days(self, org_unit_id: int | None, location_id: int | None) -> set[dt.date]:
        out: set[dt.date] = set()
        for p in self.periods:
            if p.mode == ExclusionMode.DAYS and self._applies(p, org_unit_id, location_id):
                d = p.start_date
                while d <= p.end_date:
                    out.add(d)
                    d += dt.timedelta(days=1)
        return out

    def week_excluded(self, monday: dt.date, org_unit_id: int | None, location_id: int | None) -> bool:
        sunday = monday + dt.timedelta(days=6)
        return any(
            p.mode == ExclusionMode.WEEK
            and p.start_date <= sunday
            and p.end_date >= monday
            and self._applies(p, org_unit_id, location_id)
            for p in self.periods
        )
