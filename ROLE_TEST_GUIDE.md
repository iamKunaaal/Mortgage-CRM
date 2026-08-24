# BITAR CRM — Role-by-Role Test Guide (foolproof, click-by-click)

Ye guide **har role ke liye alag** hai — Advisor, Compliance, Operations, Accountant, CEO/Manager, HR. Har section me: us role se **login** karo, aur **exactly jo likha hai wo click/type karo**. Har step me **✅ kya dikhna chahiye** likha hai. Agar ✅ na dikhe → "⚠️" line padho.

Roles ek lead ke **flow** me hain: Advisor lead laata hai → Compliance KYC karta hai → Operations process karta hai → Accountant paisa handle karta hai → CEO approve karta hai. Isi order me karo, ek hi lead (**Ahmed Test**) pe — poora system samajh aa jayega.

Test **local** pe: `http://127.0.0.1:8000/`

---

## STEP 0 — Pehle ek baar: har role ka test-user banao (CEO se)
1. `http://127.0.0.1:8000/` → CEO account se **Login**.
2. Left menu → **Users** (ya User Management) → **New User**.
3. Ye 5 users banao (har baar: naam + username + password + **Role** choose + Save):
   | Naam | Role |
   |---|---|
   | Test Advisor | Advisor |
   | Test Compliance | Compliance Officer |
   | Test Ops | Operations Executive |
   | Test Accountant | Accountant / Finance Officer |
   | Test HR | HR Executive |
   - ✅ Har user "created" green message ke saath list me aa jaye.
4. Password sabka same simple rakho (jaise `Test@1234`) — yaad rakhne ke liye.
- **Logout kaise:** upar-right apne naam pe click → **Sign out**.

> Ab neeche har role ka section — us role se login karke follow karo.

---

# 👤 ROLE 1 — ADVISOR (lead laata + handle karta hai)

**Login:** `Test Advisor` se.
**Advisor ko dikhega:** Dashboard, My Day, Leads (sirf apni), Tasks, Banks (view), Partners (view), Documents. **Finance/HR/Settings/Users NAHI dikhega** — ye normal hai.

### A1) Nayi lead banao
1. Left menu → **Leads** → **New Lead**.
2. Bharo:
   - Name: `Ahmed Test`
   - Mobile: `+971500000001`
   - Email: `ahmed@test.com`
   - Monthly income: `25000`, Loan amount: `1000000`, Property value: `1500000`
   - Source: `Meta Ads`, Priority: `High`
3. **Create Lead**.
   - ✅ Green message + lead page khulta hai + **case number** dikhta hai (BITAR-2026-xx).

### A2) First contact
1. Lead page pe **Log call** → outcome **Connected** → note `Interested` → Save.
   - ✅ Call history me aa gaya.
2. **Add follow-up** → date = 2 din baad → Save.
   - ✅ Follow-up save.

### A3) Documents + note
1. **Documents** section → **Upload** → koi PDF/image → upload.
   - ✅ Document list me.
2. **Add note** → `Docs mil gaye` → Save.
   - ✅ Note list me.

### A4) Stage aage badhao (yahan gate dikhega)
1. **Update Stage** → **Documents Complete** → Save. ✅ stage badla.
2. Dobara **Update Stage** → **Logged In** → Save.
   - ✅ **RED message: "KYC must be Passed…"** — advisor yahan atak jaata hai. Ye sahi hai — aage Compliance karega.

### A5) Confirm advisor ki limit
1. Left menu dekho — **Finance/Settings/Users nahi** hai. ✅
2. **All Leads** me sirf Ahmed (aur advisor ki apni) leads. Doosron ki nahi. ✅
3. **Logout.**

---

# 🛡️ ROLE 2 — COMPLIANCE (KYC, screening, risk, DSR)

**Login:** `Test Compliance` se.
**Compliance ko dikhega:** Dashboard, Leads (view), Documents (view), **Compliance** workspace, Reports. Sirf yahi **KYC Pass** kar sakta hai.

### C1) Lead kholo
1. Left menu → **Leads → All Leads** → **Ahmed Test** kholo.

### C2) KYC pass karo (gate kholna)
1. Lead page pe **KYC** button/tab → click.
2. Checklist tick karo → **KYC status = Passed** → Save.
   - ✅ Green message; KYC ab **Passed**.

### C3) Screening + Risk
1. **Screening** → sanctions/PEP check mark karo → Save. ✅
2. **Risk** → rating choose (jaise Medium) → Save. ✅ (High choose karoge to EDD required dikhega.)

### C4) Suspicion flag (Compliance-only)
1. Lead pe **Raise suspicion** (ya Compliance workspace me) → reason likho → Save.
   - ✅ Flag ban gaya — ye sirf Compliance ko dikhta hai.

### C5) DSR (data request)
1. Lead → **⚙ Ops** → **"Client upload link / DSR"** section.
2. **DSR export** dabao.
   - ✅ Ek file (JSON) download ho gayi (customer ka data).
3. *(Anonymize abhi mat dabao — wo data hata deta hai; sirf jaanne ke liye hai.)*
4. **Logout.**

---

# ⚙️ ROLE 3 — OPERATIONS (processing, bank, valuation, disbursement)

