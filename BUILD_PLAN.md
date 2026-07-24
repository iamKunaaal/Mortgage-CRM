# BHITR CRM — Development Build Plan (Full Detail)

> **Source of truth:** BHITR CRM PRD v1.0 (76-page PDF) + Requirements Deck v1.0.
> **Baseline:** Existing Django backend (`server/`) + v0 HTML prototype (root `*.html`).
> **Rule:** Build ON TOP of existing. Do NOT rebuild. Do NOT regress any v0 screen.
> **Golden rules:** (1) Config over code — no hardcoded regulatory values. (2) Nothing hard-deleted, everything logged. (3) Advisor's daily screen speed matters most.

---

## Legend
- 🆕 new model/module/file · ✏️ modify existing · ♻️ reuse as-is
- Field tables: **M** = mandatory at creation, **O** = optional
- 🧪 **KHUD KAISE TEST KARE** = step-by-step manual testing walkthrough (for founder, no coding needed)

## Current State (what already exists)
| Area | Status |
|---|---|
| Django 5 + SQLite + auth | ♻️ works |
| User model, 5 roles | ✏️ → 13 roles |
| Models: Bank, ReferralPartner, Lead, Task, Document | ✏️ extend + refactor |
| Lead = single track (18 stages) | ✏️ split into Client/Case/BankApplication |
| permissions.py (basic) | ✏️ add field-level security |
| v0 HTML screens | ♻️ keep, wire to real data |
| on_delete=CASCADE (hard delete) | ✏️ → soft delete |

## How we work
1. Aap bolo "**Phase N start**" → 2. Main build karu → 3. Aap neeche ka 🧪 test walkthrough follow karke check karo → 4. Approve → next phase.

---
---

# PHASE 1 — Foundation & Core Data Model

**Goal:** Single-track ko Lead→Client→Case→BankApplication mein todo, soft-delete + audit daalo, 13 roles + 2FA, purana data migrate karo.

## 1.1 BaseModel (abstract — har table isse inherit karega) 🆕
| Field | Type | Note |
|---|---|---|
| id | UUID | primary key |
| created_at | DateTime | auto |
| created_by | FK User | auto |
| updated_at | DateTime | auto |
| updated_by | FK User | auto |
| is_deleted | Boolean | soft delete flag (default False) |

## 1.2 Client model 🆕
| Field | Type | M/O | Choices/Note |
|---|---|---|---|
| full_name | Char(120) | M | |
| mobile | Char(30) | M | +971 normalized |
| email | Email | O | |
| nationality | Char(60) | O | |
| residency_status | Char | M | UAE National / UAE Resident / Non-Resident |
| dob | Date | O | |
| preferred_language | Char(40) | O | |
| emirates_id | Char(40) | O | masked display |
| lifecycle_stage | Char | auto | Lead / Applicant / Active Client / Closed Client / Advocate |
| do_not_contact | Boolean | O | default False |

## 1.3 Case model 🆕
| Field | Type | M/O | Choices/Note |
|---|---|---|---|
| reference | Char | auto | `BH-CASE-YYYY-####` gap-free |
| client | FK Client | M | |
| stage | FK Stage (config) | M | default first stage |
| sales_owner | FK User | M | advisor |
| ops_owner | FK User | O | assigned at handover |
| purpose | Char | M | Ready Purchase / Resale / Off-Plan / Buyout / Equity Release |
| property_value | Decimal(14,2) | O | |
| loan_amount | Decimal(14,2) | O | |
| ltv | Integer | O | |
| expected_commission | Decimal(14,2) | O | computed |
| parallel_state | Char | O | Active / On Hold / Declined / Withdrawn |
| hold_reason | Char | O | mandatory if On Hold |
| hold_review_date | Date | O | mandatory if On Hold |

## 1.4 BankApplication model 🆕
| Field | Type | M/O | Choices/Note |
|---|---|---|---|
| case | FK Case | M | |
| bank | FK Bank | M | |
| product | Char(80) | O | |
| submitted_date | Date | O | |
| bank_reference | Char(60) | O | |
| processing_contact | Char(120) | O | |
| status | Char | M | Draft/Submitted/Under Review/Query Raised/Pre-Approved/FOL Issued/Rejected/Expired/Withdrawn |
| offered_rate | Decimal(5,2) | O | |
| rate_type | Char | O | Fixed / Variable |
| remarks | Text | O | |
| next_followup_date | Date | M-if-active | mandatory while active |
| ts_submission / ts_first_response / ts_pre_approval / ts_fol | DateTime | auto | for bank league table |

