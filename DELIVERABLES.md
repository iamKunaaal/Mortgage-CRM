# BHITR CRM — Deliverables & Development Status Tracker

**Source of truth:** BHITR CRM Product Requirements Document v1.0 (02 July 2026, 76 pages)
**Baseline:** v0 front-end prototype → now a working Django backend (deployed on Railway, Postgres)
**Status legend:** ✅ Done & working · 🟡 Partial (basic version live, PRD depth pending) · ⬜ Not started
**Dependency legend:** 🔧 Pure development (fully in our control) · 🔌 Needs 3rd-party paid service/account · 📋 Needs business/legal sign-off (not code)
**Last updated:** 11 July 2026
**Verification:** every ✅/🟡 claim was cross-checked against the actual code (routes, views, models, templates); all PRD requirement IDs (Sections 8, 24, 25) are traced to a status. No status is asserted without code proof.

> **Is everything buildable?** Yes — nothing is architecturally impossible on Django + Postgres. But items tagged 🔌 need the client to choose & pay for an external vendor (WhatsApp BSP, telephony, e-sign, screening, OCR, accounting), and 📋 items need a business/legal decision (UAE hosting region, compliance values, pen test), not code.

---

## SUMMARY AT A GLANCE

| Phase | Theme | Status |
|---|---|---|
| **Phase 1** | Production MVP (auth, roles, leads, pipeline, ops core, docs, tasks, dashboards) | 🟡 **~40% done** — core CRUD, 5 roles, dashboards, leads, docs, tasks live; role model depth, Client/Case/Bank Application model, SLA engine, compliance gate, real audit infra pending |
| **Phase 2** | Ops depth, invoicing, partners, automation, HR, WhatsApp/telephony | ⬜ Not started |
| **Phase 3** | Intelligence & compliance automation (screening, OCR, e-sign, report builder) | ⬜ Not started |
| **Phase 4** | Client/partner portals, mobile app, industry packs | ⬜ Not started |

**Extra delivered beyond PRD (client-requested):** CEO Customization revenue sheet, per-lead Audit Log tab, custom date-range filter, CEO-only delete, deploy pipeline.

---

# PHASE 1 — PRODUCTION MVP

## 1. Authentication & Access (FR-01, AD-01, AD-02)

| ID | Requirement | Priority | Status | Dep | Notes |
|---|---|---|---|---|---|
| FR-01 | Email/password login, TOTP 2FA, lockout, session timeout, device history | M | 🟡 | 🔧 | Login/logout live. Pending: 2FA, lockout, session timeout, login/device history, password rotation |
| AD-01 | User admin: invite, deactivate, reactivate, force reset, kill sessions, IP allowlist | M | 🟡 | 🔧 | Create/edit/activate/deactivate/set-password live. Pending: force-reset, kill-sessions, 2FA reset, login history, IP allowlist |
| AD-02 | Role editor + field-level security | M | 🟡 | 🔧 | Module × role matrix editable & persisted. Pending: per-action switches, field-level security editor |

## 1b. User Roles & Permissions (Section 8) — the role model itself

PRD ships **13 default roles** with Own/Team/All hierarchy, field-level security, delegation, segregation of duties. Current app has **5 roles** + module-level matrix.

| Requirement | Priority | Status | Dep | Notes |
|---|---|---|---|---|
| Role catalogue: 13 roles | M | 🟡 | 🔧 | Have 5 (CEO, Sales Director, Ops Manager, Advisor, Accountant). Missing 8: Super Admin, Team Leader, Operations Executive, HR Executive, Compliance Officer, Marketing Executive, Telecaller (optional), External Auditor |
| Module permission matrix (per role) | M | 🟡 | 🔧 | Editable & persisted. Pending: ~300 per-action switches (F/T/O/R/A/E per PRD 8.3) |
| Field-level security | M | ⬜ | 🔧 | Hide bank commission % from Advisor/TL; salary HR+CEO only; masked identity docs for Marketing/Telecaller; suspicion flags Compliance-only |
| Own / Team / All visibility hierarchy | M | 🟡 | 🔧 | Own & All work. Pending: Team scope (needs Team Leader + manager hierarchy HR-01) |
| One primary role + per-user overrides | M | ⬜ | 🔧 | Single role only; no extra grants |
| Delegation (leave cover) with logging | S | ⬜ | 🔧 | |
| Segregation of duties (creator ≠ approver) | M | ⬜ | 🔧 | Needs approval workflows (NA-08) |
| Watchers (follow a record) | S | ⬜ | 🔧 | |
| Reassignment with reason + notify + task transfer | M | 🟡 | 🔧 | Advisor reassign + audit exist; reason/notify/task-transfer pending |

