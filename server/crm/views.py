import csv
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.contrib.auth import logout as auth_logout
from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404
from django.db.models import Sum, Count, Q
from django.utils import timezone
from django.http import HttpResponse, JsonResponse
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from django.core.exceptions import PermissionDenied

from .models import (User, Lead, Bank, Task, ReferralPartner, Document, Role, STAGES, SOURCES,
                     BankApplication, generate_case_number, FollowUp,
                     Note, LeadSourceState, RolePermission, AppSetting, Customization, LeadAudit,
                     CallLog, Notification, AuditEvent)
from .forms import LoginForm, LeadForm, UserForm, PartnerForm, BankForm
from . import permissions as perm


class CRMLoginView(LoginView):
    template_name = 'registration/login.html'
    authentication_form = LoginForm
    redirect_authenticated_user = True


def logout_view(request):
    """Log out on GET or POST and redirect to login."""
    auth_logout(request)
    return redirect('login')


# ---------- helpers ----------
def _audit(lead, user, action, field='', old='', new=''):
    LeadAudit.objects.create(lead=lead, user=user, action=action, field=field,
                             old_value=str(old)[:255], new_value=str(new)[:255])


def _audit_event(request, action, detail=''):
    """System-wide (non-lead) audit entry."""
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    ip = (xff.split(',')[0].strip() if xff else request.META.get('REMOTE_ADDR', '')) or ''
    AuditEvent.objects.create(user=request.user if request.user.is_authenticated else None,
                              action=action, detail=str(detail)[:255], ip=ip)


# human-readable labels for tracked lead fields
_AUDIT_FIELDS = {
    'name': 'Name', 'mobile': 'Mobile', 'email': 'Email', 'nationality': 'Nationality',
    'property_value': 'Property Value', 'ltv': 'LTV', 'loan_amount': 'Loan Amount',
    'advisor': 'Advisor', 'bank': 'Bank', 'source': 'Source', 'stage': 'Stage',
    'priority': 'Priority', 'referral_partner': 'Referral Partner',
}


def _snapshot(lead):
    def val(f):
        v = getattr(lead, f)
        return str(v) if v is not None else ''
    return {f: val(f) for f in _AUDIT_FIELDS}


def _set_sla_due(lead):
    """First-contact SLA due time (PRD §6/§9.5/§17.4) — computed against the business-hours
    calendar so the clock only runs in working hours."""
    if not lead.first_contact_due:
        from .models import add_business_minutes
        mins = _rules().get('sla_first_contact_mins', 15)
        lead.first_contact_due = add_business_minutes(timezone.now(), int(mins))


def _mark_contacted(lead):
    """Record first meaningful contact (stops the SLA clock) and clear silence flag."""
    if not lead:
        return
    fields = []
    if not lead.first_contacted_at:
        lead.first_contacted_at = timezone.now()
        fields.append('first_contacted_at')
    if lead.silence_notified:
        lead.silence_notified = ''   # fresh activity resets the silence escalation
        fields.append('silence_notified')
    if fields:
        lead.save(update_fields=fields)


def _norm_mobile(m):
    return ''.join(ch for ch in (m or '') if ch.isdigit())[-9:]  # last 9 digits, source-agnostic


def _link_client(lead):
    """Attach the case (lead) to a Client person, matching normalized mobile/email; create if none.
    Keeps the client's person fields + lifecycle in sync (PRD §10.1, §13.1)."""
    from .models import Client
    if lead.client_id:
        client = lead.client
    else:
        client = None
        nm = _norm_mobile(lead.mobile)
        if nm:
            for c in Client.objects.all():
                if _norm_mobile(c.mobile) == nm:
                    client = c
                    break
        if client is None and lead.email:
            client = Client.objects.filter(email__iexact=lead.email).first()
        if client is None:
            client = Client.objects.create(
                name=lead.name, mobile=lead.mobile, email=lead.email,
                nationality=lead.nationality, date_of_birth=lead.date_of_birth,
                employer=lead.employer, owner=lead.advisor)
        lead.client = client
        lead.save(update_fields=['client'])
    # record consent captured on the lead form (only grants; never auto-withdraws here)
    grants = {ch: True for ch, on in [('Call', lead.consent_call), ('SMS', lead.consent_sms),
              ('WhatsApp', lead.consent_whatsapp), ('Email', lead.consent_email)] if on}
    if grants:
        _record_consent(client, lead, grants, 'Lead form', lead.advisor)
    _sync_lifecycle(client)
    return client


def _record_consent(client, lead, channels, source, user):
    """Write a ConsentRecord for each channel whose state differs from the client's current flags."""
    from .models import ConsentRecord
    if not client:
        return
    field = {'Call': 'consent_call', 'SMS': 'consent_sms',
             'WhatsApp': 'consent_whatsapp', 'Email': 'consent_email'}
    for ch, granted in channels.items():
        cur = getattr(client, field[ch])
        if cur != granted:
            setattr(client, field[ch], granted)
            ConsentRecord.objects.create(client=client, lead=lead, channel=ch, granted=granted,
                                         source=source, captured_by=user)
    client.save()


def _sync_lifecycle(client):
    """Derive the client lifecycle from their cases (PRD §13.1):
    Lead → Applicant → Active Client → Closed Client → Advocate."""
    if client.lifecycle == 'Advocate':
        return   # Advocate is a manual/earned state — never auto-downgrade
    DISB = ['Disbursed', 'Property Transfer Scheduled', 'Property Transfer', 'Property Transferred']
    DONE = ['Property Transferred']
    cases = client.cases.filter(is_deleted=False)
    disbursed = cases.filter(stage__in=DISB)
    active_now = cases.exclude(stage__in=DONE + ['Declined'])
    if disbursed.exists() and not active_now.exists():
        lc = 'Closed Client'          # every case completed/transferred, nothing live
    elif disbursed.exists():
        lc = 'Active Client'          # has a live/disbursed loan
    elif cases.exclude(stage__in=['Lead Received', 'Documents Pending', 'Declined']).exists():
        lc = 'Applicant'
    else:
        lc = 'Lead'
    client.lifecycle = lc
    client.save()


def _notify(user, text, url='', category='', actor=None):
    """Create an in-app notification, honouring the user's category mute prefs (PRD §NA-04).
    Mandatory floor categories (sla/compliance/approval) can never be muted."""
    if not user or (actor is not None and user.pk == actor.pk):
        return
    from .models import NotificationPref
    if category and category not in NotificationPref.MANDATORY:
        if NotificationPref.objects.filter(user=user, category=category, muted=True).exists():
            return
    Notification.objects.create(user=user, text=text, url=url, category=category)


def _request_approval(request_type, title, requested_by, approver_role, detail='', link='',
                      target_model='', target_id=None):
    """Create an approval request and notify the approving role (PRD §17.5)."""
    from .models import ApprovalRequest
    ar = ApprovalRequest.objects.create(
        request_type=request_type, title=title, detail=detail, link=link,
        target_model=target_model, target_id=target_id,
        approver_role=approver_role, requested_by=requested_by)
    for u in User.objects.filter(role=approver_role):
        if not requested_by or u.pk != requested_by.pk:      # segregation: never notify the requester as approver
            _notify(u, f'Approval needed: {title}', '/approvals/', 'approval')
    return ar


def _auto_task(lead, title, task_type='Documents', days=1, actor=None):
    """Create a task automatically and notify its assignee (advisor)."""
    import datetime as _dt
    if not lead.advisor:
        return
    t = Task.objects.create(title=title, lead=lead, assignee=lead.advisor,
                            task_type=task_type, priority=lead.priority,
                            status='Pending', due_date=timezone.localdate() + _dt.timedelta(days=days))
    _notify(lead.advisor, f'New task: {title}', f'/leads/{lead.pk}/', 'task', actor=actor)
    return t


# Regulatory / SLA values are SEED DATA only — the live values live in Settings
# (PRD §4.3 "config over code"; §9.3 eligibility; §6 speed-to-lead). Never hardcode business numbers.
RULE_DEFAULTS = {
    'ltv_upto_5m': 80,        # expat first home, property <= 5M (%)
    'ltv_above_5m': 75,       # property > 5M (%)
    'dbr_cap': 50,            # debt burden ratio cap (% of gross monthly income)
    'income_multiple': 7,     # loan <= N x annual income (expats), PRD §9.3
    'cash_to_close_pct': 7,   # PRD §9.3 default 7% (DLD + fees), itemised
    'sla_first_contact_mins': 15,   # PRD §6: median first contact < 15 min in working hours
}


def _rules():
    """Live business rules from Settings, falling back to seed defaults (config over code)."""
    try:
        row = AppSetting.objects.filter(key='rules').first()
        cfg = dict(RULE_DEFAULTS)
        if row and isinstance(row.value, dict):
            cfg.update({k: v for k, v in row.value.items() if v not in (None, '')})
        return cfg
    except Exception:
        return dict(RULE_DEFAULTS)


def _compute_eligibility(lead):
    """Instant eligibility check: LTV cap, DBR, income multiple, cash-to-close.
    Advises only — never blocks saving (PRD §9.3). Sets lead.eligible + note."""
    r = _rules()
    prop = float(lead.property_value or 0)
    loan = float(lead.loan_amount or 0)
    income = float(lead.monthly_income or 0)
    annual = income * 12
    issues = []
    if prop > 0 and loan > 0:
        ltv = loan / prop * 100
        cap = r['ltv_upto_5m'] if prop <= 5_000_000 else r['ltv_above_5m']
        if ltv > cap + 0.5:
            issues.append(f'LTV {ltv:.0f}% exceeds {cap}% cap')
    if income > 0 and loan > 0:
        est_emi = loan * 0.0065           # ~ rough monthly instalment
        if est_emi > income * r['dbr_cap'] / 100:
            issues.append(f"DBR over {r['dbr_cap']}% (est. EMI AED {est_emi:,.0f})")
    if annual > 0 and loan > r['income_multiple'] * annual:
        issues.append(f"Loan exceeds {r['income_multiple']}x annual income")
    if prop > 0 and loan > 0:
        cash = (prop - loan) + prop * r['cash_to_close_pct'] / 100
        lead._cash_to_close = cash        # for display
    if prop == 0 or loan == 0:
        lead.eligible = None
        lead.eligibility_note = 'Enter property value, loan & income to assess'
    elif issues:
        lead.eligible = False
        lead.eligibility_note = '; '.join(issues)
    else:
        lead.eligible = True
        lead.eligibility_note = 'Meets LTV, DBR and cash-to-close criteria'


HIGH_RISK_NATIONALITIES = {'Iran', 'North Korea', 'Syria', 'Yemen', 'Sudan', 'Myanmar'}


def _compute_risk(lead):
    """Auto initial risk rating (PRD §16.2). Manual override allowed later by Compliance."""
    score, reasons = 0, []
    if (lead.nationality or '') in HIGH_RISK_NATIONALITIES:
        score += 2; reasons.append('High-risk nationality')
    if lead.pep_status == 'Hit':
        score += 3; reasons.append('PEP hit')
    if lead.sanctions_status == 'Hit':
        score += 3; reasons.append('Sanctions hit')
    if (lead.employment_type or '').lower() in ('self-employed', 'business', 'self employed'):
        score += 1; reasons.append('Cash-intensive / self-employed')
    rating = 'High' if score >= 3 else ('Medium' if score >= 1 else 'Low')
    lead.risk_rating = rating
    lead.risk_note = ', '.join(reasons) or 'No elevated risk factors'
    lead.edd_required = (rating == 'High')
    return rating


def _kyc_blockers(lead):
    """What still blocks KYC Passed (PRD §16.1–16.2)."""
    b = []
    if lead.sanctions_status == 'Pending' or lead.pep_status == 'Pending':
        b.append('Sanctions/PEP screening not completed')
    if lead.sanctions_status == 'Hit' or lead.pep_status == 'Hit':
        b.append('Screening hit — resolve before passing')
    if lead.edd_required and not lead.edd_complete:
        b.append('EDD checklist incomplete (High risk)')
    return b


def _coerce_lead_numbers(lead):
    """Fill NOT-NULL numeric fields that a draft may leave blank."""
    if lead.loan_amount is None:
        lead.loan_amount = 0
    if lead.property_value is None:
        lead.property_value = 0
    if lead.ltv is None:
        lead.ltv = 80


def _apply_disbursed(lead, user=None):
    """Auto-set disbursed_at the first time a lead enters a disbursed stage."""
    if lead.stage in DISBURSED_STAGES and not lead.disbursed_at:
        lead.disbursed_at = timezone.localdate()
        if user is not None:
            _audit(lead, user, 'Disbursed', 'Disbursed At', '', lead.disbursed_at.strftime('%d %b %Y'))
        return True
    return False


def _audit_diff(lead, user, before):
    """Compare a pre-edit snapshot to current lead state; log each changed field."""
    after = _snapshot(lead)
    for f, label in _AUDIT_FIELDS.items():
        if before.get(f, '') != after.get(f, ''):
            _audit(lead, user, 'Field updated', label, before.get(f, '') or '—', after.get(f, '') or '—')


def visible_leads(user):
    """Apply 'Own Leads Only' scope for advisors; exclude soft-deleted leads."""
    qs = Lead.objects.filter(is_deleted=False).select_related('advisor', 'bank')

    def _scope_ids(u):
        """None = sees all; else a set of advisor ids this user's scope covers."""
        if perm.is_own_scope(u, 'Leads'):
            return {u.id}
        if perm.is_team_scope(u, 'Leads'):
            return perm.team_member_ids(u)
        return None  # full/view scope

    ids = _scope_ids(user)
    if ids is not None:
        # union in any live delegation grantors' scope
        for g in perm.active_grantors(user):
            gids = _scope_ids(g)
            if gids is None:
                ids = None
                break
            ids |= gids
    if ids is not None:
        qs = qs.filter(advisor_id__in=ids)
    return qs


def visible_tasks(user):
    qs = Task.objects.filter(is_deleted=False).select_related('lead', 'assignee')
    if perm.is_own_scope(user, 'Tasks'):
        qs = qs.filter(assignee=user)
    elif perm.is_team_scope(user, 'Tasks'):
        qs = qs.filter(assignee_id__in=perm.team_member_ids(user))
    return qs


# ---------- dashboard ----------
@login_required
def dashboard(request):
    u = request.user
    if u.role in (Role.ADVISOR, Role.TELECALLER):
        return advisor_dashboard(request)
    if u.role in (Role.CEO, Role.SUPER_ADMIN, Role.SALES_DIRECTOR):
        return management_dashboard(request)
    return role_dashboard(request)


def role_dashboard(request):
    """Purpose-built dashboard for operational / support roles, scoped to what they can see."""
    from datetime import date
    u = request.user
    DISB = ['Disbursed', 'Property Transfer Scheduled', 'Property Transfer', 'Property Transferred']
    today = date.today()
    leads = visible_leads(u)  # respects Own / Team / All scope
    kpis, panels = [], []

    def kpi(label, val, note='', pre='', suf=''):
        kpis.append({'label': label, 'val': val, 'note': note, 'pre': pre, 'suf': suf})

    if u.role == Role.TEAM_LEADER:
        active = leads.exclude(stage__in=DISB + ['Declined'])
        disb_month = leads.filter(disbursed_at__year=today.year, disbursed_at__month=today.month)
        kpi('Team Cases', leads.count(), 'across your advisors')
        kpi('Active Pipeline', f"{_f(active.aggregate(v=Sum('loan_amount'))['v']):,.0f}", 'open value', 'AED ')
        kpi('Disbursed This Month', disb_month.count(), f"AED {_f(disb_month.aggregate(v=Sum('loan_amount'))['v']):,.0f}")
        kpi('Follow-ups Due', sum(1 for l in active if l.followups.filter(done=False, next_date__lte=today).exists()), 'action needed')
        rows = []
        for a in User.objects.filter(manager=u):
            al = leads.filter(advisor=a)
            rows.append([a.get_full_name() or a.username, al.count(),
                         al.filter(stage__in=DISB).count(),
                         f"AED {_f(al.filter(stage__in=DISB).aggregate(v=Sum('loan_amount'))['v']):,.0f}"])
        panels.append({'title': 'Team Performance', 'cols': ['Advisor', 'Cases', 'Disbursed', 'Value'], 'rows': rows,
                       'link': ('/ops/', 'Open Ops Queue')})

    elif u.role in (Role.OPS_MANAGER, Role.OPS_EXECUTIVE):
        open_cases = leads.filter(is_draft=False).exclude(stage__in=DISB + ['Declined'])
        esc = sum(1 for l in open_cases if l.silence_status == 'escalate')
        warn = sum(1 for l in open_cases if l.silence_status == 'warn')
        kpi('Open Cases', open_cases.count(), 'in processing')
        kpi('Escalated', esc, '7+ days silent')
        kpi('Warnings', warn, '3+ days silent')
        kpi('KYC Pending', leads.filter(kyc_status='Pending').count(), 'awaiting check')
        docs = Document.objects.filter(status='Pending', is_current=True, is_deleted=False).select_related('lead')[:10]
        panels.append({'title': 'Documents Awaiting Verification',
                       'cols': ['Document', 'Lead', 'Uploaded'],
                       'rows': [[d.name or d.doc_type, d.lead.name, d.created_at.strftime('%d %b %Y')] for d in docs],
                       'link': ('/documents/', 'All Documents')})
        esc_rows = [[l.case_number or f'#{l.pk}', l.name, l.stage,
                     (timezone.now() - l.last_activity_at).days]
                    for l in sorted(open_cases, key=lambda x: -(timezone.now() - x.last_activity_at).days)[:10]]
        panels.append({'title': 'Most Idle Cases', 'cols': ['Case', 'Client', 'Stage', 'Idle (days)'],
                       'rows': esc_rows, 'link': ('/ops/', 'Ops Queue')})

    elif u.role == Role.ACCOUNTANT:
        _cz = list(Customization.objects.select_related('lead'))
        revenue = sum(c.actual_revenue for c in _cz)
        net = sum(c.final_revenue for c in _cz)
        vat = sum(c.vat for c in _cz)
        disb_month = leads.filter(disbursed_at__year=today.year, disbursed_at__month=today.month)
        kpi('Revenue', f'{revenue:,.0f}', 'net commission, excl VAT', 'AED ')
        kpi('Net Profit', f'{net:,.0f}', 'final revenue', 'AED ')
        kpi('VAT', f'{vat:,.0f}', 'output tax', 'AED ')
        kpi('Disbursed This Month', f"{_f(disb_month.aggregate(v=Sum('loan_amount'))['v']):,.0f}", f'{disb_month.count()} cases', 'AED ')
        rows = [[l.case_number or f'#{l.pk}', l.name, f"AED {_f(l.loan_amount):,.0f}",
                 l.disbursed_at.strftime('%d %b %Y') if l.disbursed_at else '—']
                for l in leads.filter(stage__in=DISB).order_by('-disbursed_at')[:10]]
        panels.append({'title': 'Recent Disbursals', 'cols': ['Case', 'Client', 'Loan', 'Disbursed'],
                       'rows': rows, 'link': ('/finance/', 'Finance')})

    elif u.role == Role.COMPLIANCE:
        kpi('KYC Pending', leads.filter(kyc_status='Pending').count(), 'to review')
        kpi('KYC Passed', leads.filter(kyc_status='Passed').count(), 'cleared')
        kpi('KYC Rejected', leads.filter(kyc_status='Rejected').count(), 'flagged')
        kpi('Consent Missing', leads.filter(consent_call=False, consent_email=False,
            consent_sms=False, consent_whatsapp=False).count(), 'no channel consent')
        rows = [[l.case_number or f'#{l.pk}', l.name, l.stage,
                 (l.advisor.get_full_name() or l.advisor.username) if l.advisor else '—']
                for l in leads.filter(kyc_status='Pending')[:12]]
        panels.append({'title': 'KYC Pending Review', 'cols': ['Case', 'Client', 'Stage', 'Advisor'],
                       'rows': rows, 'link': ('/leads/', 'All Leads')})
        ev = AuditEvent.objects.select_related('user')[:10]
        panels.append({'title': 'Recent System Events', 'cols': ['When', 'User', 'Action'],
                       'rows': [[e.created_at.strftime('%d %b %H:%M'),
                                 (e.user.get_full_name() or e.user.username) if e.user else 'System',
                                 e.action + (f' · {e.detail}' if e.detail else '')] for e in ev],
                       'link': ('/audit/', 'Audit Log')})

    elif u.role == Role.HR_EXECUTIVE:
        staff = User.objects.all()
        kpi('Total Staff', staff.count(), 'all users')
        kpi('Active', staff.filter(status='Active').count(), 'enabled accounts')
        kpi('Advisors', staff.filter(role=Role.ADVISOR).count(), 'sales team')
        kpi('Roles In Use', staff.values('role').distinct().count(), 'distinct roles')
        from collections import Counter
        cnt = Counter(staff.values_list('role', flat=True))
        rows = [[dict(Role.choices).get(r, r), n] for r, n in cnt.most_common()]
        panels.append({'title': 'Headcount by Role', 'cols': ['Role', 'Count'], 'rows': rows,
                       'link': ('/users/', 'User Management')})

    elif u.role == Role.MARKETING:
        allleads = Lead.objects.filter(is_deleted=False)
        kpi('Total Leads', allleads.count(), 'all sources')
        kpi('New This Month', allleads.filter(created_at__year=today.year, created_at__month=today.month).count(), 'this month')
        best = max(SOURCES, key=lambda s: allleads.filter(source=s).count()) if allleads.exists() else '—'
        kpi('Top Source', best, 'by volume')
        kpi('Disbursed', allleads.filter(stage__in=DISB).count(), 'converted')
        rows = []
        for s in SOURCES:
            sl = allleads.filter(source=s)
            c = sl.count()
            conv = round(sl.filter(stage__in=DISB).count() / c * 100, 1) if c else 0
            rows.append([s, c, sl.filter(stage__in=DISB).count(), f'{conv}%'])
        panels.append({'title': 'Leads by Source', 'cols': ['Source', 'Leads', 'Disbursed', 'Conversion'],
                       'rows': rows, 'link': ('/leads/sources/', 'Lead Sources')})

    else:  # AUDITOR and any other read-only role
        kpi('Total Leads', leads.count(), 'all cases')
        kpi('Disbursed', leads.filter(stage__in=DISB).count(), 'completed')
        kpi('KYC Passed', leads.filter(kyc_status='Passed').count(), 'compliant')
        kpi('Staff', User.objects.count(), 'users')
        ev = AuditEvent.objects.select_related('user')[:15]
        panels.append({'title': 'Recent System Events', 'cols': ['When', 'User', 'Action'],
                       'rows': [[e.created_at.strftime('%d %b %H:%M'),
                                 (e.user.get_full_name() or e.user.username) if e.user else 'System',
                                 e.action + (f' · {e.detail}' if e.detail else '')] for e in ev],
                       'link': ('/audit/', 'Audit Log')})

    return render(request, 'crm/dashboard_role.html', {
        'kpis': kpis, 'panels': panels, 'role_label': u.role_label,
        'greet_name': u.first_name or u.username, 'active_nav': 'Dashboard',
    })


def _f(v):
    return float(v or 0)


def _spark(current, n=12):
    """Build a 12-point ramp ending at the current value (no historical store)."""
    current = _f(current)
    if not current:
        return [0] * n
    return [round(current * (i + 1) / n, 2) for i in range(n)]


