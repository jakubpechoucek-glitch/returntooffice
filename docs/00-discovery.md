# Discovery: Work Onsite (WOS) Compliance Tracker

Status: **Draft, waiting for stakeholder answers**
Last updated: 2026-11-08

Sources used:

- *Working Arrangements Manual*, version 1 (owner: CPO)
- *Cross-functional Process Map for Onsite Report* (10 steps across People/HR, HR Analytics, IT/Systems, People Partners/BU)
- Meeting transcript with HR (Karina Fornier, Remar Evangelista). The transcript is partial: it covers 0:00–0:37 and 21:32 to the end.

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
| C6 | §7.3 | "Enter decision on exemptions for typhoon/medical reasons" reads like a placeholder. Is there a rule for typhoon or office-closure days, for example treating them like a holiday? |

## 4. Proposed architecture (for discussion)

The goal is a prototype that IT can harden without rewriting it.

- **Frontend:** React + TypeScript SPA.
- **Backend:** a stateless REST API (OpenAPI-documented) plus a background worker for ingestion and compute jobs. The compute engine is a pure, unit-tested library so the policy rules are auditable.
- **Database:** PostgreSQL, with versioned migrations. Policy parameters (thresholds, commitments, holiday calendars) live in data, not code, and carry effective dating.
- **AuthN:** OpenID Connect / OAuth 2.0. We assume Microsoft Entra ID, given SharePoint and Power BI in the landscape. Local dev uses a mock IdP (Keycloak).
- **AuthZ:** RBAC plus data scoping by org hierarchy. This replaces Power BI RLS.
- **Infra:** Docker images, a Kubernetes/Helm-ready layout, Redis for caching and job queue, structured logs, health checks and metrics. CI runs tests, lint and container builds.
- **Audit:** an immutable audit log for every manual change: commitments, exemptions, dispute decisions, overrides.
- **Data ingestion:** for the MVP, CSV/XLSX uploads of the same extracts HR uses today. Later, scheduled connectors to the DWH, DACS, HC Connect and Splunk.

## 5. Open questions

The proposed default is in *italics*. If the default is fine, a "yes" is enough.

### A. Policy and calculation

1. **C1/C2:** What are the monthly and annual pass thresholds? *Default: they are configurable; ship with monthly = 100% and annual = 75%.*
2. What is a "working day" when counting the ≥3-day rule and assigning weeks to months? *Default: Mon–Fri minus official holidays.* Do any in-scope employees have Saturday shifts?
3. Regional holidays apply only to some offices. Do we keep holiday calendars per office location, and does each employee have a "designated office"? *Default: yes.*
4. What is the minimum presence for a valid onsite day? Options: any tap-in, tap-in plus tap-out, or N hours. §6.2 says partial attendance should not routinely bypass the policy. *Default: a tap-in and a tap-out on the same day, at any company office. Short days are flagged for review but still counted.*
5. Is the Monday/Friday "core day" a tracked KPI or informational only? *Default: informational indicator only.*
6. How are half-day leave and half-day OB counted? *Default: a half-day leave plus a half-day onsite = 1 valid day; a half-day leave alone = 0.5.*
7. If a commitment or exemption changes mid-week, which value applies to that week? *Default: the value in effect on the week's Monday.*
8. How are joiners, leavers and transfers handled mid-month? *Default: weeks before the hire date or after the termination date are not considered; a transfer moves visibility from the transfer date onward.*
9. Should the tool optionally track special requirements (PIP, probation) for managers, even though they stay out of the official report? *Default: not in the MVP.*

### B. Data sources

10. Can you share sample (dummy) extracts **in the exact column layout** of the masterlist, DACS door logs, HC Connect leave/OB, timekeeping and Splunk Wi-Fi? Until they arrive, I will generate synthetic data in a format I propose.
11. When sources conflict, which one wins? For example, Wi-Fi present but no door tap, or a door tap but a leave filed. *Default: door access > timekeeping > Splunk Wi-Fi for physical presence. An approved leave or OB counts as valid regardless. Conflicts are flagged.*
12. What identifies an employee across all systems (employee ID, badge ID, AD account / UPN)? Is there a mapping table?
13. Does the masterlist contain the manager hierarchy (direct manager, next-level manager), department, BU, People Partner, and ExCo / ExCo-1 flags?

### C. Workflows

14. **Commitments:** who records an employee's commitment (manager, PP, HR), and does it need approval? *Default: the manager proposes it, and the PP or HR approves it, with effective dates.*
15. **Exemptions:** who files them now that filing will be weekly: the employee, the manager or the Function Head? What is the approval chain? *Default: employee or manager files → Function Head → ExCo approves → HR is notified.* Is there still a cutoff? *Default: the 5th working day of the following month.*
16. **Disputes:** is the chain direct manager → next-level manager → People Partner review → HR Analytics applies the change? Do we need attachments, a filing window (for example 10 days after the report is published) and SLAs or auto-escalation?
17. Can an employee dispute a single day, a whole week, or both? *Default: a specific day, with a reason code.*
18. Notifications: e-mail, Microsoft Teams, or both? *Default: e-mail in the MVP and Teams later.*

### D. Access and visibility

19. Please confirm the roles. *Default:* **Employee** (sees own data), **Manager** (direct and indirect reports), **People Partner** (their assigned BUs/departments), **ExCo / Function Head** (their division), **HR Analytics** (everything, plus data operations), **HR Admin / Policy owner** (configures rules), **Auditor** (read-only).
20. Should employees see their own daily raw data (door taps, Wi-Fi), or only the derived status? Splunk Wi-Fi data is privacy-sensitive under the Philippine Data Privacy Act. *Default: employees see derived day status and its source type, not raw Wi-Fi logs.*
21. Data retention period for raw logs and for computed results?

### E. Non-functional and enterprise

22. How many employees are in scope, and what peak concurrent usage do you expect (for example, all of them on report day)?
23. Where will IT host it: Azure, on-prem Kubernetes, AWS? What is their preferred backend language: .NET, Java, Python, Node? This matters most for handover. *Default: Python (FastAPI) or TypeScript (NestJS) in containers, with Azure-friendly choices.*
24. Is Microsoft Entra ID the corporate IdP? Can we register an app for the prototype, or should we use a mock IdP until hardening?
25. Is Power BI still needed as a consumer? *Default: expose a read-only reporting schema/view in PostgreSQL and CSV/XLSX export so Power BI can connect.*
26. Where should the **POC run for the demos** (for example the Tuesday follow-up)? A paid cloud host with a managed PostgreSQL is fine for synthetic data, but **no real employee data** should go there.

---

## 6. Decisions log

| Date | Decision | By |
|---|---|---|
| | *(to be filled after answers)* | |