## 2. Lead Management (LM) — Section 9

| ID | Requirement | Priority | Status | Dep | Notes |
|---|---|---|---|---|---|
| LM-01 | Seven-step lead wizard, extended fields | M | 🟡 | 🔧 | Wizard + core fields saved. Pending: employment/DOB/residency/income/property-type fields (shown in UI, not saved) |
| LM-02 | Web-to-lead API endpoint + spam controls | M | ⬜ | 🔧 | |
| LM-03 | Ad-platform webhook capture + campaign attribution | S | ⬜ | 🔌 | Meta/Google lead ads |
| LM-04 | Quick-create (<10s) | M | ⬜ | 🔧 | |
| LM-05 | Instant eligibility check (DBR, LTV cap, cash-to-close) | M | ⬜ | 🔧📋 | Logic is dev; the cap numbers need compliance sign-off |
| LM-06 | Exact-duplicate block + fuzzy warning | M | ⬜ | 🔧 | |
| LM-07 | Merge tool preserving timelines | S | ⬜ | 🔧 | |
| LM-08 | Assignment rules engine | M | ⬜ | 🔧 | Manual assign only |
| LM-09 | First-contact SLA countdown + escalation | M | ⬜ | 🔧 | |
| LM-10 | My Queue ordered by SLA/priority/score | M | ⬜ | 🔧 | |
| LM-11 | Status set with enforced transitions + reasons | M | 🟡 | 🔧 | Stages + lost reason live; enforced transitions pending |
| LM-12 | Iron rule: open lead always has future task | M | ⬜ | 🔧 | |
| LM-13 | Attempt cadence + auto-move to Nurture | S | ⬜ | 🔧 | |
| LM-14 | Rule-based lead scoring | S | ⬜ | 🔧 | |
| LM-15 | CSV import wizard | M | ⬜ | 🔧 | Export live; import pending |
| LM-16 | Nurture reactivation + auto tasks | M | ⬜ | 🔧 | |
| LM-17 | Lost reason codes + Pareto; reopen w/ approval | M | 🟡 | 🔧 | Lost leads + restore live; Pareto + approval pending |
| LM-18 | Consent capture per channel | M | ⬜ | 🔧 | |
| — | List/edit/delete/bulk/export/pipeline/sources/notes | — | ✅ | 🔧 | Working (delete CEO-only) |

## 3. Pipeline & Cases (PL) — Section 10

| ID | Requirement | Priority | Status | Dep | Notes |
|---|---|---|---|---|---|
| PL-01 | Lead → Client + Case conversion | M | ⬜ | 🔧 | **Major gap:** no separate Client/Case entity — lead is one flat track |
| PL-02 | Configurable stages + entry/exit gates + target days | M | 🟡 | 🔧 | Stages configurable + reorder/toggle. Pending: gates, conditions, target days |
| PL-03 | Gate-blocked kanban + missing-items checklist | M | ⬜ | 🔧 | Kanban exists; no gates |
| PL-04 | In-stage aging bands + rotting badges | M | ✅ | 🔧 | Live |
| PL-05 | On Hold/Declined/Withdrawn + SLA pause | M | 🟡 | 🔧 | Declined only |
| PL-06 | Backward moves require reason; skip rules | S | ⬜ | 🔧 | |
| PL-07 | Weighted forecast from stage probabilities | S | ⬜ | 🔧 | Phase 2 |
| PL-08 | Pipeline value toggle: loan vs revenue (role-gated) | S | 🟡 | 🔧 | Value shown; toggle pending |
| PL-09 | Stage history timestamps for TAT | M | 🟡 | 🔧 | Changes logged in audit; TAT table pending |
| PL-10 | Case reference numbering | M | ⬜ | 🔧 | |

