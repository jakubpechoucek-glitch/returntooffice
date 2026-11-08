# Discovery: Work Onsite (WOS) Compliance Tracker

Status: **Draft, waiting for stakeholder answers**
Last updated: 2026-11-08

Sources used:

- *Working Arrangements Manual*, version 1 (owner: CPO)
- *Cross-functional Process Map for Onsite Report* (10 steps across People/HR, HR Analytics, IT/Systems, People Partners/BU)
- Meeting transcript with HR (Karina Fornier, Remar Evangelista). The transcript is partial: it covers 0:00–0:37 and 21:32 to the end.
- AI-generated meeting summary, "Exploratory: Automation of Work arrangement reports" (3 Nov 2026, 14:33–15:00). See §1a.

This document records what we currently understand about the domain, the rules as we read them, contradictions in the source material, and open questions. The answers will be written back into this file (or into ADRs) so they are not lost.

---

## 1. Problem statement

HR Analytics builds a **Monthly Onsite Report** by hand. They extract data from several systems (door access/DACS, HC Connect leave and OB, timekeeping, Splunk Wi-Fi, the DWH employee masterlist), merge it, tag each day, compute compliance, and roll the results up by employee, department and BU. People Partners then cut the report up per department and send it to leaders. Disputes and exemptions arrive by e-mail or spreadsheet and get copied in by hand.

Pain points raised in the meeting:

1. **Commitment capture.** An employee's onsite commitment (days per week) changes over time. There is no single source of truth with history.
2. **Exemptions are filed monthly.** HR wants **weekly** exemption filing, because many exceptions apply only to a specific week.
3. **Disputes have no workflow.** HR wants an in-tool dispute with approvals: direct manager, then next-level manager, then People Partner review. The decision should flow straight into the report.
4. **Manual distribution.** People Partners spend time slicing the report per department. HR considered Power BI row-level security. They want the web tool to decide **who sees what**.
5. **Scale.** Many employees may sign in at once, for example right after the report is published or during a dispute window. The MVP/POC can be lighter, but the design must allow IT to harden it.
6. **Managers lack visibility.** Department leadership does not always cascade the report down. Every people manager should see their direct reports and, where applicable, all teams below them, and nothing outside their own line.

## 1a. Facts from the 3 Nov 2026 meeting summary

### Current process

- **Door logs** come from the security database (DACS). **Timekeeping** and **OB** come from HC Connect, integrated into the DWH. **Leave** records also come from HC Connect.
- **Commitments and exceptions** are kept in manually maintained files, because HC Connect cannot store them. This is the main weakness of the current process.
- HR Analytics runs scripts that pull updated timekeeping and OB data. They then consolidate the datasets, tag each day, compute onsite days against committed days, and roll the results up by employee, department, BU and leadership level.
- **Review window.** After the initial report is released, BUs and managers have about **one week** to review it and raise disputes. Most disputes happen because a commitment was not updated before publication. People Partners discuss each disputed case with the manager and resolve it. The report is then finalized for ExCo.
- **Collections** runs its own dispute process and sends HR its final figures before the report is finalized. Collections already tracks commitments and exceptions **weekly**.
- Door-log and timekeeping integrations sometimes produce missing or wrong data, which needs IT support to fix.

### Scale

- About **400** potential exception filers could use the tool at the same time.
- About **900–1,000** people managers in the organization.

### Policy stability

- The baseline policy is stable and is being routed for approval. No routine changes are expected.
- One-off **waivers** do happen, for example severe weather or an earlier December holiday-period decision. Today HR Analytics handles them by manually excluding specific weeks.
- **Implication:** the tool needs an HR-managed "excluded period" feature, with company-wide or per-office/department scope, a reason and an audit trail. This partly answers **C6**.

### Requested workflow

- Employees file exemptions and disputes themselves, the way they file leave or OB today.
- Filing is **per specific week**, not for a whole month.
- **Approval chain.** An employee or manager submits a commitment change, exemption or dispute. The (next-level) manager approves it, and the People Partner reviews it. The outcome is stored in the same system, so nothing is copied between files.

### MVP / POC scope

