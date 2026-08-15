# BITAR CRM — User Guide

A simple, step-by-step guide to using your CRM. No technical knowledge needed — just follow the steps.

> **Tip:** After almost every action, a small green message appears at the top of the screen. That message confirms your action worked.

---

## Getting started

### Logging in
1. Open your browser and go to your CRM web address.
2. Enter your username and password, then click **Login**.
3. You'll land on your **Dashboard**.
4. On the **left side** you'll see the menu (sidebar) with all sections.

### Finding your way around
- The **left menu** is how you move between sections (Leads, Tasks, Finance, HR, etc.).
- What you see in the menu depends on your **role** — an advisor sees fewer items than a manager or CEO.
- The **top bar** has search and notifications (the bell icon).

---

# PART 1 — Everyday use (for advisors & staff)

## 1. Dashboard
- Your home screen. It shows an overview — key numbers, recent activity, and things needing attention.
- Managers/CEO see a company-wide dashboard (revenue, pipeline, performance).

## 2. My Day
- Menu → **My Day**.
- Your personal to-do screen: today's tasks, follow-ups due, and leads that need first contact — sorted by urgency.
- Start your day here.

## 3. Leads

### See your leads
- Menu → **Leads → All Leads** (advisors see "My Leads").
- A list of all leads with their stage, source, and status. Use the search box to find anyone.

### Create a new lead
1. Menu → **Leads → New / Create Lead**.
2. Fill in the customer's details across the form (name, contact, loan info, etc.).
3. If your source is **"Referral Partner"**, choosing the partner is required.
4. Click **Create Lead**. The lead is saved and opens.
- *If a lead with the same phone/email already exists, the system warns you about a possible duplicate.*

### Work on a lead (Lead detail page)
Click any lead to open it. From here you can:
- Update its **stage** (click **Update Stage**).
- Log calls, add notes, add follow-ups, upload documents.
- Assign an advisor (managers only).
- Mark **Not Interested** (see below).
- Use **⚙ Ops** for operational steps (see Part 2).

### Mark a lead "Not Interested"
1. On the lead page, click **Not Interested**.
2. A pop-up asks for a **reason** (choose from the list) and an optional note.
3. Click **Mark Not Interested**.
- The lead moves to **Lost Leads** with that reason recorded.

### Lead Pipeline
- Menu → **Leads → Lead Pipeline**.
- A visual board showing how many leads are at each stage — great for a quick health check.

### Lost Leads
- Menu → **Leads → Lost Leads**.
- Every lead marked lost, with the reason. Includes a "why we lose deals" breakdown.

### Meta Leads
- Menu → **Leads → Meta Leads**.
- Leads captured automatically from your Facebook/Instagram lead ads, showing every answer the customer submitted. Click a card to expand its full details.

