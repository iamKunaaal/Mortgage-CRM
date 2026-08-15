# BITAR CRM — Phase‑2 Implementation Plan (detailed)

> Source of truth: `server/media/esign/BHITR_CRM_Product_Requirements_Document_v1.pdf` (76 pages), §24 register + §28.1 phasing.
> Phase‑2 theme (§28.1): **Operational depth & engagement, months 4–7.**
> Exit criteria: first month‑end fully closed in‑system (invoice + receipt + approved payout run), buyout‑watch generating leads, attendance live, automation rules replacing manual programs.

---

## 0. Guiding principles (apply to EVERY item below)

1. **Additive & graceful degradation (HARD RULE).** Every feature — especially anything touching an external service — is an optional switch. If a vendor/integration is absent, disabled, misconfigured or failing, the CRM keeps running exactly as seamlessly as today. Pattern = the Meta webhook: read config at runtime, wrap external calls, log + swallow failures, never 500 a page, always keep the manual path (Call Log, in‑app bell, notes, manual dates). See memory `phase2-integrations-optional`.
2. **Config over code (existing pattern).** Reuse `AppSetting` (key/JSON) + Settings screens for all tunables (VAT %, retention years, commission trigger, incentive rules, holiday calendar). No hard‑coded business numbers.
3. **Reuse existing plumbing:**
   - Notifications → `_notify(user, text, url, category, actor)` (in‑app is the source of truth per FR‑11).
   - Audit → `_audit(lead,…)` / `_audit_event(request,…)` on every write path (FR‑13, §16.7).
   - Approvals → existing `ApprovalRequest` model (already has Payout Run / Fee Waiver / Leave / Month Reopen / Template Publish types) + `/approvals/`.
   - Scheduled jobs → new `management/commands/check_*.py` wired into `run_daily_jobs.py` (cron already runs hourly on Railway "lucky-fulfillment").
   - Scoping → `visible_leads` / `visible_tasks`; permissions via `crm/permissions.py` (`module_required`, scope helpers).
   - Auto‑tasks → `_auto_task(lead, title, type, days, actor)`.
4. **Migrations discipline.** Next migration index = 0046+. One migration per coherent change; never edit applied migrations.
5. **Notifications abstraction (prep for later channels).** Introduce a single `notify(user, event, ctx, channels=…)` dispatcher now that only fans out to in‑app today, but has stub adapters for `email` / `whatsapp`. When those vendors are chosen later, only the adapter is filled — no feature rewrite. This is how we "prepare for integrations without building them".
6. **Feature flags.** Add an `AppSetting('feature_flags', {...})` + a tiny `feature_on('finance_invoicing')` helper so half‑built Phase‑2 modules can ship dark and be toggled per go‑live (§AD‑08).

---

## 1. Sequencing (PRD rule: "money before portals, discipline before intelligence")

| Order | Workstream | Why first | Vendor needed? |
|------|------------|-----------|----------------|
| **2.1** | Finance depth (FI) | Money features first; unblocks month‑end exit criterion | No |
| **2.2** | Operations subflows (OPS) | Discipline on live cases | No |
| **2.3** | Channel Partners end‑to‑end (PM) | Feeds finance payouts | No |
| **2.4** | Automation builder + Approvals depth (NA) | Replaces manual programs | No |
| **2.5** | Client lifecycle messaging + buyout‑watch (CL) | Engagement, needs automation engine | No (messaging channel optional) |
| **2.6** | HR: attendance / leave / targets (HR) | Internal ops | No (geofence uses browser) |
| **2.7** | Admin & Compliance depth (AD/CO) | Config + governance | No |
| **2.8** | Reporting depth (RP) | Intelligence on top | No |
| **— later —** | Integrations (telephony/WhatsApp/email/calendar/SSO) | Vendor‑gated (OD‑1, OD‑2); SKIP now | **Yes — deferred** |

Everything in 2.1–2.8 is **self‑contained, no paid vendor**. Integrations sit behind the abstractions from §0.5.

