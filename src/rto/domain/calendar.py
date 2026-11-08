"""Reporting calendar.

Rules implemented (Working Arrangements Manual, section 6.6):

* A week starts on Monday and ends on Sunday.
* A week that spans two months belongs to the month holding the majority of its **weekdays**
  (Mon-Fri). Holidays are deliberately *not* removed before the majority vote: the manual's example
  (week of 30 Mar - 5 Apr 2026, with 2-3 April being holidays) is assigned to April.
* A week that spans a year boundary is never considered ("we only consider 51 weeks in a year").
  This reproduces the manual's table of weeks per month for 2026 (4,4,4,5,4,4,5,4,5,4,4,4).
* Only weeks with at least ``min_working_days`` working days (weekdays minus holidays) are considered.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

ONE_DAY = dt.timedelta(days=1)


def week_start(d: dt.date) -> dt.date:
    """Monday of the week containing ``d``."""
    return d - dt.timedelta(days=d.weekday())


def weekdays(monday: dt.date) -> list[dt.date]:
    return [monday + dt.timedelta(days=i) for i in range(5)]


def first_of_month(d: dt.date) -> dt.date:
    return d.replace(day=1)


def add_months(month: dt.date, n: int) -> dt.date:
    y, m = divmod(month.month - 1 + n, 12)
    return dt.date(month.year + y, m + 1, 1)


def spans_year_boundary(monday: dt.date) -> bool:
    return monday.year != (monday + dt.timedelta(days=6)).year


def reporting_month(monday: dt.date) -> dt.date:
    """Month (as its first day) to which the week starting on ``monday`` is assigned."""
    counts = Counter(first_of_month(d) for d in weekdays(monday))
    # 5 weekdays split across at most two months: never a tie.
    return counts.most_common(1)[0][0]


def weeks_of_month(month: dt.date) -> list[dt.date]:
    """Mondays of all weeks assigned to ``month`` (including weeks later excluded for other reasons)."""
    month = first_of_month(month)
    start = week_start(month - dt.timedelta(days=6))
    end = add_months(month, 1) + dt.timedelta(days=6)
    out = []
    d = start
    while d <= end:
        if reporting_month(d) == month:
            out.append(d)
        d += dt.timedelta(days=7)
    return out


def iter_days(start: dt.date, end: dt.date) -> Iterator[dt.date]:
    d = start
    while d <= end:
        yield d
        d += ONE_DAY


@dataclass(frozen=True)
class WeekFrame:
    """Calendar facts about one week for one employee (holidays depend on the employee's office)."""

    monday: dt.date
    month: dt.date
    working_days: tuple[dt.date, ...]
    holidays: tuple[dt.date, ...]
    considered: bool
    exclusion_reason: str | None


def frame_week(
    monday: dt.date,
    holidays: Iterable[dt.date],
    min_working_days: int = 3,
    excluded_whole_week: bool = False,
    exclude_year_boundary_week: bool = True,
) -> WeekFrame:
    hol = set(holidays)
    days = weekdays(monday)
    working = tuple(d for d in days if d not in hol)
    reason: str | None = None
    if exclude_year_boundary_week and spans_year_boundary(monday):
        reason = "Year-boundary week (not counted per policy)"
    elif excluded_whole_week:
        reason = "Excluded period (waiver)"
    elif len(working) < min_working_days:
        reason = f"Fewer than {min_working_days} working days"
    return WeekFrame(
        monday=monday,
        month=reporting_month(monday),
        working_days=working,
        holidays=tuple(d for d in days if d in hol),
        considered=reason is None,
        exclusion_reason=reason,
    )
