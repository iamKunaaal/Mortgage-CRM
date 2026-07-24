# Bitar Mortgage CRM — Complete Walkthrough & User Manual

**Purpose:** This document explains, step by step, exactly how the CRM works — from the moment a lead arrives to the moment the loan is disbursed, plus every supporting feature. It is written so that anyone can follow along on screen (login here, click this, fill that, then the next step) and understand the whole system without needing to ask any questions.

**How to use this document:** Keep the CRM open in one window and this document in another. Follow the steps in order. Each step says *who logs in*, *what to click*, *what to type*, and *what you should see and understand*.

---

## 0. Before You Start

### 0.1 Opening the CRM
- Open your browser and go to: **http://127.0.0.1:8000** (local) — for the live version use the deployed URL.
- You will see the **Login** screen.

### 0.2 Test Logins (username / password)
Use these to try each role. In production these are replaced by real staff accounts.

| Role | Username | Password | What they do |
|---|---|---|---|
| CEO / Managing Director | `ceo` | `ceo123` | Sees everything; final authority; only one who can permanently delete |
| Sales Director | `director` | `test123` | Runs the sales team; sees all leads; approves partners |
| Team Leader | `teamlead` | `tl123` | Sees only their own team's cases |
| Mortgage Advisor | `advisor` | `adv123` | Owns their own leads end to end |
| Mortgage Advisor (2nd) | `advisor2` | `adv123` | Second advisor (for testing team/handover) |
| Operations Manager | `opsmgr` | `test123` | Runs processing; assigns files; manages banks |
| Compliance Officer | `compl` | `test123` | KYC, screening, audit |
| Accountant | `acct` | `test123` | Revenue, disbursals, invoices |
| HR Executive | `hr` | `test123` | Staff management |
| Marketing | `mktg` | `mktg123` | Lead sources and campaigns |
| External Auditor | `auditor` | `test123` | Read-only + audit trail |

### 0.3 How to log out and switch roles
Bottom-left of the sidebar shows your profile card (initials + name + role). Click it → you are signed out → log in as the next role. You will switch roles many times during this walkthrough.

### 0.4 The sidebar (left menu)
The menu changes based on your role — each person only sees what they are allowed to use. Common items: Dashboard, My Day, Leads, Ops Queue, Compliance, Approvals, Tasks, Banks, Advisors, Referral Partners, Documents, Finance, Reports, Notifications, Audit Log, Recycle Bin, Settings.

---

## PART 1 — THE FULL LEAD LIFECYCLE (Lead → Disbursement)

We will follow one customer, **"Test Kumar"**, from first enquiry to a disbursed loan. Do these steps in order.

---

### STEP 1 — Advisor creates the lead

**Log in as:** `advisor` / `adv123`

1. In the left sidebar, click **Leads**.
2. Top-right, click **Create Lead**. A step-by-step wizard opens.
3. Fill in the details (use this test data):
   - **Name:** Test Kumar
   - **Mobile:** 0505551234
   - **Email:** test@kumar.com
   - **Nationality:** India
   - **Employment type:** Salaried
   - **Monthly income:** 30000
   - **Property value:** 2000000
   - **Loan amount:** 1500000
4. Continue to the **Consent** step. Tick **Call** and **WhatsApp** (this records that the customer allowed us to contact them on those channels).
5. The **Documents** step can be skipped for now (we upload later).
6. Click **Create Lead**.

**What you should see and understand:**
- The lead is created and **automatically assigned to you (the advisor)**.
- On the lead's detail page (right side panel) you will see:
  - **Case number** — e.g. `BHITR-2026-0012` (a unique reference for this case).
  - **Lead Score** — a number from 0–100 showing how "hot"/high-priority the lead is (higher = attend first).
  - **Eligibility** — a Green or Red banner telling you instantly whether the loan is bankable (based on LTV, DBR, income multiple, cash-to-close).
  - **Consent** — the channels the customer allowed.
- A **first-contact SLA timer** has started (default 15 minutes in working hours) — this measures how quickly the advisor makes the first call.

> **Duplicate protection:** If you try to create another lead with the same mobile or email, the system warns/blocks it so the same customer is not entered twice.

---

### STEP 2 — Advisor contacts the customer and logs a follow-up

**Log in as:** `advisor` / `adv123` (same advisor)