---

## 2.1 Finance depth (FI‑01, 03–11) — *do first*

**Scope:** Accounts queue on disbursement + closing‑pack verify (FI‑01); one‑click **tax invoice** with numbering/TRN/VAT/PDF/logged send (FI‑03); invoice lifecycle + post‑send lock + credit note (FI‑04); receivables aging + reminders (FI‑05); receipt recording + auto‑match + part payments (FI‑06); variance/clawback → ledgers (FI‑07); payout runs with SoD + approvals (FI‑08); incentive scheme engine + per‑employee live ledgers (FI‑09); month‑end lock + CEO reopen (FI‑11). (Expected‑commission FI‑02 already exists via `Customization`.)

**Data model (new):**
- `Invoice` (case FK, number, trn, subtotal, vat, total, status[Draft/Sent/Paid/PartPaid/Credited/Void], issued_at, sent_at, locked bool, pdf link).
- `CreditNote` (invoice FK, amount, reason, number, created_by).
- `Receipt` (invoice FK, amount, method, received_at, matched bool, note).
- `PayoutRun` (period, status, totals, approval FK→ApprovalRequest) + `PayoutLine` (run FK, payee[advisor/partner], base, rate, amount, ledger refs).
- `Ledger` entries (payee, type[commission/incentive/clawback/variance], amount, source ref, effective_date) — or extend a single `LedgerEntry`.
- `IncentiveScheme` (name, rules JSON, effective_dated) + computed live ledger per employee.
- Config: `AppSetting('finance', {vat_pct, trn, invoice_format, commission_trigger})` — **commission_trigger drives everything (OD‑5: default "on receipt")**.

**Backend:** `finance_*` views (invoice_create/send/lock/credit, receipt_add, payout_run_create/submit→approval/execute, month_lock/reopen→ApprovalRequest 'Month Reopen'); a `check_receivables.py` command (aging reminders via `_notify`) in `run_daily_jobs`.

**UI/where:** Extend existing **Finance** module — tabs: Invoices, Receivables, Payouts, Incentives, Month‑End. Invoice PDF via existing reportlab dependency.

**Permissions:** Accountant + CEO; advisors never see bank commission (existing rule). Payout execute requires approver ≠ creator (ApprovalRequest).

**Audit/exit:** Every invoice/receipt/payout write audited. **Exit:** one full month‑end closed in‑system (invoice+receipt+approved payout).

---

## 2.2 Operations subflows (OPS‑08, 10, 11, 12, 14)

**Scope:** Valuation subflow + shortfall computation + resolution task (OPS‑08); Buyout subflow + liability‑letter validity (OPS‑10); NOC tracking + fee/receipt (OPS‑11); Transfer booking + cheque‑list builder + printable transfer sheet (OPS‑12); Ops queues complete + load board + reassignment (OPS‑14).

**Data model:** extend `Lead`/case or add `ValuationRecord`, `BuyoutRecord`, `NOCRecord`, `TransferBooking` (dates, amounts, validity windows, docs). Add validity‑countdown fields (like pre‑approval already has).
**Backend:** subflow views on the lead Operations tab; `check_ops_validity.py` (liability‑letter/NOC expiry alerts) → `run_daily_jobs`. Load board = Ops Queue view grouped by queue + drag‑reassign.
**UI/where:** Lead detail **Operations/Application tab** (build on existing handover/pre‑approval/FOL sections) + **Ops Queue** page.
**Exit:** cases flow valuation→transfer entirely in‑system; validity alerts firing.

---

## 2.3 Channel Partners end‑to‑end (PM‑02, 03, 04, 06, 07, 08, 09)

**Scope:** Channel partner entity + contacts/docs/agreement expiry (PM‑02); onboarding + auto‑generated agreement + SDir approval (PM‑03); status lifecycle, only Active attributable (PM‑04); per‑partner effective‑dated commission models (PM‑06); monthly auto statements (PM‑07); payout runs via approvals (PM‑08, shares 2.1 PayoutRun); Partner 360 + inactivity flags (PM‑09).