- Automate process-map steps **4–9**: consolidation, onsite tagging, computation, aggregation, validation/exceptions and finalization.
- Pilot with **1–2 departments**. At least one should be structurally complex, with several org levels and common exception scenarios. **Collections is excluded.**
- The pilot must demonstrate **hierarchical permissions**: lower-level managers see only their own team, and higher-level managers see all subordinate teams.
- **Input data package** (Remar): about one month of raw data for the pilot departments, examples of exceptions and disputes, and manager reporting lines.
- **Policy document** (Karina): the latest version.
- **Data protection.** Barb's sign-off is required before anything is shared. Data will be masked: medical reasons reduced to a generic "Medical" label, and identifying details replaced with fictional values.
- **Cadence.** The tool supports weekly reporting if HR can supply weekly raw data. In the MVP, HR uploads the files by hand. Scheduled integrations come later.

## 2. Domain rules as currently understood

### 2.1 Scope

- In scope: non-mass employees in NCR offices (Ore Central, Three NEO, Spark, Vertis) and local offices, plus select roles defined by ExCo.
- Out of scope: field-based, site-specific and mass-operations roles. Maternity and prolonged leave of absence are also out of scope.

### 2.2 Required onsite days per week (the "commitment")

| Population | Days/week |
|---|---|
| ExCo | 5 |
| ExCo-1 (functional heads, direct reports of ExCo) | 4 |
| All other non-mass employees | 3 (ideally including Monday or Friday) |
| Approved exemption | < 3, as approved by ExCo |
| Special role requirements (PIP, probation, BU direction) | > minimum, **not tracked** in the report (manager responsibility) |

### 2.3 What counts as an onsite day

- A physical day in the office with time-in and time-out entries
- Approved leave (Wellness, Birthday, Bereavement, Paternity, LWOP, other government leaves)
- Approved Official Business (OB)
- Official holidays (Legal, Special Non-Working, Regional) do **not** count, and they do **not** reduce the commitment. If Friday is a holiday, the employee still needs 3 of the remaining 4 days.

### 2.4 Weekly compliance

- A week runs from Monday to Sunday.
- Only weeks with **3 or more working days** are considered.
- A week is **met** when valid onsite days ≥ commitment days. There is no make-up of missed days in later weeks.

### 2.5 Week-to-month assignment

- A week that spans two months belongs to the month that holds more of its working days. Example: the week of 30 Mar – 5 Apr 2026 belongs to April.
- Only 51 weeks per year are considered. The last week of the year is excluded.
- Check: assigning each Mon–Fri week by majority working days reproduces the manual's 2026 table (4,4,4,5,4,4,5,4,5,4,4,**4**) only after the year-end week (28 Dec 2026 – 3 Jan 2027) is excluded. Without that exclusion December would have 5 weeks.

### 2.6 Monthly adherence

`weeks met / weeks considered in the month`

### 2.7 Annual compliance rate

`months adhering / 12`. A rate of ≥ 75% means good standing.

### 2.8 Exemptions (current process)

Function Head submits by month-end, ExCo approves, and HR receives the list. Late entries are not reflected. The report is generated in the first week of the following month.

## 3. Contradictions in the manual that need a decision

| # | Where | Conflict |
|---|---|---|
| C1 | §6.2 vs §6.6.B | §6.2 says monthly adherence "should be at **100%**". The §6.6.B examples treat **75% as Passed** and 60% as Failed. What is the monthly pass threshold? |
| C2 | §6.6.C vs §6.7 | §6.6.C says annual compliance ≥ **75%** is good standing. §6.7 says failing the **100%** annual threshold hurts remuneration. Which threshold drives the consequence? |
| C3 | §3 vs §6.2 | §3 lists only ExCo (5) and others (3). §6.2 adds ExCo-1 (4). We assume §6.2 is correct. |
| C4 | §6.6.A | "The 52nd week … has only 2 working days." In 2026 the last week (28 Dec – 1 Jan) has 4 weekday working days, or 3 if 1 Jan is a holiday. Is the rule "always drop the final week of the year" or "drop weeks with < 3 working days"? |
| C5 | §6.6.B (month rule) vs §6.6.C | If monthly adherence must be 100%, a single missed week fails the month. Please confirm the intended strictness. |
| C6 | §7.3 | "Enter decision on exemptions for typhoon/medical reasons" reads like a placeholder. *Partly answered (§1a):* typhoon and similar waivers are one-off decisions that exclude specific weeks. Still open: is the whole week excluded, or only the affected days, which would then be treated like holidays? |
| C7 | Meeting | The transcript has Remar naming the **direct manager** as approver. The AI summary has Karina naming the **next-level manager**. Which is it, or are both required? |