## 1.5 Lead model ✏️ (becomes enquiry only)
- Keep existing fields. Add: `residency_status`, `next_followup_date` (M while open), `lead_score` (int), `first_touch_timestamp`, `converted_case` (OneToOne Case, nullable), `status` (New/Attempting/Contacted/Qualified/Not Eligible/Nurture/Lost/Converted)
- Remove single `bank`/`stage` reliance once converted (Case takes over)

## 1.6 AuditLog model 🆕 (append-only)
| Field | Type |
|---|---|
| record_type / record_id | Char / UUID |
| actor | FK User |
| action | Char (create/update/delete/restore/view/export/login) |
| field / old_value / new_value | Char / Text / Text |
| source | Char (UI/API/Rule) |
| timestamp | DateTime |
- Signal on every save/delete auto-writes rows. No update/delete allowed on AuditLog itself.

## 1.7 Roles ✏️
Expand 5 → 13: Super Admin, CEO, Sales Director, **Team Leader**, Advisor, **Operations Manager**, Operations Executive, Accounts Executive, **HR Executive**, **Compliance Officer**, **Marketing Executive**, **Telecaller** (disabled default), **External Auditor**.
- Add `manager` (self-FK) on User for hierarchy; 🆕 `Team` model.
- ✏️ permissions.py: Own/Team/All resolution + 🆕 field-level security helper (hide commission % from Advisor/TL/Marketing/HR).

## 1.8 Auth hardening ✏️
2FA (TOTP), session timeout, lockout after 5 fails/15 min, login+device history, configurable password policy.

## 1.9 Soft delete + recycle bin 🆕
All CASCADE → soft delete. Recycle bin view (restore) — Super Admin only.

## 1.10 Data migration 🆕
Script: har existing Lead → Client + Case + BankApplication banao, data preserve karo.

### 🧪 KHUD KAISE TEST KARE — Phase 1
1. **Admin login karo** (`/admin`) apne superuser se.
2. **Users banao:** ek "Advisor A", ek "Advisor B", ek "Compliance Officer", ek "Sales Director". Dekho dropdown mein **13 roles** aa rahe hain.
3. **Advisor A se login karo** (normal site). Login pe **2FA code** maangna chahiye → 2FA set karo, andar aao.
4. **Ek Client banao** "Rahul Sharma". Uske andar **do Case banao** (Case-1, Case-2). ✅ Ek client ke 2 cases dikhne chahiye.
5. **Case-1 ke andar 3 Bank Application banao** (Emirates NBD, ADCB, Mashreq), teeno alag status. ✅ Teeno ek saath dikhne chahiye.
6. **Case ka stage dekho** — jis application ka status sabse aage hai, case wahi dikhaye.
7. **Ek Client delete karo.** ✅ Wo list se hatna chahiye PAR **Recycle Bin mein dikhna chahiye** (DB se poora delete NAHI). Sirf Super Admin restore kar sake.
8. **Client ka naam edit karo** "Rahul Sharma" → "Rahul K Sharma". Ab **Audit Log kholo** → wahan `full_name: Rahul Sharma → Rahul K Sharma` entry dikhni chahiye (kisne, kab).
9. **Advisor B se login karo.** ✅ Advisor A ki banayi leads/clients **nahi dikhni chahiye** (Own visibility).
10. **Advisor kisi bank ka commission % dekhne ki koshish kare** → ✅ hidden hona chahiye.
11. Purana data check: pehle jo leads the, wo ab **Client+Case+BankApplication** mein migrate ho gaye, kuch gayab nahi.

---
---

# PHASE 2 — Lead Management (complete)

**Goal:** Lead capture → outcome. Speed, dedupe, eligibility, discipline.