## 4. Bank Applications (BA) — Section 10.3

| ID | Requirement | Priority | Status | Dep | Notes |
|---|---|---|---|---|---|
| BA-01→06 | Multiple bank apps per case, independent status/refs, follow-up dates, rejection Pareto, timestamps | M/S | ⬜ | 🔧 | Not started — needs Case model. Currently one lead = one bank (single FK) |

## 5. Operations Processing (OPS) — Sections 11–12

| ID | Requirement | Priority | Status | Dep | Notes |
|---|---|---|---|---|---|
| OPS-01 | Handover gate (docs, KYC, data) | M | ⬜ | 🔧 | |
| OPS-02 | Ops acceptance + completeness score | M | ⬜ | 🔧 | |
| OPS-03 | Per-doc verify/reject loop + auto chase tasks | M | 🟡 | 🔧 | Verify/reject/reupload live; auto chase-task pending |
| OPS-04 | Per-bank submission checklists | S | ⬜ | 🔧 | |
| OPS-05 | Follow-up log + 3/7-day silence rules | M | ⬜ | 🔧 | |
| OPS-06 | Query records + ownership + due dates | M | ⬜ | 🔧 | |
| OPS-07 | Pre-approval capture + validity countdown | M | ⬜ | 🔧 | |
| OPS-09 | FOL structured terms + fixed-period end date | M | ⬜ | 🔧 | |
| OPS-13 | Deed/disbursement capture → finance | M | ⬜ | 🔧 | |
| OPS-14 | Ops queues + load board + reassignment | M | ⬜ | 🔧 | |
| OPS-15 | Rework counters | S | ⬜ | 🔧 | |
| OPS-16 | Dual ownership (advisor + ops owner) | M | ⬜ | 🔧 | |
| OPS-08,10,11,12 | Valuation / Buyout / NOC / Transfer subflows | M | ⬜ | 🔧 | Phase 2 |

## 6. Client Lifecycle (CL) — Section 13

| ID | Requirement | Priority | Status | Dep | Notes |
|---|---|---|---|---|---|
| CL-01 | Client entity + 360 view | M | ⬜ | 🔧 | No Client entity yet |
| CL-02 | Automatic lifecycle stages | S | ⬜ | 🔧 | |
| CL-08 | Consent badges + do-not-contact enforcement | M | ⬜ | 🔧 | |
| CL-03→07 | Milestone messaging, post-close, buyout watch, referrals, next-best-action | M/S/C | ⬜ | 🔧🔌 | Phase 2/3; messaging needs WhatsApp/email vendor |

## 7. Tasks & Activities (TA) — Section 14

| ID | Requirement | Priority | Status | Dep | Notes |
|---|---|---|---|---|---|
| TA-01 | Task object: types, links, reminders, outcomes | M | 🟡 | 🔧 | Core live; outcomes/reminders pending |
| TA-02 | Mandatory outcome + reschedule reason/counters | M | ⬜ | 🔧 | Complete works |
| TA-03 | Auto task creation from gates/SLAs/expiries/rules | M | ⬜ | 🔧 | |
| TA-04 | My Day ordered queue + one-click actions | M | ⬜ | 🔧 | |
| TA-05 | Calendar views + meetings w/ check-in | S | ⬜ | 🔌 | Calendar sync needs Google/MS |
| TA-06 | Recurring tasks | S | ⬜ | 🔧 | |
| TA-07 | TL daily digest | M | ⬜ | 🔧 | |
| TA-08 | Unified timeline + @mentions on all records | M | 🟡 | 🔧 | Lead audit-log + notes live; @mentions + all-entity timeline pending |
| — | Task list/overdue/create/complete/export | — | ✅ | 🔧 | Working |

## 8. Documents (DM) — Section 15