def management_dashboard(request):
    from datetime import date
    leads = Lead.objects.filter(is_deleted=False)
    DISB = ['Disbursed', 'Property Transfer Scheduled', 'Property Transfer', 'Property Transferred']
    active = leads.exclude(stage__in=DISB + ['Declined'])
    submitted_stages = ['Logged In', 'Under Review', 'Pre-Approved', 'Valuation',
                        'Valuation Received', 'FOL Initiated', 'FOL Issued',
                        'FOL Signing Fixed', 'FOL Signed', 'Under Disbursement']
    disbursed_val = _f(leads.filter(stage__in=DISB).aggregate(v=Sum('loan_amount'))['v'])
    approval_val = _f(leads.filter(stage__in=['Pre-Approved'] + submitted_stages).aggregate(v=Sum('loan_amount'))['v'])
    pipeline_val = _f(active.aggregate(v=Sum('loan_amount'))['v'])
    # Revenue & Net Profit come straight from the Monthly Disbursed Pipeline (Customization) sheet:
    #   Revenue = sum of Actual Revenue,  Net Profit = sum of Final Revenue.
    _cz = list(Customization.objects.only('slab', 'broker_pct', 'broker_slab', 'vat_override', 'lead')
               .select_related('lead'))
    revenue = sum(c.actual_revenue for c in _cz)
    net_profit = sum(c.final_revenue for c in _cz)
    n_total = leads.count()
    n_disbursed = leads.filter(stage__in=DISB).count()

    IC = ['users', 'plus', 'file', 'shield', 'home', 'file', 'cash', 'trend']
    kpi_defs = [
        ('Total Leads', n_total, '', '', 'all sources'),
        ('New Leads Today', leads.filter(created_at__date=date.today()).count(), '', '', 'since midnight'),
        ('Applications Submitted', leads.filter(stage__in=submitted_stages).count(), '', '', 'in progress'),
        ('Pre-Approval', leads.filter(stage='Pre-Approved').count(), '', '', 'awaiting final'),
        ('Loan Disbursed', n_disbursed, '', '', f'AED {disbursed_val:,.0f} value'),
        ('Pending Title Deed', leads.filter(stage__in=['Disbursed', 'Property Transfer Scheduled', 'Property Transfer']).count(), '', '', 'awaiting transfer'),
        ('Revenue This Month', round(revenue), '', 'AED ', 'net commission · excl VAT'),
        ('Net Profit', round(net_profit), '', 'AED ', 'final revenue'),
    ]
    kpis_js = [
        {'label': lbl, 'val': val, 'suf': suf, 'pre': pre, 'ic': IC[i],
         'd': '', 'pos': True, 'note': note, 's': _spark(val)}
        for i, (lbl, val, suf, pre, note) in enumerate(kpi_defs)
    ]

    # ---- funnel (cumulative reach across ordered stages) ----
    stage_idx = {s: i for i, s in enumerate(STAGES)}
    live = [l for l in leads if l.stage != 'Declined']

    def reached(threshold):
        return sum(1 for l in live if stage_idx.get(l.stage, -1) >= threshold)
    funnel = [
        {'s': 'Lead', 'n': reached(0)}, {'s': 'Contacted', 'n': reached(1)},
        {'s': 'Docs Received', 'n': reached(2)}, {'s': 'Eligibility', 'n': reached(4)},
        {'s': 'Pre-Approval', 'n': reached(5)}, {'s': 'Final Approval', 'n': reached(9)},
        {'s': 'Loan Approved', 'n': reached(11)}, {'s': 'Disbursed', 'n': reached(13)},
    ]
    approval_ratio = round(funnel[4]['n'] / funnel[0]['n'] * 100, 1) if funnel[0]['n'] else 0

    # ---- revenue series (only current period known) ----
    def series(cur):
        cur = round(_f(cur) / 1e6, 2)
        return [0] * 11 + [cur]
    series_m = [
        {'n': 'Revenue', 'c': '#05448B', 'v': series(revenue)},
        {'n': 'Approvals', 'c': '#2D6CB0', 'v': series(approval_val)},
        {'n': 'Disbursed', 'c': '#7FA6CF', 'v': series(disbursed_val)},
    ]
    series_q = [{'n': s['n'], 'c': s['c'], 'v': [0, 0, 0, s['v'][-1]]} for s in series_m]

    # ---- advisor leaderboard ----
    advisors_js = []
    for u in User.objects.filter(role=Role.ADVISOR):
        al = leads.filter(advisor=u)
        cnt = al.count()
        appr = al.filter(stage__in=['Pre-Approved'] + submitted_stages + DISB).count()
        rev = _f(al.filter(stage__in=DISB).aggregate(v=Sum('loan_amount'))['v']) * 0.011
        advisors_js.append({
            'n': u.get_full_name() or u.username, 'i': u.initials,
            'rev': f'{rev/1000:.0f}K', 'conv': al.filter(stage__in=DISB).count(),
            'rate': round(appr / cnt * 100) if cnt else 0, 'comm': f'{rev*0.15/1000:.0f}K',
            '_r': rev,
        })
    advisors_js.sort(key=lambda a: a['_r'], reverse=True)
    advisors_js = advisors_js[:5]

    # ---- bank performance ----
    banks_js = []
    for b in Bank.objects.all():
        bl = leads.filter(bank=b)
        apps = bl.count()
        appr = bl.filter(stage__in=['Pre-Approved'] + submitted_stages + DISB).count()
        rev = _f(bl.filter(stage__in=DISB).aggregate(v=Sum('loan_amount'))['v']) * 0.011
        banks_js.append({
            'n': b.name, 'i': b.name[:2].upper(), 'apps': apps, 'appr': appr,
            'ratio': round(appr / apps * 100) if apps else 0, 'days': 0,
            'rev': f'{rev/1000:.0f}K', '_a': apps,
        })
    banks_js.sort(key=lambda x: x['_a'], reverse=True)
    banks_js = banks_js[:5]

    # ---- lead sources ----
    sources_js = []
    max_src = 1
    for src in SOURCES:
        sl = leads.filter(source=src)
        cnt = sl.count()
        max_src = max(max_src, cnt)
        rev = _f(sl.filter(stage__in=DISB).aggregate(v=Sum('loan_amount'))['v']) * 0.011
        disb = sl.filter(stage__in=DISB).count()
        sources_js.append({
            'n': src, 'leads': cnt, 'rev': f'{rev/1000:.0f}K',
            'conv': round(disb / cnt * 100, 1) if cnt else 0, 'cpl': '0', '_c': cnt,
        })
    for s in sources_js:
        s['w'] = round(s['_c'] / max_src * 100)
    sources_js.sort(key=lambda x: x['_c'], reverse=True)

    # ---- referral partners ----
    partners_js = []
    for p in ReferralPartner.objects.all()[:5]:
        pl = leads.filter(source='Referral Partner')  # coarse: company referral leads
        partners_js.append({
            'n': p.name, 't': p.partner_type, 'i': p.name[:2].upper(),
            'pipe': '0', 'ref': 0, 'conv': '0%', 'due': '0', 'paid': '0',
        })

    # ---- finance summary (all derived from the Monthly Disbursed Pipeline sheet) ----
    vat_total = sum(c.vat for c in _cz)                 # per-row VAT (honours overrides)
    invoice_total = sum(c.with_vat for c in _cz)        # Actual Revenue + VAT = invoice amount
    finance = {
        'revenue': f'{revenue:,.0f}',                   # net commission, excl. VAT
        'vat': f'{vat_total:,.0f}',
        'invoice': f'{invoice_total:,.0f}',             # incl. VAT
        'adv_comm': f'{revenue*0.159:,.0f}', 'ref_comm': f'{revenue*0.086:,.0f}',
        'net': f'{net_profit:,.0f}', 'projected': f'{revenue*1.1:,.0f}',
    }
    profit_bars = [0] * 11 + [round(net_profit)]

    # ---- action required ----
    unassigned = leads.filter(advisor__isnull=True).count()
    awaiting_docs = leads.filter(stage__in=['Lead Received', 'Documents Pending']).count()
    actions = []
    if awaiting_docs:
        actions.append({'t': f'{awaiting_docs} lead(s) awaiting documents',
                        'p': 'Documents pending before submission.', 'due': 'Action needed', 'dc': 'var(--danger)'})
    if unassigned:
        actions.append({'t': f'{unassigned} unassigned lead(s)',
                        'p': 'New leads waiting for advisor assignment.', 'due': 'Assign today', 'dc': 'var(--primary)'})
    overdue = Task.objects.filter(is_deleted=False).exclude(status='Completed').filter(due_date__lt=date.today()).count()
    if overdue:
        actions.append({'t': f'{overdue} overdue task(s)',
                        'p': 'Tasks past their due date.', 'due': 'Overdue', 'dc': 'var(--warning)'})

    hero = {
        'revenue': f'{revenue:,.0f}', 'approval': f'{approval_val:,.0f}',
        'disbursement': f'{disbursed_val:,.0f}',
    }

    dash = {
        'hero': hero, 'kpis': kpis_js, 'series_m': series_m, 'series_q': series_q,
        'funnel': funnel, 'approval_ratio': approval_ratio,
        'pipeline_value': f'{pipeline_val:,.0f}',
        'advisors': advisors_js, 'banks': banks_js, 'sources': sources_js,
        'partners': partners_js, 'finance': finance, 'profit': profit_bars,
        'actions': actions,
        'feed': _activity_feed(),
    }
    return render(request, 'crm/dashboard_mgmt.html', {
        'dash': dash, 'greet_name': request.user.first_name or request.user.username,
        'active_nav': 'Dashboard',
    })


def _advisor_metrics(u):
    """Scorecard data for one advisor — used by the advisor's own dashboard and the CEO drill-down."""
    my = Lead.objects.filter(advisor=u, is_deleted=False)
    # a submission counts once a lead reaches "Documents Complete" (or any later stage)
    submissions = my.exclude(stage__in=['Lead Received', 'Documents Pending', 'Declined']).count()
    _tm0 = timezone.localdate()
    disbursed_val = my.filter(disbursed_at__year=_tm0.year, disbursed_at__month=_tm0.month) \
        .aggregate(v=Sum('loan_amount'))['v'] or 0
    _tm = timezone.localdate()
    partners_added = ReferralPartner.objects.filter(created_by=u, created_at__year=_tm.year,
                                                    created_at__month=_tm.month).count()
    calls_done = CallLog.objects.filter(advisor=u, created_at__year=_tm.year,
                                        created_at__month=_tm.month).count()

    def card(title, sub, achieved, target, unit=''):
        target = float(target or 0)
        achieved = float(achieved or 0)
        pct = round(min(100, achieved / target * 100)) if target else 0
        return {'title': title, 'sub': sub, 'achieved': achieved, 'target': target,
                'remaining': max(target - achieved, 0), 'pct': pct, 'unit': unit}

    targets = [
        card('Monthly Calling Target', 'Calls logged this month', calls_done, u.target_calls),
        card('Submission Target', 'Mortgage files submitted this month', submissions, u.target_submissions),
        card('Channel Partner Target', 'New partners onboarded', partners_added, u.target_partners),
        card('Disbursement Target', 'Loan value disbursed', disbursed_val, u.target_disbursement, 'AED'),
    ]
    overall = round(sum(t['pct'] for t in targets) / len(targets))
    tasks = Task.objects.filter(assignee=u, is_deleted=False).exclude(status__in=['Completed', 'Cancelled']).select_related('lead')[:6]

    from datetime import date
    today = date.today()
    _pri_color = {'High': 'var(--danger)', 'Medium': 'var(--warning)', 'Low': 'var(--primary)'}
    tasks_js = []
    for t in tasks:
        if t.due_date and t.due_date < today:
            due = 'Overdue'
        elif t.due_date == today:
            due = 'Due today'
        elif t.due_date:
            due = 'Due ' + t.due_date.strftime('%d %b')
        else:
            due = 'No due date'
        tasks_js.append({
            't': t.title,
            'p': (t.lead.name if t.lead else t.task_type) or '—',
            'due': due,
            'dc': _pri_color.get(t.priority, 'var(--primary)'),
            'ic': '',
        })

    # this advisor's own call log (scoped — advisors never see others' calls)
    call_log = [{
        'name': cl.name or '—', 'phone': cl.phone or '—', 'outcome': cl.outcome,
        'note': cl.note or '', 'lead': cl.lead_id,
        'when': timezone.localtime(cl.created_at).strftime('%d %b %Y · %I:%M %p'),
    } for cl in CallLog.objects.filter(advisor=u)[:200]]

    # calls per day this week (Mon..Sat) for the activity chart
    import datetime as _dtmod
    monday = today - _dtmod.timedelta(days=today.weekday())
    calls_week = [CallLog.objects.filter(advisor=u, created_at__date=monday + _dtmod.timedelta(days=i)).count()
                  for i in range(6)]

    # recent activity that counts toward targets (this advisor only)
    PHONE_IC = '<path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.13.96.36 1.9.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.91.34 1.85.57 2.81.7A2 2 0 0 1 22 16.92z"/>'
    PARTNER_IC = '<path d="M18 8a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM6 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM18 22a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM8.6 13.5l6.8 4M15.4 6.5l-6.8 4"/>'
    MONEY_IC = '<rect x="2" y="6" width="20" height="12" rx="2"/><circle cx="12" cy="12" r="2.6"/>'
    _acts = []
    for cl in CallLog.objects.filter(advisor=u)[:8]:
        _acts.append((cl.created_at, {'ic': PHONE_IC, 'ok': cl.outcome == 'Interested',
                     'h': f'Logged a call — {cl.outcome}' + (f' · {cl.name}' if cl.name else ''),
                     't': timezone.localtime(cl.created_at).strftime('%d %b · %I:%M %p')}))
    for p in ReferralPartner.objects.filter(created_by=u)[:8]:
        _acts.append((p.created_at, {'ic': PARTNER_IC, 'ok': True,
                     'h': f'Added referral partner {p.name}',
                     't': timezone.localtime(p.created_at).strftime('%d %b · %I:%M %p')}))
    for l in my.filter(disbursed_at__isnull=False).order_by('-disbursed_at')[:8]:
        _dt = timezone.make_aware(_dtmod.datetime.combine(l.disbursed_at, _dtmod.time()))
        _acts.append((_dt, {'ic': MONEY_IC, 'ok': True,
                     'h': f'Lead {l.name} disbursed — AED {float(l.loan_amount or 0):,.0f}',
                     't': l.disbursed_at.strftime('%d %b %Y')}))
    _acts.sort(key=lambda x: x[0], reverse=True)
    feed = [a[1] for a in _acts[:8]]

    return {
        'greet': u.first_name or u.username,
        'targets': [dict(t) for t in targets],
        'overall': overall,
        'tasks': tasks_js,
        'calls': calls_week,
        'feed': feed,
        'callLog': call_log,
        'leadStats': {
            'assigned': my.count(),
            'approved': my.exclude(stage__in=['Lead Received', 'Documents Pending',
                                              'Documents Complete', 'Logged In',
                                              'Under Review', 'Declined']).count(),
            'disbursed': my.filter(stage__in=DISBURSED_STAGES).count(),
        },
    }


def advisor_dashboard(request):
    u = request.user
    data = _advisor_metrics(u)
    return render(request, 'crm/dashboard_advisor.html', {
        'data': data, 'greet_name': u.first_name or u.username,
        'active_nav': 'Dashboard',
    })


@login_required
@perm.module_required('Advisors')
def advisor_detail(request, pk):
    adv = get_object_or_404(User, pk=pk, role=Role.ADVISOR)
    data = _advisor_metrics(adv)
    data['advisorName'] = adv.get_full_name() or adv.username
    data['advisorEmail'] = adv.email or '—'
    data['advisorPhone'] = adv.phone or '—'
    data['advisorInitials'] = adv.initials
    return render(request, 'crm/advisor_detail.html', {
        'data': data, 'advisor': adv, 'active_nav': 'Advisors',
    })


def _decline_not_interested(lead, actor):
    """Move a lead to Lost (Declined) and remove it from the advisor's queue (client req #3)."""
    lead.stage = 'Declined'
    lead.lost_reason = lead.lost_reason or 'Not Interested'
    lead.advisor = None          # advisor no longer has access
    lead.save(update_fields=['stage', 'lost_reason', 'advisor'])
    _audit(lead, actor, 'Marked Not Interested', 'Stage', '', 'Declined (Not Interested)')


@login_required
@require_POST
def log_call(request):
    """Advisor logs a prospecting call to a new lead; counts toward the monthly calling target."""
    name = request.POST.get('name', '').strip()
    phone = request.POST.get('phone', '').strip()
    outcome = request.POST.get('outcome', 'No Answer').strip()
    note = request.POST.get('note', '').strip()
    follow = _parse_date(request.POST.get('follow_up_date', ''))
    if outcome not in dict(CallLog.OUTCOME):
        outcome = 'No Answer'
    call = CallLog.objects.create(advisor=request.user, name=name, phone=phone,
                                  outcome=outcome, note=note, follow_up_date=follow)
    # if the prospect is interested, optionally spin up a real lead from the same call
    if request.POST.get('create_lead') and name and perm.can_create(request.user, 'Leads'):
        lead = Lead.objects.create(name=name, mobile=phone, advisor=request.user,
                                   source='Cold Calling', stage='Lead Received')
        call.lead = lead
        call.save(update_fields=['lead'])
        _audit(lead, request.user, 'Lead created', 'Lead', '', name)
        messages.success(request, f'Call logged and lead "{name}" created.')
    else:
        messages.success(request, 'Call logged.')
    # a follow-up date creates a reminder task (#4)
    if follow and call.lead:
        _auto_task(call.lead, f'Call follow-up — {name or call.lead.name}', 'Customer Call',
                   days=max(0, (follow - timezone.localdate()).days), actor=request.user)
    elif follow:
        Task.objects.create(title=f'Call follow-up — {name or phone}', assignee=request.user,
                            task_type='Customer Call', priority='Medium', status='Pending',
                            due_date=follow)
    # Not Interested → decline the linked lead + drop from advisor (#3)
    if outcome == 'Not Interested' and call.lead:
        _decline_not_interested(call.lead, request.user)
        messages.info(request, f'Lead "{call.lead.name}" moved to Lost Leads (Not Interested).')
    return redirect('dashboard')


@login_required
def call_history(request):
    """Call log — filterable by date range, with edit/delete (client req #2).
    Own-scope users see only their own; managers/CEO can view any advisor via ?advisor=<pk>."""
    own = perm.is_own_scope(request.user, 'Leads')
    view_adv = request.user
    if not own and request.GET.get('advisor'):
        view_adv = get_object_or_404(User, pk=request.GET['advisor'])
    calls = CallLog.objects.filter(advisor=view_adv).select_related('lead')
    frm = _parse_date(request.GET.get('from', ''))
    to = _parse_date(request.GET.get('to', ''))
    if frm:
        calls = calls.filter(created_at__date__gte=frm)
    if to:
        calls = calls.filter(created_at__date__lte=to)
    rows = [{
        'id': c.pk, 'name': c.name or '—', 'phone': c.phone or '—', 'outcome': c.outcome,
        'note': c.note or '', 'lead_pk': c.lead_id,
        'follow': c.follow_up_date.strftime('%Y-%m-%d') if c.follow_up_date else '',
        'when': timezone.localtime(c.created_at).strftime('%d %b %Y · %I:%M %p'),
    } for c in calls[:500]]
    return render(request, 'crm/call_history.html', {
        'rows': rows, 'outcomes': [o[0] for o in CallLog.OUTCOME],
        'frm': request.GET.get('from', ''), 'to': request.GET.get('to', ''),
        'viewing_other': view_adv != request.user,
        'viewing_name': view_adv.get_full_name() or view_adv.username,
        'active_nav': 'Advisors' if view_adv != request.user else 'Dashboard'})


@login_required
@require_POST
def call_edit(request, pk):
    # own-scope users may edit only their own calls; managers/CEO may edit any
    if perm.is_own_scope(request.user, 'Leads'):
        call = get_object_or_404(CallLog, pk=pk, advisor=request.user)
    else:
        call = get_object_or_404(CallLog, pk=pk)
    if request.POST.get('delete'):
        adv=call.advisor_id
        call.delete()
        messages.success(request, 'Call log entry deleted.')
        return redirect(request.META.get('HTTP_REFERER') or 'call_history')
    outcome = request.POST.get('outcome', call.outcome)
    if outcome in dict(CallLog.OUTCOME):
        call.outcome = outcome
    call.name = request.POST.get('name', call.name).strip()
    call.phone = request.POST.get('phone', call.phone).strip()
    call.note = request.POST.get('note', call.note).strip()
    call.follow_up_date = _parse_date(request.POST.get('follow_up_date', '')) or None
    call.save()
    # if edited to Not Interested and linked to a lead, decline it
    if call.outcome == 'Not Interested' and call.lead_id:
        _decline_not_interested(call.lead, request.user)
    messages.success(request, 'Call log updated.')
    return redirect(request.META.get('HTTP_REFERER') or 'call_history')


@login_required
@perm.module_required('Leads', 'edit')
@require_POST
def lead_not_interested(request, pk):
    """Mark a lead Not Interested from its detail page → Lost Leads + advisor loses access (#3)."""
    lead = get_object_or_404(visible_leads(request.user), pk=pk)
    lead.lost_reason = request.POST.get('reason', '').strip() or 'Not Interested'
    _decline_not_interested(lead, request.user)
    messages.success(request, f'"{lead.name}" marked Not Interested and moved to Lost Leads.')
    return redirect('lead_list')


# ---------- leads ----------
@login_required
@perm.module_required('Leads')
def lead_list(request):
    q = request.GET.get('q', '').strip()
    stage = request.GET.get('stage', '')
    base = visible_leads(request.user)
    disbursed_stages = ['Disbursed', 'Property Transfer Scheduled', 'Property Transfer', 'Property Transferred']
    kpis = {
        'total': base.count(),
        'active': base.exclude(stage__in=disbursed_stages + ['Declined']).count(),
        'disbursed': base.filter(stage__in=disbursed_stages).count(),
        'lost': base.filter(stage='Declined').count(),
        'value': base.aggregate(s=Sum('loan_amount'))['s'] or 0,
    }
    leads = base
    if q:
        leads = leads.filter(Q(name__icontains=q) | Q(mobile__icontains=q) | Q(email__icontains=q))
    if stage:
        leads = leads.filter(stage=stage)
    # report drill-down filters
    src = request.GET.get('source', '')
    if src:
        leads = leads.filter(source=src)
    adv = request.GET.get('advisor', '')
    if adv:
        leads = leads.filter(advisor_id=adv)
    kyc = request.GET.get('kyc', '')
    if kyc:
        leads = leads.filter(kyc_status=kyc)
    leads = leads.order_by('-created_at')

    def _act(l):
        return l.updated_at.strftime('%d %b %Y')
    leads_js = [{
        'id': l.pk, 'name': l.name, 'mobile': l.mobile or '—', 'email': l.email or '—',
        'nat': l.nationality or '—', 'propVal': _f(l.property_value), 'loan': _f(l.loan_amount),
        'advisor': (l.advisor.get_full_name() or l.advisor.username) if l.advisor else 'Unassigned',
        'bank': l.bank.name if l.bank else '—', 'source': l.source, 'stage': l.stage,
        'priority': l.priority, 'act': _act(l), 'created': l.created_at.strftime('%Y-%m-%d'),
        'draft': l.is_draft, 'score': l.score, 'sla': l.sla_status,
    } for l in leads]
    # own-scope users (advisors) must not see other advisors' names
    own_scope = perm.is_own_scope(request.user, 'Leads')
    if own_scope:
        advisors = []
    else:
        advisors = [u.get_full_name() or u.username for u in User.objects.filter(role=Role.ADVISOR)]
    banks = [b.name for b in Bank.objects.all()]
    me = request.user.get_full_name() or request.user.username
    total_val = _f(kpis['value'])
    kpis_js = [
        {'l': 'Total Leads', 'v': str(kpis['total']),
         'ic': '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/>'},
        {'l': 'New Leads Today', 'v': str(base.filter(created_at__date=timezone.localdate()).count()),
         'ic': '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/><path d="M19 8h4M21 6v4" stroke-width="2.2"/>'},
        {'l': 'Documents Pending', 'v': str(base.filter(stage__in=['Lead Received', 'Documents Pending']).count()),
         'ic': '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M12 11v4M12 18h.01"/>'},
        {'l': 'Pre-Approvals', 'v': str(base.filter(stage='Pre-Approved').count()),
         'ic': '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="m9 12 2 2 4-4"/>'},
        {'l': 'Disbursements', 'v': str(kpis['disbursed']),
         'ic': '<path d="M3 11.5 12 4l9 7.5"/><path d="M6 10.5V20h4.5v-5h3v5H18v-9.5"/>'},
        {'l': 'Declined Cases', 'v': str(kpis['lost']),
         'ic': '<circle cx="12" cy="12" r="9"/><path d="m15 9-6 6M9 9l6 6"/>'},
        {'l': 'Pipeline Value', 'v': 'AED ' + (f'{total_val/1e6:.0f}M' if total_val >= 1e6 else f'{total_val/1e3:.0f}K'),
         'ic': '<path d="M3 3v18h18"/><path d="m7 14 4-4 4 3 5-6"/>'},
        {'l': 'This Month Revenue', 'v': 'AED ' + f'{_f(base.filter(stage__in=disbursed_stages).aggregate(v=Sum("loan_amount"))["v"])*0.011:,.0f}',
         'ic': '<rect x="2" y="6" width="20" height="12" rx="2"/><circle cx="12" cy="12" r="2.6"/>'},
    ]
    customized_ids = list(Customization.objects.values_list('lead_id', flat=True)) \
        if request.user.role == Role.CEO else []
    can_assign = perm.can_edit(request.user, 'Leads') and not own_scope
    assign_advisors = ([{'id': u.pk, 'name': u.get_full_name() or u.username}
                        for u in User.objects.filter(role=Role.ADVISOR, status='Active')]
                       if can_assign else [])
    data = {'leads': leads_js, 'advisors': advisors, 'banks': banks, 'sources': SOURCES,
            'me': me, 'kpis': kpis_js, 'customizedIds': customized_ids, 'ownScope': own_scope,
            'assignAdvisors': assign_advisors, 'canAssign': can_assign}
    from .models import SavedView
    saved_views = SavedView.objects.filter(module='Leads').filter(Q(user=request.user) | Q(shared=True))
    return render(request, 'crm/lead_list.html', {
        'data': data, 'q': q, 'stage': stage, 'kpis': kpis, 'own_scope': own_scope,
        'stages': [s[0] for s in Lead.STAGE_CHOICES],
        'can_create': perm.can_create(request.user, 'Leads'),
        'can_delete': perm.can_delete(request.user, 'Leads'),
        'saved_views': saved_views, 'cur_qs': request.META.get('QUERY_STRING', ''),
        'active_nav': 'Leads', 'active_sub': 'lead_list',
    })


def _save_lead_documents(request, lead, uploader):
    """Save dynamic document rows from the lead form.

    Each row posts doc_file_<n> (file) plus doc_name_<n> / doc_type_<n>. The old
    fixed-checklist fields (doc::<type>) are still accepted for compatibility.
    """
    n = 0
    for key in list(request.FILES.keys()):
        f = request.FILES[key]
        if key.startswith('doc_file_'):
            idx = key[len('doc_file_'):]
            dtype = (request.POST.get('doc_type_' + idx) or '').strip() or 'Document'
            dname = (request.POST.get('doc_name_' + idx) or '').strip()
        elif key.startswith('doc::'):
            dtype = key[5:]
            dname = ''
            idx = None
        else:
            continue
        exp = _parse_date(request.POST.get('doc_expiry_' + idx)) if idx is not None else None
        doc = Document.objects.create(lead=lead, name=dname, doc_type=dtype, file=f,
                                      status='Pending Review', uploaded_by=uploader, expiry_date=exp)
        _supersede_previous(doc)
        _audit(lead, request.user, 'Document uploaded', dname or dtype)
        n += 1
    return n