## 2.1 Lead fields — full extension ✏️ (PRD 9.2)
**Employment & Income:** employment_type (Salaried/Self-Employed) M, employer_name O, designation O, service_length O, monthly_salary O, allowances O, annual_turnover O, salary_transfer_bank O
**Financial position:** existing_emis O, card_limits O, aecb_score O, aecb_date O, co_applicant (bool) O, co_applicant_profile O
**Requirement:** purpose M, property_status (Found/Searching) O, emirate O, community O, property_value O, ltv O, loan_amount O, down_payment O, cash_available O, tenor O, rate_preference (Fixed/Variable/Either) O, bank_type (Conventional/Islamic/Either) O, preferred_banks (M2M Bank) O
**Source & attribution:** source M, sub_source O, partner_link (locked on save) O, first_touch_timestamp (auto)
**System:** owner M, team O, priority M, tags O, status M, lead_score (auto), next_followup_date (M while open)

## 2.2 Consent 🆕
`Consent` model: client/lead FK, channel (Call/SMS/WhatsApp/Email), status (bool), timestamp, source (web/verbal/import), captured_by FK User. Consent step in create wizard.

## 2.3 Capture 🆕
- Web-to-lead API endpoint (public, honeypot + rate limit)
- Quick-create form (<10 sec: name, mobile, note)
- ✏️ 7-step wizard stays, extended fields

## 2.4 Eligibility check 🆕 (PRD 9.3) — values from Settings, NOT hardcoded
- DBR = (existing EMIs + new EMI at stress rate) / gross income ≤ cap (default 50%)
- Income multiple ≤ 7x annual (expat)
- LTV vs cap matrix (Settings)
- Cash-to-close = down payment + ~7% fees vs cash available
- Output: **Green/Amber/Red banner + plain reason**, stored on lead. Never blocks save.

## 2.5 Dedupe 🆕
Exact mobile/email → block + name owner. Fuzzy → warning + override (logged).

## 2.6 Assignment & SLA 🆕 (PRD 9.5)
- Assignment rules engine (source/language/loan-size/residency → user/round-robin/queue), daily caps, skip out-of-office
- First-contact SLA (default 15 min, config), countdown, escalate TL at breach, SDir at 2x
- My Queue (SLA risk → priority → score)

## 2.7 Statuses & cadence 🆕 (PRD 9.6)
Enforced transitions + mandatory reasons. Iron rule: open lead must have future task (nightly integrity check). Rotting badges (0-3/4-7/8+) ♻️.

## 2.8 Scoring / Import / Lost 🆕
Rule-based scoring (admin points). CSV import wizard (mapping, dedupe preview, batch tag, 24-hr undo). Lost reason codes (Appendix B) + Pareto; reopen with TL approval; nurture reactivation dates.

### 🧪 KHUD KAISE TEST KARE — Phase 2
1. **Advisor se login** karke **naya lead banao** with income + property details. Form pe **eligibility banner** turant dikhe (Green/Amber/Red) + reason (jaise "DBR 54% — loan kam karo").
2. Ek lead banao **DBR jaan-bujh kar 60%** → banner **Red** + reason dikhe. **Save phir bhi ho jaye** (block nahi).
3. **Same mobile se dubara lead banao** → ✅ system block kare + bole "ye lead already [owner] ke paas hai".
4. **Website form / API se test lead bhejo** → ✅ apne aap lead ban jaye, source stamp ho, SLA clock chalu ho.
5. **15 min tak lead ko touch mat karo** → ✅ Team Leader ko escalation notification aaye (ya SLA breach report mein dikhe).
6. **My Queue kholo** → leads SLA risk ke order mein dikhein (sabse urgent upar).
7. Lead ko **Lost mark karo** bina reason → ✅ system reason maange (dropdown se).
8. Ek **open lead se saara task complete kar do** → ✅ system agla follow-up task maange (iron rule).
9. **CSV file import karo** (2 duplicate rows daalke) → ✅ preview mein duplicate flag ho, import ke baad 24-hr undo option ho.

---
---

# PHASE 3 — Pipeline, Cases & Operations

**Goal:** Stage gates + multi-bank + operations daily workflow + tasks/timeline.