| ID | Requirement | Priority | Status | Dep | Notes |
|---|---|---|---|---|---|
| DM-01 | Client/case/category vault + shared identity docs | M | 🟡 | 🔧 | Per-lead docs live; vault structure pending |
| DM-02 | Checklist templates by profile + per-bank | M | ⬜ | 🔧 | |
| DM-03 | Statuses + verify identity/timestamps | M | 🟡 | 🔧 | Statuses + verified-**by** live; verified-**at** not stored |
| DM-04 | Versioning + supersede + per-version access | M | ⬜ | 🔧 | |
| DM-05 | Inline preview + logged permissioned downloads | M | 🟡 | 🔧 | View/open live; download logging/watermark pending |
| DM-06 | Expiry engine (30/14/7 alerts + tile) | M | ⬜ | 🔧 | |
| DM-07 | One-click document request messages | S | ⬜ | 🔌 | Needs messaging channel |
| DM-08 | Secure tokenized client upload links + scanning | S | ⬜ | 🔧 | Phase 2 |
| DM-11 | Virus scan, size/type limits | M | ⬜ | 🔌 | Antivirus scan service |
| DM-12 | Access log of views/downloads | M | ⬜ | 🔧 | |
| — | Upload (lead form + per-lead + title deed), verify/reject/reupload, export | — | ✅ | 🔧 | Working |

## 9. Compliance & Audit (CO) — Section 16

| ID | Requirement | Priority | Status | Dep | Notes |
|---|---|---|---|---|---|
| CO-01 | KYC checklist + Compliance-only gate blocking stage 3 | M | ⬜ | 🔧 | |
| CO-02 | Screening evidence storage (manual) | M | ⬜ | 🔧 | |
| CO-04 | UBO capture + screening for corporates | S | ⬜ | 🔧 | Phase 2 |
| CO-05 | Consent objects per channel + DNC flag | M | ⬜ | 🔧 | |
| CO-09 | Append-only audit + recycle bin | M | 🟡 | 🔧 | **Lead-level audit live.** Pending: system-wide audit (logins/exports/config/views), append-only/hash-chain, recycle bin, **no-hard-delete (leads currently hard-deleted)** |

## 10. Notifications & Automation (NA) — Section 17

| ID | Requirement | Priority | Status | Dep | Notes |
|---|---|---|---|---|---|
| NA-01 | In-app notification center + preferences | M | ⬜ | 🔧 | Bell decorative |
| NA-02 | Email + push delivery; digest + quiet hours | M | ⬜ | 🔌 | Email service |
| NA-03 | WhatsApp template channel | M | ⬜ | 🔌 | WhatsApp BSP |
| NA-04 | Shipped notification matrix as config | M | ⬜ | 🔧 | |
| NA-05 | No-code automation builder | M | ⬜ | 🔧 | Phase 2 |
| NA-06 | Rule run log + simulation + loop guards | M | ⬜ | 🔧 | Phase 2 |
| NA-07 | SLA engine (business-hours + holidays + hold-pause) | M | ⬜ | 🔧 | |
| NA-08 | Approval workflow framework + delegation | M | ⬜ | 🔧 | Phase 2 |

## 11. Reporting & Dashboards (RP) — Section 18

| ID | Requirement | Priority | Status | Dep | Notes |
|---|---|---|---|---|---|
| RP-01 | Role dashboards with live data | M | 🟡 | 🔧 | CEO/management fully live. Advisor dashboard mostly live but **calls metric (1486) + partner target are demo/hardcoded**. Pending: SDir/TL/Ops/Accounts/HR/Compliance/Marketing dashboards |
| RP-02 | Drill-down to record list | M | 🟡 | 🔧 | Some links; full click-through pending |
| RP-03 | Report catalogue (top 8 Phase 1) | M | 🟡 | 🔧 | Reports/finance/sources pages live; formal catalogue pending |
| RP-04 | Saved views (personal + shared) | M | ⬜ | 🔧 | Client-side filters live; saving pending |
| RP-05,06,08 | Report builder + scheduling + KPI tooltips | S | ⬜ | 🔧 | Phase 3 |
| RP-07 | Watermarked, audited exports | M | 🟡 | 🔧 | CSV export live; watermark/audit pending |