def _supersede_previous(new_doc):
    """When a doc of the same type is re-uploaded, keep the old one as a prior version."""
    prev = (Document.objects.filter(lead=new_doc.lead, doc_type=new_doc.doc_type, is_current=True)
            .exclude(pk=new_doc.pk).order_by('-version').first())
    if prev:
        new_doc.version = prev.version + 1
        new_doc.save(update_fields=['version'])
        prev.is_current = False
        prev.save(update_fields=['is_current'])


def _parse_date(v):
    from datetime import datetime as _dt
    v = (v or '').strip()
    if not v:
        return None
    try:
        return _dt.strptime(v, '%Y-%m-%d').date()
    except ValueError:
        return None


@login_required
def audit_log(request):
    # PRD §16.7 — Compliance & External Auditor read/export the audit trail, plus system admins.
    if not perm.can_view_audit(request.user):
        raise PermissionDenied("Your role can't view the audit trail.")
    q = request.GET.get('q', '').strip()
    events = []
    la = LeadAudit.objects.select_related('user', 'lead')
    ae = AuditEvent.objects.select_related('user')
    if q:
        la = la.filter(Q(action__icontains=q) | Q(field__icontains=q) | Q(lead__name__icontains=q) |
                       Q(user__username__icontains=q) | Q(user__first_name__icontains=q))
        ae = ae.filter(Q(action__icontains=q) | Q(detail__icontains=q) |
                       Q(user__username__icontains=q) | Q(user__first_name__icontains=q))
    for a in la[:400]:
        who = (a.user.get_full_name() or a.user.username) if a.user else 'System'
        det = a.action + (f' · {a.field}' if a.field else '')
        if a.new_value:
            det += f': {a.old_value or "—"} → {a.new_value}'
        events.append({'who': who, 'what': det, 'record': a.lead.name if a.lead else '—',
                       'url': f'/leads/{a.lead_id}/' if a.lead_id else '', 'ts': a.created_at})
    for a in ae[:400]:
        who = (a.user.get_full_name() or a.user.username) if a.user else 'System'
        events.append({'who': who, 'what': a.action + (f' · {a.detail}' if a.detail else '') +
                       (f' · IP {a.ip}' if a.ip else ''), 'record': '—', 'url': '', 'ts': a.created_at})
    events.sort(key=lambda e: e['ts'], reverse=True)
    rows = [{'who': e['who'], 'what': e['what'], 'record': e['record'], 'url': e['url'],
             'when': e['ts'].strftime('%d %b %Y · %I:%M %p')} for e in events[:500]]
    return render(request, 'crm/audit_log.html', {'rows': rows, 'q': q, 'active_nav': 'Settings', 'active_sub': 'audit'})


def _web_token():
    """The web-to-lead token (auto-generated + stored on first use)."""
    import secrets
    row = AppSetting.objects.filter(key='web_token').first()
    if row and row.value.get('token'):
        return row.value['token']
    tok = secrets.token_urlsafe(24)
    AppSetting.objects.update_or_create(key='web_token', defaults={'value': {'token': tok}})
    return tok


@csrf_exempt
@require_POST
def web_to_lead(request):
    """Public web-to-lead capture endpoint (PRD §9.1 capture APIs). Token-gated + honeypot spam control.
    POST fields: token, name, mobile, email, source, loan_amount, property_value, note. (+ hidden 'company' honeypot)"""
    if request.POST.get('company'):          # honeypot — bots fill hidden fields
        return JsonResponse({'ok': True})    # silently accept, drop
    token = request.POST.get('token', '')
    if token != _web_token():
        return JsonResponse({'ok': False, 'error': 'invalid token'}, status=403)
    name = request.POST.get('name', '').strip()
    if not name:
        return JsonResponse({'ok': False, 'error': 'name required'}, status=400)
    src = request.POST.get('source', 'Website')
    if src not in SOURCES:
        src = 'Website'
    lead = Lead(name=name, mobile=request.POST.get('mobile', '').strip(),
                email=request.POST.get('email', '').strip(), source=src,
                loan_amount=_num(request.POST.get('loan_amount')),
                property_value=_num(request.POST.get('property_value')),
                bank_notes=request.POST.get('note', '').strip(), stage='Lead Received')
    lead.advisor = _auto_assign_advisor(lead)
    _coerce_lead_numbers(lead)
    lead.score = lead.compute_score(); _compute_eligibility(lead)
    _set_sla_due(lead)
    lead.case_number = generate_case_number()
    lead.save()
    _link_client(lead)
    _audit(lead, None, 'Lead created', 'Lead', '', 'Web-to-lead API')
    if lead.advisor:
        _notify(lead.advisor, f'New web lead assigned: "{lead.name}"', f'/leads/{lead.pk}/', 'lead')
    return JsonResponse({'ok': True, 'case_number': lead.case_number})


@login_required
def my_day(request):
    """My Day landing screen (PRD §14.3): my tasks ordered by SLA risk → overdue → today → priority,
    plus SLA-risk leads and follow-ups due."""
    from datetime import date
    u = request.user
    today = date.today()
    tasks = Task.objects.filter(assignee=u, is_deleted=False).exclude(
        status__in=['Completed', 'Cancelled']).select_related('lead')
    prio = {'High': 0, 'Medium': 1, 'Low': 2}

    def _key(t):
        overdue = 0 if (t.due_date and t.due_date < today) else 1
        due_today = 0 if (t.due_date == today) else 1
        return (overdue, due_today, prio.get(t.priority, 3), t.due_date or date.max)
    task_rows = [{
        'pk': t.pk, 'lead_pk': t.lead_id, 'title': t.title, 'type': t.task_type,
        'priority': t.priority, 'lead': t.lead.name if t.lead else '—',
        'due': t.due_date.strftime('%d %b') if t.due_date else '—',
        'overdue': bool(t.due_date and t.due_date < today),
    } for t in sorted(tasks, key=_key)]

    # SLA-risk: my leads not yet first-contacted, ordered by due
    sla_leads = visible_leads(u).filter(advisor=u, is_draft=False, first_contacted_at__isnull=True,
                                        first_contact_due__isnull=False).order_by('first_contact_due')[:10]
    sla_rows = [{'pk': l.pk, 'name': l.name, 'case': l.case_number or f'#{l.pk}',
                 'status': l.sla_status,
                 'due': l.first_contact_due.strftime('%d %b %H:%M') if l.first_contact_due else ''}
                for l in sla_leads]
    # follow-ups due today or overdue on my cases
    fu_due = FollowUp.objects.filter(lead__advisor=u, done=False, next_date__lte=today,
                                     lead__is_deleted=False).select_related('lead')[:10]
    fu_rows = [{'pk': f.lead_id, 'name': f.lead.name, 'channel': f.channel,
                'date': f.next_date.strftime('%d %b') if f.next_date else ''} for f in fu_due]
    return render(request, 'crm/my_day.html', {
        'tasks': task_rows, 'sla_rows': sla_rows, 'fu_rows': fu_rows,
        'greet': u.first_name or u.username, 'active_nav': 'MyDay'})


@login_required
def notifications_list(request):
    notes = request.user.notifications.all()[:100]
    return render(request, 'crm/notifications.html', {
        'notes': notes, 'unread': request.user.notifications.filter(read=False).count(),
        'active_nav': 'Notifications'})


@login_required
def notification_open(request, pk):
    n = get_object_or_404(Notification, pk=pk, user=request.user)
    n.read = True
    n.save(update_fields=['read'])
    return redirect(n.url or 'notifications_list')


@login_required
@require_POST
def notifications_read_all(request):
    request.user.notifications.filter(read=False).update(read=True)
    return redirect('notifications_list')


def _round_robin_advisor(pool=None):
    """Least-loaded active advisor, respecting daily caps + out-of-office (PRD §9.5)."""
    from django.db.models import Count
    advs = pool if pool is not None else User.objects.filter(role=Role.ADVISOR, status='Active',
                                                             out_of_office=False)
    if not advs.exists():
        advs = User.objects.filter(role=Role.ADVISOR)
    today = timezone.localdate()
    advs = advs.annotate(
        open_leads=Count('leads', filter=~Q(leads__stage='Declined') & Q(leads__is_deleted=False)),
        today_leads=Count('leads', filter=Q(leads__created_at__date=today) & Q(leads__is_deleted=False))
    ).order_by('open_leads', 'pk')
    # skip advisors who hit their daily cap
    for a in advs:
        if not a.daily_lead_cap or a.today_leads < a.daily_lead_cap:
            return a
    return advs.first()


def _auto_assign_advisor(lead=None):
    """Assignment engine (PRD §9.5): apply ordered rules (source / loan-size band → user or
    round-robin), falling back to least-loaded round-robin."""
    from .models import AssignmentRule
    if lead is not None:
        loan = float(lead.loan_amount or 0)
        for rule in AssignmentRule.objects.filter(active=True):
            if rule.match_source and rule.match_source != lead.source:
                continue
            if rule.min_loan and loan < float(rule.min_loan):
                continue
            if rule.max_loan and float(rule.max_loan) > 0 and loan > float(rule.max_loan):
                continue
            if rule.action == 'user' and rule.action_user and not rule.action_user.out_of_office:
                return rule.action_user
            return _round_robin_advisor()
    return _round_robin_advisor()


def _lead_form_data(form, init=None):
    return {
        'advisors': [{'pk': a.pk, 'name': a.get_full_name() or a.username}
                     for a in form.fields['advisor'].queryset],
        'banks': [{'pk': b.pk, 'name': b.name} for b in form.fields['bank'].queryset],
        'sources': SOURCES,
        'partners': [{'pk': p.pk, 'name': p.name} for p in ReferralPartner.objects.filter(status='Active')],
        'init': init or {},
    }


@login_required
@perm.module_required('Leads', 'delete')
def recycle_bin(request):
    def _who(u):
        return (u.get_full_name() or u.username) if u else '—'

    def _when(dt):
        return dt.strftime('%d %b %Y · %I:%M %p') if dt else '—'

    leads = Lead.objects.filter(is_deleted=True).select_related('advisor', 'deleted_by').order_by('-deleted_at')
    rows = [{
        'id': l.pk, 'name': l.name, 'mobile': l.mobile or '—',
        'advisor': _who(l.advisor), 'deletedBy': _who(l.deleted_by), 'deletedAt': _when(l.deleted_at),
    } for l in leads]
    tasks = Task.objects.filter(is_deleted=True).select_related('lead', 'deleted_by').order_by('-deleted_at')
    task_rows = [{
        'id': t.pk, 'title': t.title, 'lead': t.lead.name if t.lead else '—',
        'deletedBy': _who(t.deleted_by), 'deletedAt': _when(t.deleted_at),
    } for t in tasks]
    docs = Document.objects.filter(is_deleted=True).select_related('lead', 'deleted_by').order_by('-deleted_at')
    doc_rows = [{
        'id': d.pk, 'name': d.name or d.doc_type, 'lead': d.lead.name if d.lead else '—',
        'deletedBy': _who(d.deleted_by), 'deletedAt': _when(d.deleted_at),
    } for d in docs]
    return render(request, 'crm/recycle_bin.html', {
        'rows': rows, 'task_rows': task_rows, 'doc_rows': doc_rows,
        'is_super': request.user.role in (Role.CEO, Role.SUPER_ADMIN), 'active_nav': 'Leads'})


@login_required
@perm.module_required('Leads', 'delete')
@require_POST
def lead_restore_deleted(request, pk):
    lead = get_object_or_404(Lead, pk=pk, is_deleted=True)
    lead.is_deleted = False
    lead.deleted_at = None
    lead.deleted_by = None
    lead.save(update_fields=['is_deleted', 'deleted_at', 'deleted_by'])
    _audit(lead, request.user, 'Lead restored', 'Lead', 'Recycle Bin', lead.name)
    messages.success(request, f'Lead "{lead.name}" restored.')
    return redirect('recycle_bin')


@login_required
@perm.module_required('Tasks', 'delete')
@require_POST
def task_restore(request, pk):
    t = get_object_or_404(Task, pk=pk, is_deleted=True)
    t.is_deleted = False
    t.deleted_at = None
    t.deleted_by = None
    t.save(update_fields=['is_deleted', 'deleted_at', 'deleted_by'])
    _audit_event(request, 'Task restored', t.title)
    messages.success(request, f'Task "{t.title}" restored.')
    return redirect('recycle_bin')


@login_required
@perm.module_required('Documents', 'delete')
@require_POST
def document_restore(request, pk):
    d = get_object_or_404(Document, pk=pk, is_deleted=True)
    d.is_deleted = False
    d.deleted_at = None
    d.deleted_by = None
    d.save(update_fields=['is_deleted', 'deleted_at', 'deleted_by'])
    _audit_event(request, 'Document restored', d.name or d.doc_type)
    messages.success(request, 'Document restored.')
    return redirect('recycle_bin')


@login_required
@require_POST
def lead_purge(request, pk):
    # permanent delete — CEO only
    if request.user.role != Role.CEO:
        messages.error(request, 'Only the CEO can permanently delete.')
        return redirect('recycle_bin')
    lead = get_object_or_404(Lead, pk=pk, is_deleted=True)
    name = lead.name
    lead.delete()
    messages.success(request, f'Lead "{name}" permanently deleted.')
    return redirect('recycle_bin')


@login_required
@perm.module_required('Leads', 'create')
def lead_import(request):
    """Bulk-import leads from a CSV. Columns (case-insensitive): name, mobile, email,
    nationality, property_value, loan_amount, ltv, source, priority."""
    import csv as _csv
    import io as _io
    if request.method == 'POST' and request.FILES.get('file'):
        f = request.FILES['file']
        try:
            text = f.read().decode('utf-8-sig')
        except UnicodeDecodeError:
            text = f.read().decode('latin-1')
        reader = _csv.DictReader(_io.StringIO(text))
        norm = {}
        for col in (reader.fieldnames or []):
            norm[col] = col.strip().lower().replace(' ', '_')
        created = skipped = dup = 0
        errors = []
        dup_names = []
        created_pks = []
        preview = bool(request.POST.get('preview'))   # dry-run: report, don't save
        valid_sources = set(dict(Lead.SOURCE_CHOICES))
        valid_pri = {'High', 'Medium', 'Low'}
        for i, raw in enumerate(reader, start=2):
            row = {norm.get(k, k): (v or '').strip() for k, v in raw.items()}
            name = row.get('name', '')
            if not name:
                skipped += 1
                continue
            mobile = row.get('mobile', ''); email = row.get('email', '')
            if (mobile and Lead.objects.filter(is_draft=False, mobile=mobile).exists()) or \
               (email and Lead.objects.filter(is_draft=False, email=email).exists()):
                dup += 1
                dup_names.append(name)
                continue
            if preview:
                created += 1   # would-create count only
                continue
            def _num(v):
                try:
                    return float(str(v).replace(',', '')) if v else 0
                except ValueError:
                    return 0
            src = row.get('source', 'Website'); src = src if src in valid_sources else 'Website'
            pri = (row.get('priority', 'Medium') or 'Medium').title()
            pri = pri if pri in valid_pri else 'Medium'
            try:
                lead = Lead(name=name, mobile=mobile, email=email,
                            nationality=row.get('nationality', ''),
                            property_value=_num(row.get('property_value')),
                            loan_amount=_num(row.get('loan_amount')),
                            ltv=int(_num(row.get('ltv')) or 80), source=src, priority=pri,
                            stage='Lead Received')
                if request.user.role == Role.ADVISOR:
                    lead.advisor = request.user
                else:
                    lead.advisor = _auto_assign_advisor(lead)
                lead.score = lead.compute_score(); _compute_eligibility(lead)
                _set_sla_due(lead)
                lead.case_number = generate_case_number()
                lead.save()
                _link_client(lead)
                _audit(lead, request.user, 'Lead created', 'Lead', '', 'CSV import')
                created += 1
                created_pks.append(lead.pk)
            except Exception as ex:
                errors.append(f'Row {i}: {ex}')
        if preview:
            # dry-run — show what would happen, keep the file so the user re-submits to commit
            return render(request, 'crm/lead_import.html', {
                'active_nav': 'Leads', 'active_sub': 'lead_list',
                'preview': {'created': created, 'dup': dup, 'skipped': skipped,
                            'dup_names': dup_names[:20]}})
        request.session['last_import'] = created_pks   # enables one-click undo
        _audit_event(request, 'CSV import', f'{created} leads created')
        msg = f'Import done — {created} created, {dup} duplicates skipped, {skipped} without a name skipped.'
        if dup_names:
            msg += ' Duplicates: ' + ', '.join(dup_names[:8]) + ('…' if len(dup_names) > 8 else '')
        messages.success(request, msg)
        if errors:
            messages.error(request, ' · '.join(errors[:5]))
        return redirect('lead_list')
    return render(request, 'crm/lead_import.html', {'active_nav': 'Leads', 'active_sub': 'lead_list'})


@login_required
@perm.module_required('Leads', 'create')
@require_POST
def lead_import_undo(request):
    """Undo the most recent CSV import (soft-delete the batch just created)."""
    pks = request.session.get('last_import') or []
    if not pks:
        messages.error(request, 'Nothing to undo.')
        return redirect('lead_list')
    n = 0
    for lead in Lead.objects.filter(pk__in=pks, is_deleted=False):
        lead.is_deleted = True
        lead.deleted_at = timezone.now()
        lead.deleted_by = request.user
        lead.save(update_fields=['is_deleted', 'deleted_at', 'deleted_by'])
        n += 1
    request.session['last_import'] = []
    _audit_event(request, 'CSV import undone', f'{n} leads')
    messages.success(request, f'Import undone — {n} imported lead(s) moved to recycle bin.')
    return redirect('lead_list')


@login_required
@perm.module_required('Leads', 'create')
def lead_create(request):
    is_draft = bool(request.POST.get('draft'))
    form = LeadForm(request.POST or None)
    if is_draft:
        # a draft may be saved with only partial info — relax all field requirements
        for f in form.fields.values():
            f.required = False
    if request.user.role == Role.ADVISOR:
        form.fields['advisor'].initial = request.user
    if request.method == 'POST' and form.is_valid():
        dup = None
        if not is_draft:
            m = (form.cleaned_data.get('mobile') or '').strip()
            e = (form.cleaned_data.get('email') or '').strip()
            dq = Lead.objects.filter(is_draft=False)
            if m and e:
                dup = dq.filter(Q(mobile=m) | Q(email=e)).first()
            elif m:
                dup = dq.filter(mobile=m).first()
            elif e:
                dup = dq.filter(email=e).first()
        if is_draft and not (form.cleaned_data.get('name') or '').strip():
            messages.error(request, 'Enter at least a name to save a draft.')
        elif dup and not request.POST.get('force_dup'):
            if perm.is_own_scope(request.user, 'Leads'):
                # advisors must not see who owns another advisor's lead
                messages.error(request, 'Possible duplicate: a lead with this mobile/email already '
                               'exists in the system. Submit again to create anyway.')
            else:
                owner = (dup.advisor.get_full_name() or dup.advisor.username) if dup.advisor else 'Unassigned'
                messages.error(request, f'Possible duplicate: "{dup.name}" already exists with this '
                               f'mobile/email (advisor: {owner}). Submit again to create anyway.')
            data = _lead_form_data(form)
            return render(request, 'crm/lead_form.html', {
                'form': form, 'title': 'Create Lead', 'submit_label': 'Create Lead',
                'data': data, 'active_nav': 'Leads', 'force_dup': True,
                'own_scope': perm.is_own_scope(request.user, 'Leads')})
        else:
            lead = form.save(commit=False)
            if request.user.role == Role.ADVISOR:
                lead.advisor = request.user
            elif lead.advisor is None and not is_draft:
                lead.advisor = _auto_assign_advisor(lead)   # rules engine + round-robin
            lead.is_draft = is_draft
            _coerce_lead_numbers(lead)
            lead.score = lead.compute_score(); _compute_eligibility(lead)
            if not is_draft:
                _set_sla_due(lead)
            if not lead.case_number and not is_draft:
                lead.case_number = generate_case_number()
            lead.save()
            if not is_draft:
                _link_client(lead)   # attach to the Client person (PRD §10.1)
            if lead.advisor and not (request.user.role == Role.ADVISOR):
                _audit(lead, request.user, 'Advisor assigned', 'Advisor', '', str(lead.advisor))
                _notify(lead.advisor, f'You were assigned lead "{lead.name}"',
                        f'/leads/{lead.pk}/', 'lead', actor=request.user)
            uploader = request.user.get_full_name() or request.user.username
            _audit(lead, request.user, 'Draft saved' if is_draft else 'Lead created',
                   'Lead', '', lead.name)
            _save_lead_documents(request, lead, uploader)
            if not is_draft and lead.advisor:
                _auto_task(lead, f'First contact — {lead.name}', 'Customer Call', days=1,
                           actor=request.user)
            messages.success(request, f'Draft "{lead.name}" saved.' if is_draft
                             else f'Lead "{lead.name}" created.')
            return redirect('lead_detail', pk=lead.pk)
    data = _lead_form_data(form)
    return render(request, 'crm/lead_form.html', {
        'form': form, 'title': 'Create Lead', 'submit_label': 'Create Lead',
        'data': data, 'active_nav': 'Leads',
        'own_scope': perm.is_own_scope(request.user, 'Leads')})


@login_required
@perm.module_required('Leads')
def lead_detail(request, pk):
    lead = get_object_or_404(visible_leads(request.user), pk=pk)
    documents = lead.documents.filter(is_current=True, is_deleted=False)
    tasks = lead.tasks.filter(is_deleted=False)

    advisor_name = (lead.advisor.get_full_name() or lead.advisor.username) if lead.advisor else 'Unassigned'
    bank_name = lead.bank.name if lead.bank else ''

    lead_js = {
        'id': lead.pk, 'name': lead.name, 'caseNo': lead.case_number or '—',
        'mobile': lead.mobile or '—',
        'email': lead.email or '—', 'nat': lead.nationality or '—',
        'propVal': float(lead.property_value or 0), 'loan': float(lead.loan_amount or 0),
        'ltv': lead.ltv or 0, 'advisor': advisor_name, 'bank': bank_name or '—',
        'source': lead.source, 'stage': lead.stage, 'priority': lead.priority,
        'created': lead.created_at.isoformat(), 'act': lead.updated_at.strftime('%d %b %Y'),
        'initials': lead.initials,
        'disbursedAt': lead.disbursed_at.strftime('%d %b %Y') if lead.disbursed_at else '',
        'disbursedIso': lead.disbursed_at.isoformat() if lead.disbursed_at else '',
        'employer': lead.employer or '', 'empType': lead.employment_type or '',
        'income': float(lead.monthly_income or 0), 'years': float(lead.years_employment or 0),
        'industry': lead.industry or '',
        'company': lead.company_name or '', 'turnover': float(lead.annual_turnover or 0),
        'bizYears': float(lead.business_years or 0),
        'score': lead.score,
        'kyc': lead.kyc_status,
        'eligible': lead.eligible,
        'eligibilityNote': lead.eligibility_note,
        'consent': [c for c, on in [('Call', lead.consent_call), ('SMS', lead.consent_sms),
                    ('WhatsApp', lead.consent_whatsapp), ('Email', lead.consent_email)] if on],
        'dnc': bool(lead.client and lead.client.do_not_contact),   # global do-not-contact (PRD §16.4)
        'commRate': float(lead.bank.commission_rate) if lead.bank else 0,  # real bank commission % (PRD §4.4)
    }

    # field-level security — mask sensitive groups for restricted roles
    hidden = perm.hidden_field_groups(request.user)
    if 'financials' in hidden:
        for k in ('propVal', 'loan', 'income', 'turnover'):
            lead_js[k] = 0
        lead_js['ltv'] = 0
        lead_js['fieldMask'] = lead_js.get('fieldMask', []) + ['financials']
    if 'contact' in hidden:
        lead_js['mobile'] = '•••••'
        lead_js['email'] = '•••••'
        lead_js['fieldMask'] = lead_js.get('fieldMask', []) + ['contact']

    def _doc_badge(status):
        if status == 'Verified':
            return 'ok', 'Verified'
        if status == 'Missing':
            return 'miss', 'Missing'
        return 'pend', status

    documents_js = []
    for d in documents:
        s, txt = _doc_badge(d.status)
        documents_js.append({
            't': d.name or d.doc_type,
            'm': f'{d.doc_type} · {d.uploaded_by} · {d.created_at.strftime("%d %b %Y")}',
            's': s, 'txt': txt,
            'url': d.file.url if d.file else '',
        })

    tasks_js = [{
        'title': t.title, 'type': t.task_type, 'priority': t.priority,
        'status': t.status, 'due': t.due_date.strftime('%d %b %Y') if t.due_date else '—',
    } for t in tasks]

    notes_js = [{
        'author': (n.author.get_full_name() or n.author.username) if n.author else '—',
        'role': n.author.role_label if n.author else '',
        'initials': n.author.initials if n.author else '·',
        'when': n.created_at.strftime('%d %b %Y · %I:%M %p'),
        'text': n.text,
    } for n in lead.notes.select_related('author')]

    audits_js = [{
        'user': (a.user.get_full_name() or a.user.username) if a.user else 'System',
        'initials': a.user.initials if a.user else '·',
        'role': a.user.role_label if a.user else '',
        'action': a.action, 'field': a.field,
        'old': a.old_value, 'new': a.new_value,
        'when': a.created_at.strftime('%d %b %Y · %I:%M %p'),
    } for a in lead.audits.select_related('user')]

    bank_apps_js = [{
        'id': a.pk, 'bankId': a.bank_id or '', 'bank': a.bank.name if a.bank else '—',
        'status': a.status, 'ref': a.reference_no,
        'req': float(a.requested_amount or 0), 'sanc': float(a.sanctioned_amount or 0),
        'rate': float(a.interest_rate or 0), 'reject': a.rejection_reason, 'notes': a.notes,
        'submitted': a.submitted_at.strftime('%d %b %Y') if a.submitted_at else '',
        'decision': a.decision_at.strftime('%d %b %Y') if a.decision_at else '',
    } for a in lead.bank_applications.select_related('bank')]

    followups_js = [{
        'channel': f.channel, 'note': f.note,
        'next': f.next_date.strftime('%d %b %Y') if f.next_date else '',
        'by': (f.created_by.get_full_name() or f.created_by.username) if f.created_by else '—',
        'when': f.created_at.strftime('%d %b %Y · %I:%M %p'),
    } for f in lead.followups.select_related('created_by')]

    data = {
        'lead': lead_js, 'stageOrder': STAGES,
        'documents': documents_js, 'tasks': tasks_js, 'notes': notes_js,
        'audits': audits_js, 'bankApps': bank_apps_js, 'followups': followups_js,
        'silence': lead.silence_status,
        'bankOptions': list(Bank.objects.filter(status='Active').values('id', 'name')),
        'appStatuses': [s[0] for s in BankApplication.STATUS],
        'channels': [c[0] for c in FollowUp.CHANNEL],
        'kycPassed': lead.kyc_status == 'Passed',
    }
    # own-scope users can't reassign, so don't expose other advisors' names
    advisors = User.objects.none() if perm.is_own_scope(request.user, 'Leads') \
        else User.objects.filter(role=Role.ADVISOR)
    return render(request, 'crm/lead_detail.html', {
        'lead': lead, 'documents': documents, 'tasks': tasks, 'data': data,
        'advisors': advisors,
        'can_edit': perm.can_edit(request.user, 'Leads'),
        'can_delete': perm.can_delete(request.user, 'Leads'),
        'can_kyc': perm.can_kyc(request.user),
        'can_finance': perm.can_access(request.user, 'Finance'),  # PRD §8.1 bank commission hidden from advisors
        'can_assign': perm.can_edit(request.user, 'Leads') and not perm.is_own_scope(request.user, 'Leads'),
        # operations workflow (PRD §11–12)
        'handover_blockers': _handover_blockers(lead),
        'is_ops_mgr': request.user.role in (Role.OPS_MANAGER, Role.CEO, Role.SUPER_ADMIN),
        'ops_execs': User.objects.filter(role__in=[Role.OPS_EXECUTIVE, Role.OPS_MANAGER]),
        # compliance (PRD §16)
        'kyc_blockers': _kyc_blockers(lead),
        'screen_opts': [s[0] for s in Lead.SCREEN],
        'risk_opts': [r[0] for r in Lead.RISK],
        # processing / queries (PRD §11)
        'bank_queries': lead.bank_queries.select_related('created_by'),
        'active_nav': 'Leads',
    })