## 3.1 Stage & Gate config 🆕 (PRD 10.2)
- `Stage` model: name, order, target_days, is_active
- `StageGate` model: stage FK, required_items (docs/fields), auto_actions_on_entry
- Default mortgage stages: Docs Requested → Compliance Check → Submitted for Pre-Approval → Pre-Approved → Property & MOU → Valuation → Final Offer → Transfer Prep → Transferred & Disbursed → Completed
- Gate enforcement: blocked drag → show missing-items checklist. ♻️ Kanban wired to Case.

## 3.2 Multi-bank ✏️ (PRD 10.3)
Case stage = best application. One-click withdraw siblings on FOL accept. Rejection reason codes → per-bank Pareto. Timestamps for league table.

## 3.3 Operations 🆕 (PRD 11 & 12)
- Handover gate (docs Received+, KYC Passed, requirement complete) → Ops accepts → assign Ops Executive + completeness score
- Document verify/reject loop (reason + auto chase task)
- Follow-up log (mandatory next date; 3-day auto task, 7-day escalate)
- Query records (side ownership, due date)
- Subflows (each = fields on Case/BankApplication):
  - **Pre-approval:** amount, conditions, letter, issue_date, validity_end
  - **Valuation:** valuer, fee, appointment, report, value, shortfall
  - **FOL:** loan_amount, rate_type, **fixed_rate %, fixed_period_end_date (M on fixed)**, variable_index, margin, tenor, installment, processing_fee, early_settlement, insurance, offer_validity
  - **Buyout:** existing_lender, liability_letter_req/received/validity_end, settlement_amount, execution_date
  - **NOC:** request_date, fee, receipt, received_date, validity
  - **Transfer:** trustee_office, appointment, attendee_checklist, cheque_list (payee/amount/bank), deed_number, deed_upload
  - **Disbursement:** disbursement_date, amount (→ triggers finance)
- Ops queues (PRD 12.1): New Handovers, In Verification, Awaiting Bank, Query Raised, Valuations, FOL & Signing, Transfer This Week, Blocked/On Hold
- Load board (Ops Manager), dual ownership

## 3.4 Tasks & timeline ✏️ (PRD 14)
- ✏️ Task: polymorphic link, outcome-mandatory on complete, reschedule reason+count, recurring, reminders
- Auto-task creation (gates/SLA/expiry/rules)
- My Day queue, Calendar, meeting check-in (GPS optional)
- 🆕 Timeline model on every core record (field changes, notes, tasks, @mentions)

### 🧪 KHUD KAISE TEST KARE — Phase 3
1. **Advisor:** ek case banao, uski documents/KYC incomplete rakho. Kanban pe **case ko agle stage mein drag karo** → ✅ block ho, **missing-items list** dikhe.
2. Zaroori docs + KYC complete karo → ab drag **ho jaye**.
3. Case ko **3 banks** mein bhejo. Ek bank ko **FOL Issued** karo → ✅ baaki 2 applications **one-click Withdraw** ka option de.
4. **Advisor "Submit to Operations" dabaye** incomplete file pe → ✅ block ho (docs/KYC list dikhe). Complete karke submit → **Ops Manager ke queue** mein aaye.
5. **Ops Manager login** → case **Ops Executive ko assign** kare.
6. **Ops Executive:** ek document **Reject** kare reason ke saath → ✅ advisor ke liye **auto chase task** ban jaye.
7. Bank follow-up log karo bina next date → ✅ system next date maange. 7 working din silent → ✅ Ops Manager ko escalate + dashboard flag.
8. **Task complete karo** → ✅ outcome (Reached/No Answer...) mandatory maange.
9. **My Day** kholo → tasks SLA-risk order mein. Kisi record ka **Timeline** kholo → sab activity (kisne kya kiya) dikhe.

---
---

# PHASE 4 — Documents & Compliance

**Goal:** Controlled vault + expiry engine + KYC gate + audit.