## 12. Masters & Settings (AD)

| ID | Requirement | Priority | Status | Dep | Notes |
|---|---|---|---|---|---|
| AD-03 | Masters admin (banks/doc types/sources/reasons…) + safe deactivate | M | 🟡 | 🔧 | Banks (full CRUD), doc types, sources, source on/off live. Pending: valuers, trustee offices, insurers, reason codes, tags, offices, holidays, tiers; merge-on-deactivate |
| AD-04 | Stage/checklist/SLA/automation/approval editors | M | 🟡 | 🔧 | Stage editor + reorder/toggle live; rest pending |
| AD-05 | Custom fields + layout editor | M | ⬜ | 🔧 | Phase 2 |
| AD-06 | Numbering, localization, branding | M | ⬜ | 🔧 | **Not implemented** — Settings "Company" tab is actually personal My Profile only; no company entity/branding/numbering |
| AD-07 | Template studio | M | ⬜ | 🔧 | Phase 2 |
| AD-08 | Feature flags per module | S | ⬜ | 🔧 | Phase 2 |
| AD-09 | Masked-data sandbox refresh | S | ⬜ | 🔧 | Phase 2 |
| AD-10 | Security settings + backups panel | M | ⬜ | 🔧 | |

## 13. Partner Management — Phase-1 scope (PM) — Section 19

| ID | Requirement | Priority | Status | Dep | Notes |
|---|---|---|---|---|---|
| PM-01 | Referral partner records + commission ledger | M | 🟡 | 🔧 | List + create + real aggregates live; formal ledger pending |
| PM-05 | Locked attribution; SDir-only changes + conflict records | M | 🟡 | 🔧 | Link exists; locking/reason/history/conflict pending |
| PM-02,03,04,06→10 | Channel partners, onboarding, statements, payouts, tiering | M/S/C | ⬜ | 🔧 | Phase 2/3 |

## 14. HR — Phase-1 scope (HR) — Section 21

| ID | Requirement | Priority | Status | Dep | Notes |
|---|---|---|---|---|---|
| HR-01 | Employee master + manager hierarchy feeding permissions | M | 🟡 | 🔧 | User records (role/phone/dept/status/targets) exist; manager/reporting hierarchy pending |
| HR-02→10 | Attendance, leave, targets, appraisals, HR letters | M/S | ⬜ | 🔧 | Phase 2/3; attendance geofence needs Google Maps 🔌 |

## 15. API & Integrations (IN)

| ID | Requirement | Priority | Status | Dep | Notes |
|---|---|---|---|---|---|
| IN-01 | REST API + scoped keys + rate limits + webhooks | M | ⬜ | 🔧 | |
| IN-02 | Web-to-lead + ad webhooks | M | ⬜ | 🔧🔌 | Web-to-lead is dev; ad webhooks need Meta/Google |
| IN-03 | Email + mailbox/calendar sync | M | ⬜ | 🔌 | Google/MS 365 |
| IN-04 | Telephony + WhatsApp | M | ⬜ | 🔌 | CPaaS + WhatsApp BSP |
| IN-05 | E-sign, OCR, screening, accounting, payment | S | ⬜ | 🔌 | Multiple vendors |
| IN-06 | SSO with enforced 2FA | S | ⬜ | 🔌 | Google/MS |
| FI-02 | Expected commission from bank rates | M | 🟡 | 🔧 | Finance + Customization compute it; slabs/overrides/effective-dating pending |

---

# PHASE 2 — OPERATIONAL DEPTH & ENGAGEMENT ⬜ (Not started)