#### 2a. Check "My Day"
1. Sidebar → **My Day**.
2. You will see:
   - **Speed-to-lead — contact now:** your new lead appears here with its SLA timer (because you haven't contacted it yet).
   - **My Tasks:** your open tasks, most urgent first.
   - **Follow-ups due:** any follow-ups scheduled for today.
- *Understand:* My Day is the advisor's daily to-do list, ordered by urgency (SLA risk → overdue → today → priority).

#### 2b. Log a follow-up
1. Open the lead (from My Day or Leads).
2. Click the **Follow-ups** tab → **Log Follow-up**.
3. Fill in:
   - **Channel:** Call
   - **Note:** "Customer interested, requested document list"
   - **Next follow-up date:** tomorrow's date
4. Click **Save Follow-up**.

**What you should see and understand:**
- The follow-up appears in the log (who, when, channel, note).
- The **SLA timer stops** — first contact is recorded, so the lead leaves the "contact now" list.
- Because you set a next-date, a **task is created automatically** for that day (so nothing is forgotten).

#### 2c. (Optional) Nurture a "not now" customer
- At the top of the lead detail there is a **🌱 Nurture** button. If the customer says "call me in 2 months," click it and set a date. On that date the system automatically re-activates the lead and creates a task. This keeps future business from being lost.

---

### STEP 3 — Documents (Advisor uploads, Operations verifies)

#### 3a. Advisor uploads documents
**Log in as:** `advisor` / `adv123`
1. Open the lead → **Documents** tab.
2. Click **Add Documents**.
3. For each document: type a name (e.g. "Emirates ID"), choose a type, choose a file, optionally set an expiry date → **Upload**.
4. Add 2–3 documents (e.g. Emirates ID, Salary Certificate, Bank Statement).

- *Understand:* The advisor can only **upload**. Documents start as "Pending Review." The advisor cannot verify their own documents (this is a control).

#### 3b. Operations verifies or rejects
**Log in as:** `opsmgr` / `test123`
1. Sidebar → **Documents** (the All Documents page — shows documents across all leads).
2. On a document row, click the **⋮ (three dots)** menu → **Verify**.
3. To test rejection, on another document click **⋮ → Reject** and type a reason (e.g. "Statement is older than 3 months").

**What you should see and understand:**
- **Verify** → status turns to "Verified" (green).
- **Reject (with reason)** → the advisor gets a notification and an automatic "re-upload" task; the case is flagged for **rework** until the document is fixed.
- Every verify/reject is time-stamped with who did it (this is audit evidence).

---

### STEP 4 — KYC & Compliance (the safety checkpoint)

This is the most important control. No file can go to a bank until Compliance approves it.

**Log in as:** `compl` / `test123` (Compliance Officer)

#### 4a. Open the Compliance workspace
1. Sidebar → **Compliance**.
2. You will see:
   - **KYC / Screening Queue** — cases waiting for KYC (your test lead is here).
   - **High-Risk Cases (EDD)** — cases needing enhanced due diligence.
   - **Suspicion Flags** — confidential AML flags.
   - **Data Subject Requests (DSR)** — privacy requests.

#### 4b. Run screening and set risk
1. From the KYC queue, open the lead → **Application** tab → **Compliance & AML** card.
2. In the screening form:
   - **Sanctions:** Clear
   - **PEP:** Clear
   - (Optionally attach an evidence file.)
   - Click **Save Screening**.
3. Notice the **Risk rating** is set automatically (Low, because there were no hits). If it were High, an **EDD** section would appear requiring Source of Funds, Source of Wealth, and CEO acknowledgment before KYC can pass.

#### 4c. Pass KYC
1. On the right-side panel, find the **KYC** row → click **Pass**.

**What you should see and understand:**
- KYC status becomes **Passed** (green).
- **The gate in action — try this:** open a *different* lead where screening is still Pending and try **Pass** → it is **blocked** with a message ("complete screening first"). This proves the hard gate works.
- **Who can do this:** Only Compliance / CEO / Super Admin. If you log in as an advisor, the Pass/Reject buttons **do not even appear**. This prevents an advisor approving their own file.
- **Override (emergency only):** If genuinely needed, Compliance can apply a **time-boxed override** (mandatory reason + a review-by date + an automatic review task).

#### 4d. (Optional) Raise a confidential suspicion flag
- On any lead, under Compliance & AML, expand **"Raise confidential suspicion flag"** and enter a reason. This is visible **only to Compliance** (never to the advisor or the customer). It is used for anti-money-laundering.

> **Compliance in one line:** Compliance is the checkpoint that confirms, before any bank submission, that the customer is legally safe to deal with — protecting the company from fines and licence loss.

---

### STEP 5 — Handover to Operations

The file now moves from Sales (advisor) to Operations (processing team).

#### 5a. Advisor submits the file
**Log in as:** `advisor` / `adv123`
1. Open the lead → **Application** tab → the **Operations** card at the top.
2. Click **Submit to Operations →**.

**What you should see and understand:**
- The button only works when **KYC is Passed + documents are verified + eligibility is assessed**. If anything is missing, instead of the button you will see a **list of blockers** (e.g. "KYC not Passed", "Some documents still Pending"). **An incomplete file cannot enter Operations** — this is the "handover gate."
- On success, a **completeness score** is stamped (used later in the rework report) and the Operations Manager is notified.

#### 5b. Operations Manager assigns a process owner
**Log in as:** `opsmgr` / `test123`
1. Sidebar → **Ops Queue** → click the **New Handovers** chip → your file appears.
2. Open the file → **Operations** card → **Assign process owner** dropdown → choose an Operations Executive (or yourself) → **Save**.

**What you should see and understand:**
- The case now has **two owners**: the **Advisor = client owner** (handles the customer), and the **Operations Executive = process owner** (handles banks, valuation, transfer). Notifications go to the right person automatically.
- Once assigned, the file leaves the "New Handovers" queue.

---

### STEP 6 — Bank submission & processing (Operations)

**Log in as:** `opsmgr` / `test123` (or the assigned process owner)

#### 6a. Submit to one or more banks (in parallel)
1. Open the lead → **Application** tab → **Bank Applications** card → **Add Application**.
2. Fill in: **Bank** (e.g. Emirates NBD), **Status** = Submitted, **Reference no**, **Requested amount** → **Save**.
3. Click **Add Application** again and add a second bank (e.g. First Abu Dhabi Bank) → Save.

- *Understand:* One case can be submitted to **several banks at the same time**, each tracked separately (status, reference, rate, decision). If one bank rejects, the others continue — no starting over.

#### 6b. Record processing details
In the same Application tab, the **Processing Details** card:
1. Fill in what applies as the case progresses:
   - **Pre-approval valid till** (date)
   - **Valuation** — valuer, date, amount
   - **FOL terms** — rate type (Fixed/Variable), fixed-rate %, **fixed-period-end date**, tenor, EMI
   - **Insurance** — provider, policy number
   - **Title deed number**
2. Click **Save Processing Details**.

- *Understand:* This captures real structured data (not just a PDF). The **fixed-period-end date** later drives the automatic buyout engine (Step 7c). If the valuation is short, the system opens a shortfall resolution task automatically.

#### 6c. Log bank queries and follow-ups
- **Bank Queries:** if a bank asks for something, log it (owner = advisor or ops, due date). It shows prominently until answered.
- **Follow-ups:** log each bank follow-up with a next date. **Silence rule:** if a bank is silent for **3 working days** the process owner is warned; at **7 working days** it is escalated to the Operations Manager.

#### 6d. Track everything in the Ops Queue
- Sidebar → **Ops Queue**. Files are grouped into work queues (chips): **New Handovers, In Verification, Awaiting Bank, Query Raised, Valuations, FOL & Signing, Transfer, Blocked/On-Hold**. Each chip shows a live count, and the list is sorted so the most urgent (silent) cases appear first.
- To pause a case: on the lead's Operations card, use **Put on hold** (reason + review date). It moves to the Blocked/On-Hold queue and its SLA/silence clock pauses.

---

### STEP 7 — Disbursement & Revenue (the finish line)

#### 7a. Mark the case Disbursed
**Log in as:** `opsmgr` / `test123`
1. Open the lead → top-right **Update Stage**.
2. Move the stage forward to **Disbursed** → **Update**.
3. Set the **disbursed date** if prompted.

- *Understand:* This is the moment the company earns its commission — it is the revenue trigger.

#### 7b. See the revenue
**Log in as:** `ceo` / `ceo123`
1. Sidebar → **Monthly Disbursed Pipeline** → add this case's revenue row (slab %, broker % etc.) → revenue and net profit are calculated.
2. Sidebar → **Dashboard** → the revenue KPIs update.
3. Sidebar → **Finance** → disbursed value shows.

#### 7c. Client lifecycle & Buyout engine (automatic)
1. Open the lead → **Client 360** button.
2. The client's lifecycle badge is now **Active Client** (it was Lead → Applicant → Active Client as the case progressed).
- *Understand — Buyout engine:* if a **fixed-period-end date** was recorded on the FOL, the system will automatically create a **new buyout (refinance) lead 120 days before that fixed rate expires** — so we win the customer's next mortgage before a competitor does.

> **Lifecycle complete:** Lead → Auto-assign + Score + Eligibility → Contact/Follow-up → Documents → KYC/Compliance → Handover → Bank submission + processing → Disbursed + Revenue → Active Client → (future) Buyout.

---

## PART 2 — SUPPORTING FEATURES (used throughout, by every role)

These run in the background across the whole platform.

### 2.1 Dashboards (each role sees its own)
**Log in as any role → Dashboard.**
- **CEO / Sales Director:** full pipeline, revenue, funnel, advisor leaderboard, bank performance.
- **Team Leader:** their team's cases and performance only.
- **Operations Manager/Executive:** queue depths, escalations, docs pending.
- **Accountant:** revenue, net profit, VAT, recent disbursals.
- **Compliance:** KYC pending, high-risk, suspicion flags.
- **HR:** headcount by role.
- **Marketing:** leads and conversion by source.
- **Auditor:** read-only overview + recent system events.

### 2.2 My Day
**Sidebar → My Day.** Everyone's personal to-do, ordered by SLA risk → overdue → today → priority. Advisors and ops executives start their day here.

### 2.3 Client 360
**Open any lead → Client 360 button.** Shows one customer's entire history in one place: all their cases, documents, bank applications, follow-ups, consent history, and lifecycle stage. Also shows a **Do Not Contact** control (see 2.9).

### 2.4 Reports (catalogue + drill-down)
**Sidebar → Reports.**
1. You see a catalogue of reports grouped by category (Sales, Operations, Compliance, Finance) — filtered to what your role can access.
2. Open any report → you get a table. Click **Open →** (on record reports) or **View leads →** (on summary reports) to drill into the underlying cases.
3. Click **Export CSV** to download. Every export carries a **"CONFIDENTIAL — exported by [name] on [date]"** watermark line and is recorded in the audit log.

### 2.5 Approvals (separation of duties)
**Sidebar → Approvals.**
- When something needs approval (e.g. an advisor creates a referral partner), a request is sent to the approver's inbox here.
- **Try it:** log in as `advisor`, create a Referral Partner → it becomes **Pending**. Log in as `director` → **Approvals** → **Approve** → the partner becomes Active.
- **Rule:** the person who raised a request can **never** approve their own request.

### 2.6 Notifications & preferences
- The bell/**Notifications** item shows in-app alerts (assignments, SLA, KYC, approvals, etc.).
- **Sidebar → Notification Preferences** lets each user mute categories they don't want — **except** mandatory ones (SLA, Compliance, Approvals) which can never be muted.

### 2.7 Audit Log (tamper-proof record)
**Sidebar → Audit Log** (visible to CEO, Compliance, Auditor).
- Records every change, login, export, and configuration change — who, what, when.
- It is **append-only**: no one can edit or delete an audit entry (this is a legal/compliance requirement).
- Use the search box to filter by user, action, or lead.

### 2.8 Recycle Bin (nothing is truly deleted)
**Sidebar → Recycle Bin** (CEO).
- Deleted **leads, tasks, and documents** go here instead of being destroyed.
- Each can be **Restored**. Only the CEO can permanently purge.

### 2.9 Consent & Do-Not-Contact
- On the lead form and in Client 360, consent is captured per channel (Call/SMS/WhatsApp/Email) with source, timestamp, and who captured it.
- If a customer is set to **Do Not Contact** (in Client 360), the **Call and WhatsApp buttons are disabled** on that customer's leads, and a red "Do Not Contact" badge shows.

### 2.10 Web-to-Lead (website enquiries → CRM)
- **Settings → Web-to-Lead API** shows an endpoint URL, a token, and a ready-to-paste sample HTML form.
- Any enquiry submitted from your website through this form lands directly in the CRM as a new lead (auto-assigned, scored, deduplicated). It includes basic spam protection.

### 2.11 CSV Import (bulk leads)
**Sidebar → Leads → Import.**
1. Download the sample CSV to see the columns.
2. Upload your file → click **Preview** to see how many will be created and which are duplicates (nothing saved yet).
3. Click **Import Leads** to commit. Duplicates are skipped automatically.
4. Made a mistake? Click **Undo last import** to move that batch to the Recycle Bin.

### 2.12 Saved Views (personal & shared filters)
**On the Leads page**, filter the list, then in the "Saved views" bar type a name and click **Save view** (tick "share" to share with the team). Your saved views appear as one-click buttons.

---

## PART 3 — ADMIN & CONFIGURATION (no developer needed)

**Log in as:** `ceo` / `ceo123`

### 3.1 Settings
**Sidebar → Settings.**
- **Company Profile** — name, TRN, address (used on documents/branding).
- **Business Rules (Eligibility & SLA)** — change the LTV caps, DBR cap, income multiple, cash-to-close %, and the first-contact SLA minutes. These drive the eligibility check and SLA timer.
- **Business-Hours Calendar** — working start/end hours, working days, and public holidays. The SLA clock only runs inside these hours.
- **Numbering** — case-number prefix and padding.
- **Web-to-Lead API** — endpoint + token + sample form.
- Stages, sources, and document types are also editable.

### 3.2 Assignment Rules
**Sidebar → Assignment Rules.** Create ordered rules (e.g. "Website leads under 1M → round-robin," "Referral leads → a specific advisor"). First matching rule wins. The engine respects each advisor's daily cap and out-of-office flag.

### 3.3 Users & Roles
**Sidebar → Users.**
- Create/edit users, set their **role**, **Reports To (manager)**, and **team**.
- **Access Control** (⋮ menu on a user) → set per-user permission overrides and access delegation (cover someone while they're on leave — logged).
- **Roles & Permissions** → the role matrix (what each of the 13 roles can access), fully editable.

### 3.4 Scheduled background jobs (automatic alerts)
The following run on a schedule (set up by IT) and keep the system proactive:
- **First-contact SLA** — warns at 80% of the time, escalates on breach.
- **Silence rule** — warns/escalates on bank/case silence (3/7 working days).
- **Document expiry** — alerts at 30/14/7 days before a document expires.
- **Nightly integrity check** — flags any open lead that has no future task.
- **Nurture** — re-activates nurtured leads on their date.
- **Buyout engine** — creates buyout leads 120 days before a fixed rate expires.

---

## PART 4 — WHO SEES WHAT (roles at a glance)

| Role | Main job | Data they see |
|---|---|---|
| Super Admin | System configuration & users | All (business data is a separate grant) |
| CEO | Full oversight, final approvals, only one who deletes | Everything |
| Sales Director | Runs sales, assigns, approves partners | All leads |
| Team Leader | Coaches a pod, reassigns within team | Their team's cases |
| Mortgage Advisor | Owns the client relationship | Only their own leads |
| Operations Manager | Runs processing, manages banks | All cases |
| Operations Executive | Processes assigned cases | Assigned cases |
| Compliance Officer | KYC, screening, audit | View + KYC actions |
| Accountant | Revenue, invoices, disbursals | Finance |
| HR Executive | Staff management | Staff (no client financials) |
| Marketing | Campaign & source performance | Leads (read-only) |
| External Auditor | Independent review | Everything, read-only |

---

## PART 5 — QUICK 5-MINUTE DEMO SCRIPT (for showing the client)

1. **`advisor`** → create a lead (Step 1) → point out score, eligibility, case number, SLA timer.
2. **`compl`** → open the lead → run screening → **Pass KYC** (Step 4).
3. **`advisor`** → **Submit to Operations** (Step 5a).
4. **`opsmgr`** → Ops Queue → assign owner → add a bank application (Steps 5b, 6a).
5. **`opsmgr`** → move stage to **Disbursed** → **`ceo`** → Dashboard shows the revenue (Step 7).

That single flow demonstrates the entire system end to end.

---

*Bitar Mortgage CRM — internal walkthrough & user manual · Phase 1.*
