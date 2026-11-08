# Open Questions for HR — Onsite Compliance Tool

**Audience:** People/HR, HR Analytics, People Partners
**Purpose:** We need decisions on these points to configure the onsite compliance tool correctly.
**How to answer:** each question has a **proposed default**. Leave the answer empty if the default is fine. Otherwise write your decision in the *HR answer* line. Until a question is answered, the tool uses the default. Most values are configurable later, and **thresholds can be set per department**.

Status legend: ⬜ open · ✅ answered

---

## A. Policy interpretation

### Q1 ⬜ Starting thresholds
The manual says monthly adherence "should be **100%**", but its examples treat **75%** as *Passed*. The annual rule says ≥ **75%** means good standing, but the consequence section mentions a **100%** annual threshold.
Thresholds will be configurable **per department**. What should the company-wide defaults be?
- *Proposed default:* monthly adherence pass = **75%**, annual compliance pass = **75%**
- *Evidence:* in the sample report (Jan/Mar 2026), "Met Commitment = Y" is given at 75% adherence, so 75% is what is applied today.
- **HR answer:**

### Q2 ⬜ ExCo-1 commitment
Section 3 lists ExCo = 5 days and everyone else = 3 days. Section 6.2 adds ExCo-1 = 4 days.
- *Proposed default:* ExCo 5, ExCo-1 4, others 3
- **HR answer:**