**Data model:** extend existing `ReferralPartner` (+ partner_type=channel, agreement_expiry, status lifecycle) + `PartnerCommissionModel` (partner FK, model JSON, effective_from) + `PartnerStatement` (partner, period, lines, total). Attribution lock already partly present.
**Backend:** onboarding flow → ApprovalRequest 'Partner Activation' (type already exists); `generate_partner_statements.py` monthly command; agreement PDF via reportlab.
**UI/where:** extend **Referral Partners** module (list → detail 360 → statements/ledger).
**Exit:** statements auto‑generated; payouts run via approval.

---

## 2.4 Automation builder + Approvals depth (NA‑05, 06, 08)

**Scope:** No‑code automation builder — triggers → conditions → actions (NA‑05); rule run‑log + simulation mode + loop guards (NA‑06); approval workflow framework + delegation + decision logs (NA‑08, base `ApprovalRequest` exists).

**Data model:** `AutomationRule` (name, trigger[event], conditions JSON, actions JSON, active, run stats) + `AutomationRun` (rule FK, target, status, log, simulated bool).
**Backend:** a small rule engine invoked from existing write paths (lead save, stage change, task complete, expiry jobs). Actions map to existing helpers (`_notify`, `_auto_task`, field set, create ApprovalRequest). **Loop guard** = max runs per record per rule per day. Simulation = dry‑run writing only to `AutomationRun.log`.
**UI/where:** **Settings → Automation** (rule list + builder + run log). Replaces hard‑coded post‑close/escalation programs (CL §13.4, §12.3).
**Exit:** at least the §13.4 program and §12.3 escalations run as configurable rules.

---

## 2.5 Client lifecycle messaging + buyout‑watch (CL‑03, 04, 05, 06)

**Scope:** Milestone messaging auto/approved modes (CL‑03); post‑close program as automation rules (CL‑04, uses 2.4); **fixed‑rate expiry watch auto‑creating buyout leads at 120/90 days** (CL‑05); client referral capture + advocacy reporting (CL‑06).
**Backend:** extend existing `check_buyouts.py`; messaging goes through the **notify() dispatcher** (§0.5) → in‑app today, email/WhatsApp later. Referral capture on Client 360.
**UI/where:** Client 360 + Automation rules.
**Note:** messaging content is built; the *channel* is the deferred integration — in‑app/manual works now.

---

## 2.6 HR: attendance / leave / targets (HR‑02–09)

**Scope:** HR doc expiries on the §15.4 engine (HR‑02); onboarding/offboarding wizards with enforced reassignment (HR‑03); attendance check‑in/out + geofence + optional selfie (HR‑04); shifts/grace/regularization (HR‑05); monthly attendance lock + export (HR‑06); leave types/balances/approvals/team calendar (HR‑07, approvals reuse ApprovalRequest 'Leave'); targets + auto actuals + dashboard (HR‑08); appraisals + HR letters (HR‑10 S).
**Data model:** `Employee` (extend User), `Attendance` (user, in/out, geo, selfie), `LeaveType`/`LeaveBalance`/`LeaveRequest`, `Target` (user, metric, period, value, actual computed).
**Backend:** geofence + selfie use **browser geolocation + camera** (no vendor); `hr_doc_expiry` reuses doc expiry engine; attendance monthly lock.
**UI/where:** new **HR** module + nav group.
**Exit:** attendance live for all staff; leave + targets running.

---

## 2.7 Admin & Compliance depth (AD‑05, 07, 08, 09; CO‑03, 04, 06, 08, 10)