## 4.1 Documents ✏️ (PRD 15)
- ✏️ Document fields: type, status (Pending/Received/Verified/Rejected/Expired), issue_date, expiry_date, uploaded_by/at, verified_by/at, rejection_reason, source (staff/client/email), version, superseded_by
- Vault: Client → Case → category (Identity/Income/Liabilities/Property/Bank/Compliance/Agreements/Generated). Identity docs shared across cases.
- 🆕 ChecklistTemplate per profile (Appendix A) + per-bank additions
- 🆕 **Expiry engine:** 30/14/7 day alerts, dashboard tile, auto re-open checklist item on expiry
- 🆕 One-click "Request documents" message; virus scan; size/type limit (default 25MB); access log
- 🆕 Document generation (templates + merge fields)

## 4.2 Compliance 🆕 (PRD 16)
- 🆕 `KYCCheck`: case FK, identity_verified, address_verified, income_consistent, screening_evidence (file), checker (FK Compliance), status (Pending/Passed/Rejected)
- **Hard gate:** case can't reach "Submitted for Pre-Approval" until KYC Passed. Passed = Compliance Officer only. Override = reason + time-boxed.
- 🆕 `RiskRating` (Low/Med/High rules) + `EDDChecklist` on High (source of funds/wealth, senior sign-off)
- 🆕 `UBO` capture for corporate borrowers
- 🆕 `SuspicionFlag` (Compliance-only visibility) + goAML metadata
- 🆕 `DSRRequest` (access/correction/anonymize) + retention schedule + purge queue
- ✏️ Complete audit trail (views/downloads/exports/logins), hash-chain tamper evidence

### 🧪 KHUD KAISE TEST KARE — Phase 4
1. Ek document upload karo jiski **expiry_date 10 din baad** ho → ✅ dashboard pe "expiring" alert dikhe (30/14/7 threshold pe).
2. **Ek document replace karo** (nayi file) → ✅ naya version bane, purana version bhi dikhe (overwrite na ho), status "Verified" reset ho.
3. **Advisor** ek case ko "Submitted for Pre-Approval" stage mein le jaane ki koshish kare **bina KYC pass** → ✅ block ho.
4. **Advisor khud KYC Pass karne ki koshish kare** → ✅ na kar paaye (sirf Compliance Officer).
5. **Compliance Officer login** → KYC Pass kare → ab case aage badh sake.
6. Ek client ko **High risk** banao → ✅ EDD checklist mandatory, bina uske KYC pass na ho.
7. **Suspicion flag** lagao → ✅ sirf Compliance Officer ko dikhe, advisor/ops ko nahi.
8. **Document download karo** → ✅ audit trail mein "viewed/downloaded by X" entry aaye.

---
---

# PHASE 5 — Finance, Client 360 & Post-Close

**Goal:** Invoice → receipt → payout, aur repeat-business engine.

## 5.1 Invoicing 🆕 (PRD 20)
- Accounts queue on disbursement (closing-pack verify)
- Expected commission = disbursed × bank rate (+ slabs/overrides, effective-dated)
- 🆕 `Invoice`: number (`INV-YYYY-####` gap-free), case FK, bank billing profile, lines, VAT (default 5%), status (Draft/Sent/Part-Paid/Paid/Overdue/Disputed), post-send lock
- 🆕 `InvoiceLine`, `CreditNote` (corrections only via credit note)
- Invoice PDF per Appendix D (TRN, line items, VAT/line, IBAN)

## 5.2 Receivables & payouts 🆕
- 🆕 `Receipt` (date, amount, bank ref, auto-match by invoice no, part payments)
- 🆕 `Variance` + clawback (flow to partner/advisor ledger)
- 🆕 `PayoutRun` + `PayoutLine` (segregation: preparer ≠ approver)
- 🆕 `IncentiveScheme` + `IncentiveLedger` (per-employee live)
- Month-end lock (CEO reopen), accrual vs receipt views
- ✏️ v0 finance views wired to real data

## 5.3 Client 360 & lifecycle 🆕 (PRD 13)
- Client 360 screen (header, profile, cases, properties, documents, timeline, money, relationships, next-best-action)
- Auto lifecycle stages
- Milestone messaging (submitted/pre-approved/valuation/FOL/transfer — auto or advisor-approved)
- 🆕 **Post-close program** + **fixed-rate expiry watch** (120/90 days → auto Buyout lead)
- Client referral capture