- 🔧 **Ops subflows:** Valuation, Buyout, NOC, Transfer (OPS-08,10,11,12); ops queues (OPS-14)
- 🔧 **Channel Partners:** entity, contacts, docs, agreements, onboarding + approval, statements, partner 360 (PM-02,03,04,06,07,08,09)
- 🔧 **Finance:** accounts queue, one-click tax invoice (numbering/TRN/VAT/PDF), invoice lifecycle + credit notes, receivables, receipts, variance/clawback, payout runs, incentive engine, month-lock (FI-01,03→11)
- 🔧 **Automation & approvals:** no-code builder, run log/simulation, approval framework (NA-05,06,08)
- 🔌 **Comms:** WhatsApp (NA-03), telephony, mailbox/calendar (IN-03,04); SSO (IN-06)
- 🔧🔌 **Client lifecycle:** milestone messaging, post-close, buyout watch, referrals (CL-03→06)
- 🔧 **Documents:** secure tokenized upload links (DM-08)
- 🔧 **Compliance:** UBO capture + screening (CO-04)
- 🔧🔌 **HR:** attendance (geofence/selfie 🔌 Maps), leave, targets, onboarding/offboarding (HR-02→09); appraisals + HR letters (HR-10)
- 🔧 **Admin:** template studio, custom fields, sandbox, feature flags (AD-05,07,08,09)

# PHASE 3 — INTELLIGENCE & COMPLIANCE AUTOMATION ⬜ (Not started)

- 🔌 Screening API, 🔧 DSR tooling, retention execution, risk/EDD (CO-03,06,07,08,10)
- 🔌 OCR + e-signature (DM-09,10; IN-05)
- 🔧 Lead scoring v2 + weighted forecast (LM-14 v2, PL-07)
- 🔧 Report builder + scheduling + KPI tooltips (RP-05,06,08)
- 🔌 Accounting export (Zoho/QuickBooks/Tally) + payment links (FI-12, IN-05); client fee flows
- 🔧 Partner tiering mapped to slabs (PM-10)

# PHASE 4 — PORTALS & EXPANSION ⬜ (Not started)

- 🔧 Client portal + mobile app; partner portal; marketing campaign module; industry packs; multi-company groundwork

---

# CROSS-CUTTING (FR) & NON-FUNCTIONAL STATUS (Section 25–26)

| ID | Area | Status | Dep | Notes |
|---|---|---|---|---|
| FR-01 | Auth: login, 2FA, lockout, session, device history | 🟡 | 🔧 | Login live; rest pending |
| FR-02 | Global search across entities, permission-trimmed | ⬜ | 🔧 | Command-palette UI only, not wired |
| FR-03 | Record standards: owner/timeline/watchers; soft delete + recycle bin | ⬜ | 🔧 | **Currently hard-deletes — PRD non-negotiable** |
| FR-04 | Concurrency: optimistic locking + conflict prompt | ⬜ | 🔧 | |
| FR-05 | Validation: mobile, email, EID checksum, IBAN, server-side mandatory-by-stage | 🟡 | 🔧 | UI-level mobile/email; server-side + EID/IBAN pending |
| FR-06 | Lists: server pagination/sort, column chooser, saved filters, bulk + audit | 🟡 | 🔧 | Client-side filter/sort + bulk live; server pagination + saved filters pending |
| FR-07 | Time: UTC storage, Asia/Dubai display, DD MMM YYYY | 🟡 | 🔧 | TZ set; per-user tz + formatting pending |
| FR-08 | Files: streamed, scanned, never lost on error | 🟡 | 🔧🔌 | Uploads persist; virus scan (🔌) pending |
| FR-09 | Errors/empty states + support reference IDs | ⬜ | 🔧 | |
| FR-10 | API parity for all UI actions | ⬜ | 🔧 | |
| FR-11 | Notifications delivery: at-least-once + retry | ⬜ | 🔧 | |
| FR-12 | Accessibility/input basics | 🟡 | 🔧 | v0 basics; formal a11y pass pending |
| FR-13 | Audit hooks on every write path | 🟡 | 🔧 | Lead writes audited; system-wide pending |
| FR-14 | Localization readiness (UTF-8, RTL, externalized strings) | ⬜ | 🔧 | English hardcoded |
| — | UAE-region hosting (PDPL) | ⬜ | 📋 | Railway region check; may need AWS Bahrain — client decision |
| — | Append-only / hash-chained audit infra | 🟡 | 🔧 | Lead audit exists; tamper-evidence pending |
| — | OWASP ASVS L2 / pen test | ⬜ | 📋 | Security audit firm |
| — | Backups + PITR + restore test | 🟡 | 🔧 | Railway managed backups; formal RPO/RTO + restore test pending |
| — | Compliance values (LTV/DBR/retention) in config | ⬜ | 📋 | Client's compliance advisor verifies numbers |