@login_required
@perm.module_required('Leads')
def client_360(request, pk):
    """Consolidated client profile: every case, document, bank application, follow-up
    and note for one person (matched by mobile / email), across their whole history."""
    base = get_object_or_404(visible_leads(request.user), pk=pk)
    # prefer the real Client entity; fall back to mobile/email matching for unlinked legacy leads
    if base.client_id:
        cases = visible_leads(request.user).filter(client_id=base.client_id).distinct().order_by('-created_at')
    else:
        q = Q(pk=base.pk)
        if base.mobile:
            q |= Q(mobile=base.mobile)
        if base.email:
            q |= Q(email=base.email)
        cases = visible_leads(request.user).filter(q).distinct().order_by('-created_at')

    DISB = ['Disbursed', 'Property Transfer Scheduled', 'Property Transfer', 'Property Transferred']
    hidden = perm.hidden_field_groups(request.user)
    mask_fin = 'financials' in hidden
    mask_con = 'contact' in hidden

    def money(v):
        return '••••' if mask_fin else f'AED {_f(v):,.0f}'

    total_loan = _f(cases.aggregate(v=Sum('loan_amount'))['v'])
    disbursed = cases.filter(stage__in=DISB)
    case_rows = [{
        'pk': c.pk, 'case': c.case_number or f'#{c.pk}', 'stage': c.stage,
        'loan': money(c.loan_amount), 'bank': c.bank.name if c.bank else '—',
        'advisor': (c.advisor.get_full_name() or c.advisor.username) if c.advisor else '—',
        'kyc': c.kyc_status, 'created': c.created_at.strftime('%d %b %Y'),
    } for c in cases]

    documents = Document.objects.filter(lead__in=cases, is_current=True, is_deleted=False).select_related('lead')
    doc_rows = [{'name': d.name or d.doc_type, 'type': d.doc_type, 'status': d.status,
                 'case': d.lead.case_number or f'#{d.lead_id}',
                 'expiry': d.expiry_date.strftime('%d %b %Y') if d.expiry_date else '—'} for d in documents]

    apps = BankApplication.objects.filter(lead__in=cases).select_related('bank', 'lead')
    app_rows = [{'bank': a.bank.name if a.bank else '—', 'status': a.status,
                 'case': a.lead.case_number or f'#{a.lead_id}',
                 'sanc': money(a.sanctioned_amount)} for a in apps]

    fus = FollowUp.objects.filter(lead__in=cases).select_related('lead', 'created_by')[:20]
    fu_rows = [{'channel': f.channel, 'note': f.note,
                'case': f.lead.case_number or f'#{f.lead_id}',
                'by': (f.created_by.get_full_name() or f.created_by.username) if f.created_by else '—',
                'when': f.created_at.strftime('%d %b %Y')} for f in fus]

    cl = base.client
    client = {
        'name': cl.name if cl else base.name,
        'lifecycle': cl.lifecycle if cl else 'Lead',
        'dnc': cl.do_not_contact if cl else False,
        'mobile': '••••' if mask_con else ((cl.mobile if cl else base.mobile) or '—'),
        'email': '••••' if mask_con else ((cl.email if cl else base.email) or '—'),
        'nationality': (cl.nationality if cl else base.nationality) or '—',
        'employer': (cl.employer if cl else base.employer) or '—',
        'total_cases': cases.count(),
        'disbursed_cases': disbursed.count(),
        'total_loan': money(total_loan),
        'consent': [c for c, on in [('Call', base.consent_call), ('SMS', base.consent_sms),
                    ('WhatsApp', base.consent_whatsapp), ('Email', base.consent_email)] if on],
    }
    consent_log = []
    if cl:
        consent_log = [{'channel': r.channel, 'granted': r.granted, 'source': r.source,
                        'by': (r.captured_by.get_full_name() or r.captured_by.username) if r.captured_by else '—',
                        'when': r.created_at.strftime('%d %b %Y · %I:%M %p')}
                       for r in cl.consent_log.select_related('captured_by')[:20]]
    return render(request, 'crm/client_360.html', {
        'client': client, 'cases': case_rows, 'documents': doc_rows,
        'apps': app_rows, 'followups': fu_rows, 'base_pk': base.pk,
        'client_id': base.client_id, 'consent_log': consent_log,
        'can_edit': perm.can_edit(request.user, 'Leads'), 'active_nav': 'Leads',
    })


@login_required
@perm.module_required('Leads', 'edit')
@require_POST
def consent_update(request, pk):
    """Update a client's per-channel consent (with source) or the do-not-contact flag (PRD §16.4)."""
    from .models import Client, ConsentRecord
    client = get_object_or_404(Client, pk=pk)
    if 'dnc' in request.POST:
        client.do_not_contact = request.POST.get('dnc') == 'on'
        client.save(update_fields=['do_not_contact'])
        _audit_event(request, 'Consent updated', f'{client.name}: do-not-contact = {client.do_not_contact}')
    else:
        source = request.POST.get('source', 'Manual')
        if source not in dict(ConsentRecord.SOURCE):
            source = 'Manual'
        channels = {ch: (request.POST.get('c_' + ch) == 'on')
                    for ch in ['Call', 'SMS', 'WhatsApp', 'Email']}
        base_lead = client.cases.filter(is_deleted=False).first()
        _record_consent(client, base_lead, channels, source, request.user)
        _audit_event(request, 'Consent updated', f'{client.name}: {source}')
    messages.success(request, 'Consent updated.')
    ref = request.POST.get('next', '')
    return redirect(ref) if ref else redirect('lead_list')


@login_required
@perm.module_required('Leads', 'delete')
def lead_delete(request, pk):
    lead = get_object_or_404(visible_leads(request.user), pk=pk)
    if request.method == 'POST':
        name = lead.name
        lead.is_deleted = True
        lead.deleted_at = timezone.now()
        lead.deleted_by = request.user
        lead.save(update_fields=['is_deleted', 'deleted_at', 'deleted_by'])
        _audit(lead, request.user, 'Lead deleted', 'Lead', name, 'Recycle Bin')
        messages.success(request, f'Lead "{name}" moved to Recycle Bin.')
        return redirect('lead_list')
    return redirect('lead_detail', pk=pk)


@login_required
@perm.module_required('Leads')
@require_POST
def lead_kyc(request, pk):
    lead = get_object_or_404(visible_leads(request.user), pk=pk)
    # PRD §16.1: KYC Passed/Rejected is the Compliance Officer's action, not the advisor's.
    if not perm.can_kyc(request.user):
        messages.error(request, 'Only a Compliance Officer can pass or reject KYC.')
        return redirect('lead_detail', pk=pk)
    action = request.POST.get('action', '')
    mp = {'pass': 'Passed', 'reject': 'Rejected', 'reset': 'Pending'}
    if action == 'pass':
        blockers = _kyc_blockers(lead)
        if blockers:
            messages.error(request, 'Cannot pass KYC — ' + '; '.join(blockers))
            return redirect('lead_detail', pk=pk)
    if action in mp:
        old = lead.kyc_status
        lead.kyc_status = mp[action]
        lead.save(update_fields=['kyc_status'])
        _audit(lead, request.user, 'KYC updated', 'KYC', old, lead.kyc_status)
        if lead.advisor:
            _notify(lead.advisor, f'KYC {lead.kyc_status} for "{lead.name}"',
                    f'/leads/{lead.pk}/', 'kyc', actor=request.user)
        messages.success(request, f'KYC marked {lead.kyc_status}.')
    return redirect('lead_detail', pk=pk)


@login_required
@require_POST
def lead_screening(request, pk):
    """Compliance records sanctions/PEP screening result + evidence (PRD §16.1–16.2)."""
    lead = get_object_or_404(visible_leads(request.user), pk=pk)
    if not perm.can_kyc(request.user):
        messages.error(request, 'Only Compliance can record screening.')
        return redirect('lead_detail', pk=pk)
    sanc = request.POST.get('sanctions_status', '')
    pep = request.POST.get('pep_status', '')
    if sanc in dict(Lead.SCREEN):
        lead.sanctions_status = sanc
    if pep in dict(Lead.SCREEN):
        lead.pep_status = pep
    if request.FILES.get('evidence'):
        lead.screening_evidence = request.FILES['evidence']
    lead.screened_by = request.user
    lead.screened_at = timezone.now()
    _compute_risk(lead)   # refresh risk from screening result
    lead.save()
    _audit(lead, request.user, 'Screening recorded', 'AML',
           '', f'Sanctions {lead.sanctions_status} · PEP {lead.pep_status} · Risk {lead.risk_rating}')
    messages.success(request, 'Screening recorded.')
    return redirect('lead_detail', pk=pk)


@login_required
@require_POST
def lead_risk(request, pk):
    """Compliance sets/overrides risk rating and marks EDD complete (PRD §16.2)."""
    lead = get_object_or_404(visible_leads(request.user), pk=pk)
    if not perm.can_kyc(request.user):
        messages.error(request, 'Only Compliance can set risk / EDD.')
        return redirect('lead_detail', pk=pk)
    rating = request.POST.get('risk_rating', '')
    if rating in dict(Lead.RISK):
        old = lead.risk_rating
        lead.risk_rating = rating
        lead.edd_required = (rating == 'High')
        note = request.POST.get('risk_note', '').strip()
        if note:
            lead.risk_note = note
        _audit(lead, request.user, 'Risk rating set', 'Risk', old, rating)
    lead.edd_source_of_funds = request.POST.get('edd_source_of_funds', lead.edd_source_of_funds).strip()
    lead.edd_source_of_wealth = request.POST.get('edd_source_of_wealth', lead.edd_source_of_wealth).strip()
    lead.edd_ceo_ack = bool(request.POST.get('edd_ceo_ack'))
    # EDD is only "complete" when SoF + SoW captured and CEO acknowledged (PRD §16.2)
    lead.edd_complete = bool(request.POST.get('edd_complete')) and bool(
        lead.edd_source_of_funds and lead.edd_source_of_wealth and lead.edd_ceo_ack)
    lead.save()
    messages.success(request, 'Risk / EDD updated.')
    return redirect('lead_detail', pk=pk)


@login_required
@require_POST
def lead_kyc_override(request, pk):
    """Compliance-only KYC override: reason-mandatory, time-boxed, with a follow-up task (PRD §16.1)."""
    lead = get_object_or_404(visible_leads(request.user), pk=pk)
    if not perm.can_kyc(request.user):
        messages.error(request, 'Only Compliance can override KYC.')
        return redirect('lead_detail', pk=pk)
    reason = request.POST.get('reason', '').strip()
    until = _parse_date(request.POST.get('until', ''))
    if not reason or not until:
        messages.error(request, 'Override requires a reason and a time-box (review-by date).')
        return redirect('lead_detail', pk=pk)
    lead.kyc_override = True
    lead.kyc_override_reason = reason
    lead.kyc_override_until = until
    lead.kyc_override_by = request.user
    lead.kyc_status = 'Passed'
    lead.save()
    _audit(lead, request.user, 'KYC override', 'KYC', 'Pending', f'Override until {until}: {reason[:60]}')
    _auto_task(lead, f'KYC override review — {lead.name}', 'Compliance',
               days=max(0, (until - timezone.localdate()).days), actor=request.user)
    for u in User.objects.filter(role__in=[Role.CEO, Role.COMPLIANCE]):
        _notify(u, f'KYC override in force for "{lead.name}" until {until}', f'/leads/{lead.pk}/', 'compliance')
    messages.success(request, 'KYC override applied (time-boxed, review task created).')
    return redirect('lead_detail', pk=pk)


@login_required
@require_POST
def suspicion_raise(request, pk):
    """Any user can raise a confidential suspicion flag (PRD §16.6)."""
    from .models import SuspicionFlag
    lead = get_object_or_404(visible_leads(request.user), pk=pk)
    reason = request.POST.get('reason', '').strip()
    if reason:
        SuspicionFlag.objects.create(lead=lead, client=lead.client, raised_by=request.user, reason=reason)
        _audit_event(request, 'Suspicion flag raised', f'case {lead.case_number or lead.pk}')
        for u in User.objects.filter(role=Role.COMPLIANCE):
            _notify(u, 'A confidential suspicion flag was raised', '/compliance/', 'compliance')
        messages.success(request, 'Confidential flag raised — Compliance notified. Do not tip off the client.')
    return redirect('lead_detail', pk=pk)


@login_required
def compliance_workspace(request):
    """Compliance officer's workspace (PRD §16): screening/KYC queue, risk/EDD, suspicion flags, DSRs."""
    from .models import SuspicionFlag, DataSubjectRequest, Client
    if request.user.role not in (Role.COMPLIANCE, Role.CEO, Role.SUPER_ADMIN):
        raise PermissionDenied("Compliance workspace is restricted.")
    leads = Lead.objects.filter(is_deleted=False, is_draft=False)
    kyc_queue = leads.filter(kyc_status='Pending').select_related('advisor')[:50]
    screening_pending = leads.filter(Q(sanctions_status='Pending') | Q(pep_status='Pending'))[:50]
    high_risk = leads.filter(risk_rating='High').select_related('advisor')[:50]
    flags = SuspicionFlag.objects.exclude(status='Resolved').select_related('lead', 'raised_by')
    dsrs = DataSubjectRequest.objects.exclude(status__in=['Completed', 'Rejected']).select_related('client')
    return render(request, 'crm/compliance.html', {
        'kyc_queue': kyc_queue, 'screening_pending': screening_pending, 'high_risk': high_risk,
        'flags': flags, 'dsrs': dsrs, 'clients': Client.objects.all()[:200],
        'active_nav': 'Compliance'})


@login_required
@require_POST
def suspicion_update(request, pk):
    from .models import SuspicionFlag
    if request.user.role not in (Role.COMPLIANCE, Role.CEO, Role.SUPER_ADMIN):
        raise PermissionDenied("Restricted.")
    f = get_object_or_404(SuspicionFlag, pk=pk)
    status = request.POST.get('status', '')
    if status in dict(SuspicionFlag.STATUS):
        f.status = status
        f.resolution = request.POST.get('resolution', f.resolution).strip()
        f.goaml_ref = request.POST.get('goaml_ref', f.goaml_ref).strip()
        if status == 'Resolved':
            f.resolved_at = timezone.now()
        f.save()
        _audit_event(request, 'Suspicion flag updated', f'#{f.pk} → {status}')
    return redirect('compliance_workspace')


@login_required
@require_POST
def dsr_create(request):
    from .models import DataSubjectRequest, Client
    if request.user.role not in (Role.COMPLIANCE, Role.CEO, Role.SUPER_ADMIN):
        raise PermissionDenied("Restricted.")
    name = request.POST.get('subject_name', '').strip()
    rtype = request.POST.get('request_type', 'Access')
    if name and rtype in dict(DataSubjectRequest.TYPE):
        cid = request.POST.get('client', '')
        DataSubjectRequest.objects.create(
            subject_name=name, request_type=rtype, detail=request.POST.get('detail', '').strip(),
            client=Client.objects.filter(pk=cid).first() if cid else None, raised_by=request.user)
        _audit_event(request, 'DSR created', f'{rtype} · {name}')
        messages.success(request, 'Data subject request logged.')
    return redirect('compliance_workspace')


@login_required
@require_POST
def dsr_update(request, pk):
    from .models import DataSubjectRequest
    if request.user.role not in (Role.COMPLIANCE, Role.CEO, Role.SUPER_ADMIN):
        raise PermissionDenied("Restricted.")
    d = get_object_or_404(DataSubjectRequest, pk=pk)
    status = request.POST.get('status', '')
    if status in dict(DataSubjectRequest.STATUS):
        d.status = status
        if status in ('Completed', 'Rejected'):
            d.completed_at = timezone.now()
        d.save()
        _audit_event(request, 'DSR updated', f'#{d.pk} → {status}')
    return redirect('compliance_workspace')


@login_required
@perm.module_required('Leads', 'edit')
@require_POST
def lead_stage_update(request, pk):
    lead = get_object_or_404(visible_leads(request.user), pk=pk)
    stage = request.POST.get('stage', '')
    # KYC hard gate: block moving to bank-submission stages until KYC Passed
    submit_idx = STAGES.index('Logged In')
    if stage in dict(Lead.STAGE_CHOICES) and STAGES.index(stage) >= submit_idx \
            and lead.kyc_status != 'Passed':
        messages.error(request, 'KYC must be Passed before submitting this lead to a bank.')
        return redirect('lead_detail', pk=pk)
    if stage in dict(Lead.STAGE_CHOICES):
        old = lead.stage
        lead.stage = stage
        if stage == 'Declined':
            lead.lost_reason = request.POST.get('lost_reason', '') or lead.lost_reason
        _apply_disbursed(lead, request.user)
        lead.save()
        if old != stage:
            _audit(lead, request.user, 'Stage changed', 'Stage', old, stage)
        if stage != 'Lead Received':
            _mark_contacted(lead)
        if lead.client_id:
            _sync_lifecycle(lead.client)   # disbursal → Active Client, etc.
        messages.success(request, f'Stage updated to "{stage}".')
    else:
        messages.error(request, 'Invalid stage.')
    return redirect('lead_detail', pk=pk)


@login_required
@perm.module_required('Leads', 'edit')
@require_POST
def lead_assign(request, pk):
    lead = get_object_or_404(visible_leads(request.user), pk=pk)
    adv_id = request.POST.get('advisor', '')
    old_adv = str(lead.advisor) if lead.advisor else '—'
    if adv_id:
        advisor = get_object_or_404(User, pk=adv_id, role=Role.ADVISOR)
        lead.advisor = advisor
        lead.save()
        _audit(lead, request.user, 'Advisor assigned', 'Advisor', old_adv, str(advisor))
        _notify(advisor, f'You were assigned lead "{lead.name}"', f'/leads/{lead.pk}/',
                'lead', actor=request.user)
        messages.success(request, f'Assigned to {advisor.get_full_name() or advisor.username}.')
    else:
        lead.advisor = None
        lead.save()
        _audit(lead, request.user, 'Advisor unassigned', 'Advisor', old_adv, '—')
        messages.success(request, 'Advisor unassigned.')
    nxt = request.POST.get('next')
    return redirect(nxt) if nxt else redirect('lead_detail', pk=pk)


@login_required
@perm.module_required('Leads', 'edit')
@require_POST
def lead_nurture(request, pk):
    """Move a lead to the nurture pool with a mandatory reactivation date, or clear it (PRD §9.6)."""
    lead = get_object_or_404(visible_leads(request.user), pk=pk)
    if request.POST.get('clear'):
        lead.nurture_until = None
        lead.save(update_fields=['nurture_until'])
        _audit(lead, request.user, 'Nurture cleared', 'Nurture', '', '')
        messages.success(request, 'Lead removed from nurture.')
        return redirect('lead_detail', pk=pk)
    until = _parse_date(request.POST.get('until', ''))
    if not until:
        messages.error(request, 'A reactivation date is required to nurture a lead.')
        return redirect('lead_detail', pk=pk)
    lead.nurture_until = until
    lead.save(update_fields=['nurture_until'])
    _audit(lead, request.user, 'Lead nurtured', 'Nurture', '', str(until))
    messages.success(request, f'Lead moved to nurture — reactivates on {until}.')
    return redirect('lead_detail', pk=pk)


@login_required
@perm.module_required('Leads', 'edit')
@require_POST
def lead_disbursed_date(request, pk):
    from datetime import datetime
    lead = get_object_or_404(visible_leads(request.user), pk=pk)
    raw = request.POST.get('date', '').strip()
    old = lead.disbursed_at.strftime('%d %b %Y') if lead.disbursed_at else '—'
    if raw:
        try:
            lead.disbursed_at = datetime.strptime(raw, '%Y-%m-%d').date()
        except ValueError:
            messages.error(request, 'Invalid date.')
            return redirect('lead_detail', pk=pk)
    else:
        lead.disbursed_at = None
    lead.save(update_fields=['disbursed_at'])
    new = lead.disbursed_at.strftime('%d %b %Y') if lead.disbursed_at else '—'
    _audit(lead, request.user, 'Field updated', 'Disbursed At', old, new)
    messages.success(request, 'Disbursement date updated.')
    return redirect('lead_detail', pk=pk)


def _num(v):
    try:
        return float(str(v).replace(',', '') or 0)
    except (TypeError, ValueError):
        return 0


@login_required
@perm.module_required('Leads', 'edit')
@require_POST
def bankapp_save(request, pk):
    """Create or update a bank application for a lead. Multiple allowed in parallel."""
    from django.utils import timezone as _tz
    lead = get_object_or_404(visible_leads(request.user), pk=pk)
    if lead.kyc_status != 'Passed':
        messages.error(request, 'KYC must be Passed before submitting to a bank.')
        return redirect('lead_detail', pk=pk)
    app_id = request.POST.get('app_id', '')
    bank_id = request.POST.get('bank', '')
    status = request.POST.get('status', 'Draft')
    if status not in dict(BankApplication.STATUS):
        status = 'Draft'
    app = (get_object_or_404(BankApplication, pk=app_id, lead=lead) if app_id
           else BankApplication(lead=lead, created_by=request.user))
    old_status = app.status if app_id else '—'
    app.bank = Bank.objects.filter(pk=bank_id).first() if bank_id else None
    app.status = status
    app.reference_no = request.POST.get('reference_no', '').strip()
    app.requested_amount = _num(request.POST.get('requested_amount')) or (lead.loan_amount or 0)
    app.sanctioned_amount = _num(request.POST.get('sanctioned_amount'))
    app.interest_rate = _num(request.POST.get('interest_rate'))
    app.rejection_reason = request.POST.get('rejection_reason', '').strip()
    app.notes = request.POST.get('notes', '').strip()
    if status == 'Submitted' and not app.submitted_at:
        app.submitted_at = _tz.now()
    if status in ('Approved', 'Rejected', 'Pre-Approved', 'Withdrawn') and not app.decision_at:
        app.decision_at = _tz.now()
    app.save()
    _audit(lead, request.user, 'Bank application ' + ('updated' if app_id else 'added'),
           str(app.bank or 'Bank'), old_status, status)
    if lead.advisor and lead.advisor != request.user:
        _notify(lead.advisor, f'Bank application {status} ({app.bank}) for "{lead.name}"',
                f'/leads/{lead.pk}/', 'lead', actor=request.user)
    messages.success(request, 'Bank application saved.')
    return redirect('lead_detail', pk=pk)