### 🧪 KHUD KAISE TEST KARE — Phase 5
1. Ek case ko **Disbursed** karo → ✅ **invoice auto-draft** ban jaye, VAT 5% sahi calc ho, number gap-free ho.
2. Invoice ko **Sent** karo → ✅ ab edit lock ho, correction sirf credit note se.
3. **Receipt record karo** (invoice se kam amount) → ✅ **Variance record** bane.
4. **Payout run banao** ek user se, **usi user se approve karne ki koshish** → ✅ na ho (segregation of duties).
5. **Client 360 kholo** → us client ki saari cases + timeline + invoices + referrals ek jagah dikhein.
6. Ek case ke FOL mein **fixed_period_end_date aaj se 120 din baad** daalo → ✅ system **auto Buyout lead** banaye (source: Existing Client, original advisor ko assign).

---
---

# PHASE 6 — Notifications, Automation, Partners & HR

**Goal:** System khud kaam kare + partner/HR modules.

## 6.1 Notifications & automation 🆕 (PRD 17)
- Notification center (in-app bell, categories, preferences over admin floor), email + push, digest, quiet hours
- Notification matrix (17.2) as editable config
- No-code automation builder (triggers/conditions/actions), run log, simulation mode, loop guards
- SLA engine (business-hours calendar + UAE holidays, warning %, escalations, hold-pause)
- Approval workflow framework (single/multi-step, delegation, decision log)

## 6.2 Partners 🆕 (PRD 19)
- ✏️ Referral partner + commission ledger
- 🆕 `ChannelPartner` (contacts, documents, agreement expiry)
- Onboarding: Draft → Pending Approval → Active (SDir approval); only Active attributable
- Locked attribution (SDir-only change + reason, conflict records)
- Commission models (effective-dated, accrual on receipt), monthly auto statements, payout runs, Partner 360

## 6.3 HR 🆕 (PRD 21)
- Employee master (manager hierarchy from Phase 1)
- HR document expiries (60/30/14), onboarding checklist, offboarding wizard (reassign owned records)
- 🆕 `AttendanceLog` (check-in/out, GPS geofence, optional selfie), `Shift`, regularization
- 🆕 `LeaveType`, `LeaveRequest`, `LeaveBalance` (approvals, team calendar)
- 🆕 `Target` + `TargetActual` (auto from CRM), progress bars
- Appraisals + HR letters

### 🧪 KHUD KAISE TEST KARE — Phase 6
1. **Automation rule banao** (admin config): "Case Completed hone ke 3 din baad thank-you task banao" → case complete karo → ✅ 3 din baad task auto-bane (ya simulation mode se turant test).
2. **SLA engine:** Friday shaam SLA start karo → ✅ weekend/UAE holiday count na ho (business hours only).
3. **Channel Partner banao** → attribute karne ki koshish karo bina approval → ✅ na ho. **SDir approve** kare → ab Active + attributable.
4. Ek lead ki **partner attribution badalne** ki koshish advisor se karo → ✅ na ho (sirf SDir + reason).
5. **Employee mobile se check-in** kare office ke **bahar se** → ✅ geofence fail dikhaye. Office ke andar → ✅ success.
6. **Leave request** karo → manager approve kare → ✅ team calendar pe dikhe + delegation prompt aaye.

---
---

# PHASE 7 — Reporting, Integrations, API & Hardening

**Goal:** Live dashboards, report catalogue, API-first, production-ready.

## 7.1 Reporting 🆕 (PRD 18)
- 10 role dashboards wired to LIVE data — every number drill-down to record list
- Report catalogue (18.2): funnel, source ROI, speed-to-lead, advisor scorecard, pipeline aging, stage TAT, bank league, rejection Pareto, lost Pareto, disbursement register, receivables, commission variance, partner statements, etc.
- Saved views (personal/shared), scheduled delivery (PDF/XLSX), watermarked audited exports
- KPI formulas (Appendix E) in tooltips

## 7.2 Integrations 🆕 (PRD 23)
- Public REST API (scoped keys, rate limits) + webhooks — API parity with UI
- Web-to-lead + ad webhooks (Meta/Google)
- Transactional email, mailbox + calendar sync
- WhatsApp Business API, telephony (click-to-call, screen-pop)
- (later) e-sign, OCR, screening API, accounting export, payment links, SSO