---

# EXTRA WORK DELIVERED (client-requested, beyond PRD register)

| Item | Status | Notes |
|---|---|---|
| **CEO Customization revenue sheet** | ✅ | Add-to-Customization from All Leads (CEO only); auto-calc Actual Rev / VAT (editable) / With VAT / Broker Rev / Payout / Final Rev from Slab %; matches CRM Ref.xlsx; per-row Bank RM/CP edit; totals + CSV export |
| **Per-lead Audit Log tab** | ✅ | Who changed what (name/number/stage/advisor/etc.), old→new value, user + role, date-time stamp |
| **Custom date-range filter (All Leads)** | ✅ | Popup date picker, from/to filtering |
| **CEO-only delete** | ✅ | Delete restricted to CEO (UI + backend) |
| **Deploy pipeline** | ✅ | Railway + Postgres, gunicorn/whitenoise, media serving, migrations on deploy |

---

# TOP PRIORITIES TO COMPLETE PHASE 1 (recommended order)

1. **Full role model** (Section 8) — 8 missing roles, field-level security, Own/Team/All hierarchy, per-user overrides 🔧
2. **Client + Case + Bank Application data model** (PL-01, BA-01) — foundational for ops/finance 🔧
3. **No-hard-delete + recycle bin + system-wide audit** (CO-09, FR-03) — PRD non-negotiable 🔧
4. **2FA + session/login history + lockout** (FR-01) 🔧
5. **SLA engine + first-contact SLA + iron-rule future task** (NA-07, LM-09, LM-12) 🔧
6. **Handover gate + ops follow-up log + pre-approval/FOL/disbursement capture** (OPS-01,05,07,09,13) 🔧
7. **KYC hard gate + consent capture** (CO-01, CO-05, LM-18) 🔧
8. **Eligibility check, dedup, assignment rules, import wizard** (LM-05,06,08,15) 🔧📋
9. **Notification center + role dashboards completion** (NA-01, RP-01) 🔧
10. **REST API + web-to-lead** (IN-01, IN-02) 🔧
11. **Save all lead-wizard fields to DB** (LM-01 full) 🔧
12. **Company profile + branding + numbering** (AD-06) 🔧

---

## VENDOR / EXTERNAL DEPENDENCIES SUMMARY (🔌 items — client must choose & pay)

| Need | For which features | PRD ref |
|---|---|---|
| WhatsApp Business API (BSP) | WhatsApp notifications, doc requests, shared inbox | OD-2 |
| Telephony / CPaaS | Click-to-call, screen-pop, call recording | OD-1 |
| Transactional email service | All email notifications & digests | — |
| SMS gateway (optional) | SMS notifications | — |
| E-signature vendor | Agreements, FOL signing | OD-4 |
| Screening data provider | Sanctions / PEP checks (else manual) | — |
| OCR service | Auto-read passport/EID/salary cert | — |
| Accounting connector | Zoho Books / QuickBooks / Tally export | — |
| Google/Microsoft workspace | SSO, mailbox & calendar sync | — |
| Google Maps | Attendance geofence, address normalization | — |
| Antivirus scan service | Upload virus scanning | — |

## BUSINESS / LEGAL SIGN-OFF SUMMARY (📋 items — not code)

| Need | Detail | PRD ref |
|---|---|---|
| UAE hosting region | PDPL requires UAE-region infra; Railway region to verify or move (e.g. AWS Bahrain) | OD-3 |
| Compliance values | LTV caps, DBR, cash-to-close %, retention years — verified by compliance advisor, held in config not code | §4.3, App G |
| Penetration test | Before go-live and annually | §26 |