@login_required
@perm.module_required('Leads', 'edit')
@require_POST
def followup_add(request, pk):
    lead = get_object_or_404(visible_leads(request.user), pk=pk)
    channel = request.POST.get('channel', 'Call')
    if channel not in dict(FollowUp.CHANNEL):
        channel = 'Call'
    note = request.POST.get('note', '').strip()
    next_date = _parse_date(request.POST.get('next_date', ''))
    fu = FollowUp.objects.create(lead=lead, channel=channel, note=note,
                                 next_date=next_date, created_by=request.user)
    _mark_contacted(lead)  # a follow-up counts as first contact
    _audit(lead, request.user, 'Follow-up logged', channel, '', note[:80])
    if next_date:
        _auto_task(lead, f'Follow-up — {lead.name}', 'Follow-up',
                   days=max(0, (next_date - timezone.now().date()).days), actor=request.user)
    messages.success(request, 'Follow-up logged.')
    return redirect('lead_detail', pk=pk)


def _handover_blockers(lead):
    """PRD §11.1 handover gate — return the list of reasons a file cannot enter Operations."""
    blockers = []
    if lead.kyc_status != 'Passed':
        blockers.append('KYC not Passed (Compliance)')
    docs = lead.documents.filter(is_current=True, is_deleted=False)
    if not docs.exists():
        blockers.append('No documents uploaded')
    elif docs.filter(status__in=['Pending Review', 'Missing']).exists():
        blockers.append('Some documents still Pending/Missing')
    if not (lead.loan_amount and lead.property_value):
        blockers.append('Requirement fields incomplete (loan/property)')
    if lead.eligible is None:
        blockers.append('Eligibility not assessed')
    return blockers


def _handover_score(lead):
    docs = lead.documents.filter(is_current=True, is_deleted=False)
    total = docs.count() or 1
    verified = docs.filter(status='Verified').count()
    base = 60 if lead.kyc_status == 'Passed' else 0
    return min(100, base + int(verified / total * 40))


@login_required
@perm.module_required('Leads', 'edit')
@require_POST
def lead_handover(request, pk):
    """Advisor submits the file to Operations; the gate blocks incomplete files (PRD §11.1)."""
    lead = get_object_or_404(visible_leads(request.user), pk=pk)
    blockers = _handover_blockers(lead)
    if blockers:
        messages.error(request, 'Cannot submit to Operations — ' + '; '.join(blockers))
        return redirect('lead_detail', pk=pk)
    lead.handed_over = True
    lead.handover_at = timezone.now()
    lead.handover_score = _handover_score(lead)
    lead.save(update_fields=['handed_over', 'handover_at', 'handover_score'])
    _audit(lead, request.user, 'Submitted to Operations', 'Handover', '', f'score {lead.handover_score}%')
    for om in User.objects.filter(role=Role.OPS_MANAGER):
        _notify(om, f'New handover — "{lead.name}" awaiting an ops owner', f'/leads/{lead.pk}/',
                'ops', actor=request.user)
    messages.success(request, 'File submitted to Operations.')
    return redirect('lead_detail', pk=pk)


@login_required
@perm.module_required('Leads', 'edit')
@require_POST
def ops_assign(request, pk):
    """Ops Manager assigns the process owner (Operations Executive) — PRD §11.1 / §12.2."""
    lead = get_object_or_404(visible_leads(request.user), pk=pk)
    uid = request.POST.get('ops_owner', '')
    if uid:
        owner = get_object_or_404(User, pk=uid, role__in=[Role.OPS_EXECUTIVE, Role.OPS_MANAGER])
        lead.ops_owner = owner
        lead.save(update_fields=['ops_owner'])
        _audit(lead, request.user, 'Ops owner assigned', 'Ops Owner', '', str(owner))
        _notify(owner, f'You are the process owner for "{lead.name}"', f'/leads/{lead.pk}/',
                'ops', actor=request.user)
        messages.success(request, f'Ops owner set to {owner.get_full_name() or owner.username}.')
    else:
        lead.ops_owner = None
        lead.save(update_fields=['ops_owner'])
        messages.success(request, 'Ops owner cleared.')
    return redirect('lead_detail', pk=pk)


@login_required
@perm.module_required('Leads', 'edit')
@require_POST
def ops_hold(request, pk):
    """Put a case on hold / release it (PRD §12.1 Blocked-On-Hold queue)."""
    lead = get_object_or_404(visible_leads(request.user), pk=pk)
    if request.POST.get('release'):
        lead.ops_hold = False
        lead.hold_reason = ''
        lead.hold_review_date = None
        lead.save(update_fields=['ops_hold', 'hold_reason', 'hold_review_date'])
        _audit(lead, request.user, 'Case released from hold', 'Hold', 'On Hold', 'Active')
        messages.success(request, 'Case released from hold.')
    else:
        lead.ops_hold = True
        lead.hold_reason = request.POST.get('reason', '').strip()
        lead.hold_review_date = _parse_date(request.POST.get('review_date', ''))
        lead.save(update_fields=['ops_hold', 'hold_reason', 'hold_review_date'])
        _audit(lead, request.user, 'Case put on hold', 'Hold', '', lead.hold_reason[:80])
        messages.success(request, 'Case put on hold.')
    return redirect('lead_detail', pk=pk)


@login_required
@perm.module_required('Leads', 'edit')
@require_POST
def lead_processing(request, pk):
    """Capture structured processing data — pre-approval validity, valuation, FOL terms,
    insurance, title deed (PRD §11 steps 6–13)."""
    lead = get_object_or_404(visible_leads(request.user), pk=pk)
    g = request.POST.get
    lead.preapproval_validity_end = _parse_date(g('preapproval_validity_end', '')) or lead.preapproval_validity_end
    lead.valuer_name = g('valuer_name', lead.valuer_name).strip()
    lead.valuation_fee = _num(g('valuation_fee')) or lead.valuation_fee
    lead.valuation_date = _parse_date(g('valuation_date', '')) or lead.valuation_date
    lead.valuation_amount = _num(g('valuation_amount')) or lead.valuation_amount
    rt = g('fol_rate_type', '')
    if rt in dict(Lead.FOL_RATE):
        lead.fol_rate_type = rt
    lead.fol_fixed_rate = _num(g('fol_fixed_rate')) or lead.fol_fixed_rate
    lead.fol_fixed_period_end = _parse_date(g('fol_fixed_period_end', '')) or lead.fol_fixed_period_end
    lead.fol_variable_index = g('fol_variable_index', lead.fol_variable_index).strip()
    lead.fol_tenor_years = int(_num(g('fol_tenor_years')) or lead.fol_tenor_years)
    lead.fol_emi = _num(g('fol_emi')) or lead.fol_emi
    lead.fol_processing_fee = _num(g('fol_processing_fee')) or lead.fol_processing_fee
    lead.fol_offer_validity = _parse_date(g('fol_offer_validity', '')) or lead.fol_offer_validity
    lead.insurance_provider = g('insurance_provider', lead.insurance_provider).strip()
    lead.insurance_policy_no = g('insurance_policy_no', lead.insurance_policy_no).strip()
    lead.title_deed_number = g('title_deed_number', lead.title_deed_number).strip()
    # valuation shortfall check (PRD §11.7)
    if lead.valuation_amount and lead.loan_amount and lead.ltv:
        required = float(lead.loan_amount)
        if float(lead.valuation_amount) * (lead.ltv / 100) < required:
            _auto_task(lead, f'Valuation shortfall — {lead.name}', 'Valuation', days=1, actor=request.user)
    lead.save()
    _audit(lead, request.user, 'Processing details updated', 'Ops', '', 'FOL/valuation/pre-approval')
    messages.success(request, 'Processing details saved.')
    return redirect('lead_detail', pk=pk)


@login_required
@perm.module_required('Leads', 'edit')
@require_POST
def query_add(request, pk):
    """Log a bank query on a case (PRD §11.5)."""
    from .models import BankQuery
    lead = get_object_or_404(visible_leads(request.user), pk=pk)
    desc = request.POST.get('description', '').strip()
    if desc:
        BankQuery.objects.create(
            lead=lead, query_type=request.POST.get('query_type', '').strip(),
            description=desc, owner_side=request.POST.get('owner_side', 'Ops'),
            due_date=_parse_date(request.POST.get('due_date', '')), created_by=request.user)
        _audit(lead, request.user, 'Bank query raised', 'Query', '', desc[:80])
        messages.success(request, 'Query logged.')
    return redirect('lead_detail', pk=pk)


@login_required
@perm.module_required('Leads', 'edit')
@require_POST
def query_update(request, pk, qid):
    from .models import BankQuery
    lead = get_object_or_404(visible_leads(request.user), pk=pk)
    q = get_object_or_404(BankQuery, pk=qid, lead=lead)
    status = request.POST.get('status', '')
    if status in dict(BankQuery.STATUS):
        q.status = status
        q.save(update_fields=['status'])
        messages.success(request, 'Query updated.')
    return redirect('lead_detail', pk=pk)


@login_required
@perm.module_required('Leads')
def ops_queue(request):
    """Operations queues (PRD §12.1): named work queues + silence flags."""
    from datetime import date
    leads = list(visible_leads(request.user).filter(is_draft=False).exclude(
        stage__in=['Property Transferred', 'Declined']).select_related('advisor', 'bank', 'ops_owner')
        .prefetch_related('bank_applications', 'documents', 'followups', 'bank_queries'))
    today = date.today()
    FOL = ['FOL Initiated', 'FOL Issued', 'FOL Signing Fixed', 'FOL Signed']
    TRANSFER = ['Under Disbursement', 'Disbursed', 'Property Transfer Scheduled', 'Property Transfer']

    def _queue_of(l):
        if l.ops_hold:
            return 'blocked'
        if l.handed_over and not l.ops_owner_id:
            return 'new_handovers'
        if l.bank_queries.filter(status='Open').exists():
            return 'query_raised'
        if l.valuation_date and not l.valuation_amount:
            return 'valuations'
        if l.stage in TRANSFER:
            return 'transfer'
        if l.stage in FOL:
            return 'fol_signing'
        if l.bank_applications.filter(status__in=['Submitted', 'Under Review']).exists():
            return 'awaiting_bank'
        if l.ops_owner_id and l.documents.filter(is_current=True, is_deleted=False,
                                                 status__in=['Pending Review', 'Uploaded']).exists():
            return 'in_verification'
        return 'other'

    QUEUES = [('new_handovers', 'New Handovers'), ('in_verification', 'In Verification'),
              ('awaiting_bank', 'Awaiting Bank'), ('query_raised', 'Query Raised'),
              ('valuations', 'Valuations'), ('fol_signing', 'FOL & Signing'),
              ('transfer', 'Transfer'), ('blocked', 'Blocked / On Hold')]
    counts = {k: 0 for k, _ in QUEUES}
    for l in leads:
        qk = _queue_of(l)
        if qk in counts:
            counts[qk] += 1

    flt = request.GET.get('queue', '')
    rows, warn, esc = [], 0, 0
    for l in leads:
        qk = _queue_of(l)
        s = l.silence_status
        if s == 'warn':
            warn += 1
        elif s == 'escalate':
            esc += 1
        if flt and qk != flt:
            continue
        if not flt and l.stage in ('Disbursed',):   # keep default view to open work
            pass
        rows.append({
            'pk': l.pk, 'case': l.case_number or f'#{l.pk}', 'name': l.name, 'stage': l.stage,
            'advisor': (l.advisor.get_full_name() or l.advisor.username) if l.advisor else 'Unassigned',
            'ops': (l.ops_owner.get_full_name() or l.ops_owner.username) if l.ops_owner else '—',
            'queue': dict(QUEUES).get(qk, '—'), 'silence': s,
            'days': (timezone.now() - l.last_activity_at).days,
        })
    order = {'escalate': 0, 'warn': 1, 'active': 2, 'closed': 3}
    rows.sort(key=lambda r: (order.get(r['silence'], 9), -r['days']))
    return render(request, 'crm/ops_queue.html', {
        'rows': rows, 'warn': warn, 'esc': esc, 'flt': flt,
        'queues': [{'key': k, 'label': lbl, 'count': counts[k]} for k, lbl in QUEUES],
        'total': len(rows), 'active_nav': 'Ops'})


@login_required
@perm.module_required('Leads', 'edit')
@require_POST
def bankapp_delete(request, pk, app_id):
    lead = get_object_or_404(visible_leads(request.user), pk=pk)
    app = get_object_or_404(BankApplication, pk=app_id, lead=lead)
    name = str(app.bank or 'Bank')
    app.delete()
    _audit(lead, request.user, 'Bank application removed', name, '', '')
    messages.success(request, 'Bank application removed.')
    return redirect('lead_detail', pk=pk)


@login_required
@perm.module_required('Leads', 'edit')
@require_POST
def lead_bulk(request):
    action = request.POST.get('action', '')
    ids = request.POST.getlist('ids')
    qs = visible_leads(request.user).filter(pk__in=ids)
    n = qs.count()
    if action == 'delete':
        if not perm.can_delete(request.user, 'Leads'):
            messages.error(request, "Your role can't delete leads.")
            return redirect('lead_list')
        qs.update(is_deleted=True, deleted_at=timezone.now(), deleted_by=request.user)
        messages.success(request, f'{n} lead(s) deleted.')
    elif action == 'assign':
        adv_id = request.POST.get('advisor', '')
        advisor = User.objects.filter(pk=adv_id, role=Role.ADVISOR).first() if adv_id else None
        qs.update(advisor=advisor)
        messages.success(request, f'{n} lead(s) reassigned.')
    elif action == 'stage':
        stage = request.POST.get('stage', '')
        if stage in dict(Lead.STAGE_CHOICES):
            qs.update(stage=stage)
            if stage in DISBURSED_STAGES:
                qs.filter(disbursed_at__isnull=True).update(disbursed_at=timezone.localdate())
            messages.success(request, f'{n} lead(s) moved to "{stage}".')
    return redirect('lead_list')


@login_required
@perm.module_required('Leads')
def lead_export(request):
    q = request.GET.get('q', '').strip()
    stage = request.GET.get('stage', '')
    leads = visible_leads(request.user)
    if q:
        leads = leads.filter(Q(name__icontains=q) | Q(mobile__icontains=q) | Q(email__icontains=q))
    if stage:
        leads = leads.filter(stage=stage)
    leads = list(leads.order_by('-created_at'))
    resp = HttpResponse(content_type='text/csv')
    resp['Content-Disposition'] = 'attachment; filename="leads.csv"'
    w = csv.writer(resp)
    who = request.user.get_full_name() or request.user.username
    w.writerow([f'CONFIDENTIAL — exported by {who} on '
                f'{timezone.now().strftime("%d %b %Y %H:%M")} — do not distribute'])
    w.writerow(['ID', 'Name', 'Mobile', 'Email', 'Nationality', 'Property Value',
                'Loan Amount', 'Advisor', 'Bank', 'Source', 'Stage', 'Priority', 'Created'])
    for l in leads:
        w.writerow([l.pk, l.name, l.mobile, l.email, l.nationality, l.property_value,
                    l.loan_amount,
                    (l.advisor.get_full_name() or l.advisor.username) if l.advisor else '',
                    l.bank.name if l.bank else '', l.source, l.stage, l.priority,
                    l.created_at.strftime('%Y-%m-%d')])
    _audit_event(request, 'Data export', f'leads.csv · {len(leads)} row(s)')
    return resp


DISBURSED_STAGES = ['Disbursed', 'Property Transfer Scheduled', 'Property Transfer', 'Property Transferred']


@login_required
@perm.module_required('Leads')
def lead_pipeline(request):
    base = visible_leads(request.user)
    disbursed_stages = ['Disbursed', 'Property Transfer Scheduled', 'Property Transfer', 'Property Transferred']

    def _act(l):
        return l.updated_at.strftime('%d %b %Y')

    def _days(l):
        return max(0, (timezone.now() - l.updated_at).days)

    leads_js = [{
        'id': l.pk, 'name': l.name, 'mobile': l.mobile or '—',
        'nat': l.nationality or '—', 'propVal': _f(l.property_value), 'loan': _f(l.loan_amount),
        'advisor': (l.advisor.get_full_name() or l.advisor.username) if l.advisor else 'Unassigned',
        'bank': l.bank.name if l.bank else '—', 'source': l.source, 'stage': l.stage,
        'priority': l.priority, 'act': _act(l), 'created': l.created_at.strftime('%Y-%m-%d'),
        'days': _days(l), 'pipelineMonth': l.pipeline_month or None,
    } for l in base.order_by('-created_at')]

    advisors = [] if perm.is_own_scope(request.user, 'Leads') \
        else [u.get_full_name() or u.username for u in User.objects.filter(role=Role.ADVISOR)]
    banks = [b.name for b in Bank.objects.all()]

    active = base.exclude(stage__in=disbursed_stages + ['Declined'])
    fol_stages = ['FOL Initiated', 'FOL Issued', 'FOL Signing Fixed', 'FOL Signed']
    pipeline_val = _f(active.aggregate(v=Sum('loan_amount'))['v'])

    def _aed(v):
        return 'AED ' + (f'{v/1e6:.2f}M' if v >= 1e6 else f'{v/1e3:.0f}K')

    kpis_js = [
        {'l': 'Active Files', 'v': str(active.count()),
         'ic': '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>'},
        {'l': 'Under Review', 'v': str(base.filter(stage='Under Review').count()),
         'ic': '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/>'},
        {'l': 'Valuation Pending', 'v': str(base.filter(stage='Valuation').count()),
         'ic': '<path d="M3 21h18M5 21V8l7-5 7 5v13"/>'},
        {'l': 'FOL Pending', 'v': str(base.filter(stage__in=fol_stages).count()),
         'ic': '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M9 15l2 2 4-4"/>'},
        {'l': 'Disbursement Pending', 'v': str(base.filter(stage='Under Disbursement').count()),
         'ic': '<path d="M3 11.5 12 4l9 7.5"/><path d="M6 10.5V20h4.5v-5h3v5H18v-9.5"/>'},
        {'l': 'Declined Cases', 'v': str(base.filter(stage='Declined').count()),
         'ic': '<circle cx="12" cy="12" r="9"/><path d="m15 9-6 6M9 9l6 6"/>'},
        {'l': 'Pipeline Value', 'v': _aed(pipeline_val),
         'ic': '<rect x="2" y="6" width="20" height="12" rx="2"/><circle cx="12" cy="12" r="2.6"/>'},
    ]

    data = {'leads': leads_js, 'advisors': advisors, 'banks': banks,
            'sources': SOURCES, 'kpis': kpis_js}
    return render(request, 'crm/lead_pipeline.html', {
        'data': data, 'active_nav': 'Leads', 'active_sub': 'lead_pipeline',
    })


@login_required
@perm.module_required('Leads')
def lead_sources(request):
    base = visible_leads(request.user)
    total = base.count()

    SRC_META = {
        'Google Ads':      ('google', 'Paid'),
        'Meta Ads':        ('meta', 'Paid'),
        'Referral Partner':('ref', 'Partner'),
        'Website':         ('web', 'Organic'),
        'Walk-in':         ('walk', 'Direct'),
        'Cold Calling':    ('cold', 'Outbound'),
    }
    APPROVED_STAGES = ['Pre-Approved', 'Valuation', 'Valuation Received',
                       'FOL Initiated', 'FOL Issued', 'FOL Signing Fixed',
                       'FOL Signed', 'Under Disbursement'] + DISBURSED_STAGES

    src_states = {s.name: s.active for s in LeadSourceState.objects.all()}
    sources_js = []
    for src in SOURCES:
        qs = base.filter(source=src)
        cnt = qs.count()
        qualified = qs.exclude(stage__in=['Lead Received', 'Documents Pending', 'Declined']).count()
        applications = qs.filter(stage__in=APPROVED_STAGES + ['Logged In', 'Under Review']).count()
        approved = qs.filter(stage__in=APPROVED_STAGES).count()
        disbursed = qs.filter(stage__in=DISBURSED_STAGES).count()
        disb_val = _f(qs.filter(stage__in=DISBURSED_STAGES).aggregate(v=Sum('loan_amount'))['v'])
        loan_val = _f(qs.aggregate(v=Sum('loan_amount'))['v'])
        prop_val = _f(qs.aggregate(v=Sum('property_value'))['v'])
        key, stype = SRC_META.get(src, ('web', 'Organic'))
        sources_js.append({
            'key': key, 'name': src, 'type': stype,
            'leads': cnt, 'qualified': qualified, 'applications': applications,
            'approved': approved, 'disbursed': disbursed,
            'revenue': round(disb_val * 0.011),
            'active': '—',
            'status': 'active' if src_states.get(src, True) else 'inactive',
            'created': '—',
            'avgLoan': round(loan_val / cnt) if cnt else 0,
            'avgProp': round(prop_val / cnt) if cnt else 0,
        })
    sources_js.sort(key=lambda x: x['revenue'], reverse=True)

    advisors = [] if perm.is_own_scope(request.user, 'Leads') \
        else [u.get_full_name() or u.username for u in User.objects.filter(role=Role.ADVISOR)]
    banks = [b.name for b in Bank.objects.all()]

    partners_js = []
    for p in ReferralPartner.objects.all()[:5]:
        disb = _f(p.leads.filter(stage__in=DISBURSED_STAGES, is_deleted=False).aggregate(v=Sum('loan_amount'))['v'])
        partners_js.append({
            'n': p.name, 'leads': p.leads.filter(is_deleted=False).count(),
            'approved': p.leads.filter(stage__in=APPROVED_STAGES, is_deleted=False).count(),
            'disbursed': p.leads.filter(stage__in=DISBURSED_STAGES, is_deleted=False).count(),
            'revenue': round(disb * 0.011), 'comm': round(disb * 0.003),
        })

    trend_labels, trend_series = _monthly_trend(base)
    data = {
        'sources': sources_js,
        'advisors': advisors,
        'banks': banks,
        'partners': partners_js,
        'trend': {'labels': trend_labels, 'values': trend_series},
    }
    return render(request, 'crm/lead_sources.html', {
        'data': data, 'active_nav': 'Leads', 'active_sub': 'lead_sources',
    })


def _activity_feed(limit=8):
    """Recent activity derived from lead/task/document timestamps."""
    items = []
    for l in Lead.objects.filter(is_deleted=False).order_by('-created_at')[:limit]:
        items.append({'t': f'New lead "{l.name}" received', 'm': l.source,
                      'when': l.created_at})
    for t in Task.objects.filter(status='Completed', is_deleted=False).order_by('-created_at')[:limit]:
        items.append({'t': f'Task completed: {t.title}',
                      'm': (t.assignee.get_full_name() or t.assignee.username) if t.assignee else '',
                      'when': t.created_at})
    for d in Document.objects.filter(is_deleted=False).order_by('-created_at')[:limit]:
        items.append({'t': f'{d.doc_type} uploaded for {d.lead.name}', 'm': d.uploaded_by,
                      'when': d.created_at})
    items.sort(key=lambda x: x['when'], reverse=True)
    return [{'t': i['t'], 'm': i['m'], 'when': i['when'].strftime('%d %b · %I:%M %p')}
            for i in items[:limit]]


def _monthly_trend(qs, months=6, field='created_at'):
    """Return ([label,...], [count,...]) for the last N calendar months."""
    now = timezone.localdate()
    labels, values = [], []
    for i in range(months - 1, -1, -1):
        y, m = now.year, now.month - i
        while m <= 0:
            y, m = y - 1, m + 12
        labels.append(f'{["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"][m-1]}')
        values.append(qs.filter(**{f'{field}__year': y, f'{field}__month': m}).count())
    return labels, values


@login_required
@perm.module_required('Leads')
def lost_leads(request):
    leads = visible_leads(request.user).filter(stage='Declined').order_by('-updated_at')
    kpis = {
        'total': leads.count(),
        'value': leads.aggregate(s=Sum('loan_amount'))['s'] or 0,
    }

    base = visible_leads(request.user)

    rows = []
    for l in leads:
        loan = _f(l.loan_amount)
        rows.append({
            'id': l.pk,
            'name': l.name,
            'mobile': l.mobile or '—',
            'loan': loan,
            'propVal': _f(l.property_value),
            'advisor': (l.advisor.get_full_name() or l.advisor.username) if l.advisor else 'Unassigned',
            'bank': l.bank.name if l.bank else '—',
            'source': l.source,
            'stage': l.stage,
            'reason': l.lost_reason or 'Other',
            'lostDate': l.updated_at.strftime('%Y-%m-%d'),
            'daysAgo': max((timezone.now() - l.updated_at).days, 0),
            'revLost': round(loan * 0.011),
            'lost_reason': l.lost_reason or '—',
            'created': l.created_at.strftime('%Y-%m-%d'),
        })

    advisors = [] if perm.is_own_scope(request.user, 'Leads') \
        else [u.get_full_name() or u.username for u in User.objects.filter(role=Role.ADVISOR)]
    banks = [b.name for b in Bank.objects.all()]

    assigned = {a: 0 for a in advisors}
    for l in base.select_related('advisor'):
        if l.advisor:
            nm = l.advisor.get_full_name() or l.advisor.username
            assigned[nm] = assigned.get(nm, 0) + 1

    source_totals = {src: base.filter(source=src).count() for src in SOURCES}

    bank_rej = []
    for b in Bank.objects.all():
        apps = base.filter(bank=b).count()
        rej = base.filter(bank=b, stage='Declined').count()
        bank_rej.append({'b': b.name, 'apps': apps, 'rej': rej})

    total_val = _f(base.aggregate(s=Sum('loan_amount'))['s'] or 0)
    lost_val = _f(kpis['value'])
    leakage = f'{round(lost_val / total_val * 100, 1)}%' if total_val else '0%'

    data = {
        'rows': rows,
        'advisors': advisors,
        'banks': banks,
        'sources': list(SOURCES),
        'assigned': assigned,
        'sourceTotals': source_totals,
        'bankRej': bank_rej,
        'kpis': {'leakage': leakage},
    }
    tl, tv = _monthly_trend(visible_leads(request.user).filter(stage='Declined'), field='updated_at')
    data['trend'] = {'labels': tl, 'values': tv}
    return render(request, 'crm/lost_leads.html', {
        'data': data, 'leads': leads, 'kpis': kpis,
        'active_nav': 'Leads', 'active_sub': 'lost_leads',
    })


