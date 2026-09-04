# Aug-31 Client Meeting — Task List

Recording: https://fathom.video/share/FEUCLQ8ZLzapjGx1XKtyPgZrwmM5JXzj

## Bugs
- [x] B1. Lead source "Referral Partner" not saving — FIXED (edit form now preselects saved partner)
- [x] B2. HR attendance: 1-min check-in/out counts as full day → FIXED (Present needs full_day_hours; else Half Day/Absent; config 'hr')
- [ ] B3. Finance Hub numbers wrong → fix data (NEED: which numbers exactly)

## Operations Queue
- [x] O1. Visual graph by case status — DONE (bar chart on Ops board)
- [x] O2. "Hold" status with mandatory reason — DONE (mandatory reason + Hold badge on board)
- [ ] O3. "Due Tasks" section flagging cases with no activity

## Tasks
- [x] T1. Mandatory 30-word remark to complete a task — DONE (backend + frontend)
- [ ] T2. On "Logged In" status → popup to populate "Assigned Mortgage" field (NEED: what is Assigned Mortgage field)

## Disbursement
- [ ] D1. Disbursement form auto-populate from loan data
- [ ] D2. Add "Sub Total" field
- [ ] D3. Admin-only "Delete" button
- [ ] D4. Monthly Disbursement Pipeline: total monthly disbursement + total loan amount; filter by bank (e.g. ADIB)

## Finance
- [ ] F1. Finance Hub: clickable calendar filter (monthly / yearly / quarterly)

## Bulk Leads
- [ ] L1. Bulk lead CSV template simplify (Name + Phone only)
- [ ] L2. Manager lead tracking system (assigned leads + team progress)

## Email
- [ ] E1. Email integration (₹800/month plan), markerfinance.com domain for customer-facing email

## Bulk Leads
- [x] L1. Bulk lead CSV template simplify (Name + Phone only) — DONE (phone→mobile alias, sample updated)

## Lead status (added)
- [x] S1. Escalated persists after stage change — FIXED (last_activity_at = max(followup, updated_at))
- [x] S2. "Rejected" / "Not Proceeding" stage + mandatory Reason field — DONE

## Rule
- For anything asked to be REMOVED: HIDE it, do NOT delete outright.

## Notes
- Domain: beter.com unavailable; beter.in India-specific; decision → use markerfinance.com for customer email.
- Kaushal to message contact re CRM ranking feedback.

## E-sign (added)
- [x] ES1. Multiple documents per sign request — select many files, place boxes per doc, signer signs all in one list, download merged signed PDF. Model SignDocument (migration 0004).

## Removals
- Client asked to remove/hide something in the recording — NOT in the shared notes. Awaiting exact names from user; will HIDE (not delete).