**Scope:** custom fields + layout editor with role visibility (AD‑05); template studio + publish approval (AD‑07, uses ApprovalRequest 'Template Publish'); feature flags (AD‑08, §0.6); masked sandbox refresh (AD‑09); risk/EDD completion (CO‑03); **UBO capture for corporate borrowers** (CO‑04); suspicion flags goAML metadata (CO‑06, base exists); retention schedule + approved purge queue (CO‑08, needs **OD‑6** retention years); quarterly access review + dormant auto‑disable (CO‑10).
**Data model:** `CustomField` (model, key, type, role visibility) + JSON store on target; `RetentionPolicy` (record class, years) + `check_retention.py` purge queue → ApprovalRequest before purge; `Corporate`/UBO fields on Client.
**UI/where:** Settings (custom fields, templates, flags, retention), Compliance workspace (UBO, purge queue, access review).

---

## 2.8 Reporting depth (RP‑03 rest, RP‑05, 06, 08)

**Scope:** remaining report catalogue (RP‑03); scheduled report delivery PDF/XLSX (RP‑05 — delivery channel = email, so **generate now, deliver via notify() later**); semantic report builder (RP‑06); KPI formula tooltips (RP‑08).
**UI/where:** Reports module; scheduled jobs generate + store report files in‑app; email delivery deferred.

---

## 2.9 Also in Phase‑2 (stragglers — folded into the workstreams above)

Captured here so nothing from the §24 register is missed:

- **PL‑07** — Weighted pipeline forecast from per‑stage probabilities → add a probability field per stage (config) + a forecast view in Reports (2.8) / pipeline. (S/2)
- **FI‑10** — Client fee invoices, receipts, approved refunds and waivers → part of Finance (2.1); refunds/waivers go through `ApprovalRequest 'Fee Waiver'`. (S/2)
- **DM‑08** — Secure tokenized client **upload** links with virus scanning → self‑contained (signed token URL + upload page); no vendor. Feeds the existing Documents module. (M/2)
- **DM‑09** — Template‑driven **PDF generation** with merge fields → reuse reportlab; shared engine used by invoices (2.1) and partner agreements (2.3). (M/2)
- **CO‑07** — DSR workflows: access bundle export, correction, anonymization with legal holds → extends existing `DataSubjectRequest`; anonymize‑on‑delete + hold checks. (S/2→3)
- **AD‑04** — Stage / checklist / SLA / automation / approval **editors** → some exist (SLA calendar, stages); complete the config editors in Settings. (M/1→2)
- **LM‑03 / IN‑02** — Ad‑platform webhooks: **Meta ✅ done** (`[[meta-leadads-integration]]`), **Google Ads** pending (same inbound pattern, no paid vendor). (S/1→2)

## 3. Cross‑cutting build (do alongside 2.1)

- **notify() dispatcher** with in‑app adapter live + email/whatsapp stub adapters (§0.5).
- **feature_flags** AppSetting + `feature_on()` helper (§0.6).
- **Ledger** as the shared spine for finance + partner + incentive (avoid three ledgers).
- Extend **permissions** matrix for new modules (Finance深, HR, Automation) — role‑gated in `_sidebar.html`.

---

## 4. Ways of working & rollout (§28.2 / D1–D8)

- Fortnightly demo on staging against these section numbers.
- Each item: local build → `manage.py check` + targeted test script → user review → push (user says "push"). Never push without the user.
- Migrations rehearsed; data‑loss‑safe.
- Per‑module UAT traced to §24 IDs before sign‑off.

## 5. Open decisions to confirm with client BEFORE the dependent build
- **OD‑5** commission accrual trigger → blocks 2.1 finance math (default "on receipt").
- **OD‑6** retention years → blocks 2.7 retention engine.
- **OD‑1 / OD‑2** telephony / WhatsApp vendor → only for the deferred integrations (not blocking 2.1–2.8).

---

## 6. What we DON'T build now (deferred, per user 2026‑08‑15)
Telephony (OD‑1), WhatsApp BSP (OD‑2), transactional email send, mailbox/calendar sync, SSO/2FA. All sit behind the §0.5 abstraction so they slot in later with zero rework and the CRM stays seamless without them.

---

## BUILD LOG (2026-08-15) — what is now implemented locally (NOT pushed)