@login_required
@perm.module_required('Tasks')
def overdue_tasks(request):
    today = timezone.localdate()
    tasks_qs = visible_tasks(request.user).exclude(status='Completed').filter(
        due_date__lt=today).order_by('due_date')

    TYPE_COLORS = {
        'Documents': '#05448B', 'Bank Follow-up': '#2D6CB0', 'Valuation': '#BE185D',
        'Customer Call': '#0F766E', 'FOL': '#6D28D9', 'Disbursement': '#16A34A',
        'Application': '#B45309',
    }

    def esc_for(od):
        if od <= 3:
            return {'k': 'l1', 't': 'Level 1'}
        if od <= 7:
            return {'k': 'l2', 't': 'Level 2'}
        if od <= 14:
            return {'k': 'l3', 't': 'Level 3'}
        return {'k': 'crit', 't': 'Critical'}

    tasks_js = []
    for t in tasks_qs:
        od = (today - t.due_date).days if t.due_date else 0
        lead = t.lead
        assignee = t.assignee
        tasks_js.append({
            'id': f'TSK-{t.pk}',
            'title': t.title,
            'type': t.task_type,
            'leadName': lead.name if lead else '—',
            'leadId': lead.pk if lead else '',
            'assignee': (assignee.get_full_name() or assignee.username) if assignee else 'Unassigned',
            'source': lead.source if lead else '—',
            'bank': (lead.bank.name if lead and lead.bank else '—'),
            'priority': t.priority,
            'od': od,
            'dueStr': t.due_date.strftime('%d %b %Y') if t.due_date else '—',
            'last': 'No update',
            'esc': esc_for(od),
        })

    team = [u.get_full_name() or u.username
            for u in User.objects.filter(
                role__in=[Role.ADVISOR, Role.OPS_MANAGER, Role.SALES_DIRECTOR])]

    types = [{'name': name, 'col': TYPE_COLORS.get(name, '#05448B')}
             for name, _ in Task.TYPE]
    banks = [b.name for b in Bank.objects.all()]

    total = len(tasks_js)
    b13 = sum(1 for t in tasks_js if 1 <= t['od'] <= 3)
    b47 = sum(1 for t in tasks_js if 4 <= t['od'] <= 7)
    b7 = sum(1 for t in tasks_js if t['od'] > 7)
    crit = sum(1 for t in tasks_js if t['od'] > 14)
    leads = len({t['leadId'] for t in tasks_js if t['leadId'] != ''})

    kpis = [
        {'l': 'Total Overdue Tasks', 'v': total, 'cls': 'neg', 'ic': 'red',
         'svg': '<path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><path d="M12 9v4M12 17h.01"/>',
         'crit': 0, 'red': 0},
        {'l': '1–3 Days Overdue', 'v': b13, 'cls': 'mut', 'ic': 'amber',
         'svg': '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/>'},
        {'l': '4–7 Days Overdue', 'v': b47, 'cls': 'mut', 'ic': 'amber',
         'svg': '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/>'},
        {'l': '7+ Days Overdue', 'v': b7, 'cls': 'neg', 'ic': 'red',
         'svg': '<path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><path d="M12 9v4M12 17h.01"/>',
         'red': 1},
        {'l': 'Critical Tasks', 'v': crit, 'cls': 'neg', 'ic': 'red',
         'svg': '<circle cx="12" cy="12" r="9"/><path d="m15 9-6 6M9 9l6 6"/>',
         'crit': 1, 'red': 1},
        {'l': 'Affected Leads', 'v': leads, 'cls': 'mut', 'ic': 'amber',
         'svg': '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/>'},
    ]

    data = {
        'tasks': tasks_js, 'team': team, 'types': types,
        'sources': list(SOURCES), 'banks': banks, 'kpis': kpis,
    }
    return render(request, 'crm/overdue_tasks.html', {
        'data': data, 'today': today, 'active_nav': 'Tasks', 'active_sub': 'overdue_tasks',
    })


@login_required
@perm.module_required('Finance')
def finance(request):
    leads = Lead.objects.filter(is_deleted=False)
    disbursed = leads.filter(stage__in=DISBURSED_STAGES)
    revenue = float(disbursed.aggregate(s=Sum('loan_amount'))['s'] or 0)
    kpis = {
        'revenue': revenue,
        'commission': revenue * 0.006,
        'referral': revenue * 0.003,
        'vat': revenue * 0.006 * 0.05,
        'net': revenue * 0.006 - revenue * 0.003,
        'disbursed_loans': disbursed.count(),
    }
    return render(request, 'crm/finance.html', {'kpis': kpis, 'active_nav': 'Finance'})


# ---- report catalogue ----
REPORT_CATALOGUE = [
    ('leads-by-advisor', 'Leads by Advisor', 'Sales', 'Case load, disbursals & conversion per advisor.', 'Leads'),
    ('conversion-funnel', 'Conversion Funnel', 'Sales', 'How many leads reach each stage of the journey.', 'Leads'),
    ('source-performance', 'Source Performance', 'Sales', 'Lead volume and conversion by source channel.', 'Leads'),
    ('pipeline-by-stage', 'Pipeline by Stage', 'Operations', 'Open cases and value at each pipeline stage.', 'Leads'),
    ('sla-breaches', 'SLA / Silence Breaches', 'Operations', 'Open cases silent for 3+ / 7+ days.', 'Leads'),
    ('docs-pending', 'Documents Pending', 'Operations', 'Documents awaiting verification.', 'Documents'),
    ('kyc-status', 'KYC Status', 'Compliance', 'KYC breakdown and pending-review list.', 'Leads'),
    ('disbursals', 'Disbursals', 'Finance', 'All disbursed cases with loan amount & date.', 'Finance'),
    ('revenue-summary', 'Revenue Summary', 'Finance', 'Revenue, VAT and net profit per disbursed case.', 'Finance'),
]


def _build_report(key, user):
    """Return (columns, rows, drill_col_index_or_None). Rows scoped to the user."""
    leads = visible_leads(user)
    DISB = ['Disbursed', 'Property Transfer Scheduled', 'Property Transfer', 'Property Transferred']

    if key == 'leads-by-advisor':
        rows = []
        advs = User.objects.filter(role__in=[Role.ADVISOR, Role.TELECALLER])
        if perm.is_own_scope(user, 'Leads'):
            advs = advs.filter(pk=user.pk)
        elif perm.is_team_scope(user, 'Leads'):
            advs = advs.filter(pk__in=perm.team_member_ids(user))
        for a in advs:
            al = leads.filter(advisor=a)
            c = al.count()
            d = al.filter(stage__in=DISB).count()
            rows.append([f'/leads/?advisor={a.pk}', a.get_full_name() or a.username, c, d,
                         f'{round(d/c*100,1) if c else 0}%',
                         f"AED {_f(al.filter(stage__in=DISB).aggregate(v=Sum('loan_amount'))['v']):,.0f}"])
        return ['_url', 'Advisor', 'Cases', 'Disbursed', 'Conversion', 'Disbursed Value'], rows, None

    if key == 'conversion-funnel':
        stage_idx = {s: i for i, s in enumerate(STAGES)}
        live = [l for l in leads if l.stage != 'Declined']
        steps = [('Lead', 0), ('Contacted', 1), ('Docs Received', 2), ('Eligibility', 4),
                 ('Pre-Approval', 5), ('Final Approval', 9), ('Approved', 11), ('Disbursed', 13)]
        base = sum(1 for l in live if stage_idx.get(l.stage, -1) >= 0) or 1
        rows = [[s, sum(1 for l in live if stage_idx.get(l.stage, -1) >= t),
                 f'{round(sum(1 for l in live if stage_idx.get(l.stage,-1)>=t)/base*100)}%'] for s, t in steps]
        return ['Stage', 'Reached', '% of Leads'], rows, None

    if key == 'source-performance':
        rows = []
        for s in SOURCES:
            sl = leads.filter(source=s)
            c = sl.count()
            d = sl.filter(stage__in=DISB).count()
            rows.append([f'/leads/?source={s}', s, c, d, f'{round(d/c*100,1) if c else 0}%'])
        return ['_url', 'Source', 'Leads', 'Disbursed', 'Conversion'], rows, None

    if key == 'pipeline-by-stage':
        rows = []
        for s in STAGES:
            sl = leads.filter(stage=s)
            c = sl.count()
            if c:
                rows.append([f'/leads/?stage={s}', s, c, f"AED {_f(sl.aggregate(v=Sum('loan_amount'))['v']):,.0f}"])
        return ['_url', 'Stage', 'Cases', 'Value'], rows, None

    if key == 'sla-breaches':
        rows = []
        for l in leads.filter(is_draft=False).exclude(stage__in=DISB + ['Declined']):
            s = l.silence_status
            if s in ('warn', 'escalate'):
                rows.append([l.pk, l.case_number or f'#{l.pk}', l.name, l.stage,
                             'Escalated' if s == 'escalate' else 'Warning',
                             (timezone.now() - l.last_activity_at).days])
        rows.sort(key=lambda r: -r[5])
        return ['_pk', 'Case', 'Client', 'Stage', 'Silence', 'Idle (days)'], rows, 0

    if key == 'docs-pending':
        docs = Document.objects.filter(status='Pending', is_current=True, lead__in=leads, is_deleted=False).select_related('lead')
        rows = [[d.lead_id, d.name or d.doc_type, d.doc_type, d.lead.name,
                 d.created_at.strftime('%d %b %Y')] for d in docs]
        return ['_pk', 'Document', 'Type', 'Client', 'Uploaded'], rows, 0

    if key == 'kyc-status':
        rows = [[l.pk, l.case_number or f'#{l.pk}', l.name, l.kyc_status,
                 (l.advisor.get_full_name() or l.advisor.username) if l.advisor else '—']
                for l in leads]
        return ['_pk', 'Case', 'Client', 'KYC', 'Advisor'], rows, 0

    if key == 'disbursals':
        rows = [[l.pk, l.case_number or f'#{l.pk}', l.name, f'AED {_f(l.loan_amount):,.0f}',
                 l.bank.name if l.bank else '—',
                 l.disbursed_at.strftime('%d %b %Y') if l.disbursed_at else '—']
                for l in leads.filter(stage__in=DISB).order_by('-disbursed_at')]
        return ['_pk', 'Case', 'Client', 'Loan', 'Bank', 'Disbursed'], rows, 0

    if key == 'revenue-summary':
        rows = []
        for c in Customization.objects.select_related('lead'):
            if not visible_leads(user).filter(pk=c.lead_id).exists():
                continue
            rows.append([c.lead_id, c.lead.case_number or f'#{c.lead_id}', c.lead.name,
                         f'AED {c.actual_revenue:,.0f}', f'AED {c.vat:,.0f}',
                         f'AED {c.final_revenue:,.0f}'])
        return ['_pk', 'Case', 'Client', 'Revenue', 'VAT', 'Net Profit'], rows, 0

    return [], [], None


@login_required
@perm.module_required('Reports')
def reports(request):
    """Report catalogue — cards grouped by category, filtered by role access."""
    items = [{'key': k, 'title': t, 'cat': cat, 'desc': d}
             for (k, t, cat, d, mod) in REPORT_CATALOGUE if perm.can_access(request.user, mod)]
    cats = {}
    for it in items:
        cats.setdefault(it['cat'], []).append(it)
    leads = visible_leads(request.user)
    total = leads.count()
    disbursed = leads.filter(stage__in=DISBURSED_STAGES).count()
    kpis = {'total_leads': total, 'disbursed': disbursed,
            'conv': round(disbursed / total * 100) if total else 0,
            'reports': len(items)}
    return render(request, 'crm/reports.html', {
        'cats': cats, 'kpis': kpis, 'active_nav': 'Reports'})


@login_required
@perm.module_required('Reports')
def report_view(request, key):
    meta = next((r for r in REPORT_CATALOGUE if r[0] == key), None)
    if not meta or not perm.can_access(request.user, meta[4]):
        raise PermissionDenied("This report isn't available for your role.")
    cols, rows, drill = _build_report(key, request.user)
    has_pk = bool(cols) and cols[0] == '_pk'     # drill to a single lead detail
    has_url = bool(cols) and cols[0] == '_url'    # drill to a filtered lead list
    disp_cols = cols[1:] if (has_pk or has_url) else cols

    if request.GET.get('export'):
        out_rows = [(r[1:] if (has_pk or has_url) else r) for r in rows]
        return _csv(f'{key}.csv', disp_cols, out_rows, request=request)

    trows = []
    for r in rows:
        pk = r[0] if has_pk else None
        url = r[0] if has_url else None
        cells = r[1:] if (has_pk or has_url) else r
        trows.append({'pk': pk, 'url': url, 'cells': cells})
    return render(request, 'crm/report_detail.html', {
        'title': meta[1], 'desc': meta[3], 'cat': meta[2], 'key': key,
        'cols': disp_cols, 'rows': trows, 'drillable': has_pk or has_url,
        'active_nav': 'Reports'})


@login_required
@perm.module_required('Settings')
def settings_view(request):
    doc_types = ['Passport', 'Emirates ID', 'Salary Certificate', 'Bank Statements',
                 'Trade License', 'Property MOU', 'Title Deed', 'Liability Letter',
                 'Property Documents', 'Other Documents']
    saved = {s.key: s.value for s in AppSetting.objects.all()}
    data = {
        'stages': saved.get('stages') or [s for s in STAGES if s != 'Declined'],
        'sources': saved.get('sources') or list(SOURCES),
        'docTypes': saved.get('doc_types') or doc_types,
        'notifications': saved.get('notifications') or [],
        'company': saved.get('company') or {},
        'numbering': saved.get('numbering') or {},
    }
    from .models import business_config
    cal = business_config()
    cal_ctx = {'start': cal['start'], 'end': cal['end'],
               'days_csv': ','.join(str(d) for d in cal['days']),
               'holidays_csv': ', '.join(cal['holidays'])}
    web = {'url': request.build_absolute_uri('/api/web-to-lead/'), 'token': _web_token()}
    return render(request, 'crm/settings.html', {'data': data, 'rules': _rules(),
                                                 'cal': cal_ctx, 'web': web, 'active_nav': 'SettingsPage'})


@login_required
@perm.module_required('Leads', 'edit')
def lead_edit(request, pk):
    lead = get_object_or_404(visible_leads(request.user), pk=pk)
    orig_advisor_id = lead.advisor_id   # preserve — advisors don't see the Assign field
    # snapshot BEFORE the form binds/validates (is_valid() mutates the instance)
    before = _snapshot(lead)
    is_draft = bool(request.POST.get('draft'))
    form = LeadForm(request.POST or None, request.FILES or None, instance=lead)
    if is_draft:
        for f in form.fields.values():
            f.required = False
    if request.method == 'POST' and form.is_valid():
        lead = form.save(commit=False)
        # own-scope users (advisors) can't reassign — keep the existing owner instead of nulling it
        if perm.is_own_scope(request.user, 'Leads') and not lead.advisor_id:
            lead.advisor_id = orig_advisor_id or request.user.id
        lead.is_draft = is_draft   # normal save finalizes; Save Draft keeps it a draft
        _coerce_lead_numbers(lead)
        lead.score = lead.compute_score(); _compute_eligibility(lead)
        lead.save()
        if _apply_disbursed(lead, request.user):
            lead.save(update_fields=['disbursed_at'])
        lead.refresh_from_db()
        _audit_diff(lead, request.user, before)
        if not is_draft:
            _link_client(lead)   # keep the client link + lifecycle current
        uploader = request.user.get_full_name() or request.user.username
        _save_lead_documents(request, lead, uploader)
        messages.success(request, 'Draft saved.' if is_draft else 'Lead updated.')
        return redirect('lead_detail', pk=lead.pk)
    data = {
        'advisors': [{'pk': a.pk, 'name': a.get_full_name() or a.username}
                     for a in form.fields['advisor'].queryset],
        'banks': [{'pk': b.pk, 'name': b.name}
                  for b in form.fields['bank'].queryset],
        'sources': SOURCES,
        'partners': [{'pk': p.pk, 'name': p.name} for p in ReferralPartner.objects.filter(status='Active')],
        'init': {
            'nationality': lead.nationality or '',
            'advisor_name': (lead.advisor.get_full_name() or lead.advisor.username) if lead.advisor else '',
            'bank_name': lead.bank.name if lead.bank else '',
            'source': lead.source, 'priority': lead.priority,
            'employment_type': lead.employment_type or '',
            'industry': lead.industry or '',
            'property_type': lead.property_type or '',
            'preferred_area': lead.preferred_area or '',
        },
    }
    return render(request, 'crm/lead_form.html', {
        'form': form, 'title': 'Edit Lead', 'submit_label': 'Save Changes',
        'data': data, 'active_nav': 'Leads',
        'own_scope': perm.is_own_scope(request.user, 'Leads')})


# ---------- tasks ----------
@login_required
@perm.module_required('Tasks')
def task_list(request):
    from .forms import TaskForm
    from datetime import date
    tasks = visible_tasks(request.user)
    kpis = {
        'total': tasks.count(),
        'pending': tasks.filter(status='Pending').count(),
        'in_progress': tasks.filter(status='In Progress').count(),
        'completed': tasks.filter(status='Completed').count(),
        'overdue': tasks.filter(due_date__lt=date.today()).exclude(status__in=['Completed', 'Cancelled']).count(),
        'high': tasks.filter(priority='High').exclude(status='Completed').count(),
    }
    today = date.today()
    ordered = tasks.order_by('due_date')

    def _rem(t):
        return (t.due_date - today).days if t.due_date else 0

    rows = []
    for t in ordered:
        assignee = (t.assignee.get_full_name() or t.assignee.username) if t.assignee else 'Unassigned'
        rows.append({
            'id': t.pk,
            'title': t.title,
            'leadName': t.lead.name if t.lead else '—',
            'leadId': t.lead.pk if t.lead else '',
            'assignee': assignee,
            'creator': 'System',
            'type': t.task_type,
            'priority': t.priority,
            'status': t.status,
            'rem': _rem(t),
            'due': t.due_date.strftime('%Y-%m-%d') if t.due_date else '—',
            'created': t.created_at.strftime('%Y-%m-%d'),
        })

    team = [u.get_full_name() or u.username for u in User.objects.filter(role=Role.ADVISOR)]
    team += [u.get_full_name() or u.username
             for u in User.objects.exclude(role=Role.ADVISOR).exclude(role=Role.CEO)]
    seen = set()
    team = [n for n in team if not (n in seen or seen.add(n))]
    creators = ['System']
    statuses = [s[0] for s in Task.STATUS]

    flat = [0, 0, 0, 0, 0, 0, 0, 0]
    kpis_js = [
        {'l': 'Total Tasks', 'v': str(kpis['total']), 'd': '', 'cls': 'mut', 'ic': '',
         'svg': '<rect x="8" y="2" width="8" height="4" rx="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><path d="m9 14 2 2 4-4"/>', 's': flat},
        {'l': 'Pending Tasks', 'v': str(kpis['pending']), 'd': '', 'cls': 'mut', 'ic': 'amber',
         'svg': '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/>', 's': flat},
        {'l': 'In Progress', 'v': str(kpis['in_progress']), 'd': '', 'cls': 'mut', 'ic': '',
         'svg': '<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><path d="m9 11 3 3L22 4"/>', 's': flat},
        {'l': 'Overdue Tasks', 'v': str(kpis['overdue']), 'd': '', 'cls': 'neg', 'ic': 'red',
         'svg': '<path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><path d="M12 9v4M12 17h.01"/>', 's': flat},
        {'l': 'High Priority Tasks', 'v': str(kpis['high']), 'd': '', 'cls': 'mut', 'ic': 'red',
         'svg': '<path d="m13 2-3 7h7l-5 13 3-9H8z"/>', 's': flat},
        {'l': 'Completed', 'v': str(kpis['completed']), 'd': '', 'cls': 'mut', 'ic': 'green',
         'svg': '<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18M12 14v4M10 16h4"/>', 's': flat},
    ]

    data = {
        'rows': rows, 'team': team, 'creators': creators,
        'statuses': statuses, 'kpis': kpis_js,
    }
    return render(request, 'crm/task_list.html', {
        'data': data, 'tasks': ordered, 'kpis': kpis, 'form': TaskForm(),
        'can_create': perm.can_create(request.user, 'Tasks'),
        'today': today, 'active_nav': 'Tasks', 'active_sub': 'task_list',
    })


@login_required
@perm.module_required('Tasks', 'create')
def task_create(request):
    from .forms import TaskForm
    form = TaskForm(request.POST or None)
    if request.method == 'POST':
        if form.is_valid():
            task = form.save()
            if task.assignee:
                url = f'/leads/{task.lead.pk}/' if task.lead_id else '/tasks/'
                _notify(task.assignee, f'New task assigned: {task.title}', url, 'task', actor=request.user)
            messages.success(request, 'Task created.')
        else:
            messages.error(request, 'Task title is required.')
    return redirect('task_list')


@login_required
@perm.module_required('Tasks', 'edit')
def task_complete(request, pk):
    t = get_object_or_404(Task, pk=pk)
    t.status = 'Completed'
    t.outcome = (request.POST.get('outcome', '') or '').strip()
    t.completed_at = timezone.now()
    t.save()
    if t.lead_id:
        _mark_contacted(t.lead)   # completing a task = contact activity
    messages.success(request, f'Task "{t.title}" marked complete.')
    return redirect('task_list')


@login_required
@perm.module_required('Tasks', 'delete')
@require_POST
def task_delete(request, pk):
    t = get_object_or_404(Task, pk=pk, is_deleted=False)
    title = t.title
    t.is_deleted = True
    t.deleted_at = timezone.now()
    t.deleted_by = request.user
    t.save(update_fields=['is_deleted', 'deleted_at', 'deleted_by'])
    _audit_event(request, 'Task deleted', title)
    messages.success(request, f'Task "{title}" moved to recycle bin.')
    return redirect('task_list')


# ---------- banks ----------
@login_required
@perm.module_required('Banks')
def bank_list(request):
    banks = []
    total_leads = total_approved = total_disbursed = 0
    total_revenue = 0.0
    approved_stages = ['Pre-Approved', 'Valuation', 'Valuation Received',
                       'FOL Initiated', 'FOL Issued', 'FOL Signing Fixed',
                       'FOL Signed', 'Under Disbursement', 'Disbursed']
    for b in Bank.objects.all():
        bl = Lead.objects.filter(bank=b, is_deleted=False)
        submitted = bl.count()
        approved = bl.filter(stage__in=approved_stages).count()
        disbursed = bl.filter(stage='Disbursed').count()
        revenue = float(bl.filter(stage='Disbursed').aggregate(v=Sum('loan_amount'))['v'] or 0) * 0.011
        ratio = round(approved / submitted * 100) if submitted else 0
        banks.append({'obj': b, 'submitted': submitted, 'approved': approved,
                      'disbursed': disbursed, 'ratio': ratio, 'revenue': revenue})
        total_leads += submitted; total_approved += approved
        total_disbursed += disbursed; total_revenue += revenue
    banks.sort(key=lambda x: x['revenue'], reverse=True)
    kpis = {
        'total_banks': Bank.objects.count(),
        'active_banks': Bank.objects.exclude(status='Inactive').count(),
        'partner_banks': Bank.objects.filter(status='Partner').count(),
        'active_apps': total_leads,
        'approved': total_approved,
        'revenue': total_revenue,
    }
    can_edit = perm.can_edit(request.user, 'Banks')

    FLAT = [0, 0, 0, 0, 0, 0, 0, 0]
    banks_js = [{
        'name': r['obj'].name,
        'type': r['obj'].bank_type,
        'contact': r['obj'].contact_person or '—',
        'submitted': r['submitted'],
        'approved': r['approved'],
        'disbursed': r['disbursed'],
        'revenue': round(r['revenue'], 2),
        'status': r['obj'].status,
    } for r in banks]
    rev = kpis['revenue']
    rev_disp = 'AED ' + (f'{rev/1e6:.2f}M' if rev >= 1e6 else f'{rev/1e3:.0f}K')
    kpis_js = [
        {'l': 'Total Banks', 'v': str(kpis['total_banks']), 'd': '', 'cls': 'mut', 'ic': '',
         'svg': '<path d="M3 21h18M5 21V8l7-5 7 5v13M9 21v-6h6v6"/>', 's': FLAT},
        {'l': 'Active Banks', 'v': str(kpis['active_banks']), 'd': '', 'cls': 'mut', 'ic': 'green',
         'svg': '<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><path d="m9 11 3 3L22 4"/>', 's': FLAT},
        {'l': 'Partner Banks', 'v': str(kpis['partner_banks']), 'd': '', 'cls': 'mut', 'ic': '',
         'svg': '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/>', 's': FLAT},
        {'l': 'Active Applications', 'v': str(kpis['active_apps']), 'd': '', 'cls': 'mut', 'ic': '',
         'svg': '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/>', 's': FLAT},
        {'l': 'Approved Loans', 'v': str(kpis['approved']), 'd': '', 'cls': 'mut', 'ic': 'green',
         'svg': '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="m9 12 2 2 4-4"/>', 's': FLAT},
        {'l': 'Total Revenue', 'v': rev_disp, 'd': '', 'cls': 'mut', 'ic': 'green',
         'svg': '<rect x="2" y="6" width="20" height="12" rx="2"/><circle cx="12" cy="12" r="2.6"/>', 's': FLAT},
    ]
    data = {'banks': banks_js, 'kpis': kpis_js, 'feed': []}

    return render(request, 'crm/bank_list.html', {
        'banks': banks, 'kpis': kpis, 'top': banks[:3], 'can_edit': can_edit,
        'can_finance': perm.can_access(request.user, 'Finance'),  # hide commission % from advisors (§8.1)
        'form': BankForm(), 'data': data, 'active_nav': 'Banks', 'active_sub': 'bank_list',
    })


