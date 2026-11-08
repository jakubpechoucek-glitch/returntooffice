# Compliance rules as implemented

Source: *Working Arrangements Manual*, version 1, section 6. This document is the reference for HR and
for developers. Every rule names the code that implements it and the test that proves it.
Values marked **configurable** can be changed per department in *Admin → Thresholds*, without a code change.

## 1. Who is tracked

- Employees with `in_scope = true`. The legacy report and masterlist imports set this flag. Admins change it under *Admin → People*.
- People who appear only as a manager or leader in an import (for example the *CEO 2* column) are created with `in_scope = false`. They appear in the hierarchy and can sign in, but have no compliance figures.
- Joiners: weeks starting before the hire date are not counted. Leavers: weeks ending (Friday) after the separation date are not counted.

## 2. The weekly commitment

The number of valid onsite days required in a week comes from, in priority order:

1. an approved **dispute** that corrects the commitment (*after-dispute* figures only);
2. an approved **exemption** covering the week;
3. the **baseline** commitment in effect on the Monday of the week;
4. if no baseline exists, a policy default:

   | Population | Default |
   |---|---|
   | ExCo (CEO-1) | 5 days |
   | ExCo-1 (CEO-2) | 4 days |
   | Others | the department's *default commitment*, **configurable**, built-in 3 |

The value in effect on **Monday** applies to the whole week (open question Q10).

Code: `services/commitments.py`.

## 3. Valid onsite days (day tagging)

Code: `domain/tagging.py`. Tests: `tests/test_compliance.py`, `tests/test_services.py::test_daily_pipeline_tags_and_computes`.

| Situation | Tag | Counts as |
|---|---|---|
| Door tap-in **and** tap-out on the same day | Onsite | 1 |
| Only one door tap | Onsite, flag `INCOMPLETE_TAP` | 1 (configurable in code: `TaggingRules`) |
| Timekeeping in/out without a door tap | Onsite, flag `NO_DOOR_TAP` | 1 |
| Wi-Fi presence only | Not onsite, flag `WIFI_ONLY` | 0 (can be credited through a dispute) |
| Approved leave (any type) | Leave | 1, or 0.5 for a half day |
| Approved Official Business | OB | 1, or 0.5 for a half day |
| Half-day leave or OB **and** presence | Onsite | 1 |
| Pending or rejected leave | ignored | 0 |
| Official holiday (all offices, or the employee's office) | Holiday | not counted; does **not** reduce the commitment |
| Day inside an excluded period in *days* mode | Excluded | not counted (like a holiday) |
| Saturday / Sunday | Weekend | 0, flag `WEEKEND_PRESENCE` if present |

A day never counts more than 1. Flags do not change the result. They are shown to managers and HR (orange
outline in the daily strip) so that suspicious patterns can be reviewed and disputes resolved quickly.

## 4. Weeks

Code: `domain/calendar.py`. Tests: `tests/test_calendar.py`.

- A week runs Monday–Sunday.
- **Month assignment.** A week that spans two months belongs to the month with more of its **weekdays**
  (Mon–Fri). Holidays are not removed first. Example from the manual: 30 Mar – 5 Apr 2026 belongs to April,
  even though 2–3 April are holidays.
- **Considered weeks.** A week counts only if it has at least *min working days* working days
  (weekdays minus holidays). This is **configurable**; the policy value is 3.
- The **year-boundary week** (Monday and Sunday in different years) is never counted. This reproduces the manual's
  table of 51 weeks for 2026 (Jan 4, Feb 4, Mar 4, Apr 5, May 4, Jun 4, Jul 5, Aug 4, Sep 5, Oct 4, Nov 4,
  Dec 4). It is verified by `test_2026_weeks_per_month_matches_policy_table`.
- Weeks overlapping an **excluded period** in *week* mode (for example a typhoon waiver) are not counted.

## 5. Weekly compliance

`valid days ≥ commitment` ⇒ **met**. There is no make-up mechanism: every week stands alone.

Examples from the manual (both in `tests/test_compliance.py`), with a commitment of 3 days:

- 2 days onsite + 1 day leave + 2 days WFH = 3 valid days, so the week is **met**.
- 1 day onsite + 1 day OB + 1 holiday + 2 WFH = 2 valid days, so the week is **not met**.

## 6. Monthly adherence

`adherence = weeks met / weeks considered`

The month is **passed** when adherence ≥ the *monthly pass %*. This is **configurable per department**; the
built-in default is **75%**.

> **Why 75 % and not 100 %?** Section 6.2 of the manual says "100 % adherence", but its examples in 6.6.B
> treat 75 % as *Passed*. The sample report (`Met Commitment = Y` at 75 %) confirms 75 % is applied in practice.
> HR confirms the final value under open question Q1. It is a single setting.

## 7. Annual compliance

`annual rate = months passed (after dispute) / 12`

Good standing applies when the rate ≥ the *annual pass %* (**configurable**, default 75%).
The screens also show a **year-to-date** rate (passed / evaluated months), so that a partial year is not shown
as a failure. Code: `domain/compliance.py::evaluate_year`.

## 8. Initial vs. after-dispute figures

The legacy report has both figures, and so does the tool:

| | Commitment used | Valid days used |
|---|---|---|
| **Initial** | baseline or exemption | tagged days (or imported weekly count) |
| **After dispute** | dispute correction, else as initial | plus days credited by approved disputes (capped at the working days) |

The **final** (official) figure is the after-dispute one. Week cells outlined in blue were changed by a dispute.

## 9. Thresholds and inheritance

Each setting is resolved independently along **department → department group → company default → built-in**.
For each level, the newest row with `effective_from ≤ reference date` is used. The reference date is the
Friday of the last week of the month. Example: Fraud Risk overrides only the monthly pass % (100%) and
inherits everything else. *Admin → Thresholds* shows for each unit where every value comes from.

| Setting | Built-in | Range |
|---|---|---|
| Monthly pass % | 75 | 0–100 |
| Annual pass % | 75 | 0–100 |
| Min. working days per considered week | 3 | 1–5 |
| Default commitment (days/week) | 3 | 0–5 |

Saving a threshold recomputes all **open** months from its effective date. Finalized months never change.

## 10. Month lifecycle

```
OPEN ──publish──▶ PUBLISHED (review window, default 7 days) ──finalize──▶ FINALIZED (locked)
                                         ▲                                     │
                                         └────────── reopen (HR Admin, with reason) ─┘
```

- In **Open** and **Published** months, results are recomputed after every import, approval or setting change.
- In **Finalized** months nothing is recomputed. Employees and managers cannot file requests that touch them; HR can.

## 11. Not tracked (by policy)

These requirements are monitored by managers outside the tool, as the manual specifies:

- special role requirements (more days for certain roles);
- PIP (5 days);
- probation requirements.

The manual's Monday/Friday "core days" preference is also not evaluated (Q8).