## 4. Tasks
- Menu → **Tasks → All Tasks** (or **Overdue Tasks**).
- Your work items. Many are created automatically (e.g. "First contact") so nothing is forgotten.
- Open a task to complete it (you'll be asked for an outcome).

## 5. Documents
- Menu → **Documents**.
- All uploaded documents. You can view, verify, and track expiry dates.
- You can also upload documents directly on a lead's page.

## 6. Notifications & Approvals
- The **bell icon** (top right) and **Notifications** in the menu show your alerts (new leads, reminders, approvals needed).
- **Approvals** — items waiting for your decision (e.g. a payout run, a leave request).

---

# PART 2 — Operations (processing a mortgage case)

Open a lead → click the **⚙ Ops** button. A pop-up opens with sections (click a section title to expand it).

## Valuation
1. Open **Valuation**.
2. Enter the **valued amount** and **purchase price** → **Save valuation**.
- If the valuation is lower than the price, the system automatically creates a task to resolve the shortfall.

## Buyout
1. Open **Buyout**.
2. Enter current bank, liability amount, liability letter date and **valid until** date → **Save buyout**.
- The system will remind you before the liability letter expires.

## NOC
1. Open **NOC** → enter developer, fee, request/received dates → **Save NOC**.

## Transfer booking
1. Open **Transfer booking** → enter the trustee office, date, and the cheque list → **Save transfer**.

## Ops Queue
- Menu → **Ops Queue** — a work board for the operations team to see and manage cases in progress.

---

# PART 3 — Finance

Menu → **Finance Hub**.

## Create an invoice
1. In **New invoice**: choose the lead (or type a client name) and enter the **subtotal**.
2. Click **Create** — VAT is added automatically and the invoice appears as **Draft**.

## Send the invoice
- On the invoice row, click **Send** — it's now locked and marked **Sent**.

## Record a payment (receipt)
- In the invoice row, type the amount in the **Receipt** box and click **Add**.
- The status updates automatically to **Part-Paid** or **Paid**.

## Payout runs (paying commissions)
1. In **Payout runs**, enter a period (e.g. `2026-08`) → **Create run**. The system totals what's owed to advisors/partners.
2. Click **Submit** — this goes to the CEO for approval (the person who creates it can't approve it — a safety control).
3. After approval it can be marked **Paid**.

## Incentive schemes
- Add a scheme (name + %) in the **Incentive schemes** box. Later, running "compute" creates each employee's incentive amount automatically.

## Month-end lock
- Enter a period and click **Lock** to freeze that month's finances so nothing can be changed by mistake. Only the CEO can **Reopen**.

---

# PART 4 — Referral Partners

Menu → **Referral Partners → All Partners**.

- **Add Partner** — create a new referral partner.
- **Generate Statements** — creates each partner's monthly commission summary.
- **View Statements** — see each partner's statement (deals + total commission).

---

# PART 5 — Reports

## Forecast
- Menu → **Forecast**.
- Shows a **weighted pipeline** — your pipeline value adjusted by how likely each stage is to close. A realistic view of expected business.

## Report Builder
- Menu → **Report Builder**.
- Choose a field (stage, source, priority, KYC status) → **Run** → get a grouped count. Build your own quick reports without help.

## Reports & Analytics
- Menu → **Reports & Analytics** — the standard set of dashboards and reports, with drill-down into the underlying records.

---

# PART 6 — HR

Menu → **HR**.

## Attendance
- Click **Check in** when you start (allow location if asked) and **Check out** when you leave. Your times are recorded.

## Leave
1. In **Request leave**: pick leave type, start and end dates, and a reason → **Submit**.
2. Your request goes to HR/CEO, who can **Approve** or **Reject**. You're notified of the decision.

## Targets (managers)
- In **Set target**: choose the employee, metric, period and value → **Save**.

---

# PART 7 — Automation (set-and-forget rules)

Menu → **Automation**. Create rules so routine work happens by itself — no coding.

1. **Rule name** — e.g. "Follow up new leads".
2. **WHEN this happens** — choose a trigger (a lead is created / stage changes / lead assigned).
3. **ONLY IF (optional)** — add a condition (e.g. Stage *is* Pre-Approved).
4. **THEN do this** — choose an action: create a task, notify the advisor, notify a team, set priority, add a note, escalate, or send a message template.
5. Tick **Turn this rule ON** → **Save rule**.

*Example:* When a new lead is created → create a task "Call the customer" due in 1 day. Now every new lead gets that task automatically.

---

# PART 8 — Templates & Custom Fields (admin)

## Template Studio
- Menu → **Template Studio**.
- Create reusable messages. Use `{{name}}`, `{{case}}`, `{{stage}}` and the system fills in the real values.
- Set a **milestone stage** + **Auto-send** so a message goes out automatically when a case reaches that stage.
- Publishing a template needs CEO approval (quality control).

## Custom Fields
- Menu → **Custom Fields**.
- Add your own extra fields to the lead form:
  1. Enter a **field label** (e.g. "Referral code"), choose a **type** (text, number, date, dropdown, yes/no) → **Add field**.
  2. The new field appears in the **"Additional details"** section on the Create/Edit lead form.
- No developer needed — the CRM adapts to your business.

---

# PART 9 — Compliance (on the lead's ⚙ Ops pop-up)

## UBO (corporate borrowers)
- For company loans, record the ultimate owners: name, share %, nationality, ID, and whether they're a PEP (politically exposed person).

## Data requests (DSR)
- **DSR export** — download all of a customer's data as a file (for a data-access request).
- **Anonymize (DSR)** — permanently remove a customer's personal details while keeping the record for audit (for a delete request).

## Client upload link
- Click **Generate upload link** to create a secure link (valid 7 days). Send it to the customer; they upload their documents directly — no login needed — and the files land straight on the lead.

---

# PART 10 — Administration (CEO / Admin)

- **Users** — add staff, set roles.
- **Roles & Permissions** — control what each role can see and do.
- **Settings** — company details, business rules, SLA/holiday calendar, VAT, etc.
- **Assignment Rules** — how new leads are auto-assigned to advisors.
- **Access Review** — see who's active and who's dormant (no login in 90 days).
- **Audit Log** — a complete, tamper-resistant history of every change.
- **Recycle Bin** — restore accidentally deleted items (nothing is ever hard-deleted).

---

## Quick reference — where do I go for…?

| I want to… | Go to |
|---|---|
| Add a new customer | Leads → New Lead |
| See my work today | My Day |
| Process a case (valuation/NOC/transfer) | Lead → ⚙ Ops |
| Raise an invoice / record a payment | Finance Hub |
| Pay partner/advisor commissions | Finance Hub → Payout runs |
| Mark attendance / apply leave | HR |
| Automate a routine task | Automation |
| Add an extra field to the lead form | Custom Fields |
| See leads from Facebook ads | Leads → Meta Leads |
| Check expected business | Forecast |

---

*If anything is unclear, note the question and we'll cover it in the meeting. This guide will be kept up to date as the system grows.*