@login_required
@perm.module_required('Banks', 'access')
def bank_create(request):
    form = BankForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, f'Bank "{form.cleaned_data["name"]}" added.')
    else:
        messages.error(request, 'Bank name is required.')
    return redirect('bank_list')


@login_required
@perm.module_required('Banks', 'access')
def bank_edit(request, pk):
    bank = get_object_or_404(Bank, pk=pk)
    form = BankForm(request.POST or None, instance=bank)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, f'{bank.name} updated.')
    return redirect('bank_list')


@login_required
@perm.module_required('Banks', 'edit')
def bank_toggle(request, pk):
    bank = get_object_or_404(Bank, pk=pk)
    bank.status = 'Inactive' if bank.status != 'Inactive' else 'Active'
    bank.save()
    messages.success(request, f'{bank.name} {"deactivated" if bank.status == "Inactive" else "activated"}.')
    return redirect('bank_list')


@login_required
@perm.module_required('Banks', 'delete')
@require_POST
def bank_delete(request, pk):
    bank = get_object_or_404(Bank, pk=pk)
    name = bank.name
    bank.delete()
    messages.success(request, f'Bank "{name}" deleted.')
    return redirect('bank_list')


# ---------- advisors ----------
@login_required
@perm.module_required('Advisors')
def advisor_list(request):
    DISB = ['Disbursed', 'Property Transfer Scheduled', 'Property Transfer', 'Property Transferred']
    APPROVED_STAGES = ['Pre-Approved', 'Disbursed', 'FOL Signed', 'Under Disbursement']
    _nd = Q(leads__is_deleted=False)
    advisors = User.objects.filter(role=Role.ADVISOR).annotate(
        lead_count=Count('leads', filter=_nd),
        approved=Count('leads', filter=_nd & Q(leads__stage__in=APPROVED_STAGES)),
        disbursed=Count('leads', filter=_nd & Q(leads__stage='Disbursed')),
        active_leads=Count('leads', filter=_nd & ~Q(leads__stage__in=DISB + ['Declined'])))
    rows = []
    for a in advisors:
        rev = float(Lead.objects.filter(advisor=a, stage='Disbursed', is_deleted=False).aggregate(
            v=Sum('loan_amount'))['v'] or 0) * 0.011
        conv = round(a.approved / a.lead_count * 100) if a.lead_count else 0
        rows.append({'obj': a, 'leads': a.lead_count, 'approved': a.approved,
                     'disbursed': a.disbursed, 'conv': conv, 'revenue': rev,
                     'active': a.active_leads})
    rows.sort(key=lambda x: x['revenue'], reverse=True)
    kpis = {
        'total': advisors.count(),
        'active': advisors.filter(status='Active').count(),
        'assigned': sum(r['leads'] for r in rows),
        'approved': sum(r['approved'] for r in rows),
        'disbursed': sum(r['disbursed'] for r in rows),
        'revenue': sum(r['revenue'] for r in rows),
    }

    STATUS_MAP = {'Active': 'Active', 'On Leave': 'On Leave', 'Inactive': 'Inactive'}
    data_rows = [{
        'pk': r['obj'].pk,
        'name': r['obj'].get_full_name() or r['obj'].username,
        'role': r['obj'].role_label,
        'assigned': r['leads'],
        'active': r['active'],
        'approved': r['approved'],
        'disbursed': r['disbursed'],
        'revenue': round(r['revenue'], 2),
        'status': STATUS_MAP.get(getattr(r['obj'], 'status', 'Active') or 'Active', 'Active'),
    } for r in rows]

    roles = sorted({row['role'] for row in data_rows})

    flat = [0, 0, 0, 0, 0, 0, 0, 0]
    total_rev = kpis['revenue']
    rev_val = ('AED ' + (f'{total_rev/1e6:.2f}M' if total_rev >= 1e6
                         else f'{total_rev/1e3:.0f}K'))
    kpis_js = [
        {'l': 'Total Advisors', 'v': str(kpis['total']), 'd': '', 'cls': 'mut', 'ic': '',
         'svg': '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/>', 's': flat},
        {'l': 'Active Advisors', 'v': str(kpis['active']), 'd': '', 'cls': 'pos', 'ic': 'green',
         'svg': '<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><path d="m9 11 3 3L22 4"/>', 's': flat},
        {'l': 'Assigned Leads', 'v': str(kpis['assigned']), 'd': '', 'cls': 'pos', 'ic': '',
         'svg': '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/>', 's': flat},
        {'l': 'Approved Loans', 'v': str(kpis['approved']), 'd': '', 'cls': 'pos', 'ic': 'green',
         'svg': '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="m9 12 2 2 4-4"/>', 's': flat},
        {'l': 'Disbursed Loans', 'v': str(kpis['disbursed']), 'd': '', 'cls': 'pos', 'ic': 'green',
         'svg': '<path d="M3 11.5 12 4l9 7.5"/><path d="M6 10.5V20h4.5v-5h3v5H18v-9.5"/>', 's': flat},
        {'l': 'Revenue Generated', 'v': rev_val, 'd': '', 'cls': 'pos', 'ic': 'green',
         'svg': '<rect x="2" y="6" width="20" height="12" rx="2"/><circle cx="12" cy="12" r="2.6"/>', 's': flat},
    ]

    data = {'rows': data_rows, 'roles': roles, 'kpis': kpis_js, 'feed': []}

    return render(request, 'crm/advisor_list.html', {
        'advisors': rows, 'top': rows[:3], 'kpis': kpis, 'data': data,
        'active_nav': 'Advisors',
    })


# ---------- referral partners ----------
@login_required
@perm.module_required('Referral Partners')
def partner_list(request):
    partners = ReferralPartner.objects.order_by('-created_at')
    # CEO sees every partner; all other roles see only the ones they added.
    if request.user.role != Role.CEO:
        partners = partners.filter(created_by=request.user)
    kpis = {
        'total': partners.count(),
        'active': partners.filter(status='Active').count(),
        'on_hold': partners.filter(status='On Hold').count(),
        'inactive': partners.filter(status='Inactive').count(),
    }

    STC_STATUS = {'Active', 'On Hold', 'Inactive'}

    def _ini(n):
        return ''.join(w[0] for w in (n or '').replace('&amp;', '').split() if w)[:2].upper()

    partners_js = [{
        'name': p.name,
        'company': p.company or p.name,
        'org': p.organization or '',
        'type': p.partner_type,
        'contact': p.name,
        'phone': p.mobile or '—',
        'email': p.email or '—',
        'leads': p.leads.filter(is_deleted=False).count(),
        'approved': p.leads.filter(is_deleted=False).exclude(stage__in=['Lead Received', 'Documents Pending',
                                               'Documents Complete', 'Logged In',
                                               'Under Review', 'Declined']).count(),
        'disbursed': p.leads.filter(stage__in=DISBURSED_STAGES, is_deleted=False).count(),
        'revenue': round(_f(p.leads.filter(stage__in=DISBURSED_STAGES, is_deleted=False)
                            .aggregate(v=Sum('loan_amount'))['v']) * 0.011),
        'status': p.status if p.status in STC_STATUS else 'Active',
        'i': _ini(p.name),
        'created': p.created_at.strftime('%Y-%m-%d'),
        'organization': p.organization or '—',
        'emiratesId': p.emirates_id or '—',
        'passportNo': p.passport_no or '—',
        'bankName': p.bank_name or '—',
        'accountNo': p.account_no or '—',
        'iban': p.iban or '—',
        'agreementUrl': p.agreement.url if p.agreement else '',
        'kycUrl': p.kyc_doc.url if p.kyc_doc else '',
        'addedBy': (p.created_by.get_full_name() or p.created_by.username) if p.created_by else '—',
        'recentLeads': [{'name': l.name, 'stage': l.stage,
                         'date': l.created_at.strftime('%d %b %Y')}
                        for l in p.leads.order_by('-created_at')[:6]],
    } for p in partners]

    domain_types = ['Real Estate Agency', 'Property Consultant', 'Developer',
                    'Financial Consultant', 'Corporate Partner',
                    'Insurance Partner', 'Independent Agent']
    real_types = sorted({p.partner_type for p in partners if p.partner_type})
    pt_types = real_types or domain_types

    kpis_js = [
        {'l': 'Total Partners', 'v': str(kpis['total']), 'ic': '',
         'svg': '<path d="M18 8a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM6 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM18 22a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM8.6 13.5l6.8 4M15.4 6.5l-6.8 4"/>'},
        {'l': 'Active Partners', 'v': str(kpis['active']), 'ic': 'green',
         'svg': '<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><path d="m9 11 3 3L22 4"/>'},
        {'l': 'On Hold', 'v': str(kpis['on_hold']), 'ic': '',
         'svg': '<circle cx="12" cy="12" r="9"/><path d="M10 9v6M14 9v6"/>'},
        {'l': 'Inactive Partners', 'v': str(kpis['inactive']), 'ic': '',
         'svg': '<circle cx="12" cy="12" r="9"/><path d="m15 9-6 6M9 9l6 6"/>'},
        {'l': 'Referral Leads', 'v': '0', 'ic': '',
         'svg': '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/>'},
        {'l': 'Commission Payable', 'v': 'AED 0', 'ic': '',
         'svg': '<rect x="2" y="6" width="20" height="12" rx="2"/><circle cx="12" cy="12" r="2.6"/>'},
    ]

    data = {'partners': partners_js, 'kpis': kpis_js, 'pt_types': pt_types}
    return render(request, 'crm/partner_list.html', {
        'partners': partners, 'kpis': kpis, 'data': data,
        'can_create': perm.can_create(request.user, 'Referral Partners'),
        'active_nav': 'Referral Partners',
    })


@login_required
@perm.module_required('Referral Partners', 'access')
def partner_create(request):
    form = PartnerForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        partner = form.save(commit=False)
        partner.created_by = request.user
        # PRD §19.2: partners created by an advisor are pending Sales Director approval
        auto_ok = request.user.role in (Role.CEO, Role.SUPER_ADMIN, Role.SALES_DIRECTOR)
        partner.status = 'Active' if auto_ok else 'Pending'
        partner.save()
        if not auto_ok:
            _request_approval('Partner Activation', f'Activate partner "{partner.name}"',
                              request.user, Role.SALES_DIRECTOR,
                              detail=f'Created by {request.user.get_full_name() or request.user.username}',
                              link=f'/partners/{partner.pk}/edit/',
                              target_model='ReferralPartner', target_id=partner.pk)
            messages.success(request, 'Partner submitted for Sales Director approval.')
        else:
            messages.success(request, 'Referral partner created and activated.')
        return redirect('partner_list')
    return render(request, 'crm/partner_form.html', {'form': form, 'active_nav': 'Referral Partners'})


@login_required
def approvals_list(request):
    """Approval inbox — pending items this user's role can decide, plus their own requests (PRD §17.5)."""
    from .models import ApprovalRequest
    to_decide = ApprovalRequest.objects.filter(status='Pending', approver_role=request.user.role) \
        .select_related('requested_by')
    mine = ApprovalRequest.objects.filter(requested_by=request.user).select_related('decided_by')[:30]
    return render(request, 'crm/approvals.html', {
        'to_decide': to_decide, 'mine': mine, 'active_nav': 'Approvals'})


@login_required
@require_POST
def approval_decide(request, pk):
    from .models import ApprovalRequest, ReferralPartner
    ar = get_object_or_404(ApprovalRequest, pk=pk, status='Pending')
    # segregation of duties — the requester can never approve their own request (PRD §8.1/§17.5)
    if ar.requested_by_id == request.user.pk:
        messages.error(request, "You can't approve your own request.")
        return redirect('approvals_list')
    if request.user.role != ar.approver_role and request.user.role not in (Role.CEO, Role.SUPER_ADMIN):
        messages.error(request, 'Your role cannot decide this request.')
        return redirect('approvals_list')
    decision = request.POST.get('decision', '')
    if decision not in ('Approved', 'Rejected'):
        return redirect('approvals_list')
    ar.status = decision
    ar.decided_by = request.user
    ar.decided_at = timezone.now()
    ar.comment = request.POST.get('comment', '').strip()
    ar.save()
    # apply the effect
    if ar.target_model == 'ReferralPartner' and ar.target_id:
        p = ReferralPartner.objects.filter(pk=ar.target_id).first()
        if p:
            p.status = 'Active' if decision == 'Approved' else 'Inactive'
            p.save(update_fields=['status'])
    _audit_event(request, f'Approval {decision}', ar.title)
    if ar.requested_by:
        _notify(ar.requested_by, f'{ar.request_type} {decision}: {ar.title}', '/approvals/', 'approval')
    messages.success(request, f'Request {decision.lower()}.')
    return redirect('approvals_list')


@login_required
@perm.module_required('Referral Partners', 'access')
def partner_edit(request, pk):
    partners = ReferralPartner.objects.all()
    if request.user.role != Role.CEO:   # non-CEO can only edit partners they added
        partners = partners.filter(created_by=request.user)
    partner = get_object_or_404(partners, pk=pk)
    form = PartnerForm(request.POST or None, request.FILES or None, instance=partner)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, f'Referral partner "{partner.name}" updated.')
        return redirect('partner_list')
    return render(request, 'crm/partner_form.html', {
        'form': form, 'active_nav': 'Referral Partners', 'editing': True, 'partner': partner})


@login_required
@perm.module_required('Referral Partners', 'delete')
@require_POST
def partner_delete(request, pk):
    p = get_object_or_404(ReferralPartner, pk=pk)
    name = p.name
    p.delete()
    messages.success(request, f'Referral partner "{name}" deleted.')
    return redirect('partner_list')


# ---------- documents ----------
@login_required
@perm.module_required('Documents')
def document_list(request):
    docs = Document.objects.filter(is_current=True, is_deleted=False).select_related('lead', 'lead__advisor', 'verified_by')
    if perm.is_own_scope(request.user, 'Documents'):
        docs = docs.filter(lead__advisor=request.user)
    docs = docs.order_by('-created_at')
    kpis = {
        'total': docs.count(),
        'verified': docs.filter(status='Verified').count(),
        'pending': docs.filter(status='Pending Review').count(),
        'rejected': docs.filter(status='Rejected').count(),
        'missing': docs.filter(status='Missing').count(),
    }

    now = timezone.now()

    def _adv(d):
        a = d.lead.advisor
        return (a.get_full_name() or a.username) if a else '—'

    def _vby(d):
        v = d.verified_by
        return (v.get_full_name() or v.username) if v else '—'

    rows = []
    for d in docs:
        pending_days = max(0, (now - d.created_at).days)
        rows.append({
            'id': d.pk,
            'name': d.name or (d.doc_type or 'Document'),
            'type': d.doc_type or '—',
            'leadName': d.lead.name,
            'leadId': d.lead.pk,
            'uploader': d.uploaded_by or '—',
            'advisor': _adv(d),
            'upDate': None if d.status == 'Missing' else d.created_at.strftime('%Y-%m-%d'),
            'updated': None if d.status == 'Missing' else d.created_at.strftime('%Y-%m-%d'),
            'status': d.status,
            'verifiedBy': _vby(d) if d.status == 'Verified' else '—',
            'reason': '',
            'pendingDays': pending_days,
            'priority': d.lead.priority,
            'url': d.file.url if d.file else '',
            'expiry': d.expiry_date.strftime('%d %b %Y') if d.expiry_date else '',
            'expiryStatus': d.expiry_status,
            'version': d.version,
        })

    DOC_SVG = '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/>'
    kpis_js = [
        {'l': 'Total Documents', 'v': str(kpis['total']), 'cls': 'mut', 'ic': '', 'svg': DOC_SVG},
        {'l': 'Verified Documents', 'v': str(kpis['verified']), 'cls': 'pos', 'ic': 'green',
         'svg': '<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><path d="m9 11 3 3L22 4"/>'},
        {'l': 'Pending Review', 'v': str(kpis['pending']), 'cls': 'mut', 'ic': 'amber',
         'svg': '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/>'},
        {'l': 'Rejected Documents', 'v': str(kpis['rejected']), 'cls': 'neg', 'ic': 'red',
         'svg': '<circle cx="12" cy="12" r="9"/><path d="m15 9-6 6M9 9l6 6"/>'},
        {'l': 'Missing Documents', 'v': str(kpis['missing']), 'cls': 'neg', 'ic': 'red',
         'svg': '<path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><path d="M12 9v4M12 17h.01"/>'},
        {'l': "Today's Uploads", 'v': str(docs.filter(created_at__date=timezone.localdate()).count()),
         'cls': 'pos', 'ic': '',
         'svg': '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12"/>'},
    ]

    doc_types = sorted(set(d.doc_type for d in docs if d.doc_type))
    advisors = [u.get_full_name() or u.username for u in User.objects.filter(role=Role.ADVISOR)]
    statuses = [c[0] for c in Document.STATUS]
    uploaders = sorted(set(d.uploaded_by for d in docs if d.uploaded_by))
    leads = [{'id': l.pk, 'name': l.name,
              'advisor': (l.advisor.get_full_name() or l.advisor.username) if l.advisor else '—'}
             for l in visible_leads(request.user).order_by('name')]

    data = {
        'rows': rows, 'kpis': kpis_js,
        'doc_types': doc_types, 'advisors': advisors,
        'statuses': statuses, 'uploaders': uploaders, 'leads': leads,
    }
    return render(request, 'crm/document_list.html', {
        'data': data, 'documents': docs, 'kpis': kpis,
        'can_edit': perm.can_edit(request.user, 'Documents'), 'active_nav': 'Documents',
    })


@login_required
@perm.module_required('Documents', 'edit')
def document_action(request, pk, action):
    if request.method != 'POST':
        return redirect('document_list')
    doc = get_object_or_404(Document, pk=pk)
    mapping = {'verify': ('Verified', 'verified'), 'reject': ('Rejected', 'rejected'),
               'reupload': ('Missing', 're-upload requested for')}
    if action in mapping:
        doc.status = mapping[action][0]
        if action == 'verify':
            doc.verified_by = request.user
            doc.rejection_reason = ''
            # clear the case rework flag once no current docs remain rejected
            others = doc.lead.documents.filter(is_current=True, is_deleted=False, status='Rejected').exclude(pk=doc.pk)
            if doc.lead.rework_flag and not others.exists():
                doc.lead.rework_flag = False
                doc.lead.save(update_fields=['rework_flag'])
        if action == 'reject':
            doc.rejection_reason = request.POST.get('reason', '').strip()   # reason code (PRD §11.2)
            # flag the case for rework until cleared
            if not doc.lead.rework_flag:
                doc.lead.rework_flag = True
                doc.lead.save(update_fields=['rework_flag'])
        doc.save()
        if action in ('reject', 'reupload') and doc.lead.advisor:
            reason = f' ({doc.rejection_reason})' if (action == 'reject' and doc.rejection_reason) else ''
            _notify(doc.lead.advisor,
                    f'Document "{doc.doc_type}" {mapping[action][1]}{reason} — {doc.lead.name}',
                    f'/leads/{doc.lead.pk}/', 'document', actor=request.user)
            _auto_task(doc.lead, f'Re-upload {doc.doc_type} — {doc.lead.name}', 'Documents',
                       days=2, actor=request.user)
        messages.success(request, f'{doc.doc_type} {mapping[action][1]}.')
    return redirect(request.POST.get('next') or 'document_list')


@login_required
@perm.module_required('Documents', 'delete')
@require_POST
def document_delete(request, pk):
    doc = get_object_or_404(Document, pk=pk, is_deleted=False)
    name = doc.name or doc.doc_type
    doc.is_deleted = True
    doc.deleted_at = timezone.now()
    doc.deleted_by = request.user
    doc.save(update_fields=['is_deleted', 'deleted_at', 'deleted_by'])
    _audit_event(request, 'Document deleted', name)
    messages.success(request, f'Document "{name}" moved to recycle bin.')
    return redirect('document_list')


# ---------- users ----------
@login_required
@perm.module_required('Users')
def user_list(request):
    users = User.objects.all().order_by('role')
    kpis = {
        'total': users.count(),
        'active': users.filter(status='Active').count(),
        'inactive': users.exclude(status='Active').count(),
        'advisors': users.filter(role=Role.ADVISOR).count(),
        'admins': users.filter(role=Role.CEO).count(),
        'new_month': users.filter(date_joined__year=timezone.localdate().year,
                                  date_joined__month=timezone.localdate().month).count(),
    }

    rows = []
    for u in users:
        rows.append({
            'id': str(u.pk),
            'name': u.get_full_name() or u.username,
            'initials': u.initials,
            'email': u.email or '—',
            'phone': u.phone or '—',
            'role': u.role_label,
            'dept': u.department or '—',
            'status': u.status,
            'lastLogin': u.last_login.strftime('%d %b %Y, %H:%M') if u.last_login else '—',
            'created': u.date_joined.strftime('%Y-%m-%d'),
            'online': False,
            'leadsCount': Lead.objects.filter(advisor=u, is_deleted=False).count(),
            'openTasks': Task.objects.filter(assignee=u, is_deleted=False).exclude(
                status__in=['Completed', 'Cancelled']).count(),
            'completedTasks': Task.objects.filter(assignee=u, status='Completed', is_deleted=False).count(),
        })

    role_labels = list(dict.fromkeys(u.role_label for u in users)) or [r.label for r in Role]
    dept_labels = [d for d in dict.fromkeys(u.department for u in users) if d]

    def _kpi(l, v, d, cls, ic, svg):
        return {'l': l, 'v': str(v), 'd': d, 'cls': cls, 'ic': ic, 'svg': svg,
                's': [0, 0, 0, 0, 0, 0, 0, 0]}

    kpis_js = [
        _kpi('Total Users', kpis['total'], '', 'mut', '',
             '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/>'),
        _kpi('Active Users', kpis['active'], '', 'pos', 'green',
             '<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><path d="m9 11 3 3L22 4"/>'),
        _kpi('Inactive Users', kpis['inactive'], '', 'mut', 'amber',
             '<circle cx="12" cy="12" r="9"/><path d="M12 8v4M12 16h.01"/>'),
        _kpi('Advisors', kpis['advisors'], '', 'mut', '',
             '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>'),
        _kpi('Admins', kpis['admins'], '', 'mut', '',
             '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>'),
        _kpi('New This Month', kpis['new_month'], '', 'mut', '',
             '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/><path d="M19 8h4M21 6v4" stroke-width="2.2"/>'),
    ]

    data = {
        'rows': rows,
        'roles': role_labels,
        'depts': dept_labels,
        'kpis': kpis_js,
        'logins': [],
        'feed': [],
    }
    return render(request, 'crm/user_list.html', {
        'users': users, 'kpis': kpis, 'data': data,
        'can_create': perm.can_create(request.user, 'Users'),
        'active_nav': 'Users',
    })


@login_required
@perm.module_required('Users', 'create')
def user_create(request):
    form = UserForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        u = form.save()
        messages.success(request, f'User "{u}" created as {u.role_label}.')
        return redirect('user_list')
    return render(request, 'crm/user_form.html', {'form': form, 'title': 'Create User', 'active_nav': 'Users'})


@login_required
@perm.module_required('Users', 'edit')
def user_edit(request, pk):
    obj = get_object_or_404(User, pk=pk)
    form = UserForm(request.POST or None, instance=obj)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'User updated.')
        return redirect('user_list')
    return render(request, 'crm/user_form.html', {'form': form, 'title': 'Edit User', 'active_nav': 'Users'})


LEVEL_CHOICES = ['', 'No', 'View Only', 'View & Edit', 'View & Assign', 'Own Leads Only',
                 'Team Leads', 'Own Tasks', 'Team Tasks', 'Full', 'Limited', 'Yes']


@login_required
@perm.module_required('Settings')
def user_access(request, pk):
    """Per-user permission overrides + access delegation (field/module-level security)."""
    from .models import UserPermission, Delegation
    obj = get_object_or_404(User, pk=pk)
    if request.method == 'POST':
        action = request.POST.get('action', 'overrides')
        if action == 'overrides':
            for m in perm.MODULES:
                lvl = request.POST.get('mod_' + m, '').strip()
                if lvl:
                    UserPermission.objects.update_or_create(
                        user=obj, module=m, defaults={'level': lvl})
                else:
                    UserPermission.objects.filter(user=obj, module=m).delete()
            _audit_event(request, 'Access override', f'{obj.username} permissions updated')
            messages.success(request, f'Access overrides saved for {obj}.')
        elif action == 'delegate':
            dg = request.POST.get('delegate', '')
            starts = _parse_date(request.POST.get('starts', ''))
            ends = _parse_date(request.POST.get('ends', ''))
            if dg and starts and ends and ends >= starts:
                Delegation.objects.create(grantor=obj, delegate_id=dg, starts=starts,
                                          ends=ends, note=request.POST.get('note', '').strip())
                _audit_event(request, 'Delegation created', f'{obj.username} → user {dg}')
                messages.success(request, 'Delegation created.')
            else:
                messages.error(request, 'Pick a delegate and a valid date range.')
        elif action == 'deleg_toggle':
            d = Delegation.objects.filter(pk=request.POST.get('deleg_id'), grantor=obj).first()
            if d:
                d.active = not d.active
                d.save(update_fields=['active'])
                messages.success(request, 'Delegation updated.')
        return redirect('user_access', pk=pk)

    role_base = perm.effective_access(obj.role)
    overrides = perm.user_overrides(obj)
    mods = [{'name': m, 'role': role_base.get(m, 'No'),
             'override': overrides.get(m, '')} for m in perm.MODULES]
    delegations = obj.delegations_given.select_related('delegate')
    others = User.objects.exclude(pk=obj.pk).order_by('first_name', 'username')
    return render(request, 'crm/user_access.html', {
        'obj': obj, 'mods': mods, 'levels': LEVEL_CHOICES, 'delegations': delegations,
        'others': others, 'hidden_fields': ', '.join(perm.hidden_field_groups(obj)) or 'none',
        'active_nav': 'Users'})