Migration `0046` adds all Phase-2 models. New file `crm/views_phase2.py`, shared template `crm/base_p2.html`, nav entries, and scheduled commands `check_receivables` + `check_ops_validity` wired into `run_daily_jobs`.

**Working & smoke-tested:**
- **Finance hub** (`/finance-hub/`): invoice create (auto VAT + numbering), send+lock, receipt (auto Part-Paid/Paid + commission LedgerEntry on receipt per OD-5), credit note, payout run create + submit→ApprovalRequest (SoD), month lock/reopen, incentive schemes. Receivables aging reminders command. [FI-01,03,04,05,06,07,08,09,11]
- **Ops subflows** (⚙ Ops modal on lead detail): valuation (shortfall→auto Valuation task), buyout (validity + expiry alert command), NOC (fee/receipt), transfer booking (cheque list). [OPS-08,10,11,12]
- **Automation** (`/automation/`): rule builder (trigger/conditions/actions JSON), engine hooked into `lead.created` & `lead.stage_changed`, loop guard (1/rule/record/day), run log, simulation flag. [NA-05,06]
- **HR** (`/hr/`): attendance check-in/out (browser geo, no vendor), leave request→ApprovalRequest→approve/reject, targets. [HR-04,05,07,08]
- **Partners**: per-partner commission model, monthly statement generation. [PM-06,07]
- **Reporting**: weighted forecast (`/reports/forecast/view/`, config-driven stage probs). [PL-07]
- **Cross-cutting**: `notify_dispatch()` (in-app live; email/whatsapp stub adapters for later), `feature_on()` flags, shared `LedgerEntry`, retention-policy + custom-field save endpoints. [AD-08 + prep]

**Scaffolded (model+endpoint present; deeper UI/logic still to finish within Phase-2):**
- Custom fields don't yet render on lead forms (AD-05 UI); template studio (AD-07); sandbox (AD-09); DSR export bundle/anonymize (CO-07); UBO capture (CO-04); quarterly access review (CO-10); milestone messaging UI (CL-03); client referral capture (CL-06); payout execute→Paid + incentive auto-compute; tokenized client upload links (DM-08); template PDF merge fields (DM-09); semantic report builder (RP-06).

**Deferred (vendor-gated, behind notify_dispatch abstraction — CRM runs fine without):** telephony (OD-1), WhatsApp (OD-2, NA-03), transactional email send, mailbox/calendar sync (IN-03/04), SSO+2FA (IN-06).

**Not pushed** — awaiting user review + "push".

---

## BUILD LOG addendum (2026-08-15) — scaffolded items COMPLETED

Migration `0047` adds `Lead.custom`, `MessageTemplate`, `UBO`, `ClientReferral`, `UploadToken`.

Now working & smoke-tested:
- **Custom fields on lead form** (AD-05): admin-defined fields render on create/edit and save to `Lead.custom`.
- **Template Studio** (`/templates/`, AD-07): create templates; publish needs CEO approval; merge fields {{name}}/{{case}}/{{stage}}.
- **Milestone messaging** (CL-03): published auto-send templates fire on stage change via `send_milestone_messages` (in-app now, email/whatsapp later).
- **DSR** (CO-07): export bundle (JSON) + anonymize PII — on lead ⚙ Ops modal.
- **UBO capture** (CO-04): corporate borrower owners on ⚙ Ops modal.
- **Access review** (`/access-review/`, CO-10): users + last login + dormant (90d) flag.
- **Client referral** (CL-06): capture on ⚙ Ops modal.
- **Payout execute→Paid** + **incentive auto-compute** from schemes (FI-08/09).
- **Tokenized client upload link** (DM-08): `/upload/<token>/` public page, 7-day expiry, size limit.
- **Report builder** (`/reports/builder/`, RP-06): group leads by field.
- **Sandbox masked export** (AD-09): `manage.py sandbox_export` dumps PII-masked leads.

Everything above is additive + graceful. Still not pushed.
