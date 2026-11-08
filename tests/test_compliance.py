import datetime as dt

from rto.domain.compliance import WeekInput, evaluate_month, evaluate_week, evaluate_year
from rto.domain.tagging import Absence, Presence, tag_day
from rto.models import AbsenceKind, DayStatus, PresenceDirection, PresenceSource

D = dt.date
M = D(2026, 1, 1)


def week(valid, commit, adj_valid=None, adj_commit=None, considered=True):
    return evaluate_week(
        WeekInput(
            monday=D(2026, 1, 5),
            month=M,
            considered=considered,
            exclusion_reason=None,
            working_days=5,
            valid_days=valid,
            commitment=commit,
            adj_valid_days=valid if adj_valid is None else adj_valid,
            adj_commitment=commit if adj_commit is None else adj_commit,
        )
    )


def test_manual_example_weekly_met():
    # "2 days onsite + 1 day leave + 2 days WFH" vs commitment 3 -> met
    assert week(3, 3).met is True


def test_manual_example_weekly_not_met():
    # "1 day onsite + 1 day OB + 1 day holiday + 2 WFH" -> 2 valid vs 3 -> not met
    assert week(2, 3).met is False


def test_monthly_adherence_examples():
    weeks = [week(3, 3), week(3, 3), week(3, 3), week(1, 3)]
    m = evaluate_month(M, weeks, threshold_pct=75)
    assert m.adherence == 0.75 and m.passed is True
    weeks5 = [week(3, 3)] * 3 + [week(0, 3)] * 2
    m5 = evaluate_month(M, weeks5, threshold_pct=75)
    assert m5.adherence == 0.6 and m5.passed is False


def test_per_department_threshold_changes_outcome():
    weeks = [week(3, 3), week(3, 3), week(3, 3), week(1, 3)]
    assert evaluate_month(M, weeks, threshold_pct=100).passed is False


def test_after_dispute_figures_are_independent():
    weeks = [week(2, 3, adj_valid=3), week(3, 3), week(3, 3), week(1, 3, adj_commit=1)]
    m = evaluate_month(M, weeks, threshold_pct=100)
    assert (m.met_weeks, m.adj_met_weeks) == (2, 4)
    assert m.passed is False and m.adj_passed is True


def test_unconsidered_weeks_do_not_count():
    weeks = [week(3, 3), week(0, 3, considered=False)]
    m = evaluate_month(M, weeks, threshold_pct=100)
    assert m.considered_weeks == 1 and m.passed is True


def test_annual_rate_uses_twelve_months():
    a = evaluate_year(2026, [True] * 9 + [False] * 3, threshold_pct=75)
    assert a.rate == 0.75 and a.passed
    partial = evaluate_year(2026, [True, True, True], threshold_pct=75)
    assert partial.rate == 0.25 and partial.ytd_rate == 1.0 and not partial.passed


# --- tagging ---------------------------------------------------------------------------------------

DOOR_IN = Presence(PresenceSource.DOOR, PresenceDirection.IN)
DOOR_OUT = Presence(PresenceSource.DOOR, PresenceDirection.OUT)
WIFI = Presence(PresenceSource.WIFI, PresenceDirection.PRESENT)
TUE = D(2026, 1, 6)


def test_tag_full_presence():
    t = tag_day(TUE, [DOOR_IN, DOOR_OUT], [])
    assert (t.status, t.valid_value, t.flags) == (DayStatus.ONSITE, 1.0, [])


def test_tag_single_tap_counts_but_flagged():
    t = tag_day(TUE, [DOOR_IN], [])
    assert t.valid_value == 1.0 and "INCOMPLETE_TAP" in t.flags


def test_tag_wifi_only_not_counted_by_default():
    t = tag_day(TUE, [WIFI], [])
    assert t.status == DayStatus.NOT_ONSITE and t.valid_value == 0 and "WIFI_ONLY" in t.flags


def test_tag_leave_and_ob_are_valid():
    assert tag_day(TUE, [], [Absence(AbsenceKind.LEAVE)]).status == DayStatus.LEAVE
    assert tag_day(TUE, [], [Absence(AbsenceKind.OB)]).valid_value == 1.0


def test_tag_half_day_rules():
    assert tag_day(TUE, [], [Absence(AbsenceKind.LEAVE, 0.5)]).valid_value == 0.5
    assert tag_day(TUE, [DOOR_IN, DOOR_OUT], [Absence(AbsenceKind.LEAVE, 0.5)]).valid_value == 1.0


def test_tag_unapproved_leave_ignored():
    assert tag_day(TUE, [], [Absence(AbsenceKind.LEAVE, approved=False)]).valid_value == 0


def test_tag_holiday_never_counts():
    t = tag_day(TUE, [DOOR_IN, DOOR_OUT], [], is_holiday=True)
    assert t.status == DayStatus.HOLIDAY and t.valid_value == 0


def test_tag_weekend():
    t = tag_day(D(2026, 1, 10), [DOOR_IN, DOOR_OUT], [])
    assert t.valid_value == 0 and "WEEKEND_PRESENCE" in t.flags