## 7.3 Hardening ✏️ (PRD 26)
- SQLite → **PostgreSQL** (JSONB custom fields), S3-compatible storage
- 🆕 Custom fields engine + layout editor (Section 22.4) — no schema change for new fields
- 🆕 Admin config studio (masters, numbering, localization, branding, templates, feature flags, sandbox)
- Performance (95p <2s), backups, encryption, pen test, observability
- Global search (Cmd-K) ♻️

### 🧪 KHUD KAISE TEST KARE — Phase 7
1. **CEO dashboard** kholo → kisi bhi number pe **click karo** → ✅ us number ke peeche ki record list khule (drill-down).
2. **Report schedule karo** (jaise weekly funnel) → ✅ email pe PDF/XLSX aaye. Export pe **watermark + audit entry**.
3. **Website form / API se lead bhejo** → ✅ lead bane (jo UI se hota hai wo API se bhi ho).
4. **WhatsApp milestone message** trigger karo bina consent → ✅ na jaye (consent check).
5. **Admin naya custom field** add kare (jaise "Referral Code") bina developer → ✅ form pe dikhe.
6. **Global search (Cmd-K)** se client/case/bank dhoondo → ✅ turant result (sub-second).

---
---

## Cross-cutting (har phase mein zaroori)
- Config over code (regulatory values Settings mein, effective-dated)
- Soft delete + audit on every write
- Field-level security server-side
- +971 mobile normalize, dates DD MMM YYYY, timezone Asia/Dubai
- Mobile-responsive, 2 clicks to common actions
- v0 screens never regressed

---

## Build Log — Phase-1 Feature Completion (2026-07-20)

Phase-1 completed feature-wise (local, not pushed). Verified against PRD PDF.

**8 correctness fixes:** KYC=Compliance-only; eligibility/SLA moved to Settings (config-over-code); bank commission captured + hidden from advisor; Task/Document soft-delete + recycle bin; atomic gap-free case numbers; SLA + silence escalation commands; audit append-only + export/config logging; report drill-downs (8/9). Plus Compliance/Auditor audit-trail access.

**Sequence #1–#9:**
1. Client + Case data model (Client entity + lifecycle).
2. Ops workflow — handover gate, dual ownership, hold, 8 named queues, structured FOL/valuation/pre-approval/query/rework/title-deed/insurance, buyout engine.
3. Compliance depth — sanctions/PEP screening, risk+EDD, suspicion flags, DSR, KYC override, compliance workspace.
4. SLA business-hours engine (calendar + holidays + 80% warning + hold-pause).
5. Consent object (ConsentRecord + DNC enforcement).
6. Segregation of duties / approval workflows.
7. My Day.
8. Web-to-lead API.
9. CSV import polish (preview + undo).

Migrations 0028–0038. All 19 key pages 200 OK across roles. `manage.py check` clean.

Remaining = Phase-2 / governance / vendor-gated items + process sign-off (parallel run, migration, pen-test). See memory `phase1-remaining-work`.

## Build Log — Phase-1 Must-have gap closure (2026-07-20, cont.)

Closed 6 of 8 remaining Phase-1 Must-have build gaps (email NA-02 + REST API IN-01 skipped per client):
- LM-08 assignment rules engine (`AssignmentRule` + ordered rules + daily caps + out-of-office in `_auto_assign_advisor`)
- LM-12 nightly integrity check (`check_integrity` command — open lead w/o future task → advisor+TL)
- LM-16 nurture pool + reactivation (`nurture_until` + `check_nurture` command + lead nurture action)
- NA-04 editable notification matrix (`NotificationPref` per-user mute + mandatory floor in `_notify`; `/notification-prefs/`)
- RP-04 saved views (`SavedView` + save/apply on lead list)
- RP-07/DM-05 watermark on CSV exports (confidential provenance row) + already audited

Migration 0039. All 21 key pages 200 OK. `manage.py check` clean.
Remaining (skipped/deferred): NA-02 email/push (needs SMTP), IN-01 full REST API, FR-01 2FA/auth (user-deferred). Plus §28 process/exit-criteria.
