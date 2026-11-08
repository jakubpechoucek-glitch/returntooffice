"""Weekly, monthly and annual compliance (Working Arrangements Manual, section 6.6).

* Weekly compliance: valid onsite days (onsite + approved leave + approved OB) >= commitment days.
  No make-up mechanism: every week stands on its own.
* Monthly adherence: weeks met / weeks considered. Passed when >= the department's monthly threshold.
* Annual compliance rate: months adhering / 12. Good standing when >= the annual threshold.

Two figures are always produced, mirroring the legacy report:
the *initial* figure and the *after dispute* (``adj_``) figure.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

EPS = 1e-9


@dataclass(frozen=True)
class WeekInput:
    monday: dt.date
    month: dt.date
    considered: bool
    exclusion_reason: str | None
    working_days: int
    valid_days: float | None  # None = no attendance data for the week
    commitment: float | None
    adj_valid_days: float | None
    adj_commitment: float | None


@dataclass(frozen=True)
class WeekOutcome:
    monday: dt.date
    month: dt.date
    considered: bool
    exclusion_reason: str | None
    working_days: int
    valid_days: float | None
    commitment: float | None
    met: bool | None
    adj_valid_days: float | None
    adj_commitment: float | None
    adj_met: bool | None


@dataclass(frozen=True)
class MonthOutcome:
    month: dt.date
    considered_weeks: int
    met_weeks: int
    adherence: float | None
    passed: bool | None
    adj_met_weeks: int
    adj_adherence: float | None
    adj_passed: bool | None
    threshold_pct: float
    total_valid_days: float
    avg_valid_days: float | None


@dataclass(frozen=True)
class AnnualOutcome:
    year: int
    adhering_months: int
    evaluated_months: int
    rate: float  # adhering months / 12 (policy definition)
    ytd_rate: float | None  # adhering months / evaluated months (progress indicator)
    passed: bool
    threshold_pct: float


def is_met(valid_days: float | None, commitment: float | None) -> bool | None:
    if valid_days is None or commitment is None:
        return None
    return valid_days + EPS >= commitment


def evaluate_week(w: WeekInput) -> WeekOutcome:
    if not w.considered:
        met = adj_met = None
    else:
        met = is_met(w.valid_days, w.commitment)
        adj_met = is_met(w.adj_valid_days, w.adj_commitment)
    return WeekOutcome(
        monday=w.monday,
        month=w.month,
        considered=w.considered,
        exclusion_reason=w.exclusion_reason,
        working_days=w.working_days,
        valid_days=w.valid_days,
        commitment=w.commitment,
        met=met,
        adj_valid_days=w.adj_valid_days,
        adj_commitment=w.adj_commitment,
        adj_met=adj_met,
    )


def evaluate_month(month: dt.date, weeks: Sequence[WeekOutcome], threshold_pct: float) -> MonthOutcome:
    """``threshold_pct`` is expressed in percent (e.g. 75.0)."""
    considered = [w for w in weeks if w.considered and w.valid_days is not None]
    n = len(considered)
    met = sum(1 for w in considered if w.met)
    adj_met = sum(1 for w in considered if w.adj_met)
    adherence = met / n if n else None
    adj_adherence = adj_met / n if n else None
    total = sum(w.valid_days or 0.0 for w in considered)

    def passed(a: float | None) -> bool | None:
        return None if a is None else a * 100 + EPS >= threshold_pct

    return MonthOutcome(
        month=month,
        considered_weeks=n,
        met_weeks=met,
        adherence=adherence,
        passed=passed(adherence),
        adj_met_weeks=adj_met,
        adj_adherence=adj_adherence,
        adj_passed=passed(adj_adherence),
        threshold_pct=threshold_pct,
        total_valid_days=total,
        avg_valid_days=(total / n) if n else None,
    )


def evaluate_year(year: int, month_passed: Iterable[bool | None], threshold_pct: float) -> AnnualOutcome:
    flags = [p for p in month_passed if p is not None]
    adhering = sum(1 for p in flags if p)
    rate = adhering / 12
    return AnnualOutcome(
        year=year,
        adhering_months=adhering,
        evaluated_months=len(flags),
        rate=rate,
        ytd_rate=(adhering / len(flags)) if flags else None,
        passed=rate * 100 + EPS >= threshold_pct,
        threshold_pct=threshold_pct,
    )