### Q3 ⬜ Last week of the year
The manual says the last week of the year is excluded because "it has only 2 working days". In 2026 that week (28 Dec – 3 Jan) has 3–4 working days.
- *Proposed default:* the final week of each year is **always excluded** (this matches the manual's 2026 week table)
- **HR answer:**

### Q4 ⬜ Typhoon / one-off waivers
One-off waivers (typhoon, December holiday-period decisions) exclude specific weeks. Is the **whole week** removed from the calculation, or only the **affected days**, which would then be treated like holidays?
- *Proposed default:* HR can choose either one when entering the waiver, and the scope can be the whole company, specific offices or specific departments
- **HR answer:**

### Q5 ⬜ What is a "working day"?
This affects the "≥ 3 working days" rule and how a week is assigned to a month.
- *Proposed default:* Monday–Friday minus official holidays. Are there in-scope employees with Saturday shifts?
- **HR answer:**

### Q6 ⬜ Regional holidays
- *Proposed default:* each employee has a designated office, and regional holidays apply only to that office
- **HR answer:**

### Q7 ⬜ Valid onsite day: minimum presence
- *Proposed default:* a tap-in **and** a tap-out on the same day, at any company office. Days shorter than N hours are **flagged** for manager attention but still counted.
- **HR answer** (and the value of N, if any):

### Q8 ⬜ Monday/Friday core day
- *Proposed default:* shown as an informational indicator only; it does not affect compliance
- **HR answer:**

### Q9 ⬜ Half-day leave / half-day OB
- *Proposed default:* a half-day leave or OB plus a half day onsite = 1 valid day. A half-day leave alone = 0.5 day.
- **HR answer:**

### Q10 ⬜ Commitment or exemption changes during a week
- *Proposed default:* the value in effect on the **Monday** of that week applies to the whole week
- **HR answer:**

### Q11 ⬜ Joiners, leavers, transfers
- *Proposed default:* weeks before the hire date or after the last day are not counted. After a transfer, the new manager sees the employee from the transfer date; earlier history stays with the previous team.
- **HR answer:**

### Q12 ⬜ Special requirements (PIP, probation, BU-specific)
The manual says these are not tracked in the report. Should the tool still let managers record them for their own monitoring?
- *Proposed default:* not in the pilot
- **HR answer:**

## B. Data

### Q13 ⬜ Conflicting sources
Which source wins when they disagree, for example Wi-Fi presence without a door tap, or a door tap on a day with an approved leave?
- *Proposed default:* door access > timekeeping > Splunk Wi-Fi for physical presence. An approved leave or OB is always valid. Conflicts are flagged for review.
- **HR answer:**

### Q14 ⬜ Splunk Wi-Fi data
Is it part of the pilot data package?
- **HR answer:**

### Q15 ⬜ Employee identifier
Which ID links the masterlist, door access (badge), HC Connect, timekeeping and Splunk? Is there a mapping table?
- **HR answer:**

### Q16 ⬜ Masterlist content
Does the masterlist include the direct manager, department, BU, People Partner, office location and ExCo / ExCo-1 flags?
- **HR answer:**

### Q17 ⬜ Data retention
How long should raw logs (door, Wi-Fi) and computed results be kept?
- *Proposed default:* raw logs for 13 months, computed results for 5 years
- **HR answer:**

## C. Workflows

### Q18 ⬜ Approval chain
In the meeting, the approver for disputes, exemptions and commitment changes was named both as the **direct manager** and as the **next-level manager**. Which is it?
- *Proposed default:* direct manager approves → People Partner reviews. If the submitter *is* the manager, the next-level manager approves.
- **HR answer:**

### Q19 ⬜ Who can submit what
- *Proposed default:*
  - **Exemption** (specific week): the employee or their manager submits.
  - **Commitment change:** the manager submits.
  - **Dispute** (specific day): the employee or their manager submits.
- **HR answer:**

### Q20 ⬜ Is ExCo approval still needed for exemptions?
The manual requires ExCo approval of the department exemption list.
- *Proposed default:* manager → People Partner, plus an ExCo approval step only for exemptions longer than N weeks
- **HR answer:**

### Q21 ⬜ Deadlines and the review window
- *Proposed default:*
  - Exemptions for a week can be filed until the report for that month is published.
  - Disputes can be filed for **7 calendar days** after the initial report.
  - HR Analytics then **finalizes and locks** the month. Changes after that are HR overrides with a reason.
- **HR answer:**

### Q22 ⬜ Attachments and reminders
- *Proposed default:* optional attachments (medical documents must not be uploaded, only the "Medical" category). Approvers get reminder e-mails, with no automatic escalation.
- **HR answer:**

### Q23 ⬜ Notifications channel
- *Proposed default:* e-mail in the pilot, Microsoft Teams later
- **HR answer:**

## D. Access and visibility

### Q24 ⬜ Roles
- *Proposed default:*
  - **Employee:** own data
  - **Manager:** direct and all indirect reports
  - **People Partner:** assigned departments/BUs
  - **ExCo / Function Head:** their division
  - **HR Analytics:** everything, plus data uploads and finalization
  - **HR Admin:** thresholds, policy settings, managers and hierarchy
  - **Auditor:** read-only
- **HR answer:**

### Q25 ⬜ What employees see about themselves
- *Proposed default:* each day's status (onsite / leave / OB / holiday / not onsite) and the type of source, but **not** raw Wi-Fi logs
- **HR answer:**

### Q26 ⬜ Power BI
- *Proposed default:* the tool provides an export and a read-only reporting view that Power BI can connect to. No Power BI is needed for daily use.
- **HR answer:**

## E. Pilot

### Q27 ⬜ Pilot departments
Which 1–2 departments, and roughly how many people and org levels do they have?
- **HR answer:**

### Q28 ⬜ Employee self-service in the pilot
- *Proposed default:* yes, all roles take part in the pilot, limited to the pilot departments
- **HR answer:**

### Q29 ⬜ Collections
- *Proposed default:* out of scope for the pilot. Later, Collections' final figures could be imported so the company report is complete.
- **HR answer:**

## F. Findings from the sample data

### Q30 ⬜ "CEO 2" column
The report has a "CEO 2" leader for each row. Is this the ExCo-1 head (CEO-2 level) of the reporting chain? Should that person automatically get an *Executive* view of their organisation?
- *Proposed default:* imported as a person only (no automatic role); Executive roles are granted in Admin → Roles
- **HR answer:**

### Q31 ⬜ Disputes sheet values
The `COLLATED_DISPUTES` sheet in the sample is empty. Please share a few masked example rows, so the TYPE / SUBTYPE / CATEGORY values can be mapped correctly. Example question: is TYPE "Exemption" vs "Dispute"?
- **HR answer:**

### Q32 ⬜ Months with five weeks in the legacy layout
The legacy file has 4 weekly columns, but April, July and September 2026 have 5 considered weeks. How is the 5th week reported today?
- *Proposed default:* not needed going forward, because the tool computes all weeks from daily data
- **HR answer:**

### Q33 ⬜ Real extract layouts
For the next iteration, please share masked samples of the **raw** extracts (DACS door log, HC Connect leave/OB, timekeeping, Splunk Wi-Fi, DWH masterlist). The tool currently accepts canonical CSV layouts (`docs/data-import.md`); small adapters will map the real ones.
- **HR answer:**
