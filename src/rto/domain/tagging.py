"""Onsite day tagging (process step 5): classify one employee-day from raw events.

Rules (Working Arrangements Manual 6.3.1 plus the proposed defaults in ``docs/open-questions-hr.md``):

* Valid onsite days are: physical presence, approved leave, approved Official Business (OB).
* Official holidays and excluded (waiver) days are neither valid nor missing.
* Physical presence: door tap-in **and** tap-out. Fallbacks are configurable:
  a single door tap (flag ``INCOMPLETE_TAP``) and timekeeping in/out (flag ``NO_DOOR_TAP``)
  count by default; Wi-Fi only presence (flag ``WIFI_ONLY``) does not count by default and is
  surfaced for review / dispute.
* Half-day leave or OB counts 0.5; combined with presence the day counts 1. A day never exceeds 1.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass, field

from rto.models import AbsenceKind, DayStatus, PresenceDirection, PresenceSource


@dataclass(frozen=True)
class TaggingRules:
    count_single_door_tap: bool = True
    count_timekeeping_without_door: bool = True
    count_wifi_only: bool = False
    count_weekend_presence: bool = False


@dataclass(frozen=True)
class Presence:
    source: PresenceSource
    direction: PresenceDirection


@dataclass(frozen=True)
class Absence:
    kind: AbsenceKind
    portion: float = 1.0
    approved: bool = True


@dataclass
class TaggedDay:
    date: dt.date
    status: DayStatus
    valid_value: float
    flags: list[str] = field(default_factory=list)
    sources: str = ""


def _presence_value(events: list[Presence], rules: TaggingRules, flags: list[str]) -> bool:
    door = {e.direction for e in events if e.source == PresenceSource.DOOR}
    tk = {e.direction for e in events if e.source == PresenceSource.TIMEKEEPING}
    wifi = any(e.source == PresenceSource.WIFI for e in events)

    if PresenceDirection.IN in door and PresenceDirection.OUT in door:
        return True
    if door:
        flags.append("INCOMPLETE_TAP")
        if rules.count_single_door_tap:
            return True
    if tk:
        flags.append("NO_DOOR_TAP")
        if rules.count_timekeeping_without_door:
            return True
    if wifi:
        flags.append("WIFI_ONLY")
        if rules.count_wifi_only:
            return True
    return False


def tag_day(
    date: dt.date,
    presence: Iterable[Presence],
    absences: Iterable[Absence],
    is_holiday: bool = False,
    is_excluded: bool = False,
    rules: TaggingRules | None = None,
) -> TaggedDay:
    rules = rules or TaggingRules()
    events = list(presence)
    approved = [a for a in absences if a.approved]
    flags: list[str] = []
    sources = ",".join(sorted({e.source.value for e in events} | {a.kind.value for a in approved}))

    if date.weekday() >= 5:
        present = _presence_value(events, rules, flags) if events else False
        if present:
            flags.append("WEEKEND_PRESENCE")
        value = 1.0 if (present and rules.count_weekend_presence) else 0.0
        return TaggedDay(date, DayStatus.ONSITE if value else DayStatus.WEEKEND, value, flags, sources)

    if is_holiday or is_excluded:
        if events:
            flags.append("PRESENCE_ON_NON_WORKING_DAY")
        status = DayStatus.HOLIDAY if is_holiday else DayStatus.EXCLUDED
        return TaggedDay(date, status, 0.0, flags, sources)

    present = _presence_value(events, rules, flags) if events else False
    leave = min(1.0, sum(a.portion for a in approved if a.kind == AbsenceKind.LEAVE))
    ob = min(1.0, sum(a.portion for a in approved if a.kind == AbsenceKind.OB))
    absence = min(1.0, leave + ob)

    if present:
        if absence >= 1.0:
            flags.append("PRESENCE_AND_FULL_DAY_ABSENCE")
        return TaggedDay(date, DayStatus.ONSITE, 1.0, flags, sources)
    if absence > 0:
        status = DayStatus.OB if ob > leave else DayStatus.LEAVE
        if absence < 1.0:
            flags.append("HALF_DAY")
        return TaggedDay(date, status, absence, flags, sources)
    return TaggedDay(date, DayStatus.NOT_ONSITE, 0.0, flags, sources)