@login_required
@perm.module_required('Users', 'delete')
@require_POST
def user_delete(request, pk):
    obj = get_object_or_404(User, pk=pk)
    if obj.pk == request.user.pk:
        messages.error(request, "You can't delete your own account.")
        return redirect('user_list')
    name = obj.get_full_name() or obj.username
    obj.delete()
    messages.success(request, f'User "{name}" deleted.')
    return redirect('user_list')


# ---------- CSV exports ----------
def _csv(filename, header, rows, request=None):
    rows = list(rows)
    resp = HttpResponse(content_type='text/csv')
    resp['Content-Disposition'] = f'attachment; filename="{filename}"'
    w = csv.writer(resp)
    # PRD §RP-07/§16.5 — watermark: confidential provenance line on every export
    if request is not None and request.user.is_authenticated:
        who = request.user.get_full_name() or request.user.username
        w.writerow([f'CONFIDENTIAL — exported by {who} on '
                    f'{timezone.now().strftime("%d %b %Y %H:%M")} — do not distribute'])
    w.writerow(header)
    for r in rows:
        w.writerow(r)
    # PRD §16.7 — every export of personal data is logged with a row count
    if request is not None:
        try:
            _audit_event(request, 'Data export', f'{filename} · {len(rows)} row(s)')
        except Exception:
            pass
    return resp


@login_required
@perm.module_required('Tasks')
def task_export(request):
    tasks = visible_tasks(request.user).order_by('due_date')
    return _csv('tasks.csv',
                ['ID', 'Title', 'Lead', 'Assignee', 'Type', 'Priority', 'Status', 'Due', 'Created'],
                [[t.pk, t.title, t.lead.name if t.lead else '',
                  (t.assignee.get_full_name() or t.assignee.username) if t.assignee else '',
                  t.task_type, t.priority, t.status,
                  t.due_date.strftime('%Y-%m-%d') if t.due_date else '',
                  t.created_at.strftime('%Y-%m-%d')] for t in tasks], request=request)


@login_required
@perm.module_required('Banks')
def bank_export(request):
    return _csv('banks.csv', ['Name', 'Type', 'Contact', 'Status'],
                [[b.name, b.bank_type, b.contact_person, b.status] for b in Bank.objects.all()], request=request)


@login_required
@perm.module_required('Documents')
def document_export(request):
    docs = Document.objects.filter(is_deleted=False).select_related('lead')
    if perm.is_own_scope(request.user, 'Documents'):
        docs = docs.filter(lead__advisor=request.user)
    return _csv('documents.csv', ['ID', 'Type', 'Lead', 'Status', 'Uploaded By', 'Created'],
                [[d.pk, d.doc_type, d.lead.name, d.status, d.uploaded_by,
                  d.created_at.strftime('%Y-%m-%d')] for d in docs.order_by('-created_at')], request=request)


@login_required
@perm.module_required('Advisors')
def advisor_export(request):
    rows = []
    for a in User.objects.filter(role=Role.ADVISOR):
        cnt = Lead.objects.filter(advisor=a, is_deleted=False).count()
        rows.append([a.get_full_name() or a.username, a.email, a.phone, a.status, cnt])
    return _csv('advisors.csv', ['Name', 'Email', 'Phone', 'Status', 'Assigned Leads'], rows, request=request)


@login_required
@perm.module_required('Referral Partners')
def partner_export(request):
    qs = ReferralPartner.objects.all()
    if request.user.role != Role.CEO:
        qs = qs.filter(created_by=request.user)
    return _csv('partners.csv', ['Name', 'Company', 'Type', 'Mobile', 'Email', 'Status'],
                [[p.name, p.company, p.partner_type, p.mobile, p.email, p.status]
                 for p in qs], request=request)


@login_required
@perm.module_required('Users')
def user_export(request):
    return _csv('users.csv', ['Name', 'Username', 'Email', 'Phone', 'Role', 'Department', 'Status'],
                [[u.get_full_name() or u.username, u.username, u.email, u.phone,
                  u.role_label, u.department, u.status] for u in User.objects.all()], request=request)


@login_required
@perm.module_required('Finance')
def finance_export(request):
    leads = Lead.objects.filter(stage__in=DISBURSED_STAGES, is_deleted=False).select_related('advisor', 'bank')
    return _csv('finance.csv', ['Lead', 'Loan Amount', 'Advisor', 'Bank', 'Stage'],
                [[l.name, l.loan_amount,
                  (l.advisor.get_full_name() or l.advisor.username) if l.advisor else '',
                  l.bank.name if l.bank else '', l.stage] for l in leads], request=request)


@login_required
@perm.module_required('Reports')
def report_export(request):
    rows = []
    for src in SOURCES:
        qs = Lead.objects.filter(source=src, is_deleted=False)
        rows.append([src, qs.count(), qs.filter(stage__in=DISBURSED_STAGES).count(),
                     qs.filter(stage='Declined').count()])
    return _csv('report.csv', ['Source', 'Total Leads', 'Disbursed', 'Declined'], rows, request=request)


@login_required
@require_POST
def settings_save(request):
    """Save the logged-in user's own editable profile fields."""
    u = request.user
    u.first_name = request.POST.get('first_name', u.first_name)
    u.last_name = request.POST.get('last_name', u.last_name)
    u.email = request.POST.get('email', u.email)
    u.phone = request.POST.get('phone', u.phone)
    u.save()
    messages.success(request, 'Settings saved.')
    return redirect('settings_view')


@login_required
@perm.module_required('Settings')
@require_POST
def settings_rules_save(request):
    """Save business rules (eligibility caps + first-contact SLA) — config over code, PRD §4.3."""
    keys = ['ltv_upto_5m', 'ltv_above_5m', 'dbr_cap', 'income_multiple',
            'cash_to_close_pct', 'sla_first_contact_mins']
    vals = {}
    for k in keys:
        raw = request.POST.get(k, '').strip()
        if raw != '':
            try:
                vals[k] = float(raw) if '.' in raw else int(raw)
            except ValueError:
                pass
    AppSetting.objects.update_or_create(key='rules', defaults={'value': vals})
    # business-hours calendar (PRD §17.4)
    cal = {}
    try:
        cal['start'] = int(request.POST.get('work_start', '9'))
        cal['end'] = int(request.POST.get('work_end', '18'))
    except ValueError:
        cal['start'], cal['end'] = 9, 18
    days_raw = request.POST.get('work_days', '0,1,2,3,4')
    cal['days'] = [int(x) for x in days_raw.replace(' ', '').split(',') if x.isdigit()]
    hol = request.POST.get('holidays', '')
    cal['holidays'] = [h.strip() for h in hol.replace('\n', ',').split(',') if h.strip()]
    AppSetting.objects.update_or_create(key='sla_calendar', defaults={'value': cal})
    _audit_event(request, 'Config changed', 'Business rules + SLA calendar updated')
    messages.success(request, 'Business rules & SLA calendar updated.')
    return redirect('settings_view')


# ---------- saved views (RP-04) ----------
@login_required
@perm.module_required('Leads')
@require_POST
def saved_view_create(request):
    from .models import SavedView
    name = request.POST.get('name', '').strip()
    qs = request.POST.get('querystring', '').lstrip('?')
    if name:
        SavedView.objects.create(user=request.user, module='Leads', name=name,
                                 querystring=qs, shared=bool(request.POST.get('shared')))
        messages.success(request, f'View "{name}" saved.')
    return redirect(f'/leads/?{qs}' if qs else 'lead_list')


@login_required
@perm.module_required('Leads')
@require_POST
def saved_view_delete(request, pk):
    from .models import SavedView
    SavedView.objects.filter(pk=pk, user=request.user).delete()
    messages.success(request, 'View deleted.')
    return redirect('lead_list')


# ---------- notification preferences (NA-04) ----------
@login_required
def notification_prefs(request):
    from .models import NotificationPref
    cats = [('lead', 'Lead updates'), ('task', 'Tasks & integrity'), ('document', 'Documents'),
            ('kyc', 'KYC'), ('ops', 'Operations'), ('sla', 'SLA (mandatory)'),
            ('compliance', 'Compliance (mandatory)'), ('approval', 'Approvals (mandatory)'),
            ('silence', 'Silence alerts')]
    if request.method == 'POST':
        for key, _ in cats:
            if key in NotificationPref.MANDATORY:
                continue
            muted = request.POST.get('mute_' + key) == 'on'
            NotificationPref.objects.update_or_create(user=request.user, category=key,
                                                      defaults={'muted': muted})
        messages.success(request, 'Notification preferences saved.')
        return redirect('notification_prefs')
    current = {p.category: p.muted for p in NotificationPref.objects.filter(user=request.user)}
    rows = [{'key': k, 'label': lbl, 'muted': current.get(k, False),
             'mandatory': k in NotificationPref.MANDATORY} for k, lbl in cats]
    return render(request, 'crm/notification_prefs.html', {'rows': rows, 'active_nav': 'Notifications'})


# ---------- assignment rules (LM-08) ----------
@login_required
@perm.module_required('Settings')
def assignment_rules(request):
    from .models import AssignmentRule
    if request.method == 'POST':
        act = request.POST.get('action_kind', 'add')
        if act == 'add':
            AssignmentRule.objects.create(
                order=int(_num(request.POST.get('order')) or 0),
                name=request.POST.get('name', 'Rule').strip() or 'Rule',
                match_source=request.POST.get('match_source', '').strip(),
                min_loan=_num(request.POST.get('min_loan')), max_loan=_num(request.POST.get('max_loan')),
                action=request.POST.get('action', 'round_robin'),
                action_user_id=request.POST.get('action_user') or None)
            _audit_event(request, 'Config changed', 'Assignment rule added')
        elif act == 'delete':
            AssignmentRule.objects.filter(pk=request.POST.get('rule_id')).delete()
        return redirect('assignment_rules')
    return render(request, 'crm/assignment_rules.html', {
        'rules': AssignmentRule.objects.select_related('action_user'),
        'advisors': User.objects.filter(role=Role.ADVISOR), 'sources': SOURCES,
        'active_nav': 'Settings'})


# ---------- roles ----------
@login_required
@perm.module_required('Settings')
def role_list(request):
    proto_modules = ['Dashboard', 'Leads', 'Tasks', 'Banks', 'Documents',
                     'Finance', 'Reports', 'Users', 'Settings']
    descriptions = {
        Role.CEO: 'Oversees the entire business, revenue, compliance, and team performance. Full system access.',
        Role.SALES_DIRECTOR: 'Manages the sales team, monitors lead generation, assigns leads, tracks targets, and approves important decisions.',
        Role.OPS_MANAGER: 'Manages loan processing, verifies documents, coordinates with banks, ensures files move through every stage until disbursement.',
        Role.ADVISOR: 'Handles clients, collects documents, submits applications, follows up with customers and banks.',
        Role.ACCOUNTANT: 'Verifies completed transactions, raises invoices, tracks commissions, manages payments.',
    }

    rows = []
    roles_js = []
    for role in Role:
        access = perm.effective_access(role)
        user_count = User.objects.filter(role=role).count()
        rows.append({
            'label': role.label,
            'users': user_count,
            'access': [(m, access.get(m, 'No')) for m in perm.MODULES],
        })
        roles_js.append({
            'name': role.label,
            'desc': descriptions.get(role, '—'),
            'users': user_count,
            'created': '—',
            'status': 'Active',
            'custom': False,
            'access': {m: access.get(m, 'No') for m in proto_modules},
        })

    total_users = User.objects.count()
    data = {
        'roles': roles_js,
        'modules': list(perm.MODULES),
        'role_keys': {r.label: r.value for r in Role},
    }
    return render(request, 'crm/role_list.html', {
        'roles': rows, 'modules': perm.MODULES, 'data': data,
        'total_users': total_users, 'active_nav': 'Settings',
    })


# ---------- QC additions: notes, uploads, restore, pipeline month, sources, roles, settings ----------
@login_required
@perm.module_required('Leads')
@require_POST
def lead_note_add(request, pk):
    lead = get_object_or_404(visible_leads(request.user), pk=pk)
    text = request.POST.get('text', '').strip()
    if text:
        Note.objects.create(lead=lead, author=request.user, text=text)
        _audit(lead, request.user, 'Note added', 'Note', '', text[:80])
        _mark_contacted(lead)
        messages.success(request, 'Note added.')
    return redirect('lead_detail', pk=pk)


@login_required
@perm.module_required('Documents', 'create')
@require_POST
def lead_document_upload(request, pk):
    lead = get_object_or_404(visible_leads(request.user), pk=pk)
    uploader = request.user.get_full_name() or request.user.username
    doc_type = request.POST.get('doc_type', '').strip() or 'Document'
    doc_name = request.POST.get('doc_name', '').strip()
    doc_exp = _parse_date(request.POST.get('doc_expiry'))
    n = 0
    # simple single-field upload (e.g. Title Deed): field name 'file'
    for f in request.FILES.getlist('file'):
        doc = Document.objects.create(lead=lead, name=doc_name, doc_type=doc_type, file=f,
                                      status='Pending Review', uploaded_by=uploader, expiry_date=doc_exp)
        _supersede_previous(doc)
        _audit(lead, request.user, 'Document uploaded', doc_name or doc_type)
        n += 1
    # dynamic rows: doc_name_<n> / doc_type_<n> / doc_file_<n>
    n += _save_lead_documents(request, lead, uploader)
    if n:
        messages.success(request, f'{n} document(s) uploaded.')
        new_stage = request.POST.get('stage', '')
        if new_stage in dict(Lead.STAGE_CHOICES) and perm.can_edit(request.user, 'Leads'):
            old = lead.stage
            lead.stage = new_stage
            _apply_disbursed(lead, request.user)
            lead.save()
            if old != new_stage:
                _audit(lead, request.user, 'Stage changed', 'Stage', old, new_stage)
    else:
        messages.error(request, 'No file selected.')
    return redirect('lead_detail', pk=pk)


@login_required
@perm.module_required('Leads', 'edit')
@require_POST
def lead_restore(request, pk):
    lead = get_object_or_404(visible_leads(request.user), pk=pk)
    old = lead.stage
    lead.stage = 'Lead Received'
    lead.lost_reason = ''
    lead.save()
    _audit(lead, request.user, 'Lead restored', 'Stage', old, 'Lead Received')
    messages.success(request, f'Lead "{lead.name}" restored to pipeline.')
    return redirect('lost_leads')


@login_required
@perm.module_required('Leads', 'edit')
@require_POST
def lead_pipeline_month(request, pk):
    lead = get_object_or_404(visible_leads(request.user), pk=pk)
    lead.pipeline_month = request.POST.get('month', '').strip()
    lead.save()
    return HttpResponse('ok')


@login_required
@perm.module_required('Leads', 'edit')
@require_POST
def source_toggle(request):
    name = request.POST.get('name', '').strip()
    if name:
        st, _ = LeadSourceState.objects.get_or_create(name=name)
        st.active = not st.active
        st.save()
        return HttpResponse('on' if st.active else 'off')
    return HttpResponse('err', status=400)


@login_required
@perm.module_required('Settings', 'edit')
@require_POST
def role_perm_save(request):
    role = request.POST.get('role', '')
    module = request.POST.get('module', '')
    lvl = request.POST.get('level', '')
    if role in dict(Role.choices) and module in perm.MODULES and lvl:
        rp, _ = RolePermission.objects.get_or_create(role=role, module=module,
                                                     defaults={'level': lvl})
        rp.level = lvl
        rp.save()
        _audit_event(request, 'Permission changed', f'{Role(role).label} · {module} = {lvl}')
        return HttpResponse('ok')
    return HttpResponse('err', status=400)


@login_required
@perm.module_required('Settings', 'edit')
@require_POST
def settings_state_save(request):
    import json
    key = request.POST.get('key', '')
    if key not in ('stages', 'sources', 'doc_types', 'notifications', 'company', 'numbering'):
        return HttpResponse('err', status=400)
    try:
        value = json.loads(request.POST.get('value', '[]'))
    except ValueError:
        return HttpResponse('err', status=400)
    s, _ = AppSetting.objects.get_or_create(key=key, defaults={'value': value})
    s.value = value
    s.save()
    _audit_event(request, 'Config changed', f'{key} updated')
    return HttpResponse('ok')


# ---------- Customization (CEO-only revenue sheet) ----------
def _ceo_required(view):
    from functools import wraps
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        from django.core.exceptions import PermissionDenied
        if not (request.user.is_authenticated and request.user.role == Role.CEO):
            raise PermissionDenied('CEO only.')
        return view(request, *args, **kwargs)
    return wrapper


def _cz_row(c):
    l = c.lead
    return {
        'id': c.pk, 'leadId': l.pk,
        'month': l.created_at.strftime('%b %Y'),
        'disbursedMonth': l.disbursed_at.strftime('%b %Y') if l.disbursed_at else '—',
        'disbursedMonthKey': l.disbursed_at.strftime('%Y-%m') if l.disbursed_at else '',
        'dateIso': (l.disbursed_at or l.created_at.date()).strftime('%Y-%m-%d'),
        'client': l.name,
        'loan': float(l.loan_amount or 0),
        'bank': l.bank.name if l.bank else '—',
        'rm': (l.advisor.get_full_name() or l.advisor.username) if l.advisor else '—',
        'slab': float(c.slab or 0),
        'brokerPct': float(c.broker_pct or 0),
        'brokerSlab': float(c.broker_slab or 0),
        'vatOverride': (float(c.vat_override) if c.vat_override is not None else None),
        'actualRevenue': c.actual_revenue,
        'vat': c.vat,
        'withVat': c.with_vat,
        'brokerRevenue': c.broker_revenue,
        'brokerPayout': c.broker_payout,
        'finalRevenue': c.final_revenue,
        'cp': c.cp,
        'status': l.stage,
    }


@login_required
@_ceo_required
def customization_list(request):
    rows = [_cz_row(c) for c in Customization.objects.select_related('lead', 'lead__advisor', 'lead__bank')]
    totals = {
        'count': len(rows),
        'actual': sum(r['actualRevenue'] for r in rows),
        'final': sum(r['finalRevenue'] for r in rows),
        'payout': sum(r['brokerPayout'] for r in rows),
    }
    # distinct disbursed months for the filter, newest first
    months = sorted({(r['disbursedMonthKey'], r['disbursedMonth'])
                     for r in rows if r['disbursedMonthKey']}, reverse=True)
    disbursed_months = [{'key': k, 'label': lbl} for k, lbl in months]
    return render(request, 'crm/customization.html', {
        'data': {'rows': rows, 'totals': totals, 'months': disbursed_months},
        'active_nav': 'Leads', 'active_sub': 'customization',
    })


@login_required
@_ceo_required
@require_POST
def customization_add(request, pk):
    lead = get_object_or_404(Lead, pk=pk)
    Customization.objects.get_or_create(lead=lead)
    messages.success(request, f'"{lead.name}" added to Customization.')
    nxt = request.POST.get('next')
    return redirect(nxt) if nxt else redirect('lead_list')


@login_required
@_ceo_required
@require_POST
def customization_update(request, pk):
    c = get_object_or_404(Customization, pk=pk)
    from decimal import Decimal, InvalidOperation
    for field, attr in (('slab', 'slab'), ('broker_pct', 'broker_pct'), ('broker_slab', 'broker_slab')):
        if field in request.POST:
            try:
                setattr(c, attr, Decimal(request.POST[field] or '0'))
            except (InvalidOperation, ValueError):
                return HttpResponse('bad number', status=400)
    if 'vat' in request.POST:
        raw = request.POST['vat'].strip()
        if raw == '':
            c.vat_override = None          # revert to auto 5%
        else:
            try:
                c.vat_override = Decimal(raw)
            except (InvalidOperation, ValueError):
                return HttpResponse('bad number', status=400)
    for field in ('cp',):
        if field in request.POST:
            setattr(c, field, request.POST[field].strip())
    c.save()
    import json
    return HttpResponse(json.dumps(_cz_row(c)), content_type='application/json')


@login_required
@_ceo_required
@require_POST
def customization_remove(request, pk):
    c = get_object_or_404(Customization, pk=pk)
    name = c.lead.name
    c.delete()
    messages.success(request, f'"{name}" removed from Customization.')
    return redirect('customization_list')


@login_required
@_ceo_required
def customization_export(request):
    resp = HttpResponse(content_type='text/csv')
    resp['Content-Disposition'] = 'attachment; filename="customization.csv"'
    w = csv.writer(resp)
    w.writerow(['Month', 'Client Name', 'Loan Amount', 'Bank Name',
                'RM Name', 'Slab', 'Actual Revenue', 'VAT', 'With VAT', 'Broker %', 'Broker Revenue',
                'Broker Slab', 'Broker Payout', 'Final Revenue', 'CP', 'Status'])
    for c in Customization.objects.select_related('lead', 'lead__advisor', 'lead__bank'):
        r = _cz_row(c)
        w.writerow([r['month'], r['client'], r['loan'], r['bank'],
                    r['rm'], r['slab'], round(r['actualRevenue'], 2), round(r['vat'], 2),
                    round(r['withVat'], 2), r['brokerPct'], round(r['brokerRevenue'], 2),
                    r['brokerSlab'], round(r['brokerPayout'], 2), round(r['finalRevenue'], 2),
                    r['cp'], r['status']])
    return resp


# ---------- global search ----------
@login_required
def global_search(request):
    """Role-scoped global search across the platform (powers the header ⌘K box)."""
    q = request.GET.get('q', '').strip()
    if len(q) < 2:
        return JsonResponse({'q': q, 'groups': []})
    u = request.user
    groups = []

    def add(cat, items):
        if items:
            groups.append({'cat': cat, 'items': items})

    # Leads (own-scope aware via visible_leads)
    if perm.can_access(u, 'Leads'):
        lqs = visible_leads(u).filter(
            Q(name__icontains=q) | Q(mobile__icontains=q) | Q(email__icontains=q)
        ).select_related('bank')[:6]
        add('Leads', [{
            'label': l.name,
            'sub': f'{l.stage} · {l.mobile or "—"}',
            'url': reverse('lead_detail', args=[l.pk]),
        } for l in lqs])

    # Banks
    if perm.can_access(u, 'Banks'):
        bqs = Bank.objects.filter(
            Q(name__icontains=q) | Q(contact_person__icontains=q)
        )[:6]
        add('Banks', [{
            'label': b.name, 'sub': b.bank_type, 'url': reverse('bank_list'),
        } for b in bqs])

    # Referral Partners (CEO sees all; others only their own)
    if perm.can_access(u, 'Referral Partners'):
        pqs = ReferralPartner.objects.all()
        if u.role != Role.CEO:
            pqs = pqs.filter(created_by=u)
        pqs = pqs.filter(
            Q(name__icontains=q) | Q(company__icontains=q) | Q(mobile__icontains=q)
        )[:6]
        add('Referral Partners', [{
            'label': p.name, 'sub': p.company or p.partner_type,
            'url': reverse('partner_list'),
        } for p in pqs])

    # Tasks (own-scope aware via visible_tasks)
    if perm.can_access(u, 'Tasks'):
        tqs = visible_tasks(u).filter(Q(title__icontains=q)).select_related('lead')[:6]
        add('Tasks', [{
            'label': t.title,
            'sub': f'{t.status}' + (f' · {t.lead.name}' if t.lead else ''),
            'url': reverse('task_list'),
        } for t in tqs])

    # Documents (own-scope: advisor sees only docs on their leads)
    if perm.can_access(u, 'Documents'):
        dqs = Document.objects.filter(is_deleted=False).select_related('lead')
        if perm.is_own_scope(u, 'Documents'):
            dqs = dqs.filter(lead__advisor=u)
        dqs = dqs.filter(Q(doc_type__icontains=q) | Q(lead__name__icontains=q))[:6]
        add('Documents', [{
            'label': d.doc_type, 'sub': d.lead.name if d.lead else '',
            'url': reverse('document_list'),
        } for d in dqs])

    # People — Users (full) or Advisors (limited)
    people_q = Q(first_name__icontains=q) | Q(last_name__icontains=q) | \
        Q(username__icontains=q) | Q(email__icontains=q)
    if perm.can_access(u, 'Users'):
        uqs = User.objects.filter(people_q)[:6]
        add('Users', [{
            'label': x.get_full_name() or x.username, 'sub': x.role_label,
            'url': reverse('user_list'),
        } for x in uqs])
    elif perm.can_access(u, 'Advisors'):
        aqs = User.objects.filter(role=Role.ADVISOR).filter(people_q)[:6]
        add('Advisors', [{
            'label': x.get_full_name() or x.username, 'sub': 'Advisor',
            'url': reverse('advisor_list'),
        } for x in aqs])

    return JsonResponse({'q': q, 'groups': groups})
