# Data import

Uploads are made under *Admin → Imports* (roles HR_ADMIN, HR_ANALYTICS) or with the CLI
(`rto import KIND FILE`). They can also be sent to the API (`POST /api/v1/admin/imports/{kind}`).

General behaviour:

- **All-or-nothing.** CSV files are validated completely first. With any error, nothing is stored and up to 50
  errors are listed with their line numbers.
- **Idempotent.** Re-importing data for the same employee, date and source replaces the earlier rows.
- After an import, the affected days are tagged and the affected **open** months recomputed.
- Every import is logged as an `import_batch`, with a summary, and in the audit log.

Recommended order for a new period: **masterlist → absences → presence**.

## 1. Legacy Monthly Onsite Report (`onsite_report`, .xlsx)

This is the workbook HR Analytics produces today, the format of `sample_data/onsite_report_2026_jan_mar_anonymized.xlsx`.

- Sheet **`DATA`**: one row per employee and month. Columns are matched by name, ignoring case and spacing:

  | Column | Use |
  |---|---|
  | `MONTH` | Reporting month (any date in the month) |
  | `DEPARTMENT GROUP`, `DEPARTMENT NAME` | Org units (group → department) |
  | `EMPLOYEE NUMBER`, `EMPLOYEE EMAIL`, `FIRST NAME`, `LAST NAME`, `POSITION`, `STATUS`, `SITE`, `ARRANGEMENT`, `HIRED DATE` | Employee master data |
  | `MANAGER`, `MANAGER EMAIL` | Reporting line, effective from the month's first week |
  | `CEO 2`, `CEO 2 EMAIL` | Leader. Created as a non-tracked person if unknown |
  | `PEOPLE LEVEL`, `CEO LEVEL` (`CEO-5` → 5) | Level |
  | `LOCATION` | Office: drives regional holidays |
  | `COMMITTED DAYS PER WEEK` | Baseline commitment, effective from the month's first week |
  | `W1 …`, `W2 …` (any header starting with `W<n>` and without "dispute") | Valid days per considered week, in order |
  | `W<n> + dispute …` | After-dispute weekly count (optional) |
  | `COMMITMENT PER WEEK WITH DISPUTE` | After-dispute commitment for the month (optional) |
  | `MONTHLY ADHERENCE`, `MET COMMITMENT`, `… AFTER DISPUTE`, `MET_COMMITMENT WITH DISPUTE` | **Used only for reconciliation** |

- Sheet **`COLLATED_DISPUTES`** (optional):
  `IMMEDIATE_MANAGER, EMP_NAME, EMP_NO, TYPE, SUBTYPE, CATEGORY, COUNT_COMMIT, START_DATE, END_DATE`.
  Each row becomes an already-approved request. If `TYPE` contains "exempt" or "except", it is an exemption; otherwise it is a dispute.
  `COUNT_COMMIT` is the commitment for the period.

**Reconciliation.** After the import, the computed monthly adherence and the *Met Commitment* flag (initial and after
dispute) are compared with the workbook. Differences are listed per employee and month. Result for the sample file:
**26 of 26 rows match**.

Weeks that already have daily data are never overwritten by a legacy import.

## 2. Masterlist / hierarchy (`masterlist`, .csv, UTF-8)

```
employee_number,email,first_name,last_name,position,people_level,ceo_level,department_group,department,
manager_email,location,hired_date,termination_date,status,committed_days,effective_from
```

- Required columns: `employee_number`, `email`, `department` and `manager_email` (it may be empty for the top of the tree).
- Managers may appear anywhere in the file. The file is loaded in two passes.
- `effective_from` (ISO date, default today) dates the reporting-line and commitment changes.
- People missing from the file are **not** deleted. Set `termination_date` instead.

## 3. Presence events (`presence`, .csv)

```
employee_number,date,time,source,direction,site
900001,2026-03-02,08:31,DOOR,IN,Spark
900001,2026-03-02,18:02,DOOR,OUT,Spark
900001,2026-03-03,,WIFI,PRESENT,Spark
```

| Column | Values |
|---|---|
| `source` | `DOOR` (DACS), `TIMEKEEPING`, `WIFI` (Splunk) |
| `direction` | `IN` / `OUT` for door and timekeeping, `PRESENT` for Wi-Fi |
| `time` | Optional, `HH:MM[:SS]` |

## 4. Leave and Official Business (`absences`, .csv)

```
employee_number,date,kind,subtype,status,portion
900001,2026-03-06,LEAVE,Wellness,APPROVED,1
900001,2026-03-09,OB,Client visit,APPROVED,0.5
```

| Column | Values |
|---|---|
| `kind` | `LEAVE` or `OB` |
| `subtype` | Free text, for example the leave type. **Do not export medical details** |
| `status` | Only `APPROVED` counts |
| `portion` | `1` or `0.5` |

One row per day: multi-day leave must be expanded to days by the extract.

## Mapping real extracts

The real DACS, HC Connect, timekeeping and Splunk extracts will have their own layouts. Add a thin adapter per
source that maps them to the canonical columns above. This is a small function in `importers/`; the tagging and
computation do not change. Later, a scheduled job (Kubernetes CronJob) can pull the same data directly from the
DWH.

## Findings on the anonymized sample (Jan–Mar 2026)

1. The file holds **13 Collections employees** for **January and March**. There are no February rows: the "dispute"
   headers mention February weeks but the values are `#N/A`. `COLLATED_DISPUTES` is empty.
2. All 13 have a commitment of 2 days (`COMMITMENT GROUP` = "< 3 days/week"): an exempted population.
3. `Met Commitment = Y` at **75%** monthly adherence, so the monthly pass threshold in practice is 75% (see Q1).
4. The file has **four** weekly columns. 2026 has months with five considered weeks (April, July, September).
   The legacy layout cannot hold the fifth week; the importer warns about this. Daily data has no such limit.
5. `CEO 2` appears to be the leader at CEO-2 level (ExCo-1) in the reporting chain. To be confirmed.
6. The meeting excluded Collections from the pilot, but the sample is Collections. The demo therefore adds a
   synthetic, structurally deeper department ("Risk Management") with daily data to exercise the full pipeline.