**Login:** `Test Ops` se.
**Ops ko dikhega:** Leads (view & edit), **Ops Queue**, Documents (full), Tasks, Reports.

### O1) Stage aage (ab KYC pass hai, gate khula)
1. **Leads → All Leads → Ahmed Test** kholo.
2. **Update Stage → Logged In** → Save.
   - ✅ Ab badal gaya (kyunki Compliance ne KYC pass kar diya). Gate khul gaya.

### O2) Bank application add karo
1. Lead page pe **Bank Application** section → **Add**.
2. Bank choose karo + reference + status + **next follow-up date** → Save.
   - ✅ Application list me aa gayi.

### O3) Valuation (auto-task wala)
1. Lead → **⚙ Ops** → **Valuation**.
2. Valued amount `900000`, Purchase price `1000000` → **Save valuation**.
   - ✅ Green message. Shortfall (1 lakh) → **ek task apne aap bana** "Resolve valuation shortfall".
3. **Tasks → All Tasks** → wo task dikhega. ✅

### O4) Baaki subflows (short)
1. ⚙ Ops → **Buyout** (current bank + validity date) → Save. ✅
2. ⚙ Ops → **NOC** (developer + fee) → Save. ✅
3. ⚙ Ops → **Transfer booking** (trustee + cheque) → Save. ✅

### O5) Disbursement tak le jao
1. **Update Stage** se ek-ek karke: Under Review → Pre-Approved → Valuation → FOL Issued → FOL Signed → Under Disbursement → **Disbursed** (har baar Save).
2. **Disbursed date** maange → aaj ki date.
   - ✅ Lead ab **Disbursed** — deal complete.
3. **Logout.**

---

# 💰 ROLE 4 — ACCOUNTANT (invoice, receipt, payout)

**Login:** `Test Accountant` se.
**Accountant ko dikhega:** **Finance (full) + Finance Hub**, Leads (view only), Finance Reports. Leads edit nahi kar sakta.

### F1) Invoice banao
1. Left menu → **Finance Hub**.
2. **New invoice** → Lead = **Ahmed Test**, Subtotal `10000` → **Create**.
   - ✅ Table me row: total **10500** (VAT +500), status **Draft**.

### F2) Send + receipt
1. Row me **Send** → ✅ status **Sent** (lock).
2. "Receipt AED" box me `10500` → **Add**.
   - ✅ Status **Paid**. Upar "Received" badha.
   - (Background me advisor ki commission entry ban gayi.)

### F3) Payout run
1. **Payout runs** → period `2026-08` → **Create run**. ✅ Draft ban gaya.
2. **Submit** → ✅ CEO approval ko chala gaya (Accountant khud approve nahi kar sakta — safety).
3. **Logout.**

---

# 👑 ROLE 5 — CEO / MANAGER (approve, dashboard, reports, close)

**Login:** CEO account se.
**CEO ko dikhega:** SAB.

### M1) Payout approve karo
1. Left menu → **Approvals** → payout run dikhega → **Approve**.
   - ✅ Approved. (Ab Finance Hub me use "Paid" mark kar sakte ho.)

### M2) Month lock
1. **Finance Hub → Month-end lock** → period `2026-08` → **Lock**. ✅ 🔒

### M3) Dekho poora picture
1. **Dashboard** → revenue/funnel/"This Month at a Glance". ✅
2. **Forecast** → weighted pipeline. ✅
3. **Reports → Report Builder** → group by "source" → Run. ✅
4. **Referral Partners → Generate Statements → View Statements**. ✅
5. **Client 360** (Ahmed ke client se) → poora history + lifecycle. ✅

### M4) Automation banao (CEO/admin)
1. **Automation** → rule: name `Call new leads`, WHEN "new lead created", THEN "Create a task" title `Call the customer` days `1` → **Save**.
2. Ek nayi lead banao → us pe task **auto** ban jaana chahiye. ✅ Runs = 1.
3. **Logout.**

---

# 🧑‍💼 ROLE 6 — HR (attendance, leave, targets)

**Login:** `Test HR` se (ya CEO).
**HR ko dikhega:** **HR** module, Advisors (view), Reports. Leads/Finance nahi.

### H1) Attendance
1. Left menu → **HR** → **Check in** (location Allow) → ✅ time dikha → **Check out** → ✅.

### H2) Leave
1. **Request leave** → type + dates + reason → **Submit** → ✅ Pending.
2. Usi ke saamne **Approve** → ✅ Approved.

### H3) Target
1. **Set target** → advisor choose + metric + period `2026-08` + value `500000` → **Save**. ✅
2. **Logout.**

---

## ✅ Sab ho gaya!
Aapne har role se, lead ki poori journey chala li:
**Advisor** (lead+contact) → **Compliance** (KYC) → **Operations** (process+disburse) → **Accountant** (invoice+payout) → **CEO** (approve+reports) → **HR** (staff).

Yehi client demo ka best tareeka hai — har role ka kaam alag dikhao.

---

## Agar kahin atko
| Role / Step | Kya dikha / samajh nahi aaya |
|---|---|
|  |  |

Us step ka screenshot le ke mujhe bolo "ROLE X, STEP Y pe ye hua" — main us point pe guide kar dunga.