## 4. Proposed architecture (for discussion)

The goal is a prototype that IT can harden without rewriting it.

- **Frontend:** React + TypeScript SPA.
- **Backend (Python, confirmed):** a stateless FastAPI REST API (OpenAPI-documented) plus a background worker for ingestion and compute jobs. The compute engine is a pure, unit-tested library so the policy rules are auditable.
- **Database:** PostgreSQL, with Alembic migrations. Policy parameters (per-department thresholds, commitments, holiday calendars, excluded periods) live in data, not code, and carry effective dating.
- **AuthN (Entra ID, confirmed):** OpenID Connect / OAuth 2.0 with Microsoft Entra ID. Local dev uses a mock OIDC provider (Keycloak).
- **AuthZ:** RBAC plus data scoping by org hierarchy. This replaces Power BI RLS. The hierarchy is maintained in the admin UI.
- **Infra (Kubernetes, confirmed):** Docker images, a Helm chart, horizontally scalable API pods, Redis for caching and job queue, structured logs, health/readiness probes and Prometheus metrics. Docker Compose is used for local dev. CI runs tests, lint and container builds.
- **Audit:** an immutable audit log for every manual change: commitments, exemptions, dispute decisions, overrides.
- **Data ingestion:** for the MVP, CSV/XLSX uploads of the same extracts HR uses today. Later, scheduled connectors to the DWH, DACS, HC Connect and Splunk.

## 5. Open questions

The questions still open for HR are in [`open-questions-hr.md`](open-questions-hr.md), written so HR can answer them directly. The technical questions (stack, identity provider, scale) are answered; see §6.

---

## 6. Decisions log

| Date | Decision | By |
|---|---|---|
| 2026-11-03 | MVP scope = process steps 4–9 plus self-service filing workflows | Meeting |
| 2026-11-03 | Pilot on 1–2 departments, at least one structurally complex; Collections excluded | Meeting |
| 2026-11-03 | Visibility is controlled by the web tool based on the org hierarchy (instead of Power BI RLS) | Meeting |
| 2026-11-03 | Exemptions and commitment changes are recorded per week, not per month | Meeting |
| 2026-11-03 | Pilot data is masked: medical reasons become a generic "Medical" label, identities become fictional | Meeting |
| 2026-11-08 | **Compliance thresholds are configurable per department.** Weekly commitment default, monthly adherence pass %, annual compliance pass % and minimum working days per considered week. Resolution order: department override → BU → company default. All values are effective-dated and audited. | Jakub |
| 2026-11-08 | **Admin UI to maintain managers and the org hierarchy.** Admins can assign or change a manager, move employees or departments, and set People Partner assignments. All changes are effective-dated and audited. Imports from the masterlist can overwrite them or be reviewed first. | Jakub |
| 2026-11-08 | Target runtime: **Kubernetes** (containers, Helm chart) | Jakub |
| 2026-11-08 | Backend language: **Python** | Jakub |
| 2026-11-08 | Identity provider: **Microsoft Entra ID** (OIDC / OAuth 2.0). Local dev uses a mock OIDC provider. | Jakub |
| 2026-11-08 | Scale: **thousands of employees, hundreds of managers**. Design target is 10,000 employees and 1,000 concurrent users. | Jakub |
| 2026-11-08 | Open policy and process questions go to HR (`open-questions-hr.md`). Until they are answered, the documented defaults apply and stay configurable where possible. | Jakub |
| 2026-11-08 | Implementation starts once the dummy data file arrives. The importer is built against its exact layout. | Jakub |
