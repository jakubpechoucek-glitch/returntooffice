import datetime as dt

from rto.domain.calendar import frame_week, reporting_month, weeks_of_month

D = dt.date


def considered_weeks(month):
    return [m for m in weeks_of_month(month) if frame_week(m, holidays=[]).considered]


def test_2026_weeks_per_month_matches_policy_table():
    # Working Arrangements Manual 6.6.A: "Number of Weeks per Month in 2026"
    expected = [4, 4, 4, 5, 4, 4, 5, 4, 5, 4, 4, 4]
    assert [len(considered_weeks(D(2026, m, 1))) for m in range(1, 13)] == expected
    assert sum(expected) == 51


def test_cross_month_week_assigned_by_majority_of_weekdays_not_working_days():
    # Week of 30 Mar - 5 Apr 2026 belongs to April even though 2-3 April are holidays.
    assert reporting_month(D(2026, 3, 30)) == D(2026, 4, 1)
    holy_week = frame_week(D(2026, 3, 30), holidays=[D(2026, 4, 2), D(2026, 4, 3)])
    assert holy_week.month == D(2026, 4, 1)
    assert holy_week.working_days == (D(2026, 3, 30), D(2026, 3, 31), D(2026, 4, 1))
    assert holy_week.considered  # exactly 3 working days -> considered


def test_january_2026_weeks_match_legacy_report_columns():
    # Legacy report: W1 (Jan 5-11) ... W4 (Jan 26 - Feb 1)
    assert considered_weeks(D(2026, 1, 1)) == [D(2026, 1, 5), D(2026, 1, 12), D(2026, 1, 19), D(2026, 1, 26)]
    assert considered_weeks(D(2026, 3, 1)) == [D(2026, 3, 2), D(2026, 3, 9), D(2026, 3, 16), D(2026, 3, 23)]


def test_week_with_fewer_than_three_working_days_is_not_considered():
    f = frame_week(D(2026, 3, 30), holidays=[D(2026, 3, 31), D(2026, 4, 2), D(2026, 4, 3)])
    assert not f.considered
    assert "Fewer than 3" in f.exclusion_reason


def test_year_boundary_week_excluded():
    f = frame_week(D(2026, 12, 28), holidays=[])
    assert not f.considered
    assert f.month == D(2026, 12, 1)


def test_min_working_days_configurable():
    f = frame_week(D(2026, 3, 30), holidays=[D(2026, 4, 2), D(2026, 4, 3)], min_working_days=4)
    assert not f.considered
