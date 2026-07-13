import csv
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.contrib.auth import logout as auth_logout
from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404
from django.db.models import Sum, Count, Q
from django.utils import timezone
from django.http import HttpResponse
from django.views.decorators.http import require_POST

from .models import (User, Lead, Bank, Task, ReferralPartner, Document, Role, STAGES, SOURCES,
                     Note, LeadSourceState, RolePermission, AppSetting, Customization, LeadAudit)
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
    """Apply 'Own Leads Only' scope for advisors."""
    qs = Lead.objects.select_related('advisor', 'bank')
    if perm.is_own_scope(user, 'Leads'):
        qs = qs.filter(advisor=user)
    return qs


def visible_tasks(user):
    qs = Task.objects.select_related('lead', 'assignee')
    if perm.is_own_scope(user, 'Tasks'):
        qs = qs.filter(assignee=user)
    return qs


# ---------- dashboard ----------
@login_required
def dashboard(request):
    u = request.user
    if u.role == Role.ADVISOR:
        return advisor_dashboard(request)
    return management_dashboard(request)


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
    leads = Lead.objects.all()
    DISB = ['Disbursed', 'Property Transferred']
    active = leads.exclude(stage__in=DISB + ['Declined'])
    submitted_stages = ['Logged In', 'Under Review', 'Pre-Approved', 'Valuation',
                        'Valuation Received', 'FOL Initiated', 'FOL Issued',
                        'FOL Signing Fixed', 'FOL Signed', 'Under Disbursement']
    disbursed_val = _f(leads.filter(stage__in=DISB).aggregate(v=Sum('loan_amount'))['v'])
    approval_val = _f(leads.filter(stage__in=['Pre-Approved'] + submitted_stages).aggregate(v=Sum('loan_amount'))['v'])
    pipeline_val = _f(active.aggregate(v=Sum('loan_amount'))['v'])
    revenue = disbursed_val * 0.011
    net_profit = revenue * 0.705
    n_total = leads.count()
    n_disbursed = leads.filter(stage__in=DISB).count()

    IC = ['users', 'plus', 'file', 'shield', 'home', 'file', 'cash', 'trend']
    kpi_defs = [
        ('Total Leads', n_total, '', '', 'all sources'),
        ('New Leads Today', leads.filter(created_at__date=date.today()).count(), '', '', 'since midnight'),
        ('Applications Submitted', leads.filter(stage__in=submitted_stages).count(), '', '', 'in progress'),
        ('Pre-Approval', leads.filter(stage='Pre-Approved').count(), '', '', 'awaiting final'),
        ('Loan Disbursed', n_disbursed, '', '', f'AED {disbursed_val:,.0f} value'),
        ('Pending Title Deed', leads.filter(stage__in=['Property Transfer Scheduled', 'Property Transfer']).count(), '', '', 'awaiting transfer'),
        ('Revenue This Month', round(revenue), '', 'AED ', 'commission est.'),
        ('Net Profit', round(net_profit), '', 'AED ', '70.5% margin'),
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

    # ---- finance summary ----
    finance = {
        'revenue': f'{revenue/1e6:.2f}M', 'vat': f'{revenue*0.05/1000:.1f}K',
        'adv_comm': f'{revenue*0.159/1000:.1f}K', 'ref_comm': f'{revenue*0.086/1000:.1f}K',
        'net': f'{net_profit/1e6:.2f}M', 'projected': f'{revenue*1.1/1e6:.2f}M',
    }
    profit_bars = [0] * 11 + [round(net_profit / 1e6, 2)]

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
    overdue = Task.objects.exclude(status='Completed').filter(due_date__lt=date.today()).count()
    if overdue:
        actions.append({'t': f'{overdue} overdue task(s)',
                        'p': 'Tasks past their due date.', 'due': 'Overdue', 'dc': 'var(--warning)'})

    hero = {
        'revenue': f'{revenue/1e6:.2f}M', 'approval': f'{approval_val/1e6:.1f}M',
        'disbursement': f'{disbursed_val/1e6:.1f}M',
    }

    dash = {
        'hero': hero, 'kpis': kpis_js, 'series_m': series_m, 'series_q': series_q,
        'funnel': funnel, 'approval_ratio': approval_ratio,
        'pipeline_value': f'{pipeline_val/1e6:.1f}M',
        'advisors': advisors_js, 'banks': banks_js, 'sources': sources_js,
        'partners': partners_js, 'finance': finance, 'profit': profit_bars,
        'actions': actions,
        'feed': _activity_feed(),
    }
    return render(request, 'crm/dashboard_mgmt.html', {
        'dash': dash, 'greet_name': request.user.first_name or request.user.username,
        'active_nav': 'Dashboard',
    })


def advisor_dashboard(request):
    u = request.user
    my = Lead.objects.filter(advisor=u)
    submissions = my.exclude(stage__in=['Lead Received', 'Documents Pending', 'Documents Complete', 'Declined']).count()
    disbursed_val = my.filter(stage__in=['Disbursed', 'Property Transferred']).aggregate(v=Sum('loan_amount'))['v'] or 0
    partners_added = ReferralPartner.objects.count()  # demo: company-wide
    calls_done = 1486  # demo metric (would come from call logs)

    def card(title, sub, achieved, target, unit=''):
        target = float(target or 0)
        achieved = float(achieved or 0)
        pct = round(min(100, achieved / target * 100)) if target else 0
        return {'title': title, 'sub': sub, 'achieved': achieved, 'target': target,
                'remaining': max(target - achieved, 0), 'pct': pct, 'unit': unit}

    targets = [
        card('Monthly Calling Target', '100 calls/day · 2,200/month', calls_done, u.target_calls or 2200),
        card('Submission Target', 'Mortgage files submitted this month', submissions, u.target_submissions or 24),
        card('Channel Partner Target', 'New partners onboarded', partners_added, u.target_partners or 10),
        card('Disbursement Target', 'Loan value disbursed', disbursed_val, u.target_disbursement or 2500000, 'AED'),
    ]
    overall = round(sum(t['pct'] for t in targets) / len(targets))
    tasks = Task.objects.filter(assignee=u).exclude(status__in=['Completed', 'Cancelled']).select_related('lead')[:6]

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

    data = {
        'greet': u.first_name or u.username,
        'targets': [dict(t) for t in targets],
        'overall': overall,
        'tasks': tasks_js,
        'calls': [0, 0, 0, 0, 0, 0],
        'feed': [],
    }
    return render(request, 'crm/dashboard_advisor.html', {
        'targets': targets, 'overall': overall, 'tasks': tasks,
        'data': data, 'greet_name': u.first_name or u.username,
        'active_nav': 'Dashboard',
    })


# ---------- leads ----------
@login_required
@perm.module_required('Leads')
def lead_list(request):
    q = request.GET.get('q', '').strip()
    stage = request.GET.get('stage', '')
    base = visible_leads(request.user)
    disbursed_stages = ['Disbursed', 'Property Transferred']
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
    leads = leads.order_by('-created_at')

    def _act(l):
        return l.updated_at.strftime('%d %b %Y')
    leads_js = [{
        'id': l.pk, 'name': l.name, 'mobile': l.mobile or '—', 'email': l.email or '—',
        'nat': l.nationality or '—', 'propVal': _f(l.property_value), 'loan': _f(l.loan_amount),
        'advisor': (l.advisor.get_full_name() or l.advisor.username) if l.advisor else 'Unassigned',
        'bank': l.bank.name if l.bank else '—', 'source': l.source, 'stage': l.stage,
        'priority': l.priority, 'act': _act(l), 'created': l.created_at.strftime('%Y-%m-%d'),
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
        {'l': 'This Month Revenue', 'v': 'AED ' + f'{_f(base.filter(stage__in=disbursed_stages).aggregate(v=Sum("loan_amount"))["v"])*0.011/1e6:.2f}M',
         'ic': '<rect x="2" y="6" width="20" height="12" rx="2"/><circle cx="12" cy="12" r="2.6"/>'},
    ]
    customized_ids = list(Customization.objects.values_list('lead_id', flat=True)) \
        if request.user.role == Role.CEO else []
    data = {'leads': leads_js, 'advisors': advisors, 'banks': banks, 'sources': SOURCES,
            'me': me, 'kpis': kpis_js, 'customizedIds': customized_ids, 'ownScope': own_scope}
    return render(request, 'crm/lead_list.html', {
        'data': data, 'q': q, 'stage': stage, 'kpis': kpis, 'own_scope': own_scope,
        'stages': [s[0] for s in Lead.STAGE_CHOICES],
        'can_create': perm.can_create(request.user, 'Leads'),
        'can_delete': perm.can_delete(request.user, 'Leads'),
        'active_nav': 'Leads', 'active_sub': 'lead_list',
    })


@login_required
@perm.module_required('Leads', 'create')
def lead_create(request):
    form = LeadForm(request.POST or None)
    if request.user.role == Role.ADVISOR:
        form.fields['advisor'].initial = request.user
    if request.method == 'POST' and form.is_valid():
        lead = form.save(commit=False)
        if request.user.role == Role.ADVISOR:
            lead.advisor = request.user
        lead.save()
        uploader = request.user.get_full_name() or request.user.username
        _audit(lead, request.user, 'Lead created', 'Lead', '', lead.name)
        for key, f in request.FILES.items():
            if key.startswith('doc::'):
                Document.objects.create(lead=lead, doc_type=key[5:], file=f,
                                        status='Pending Review', uploaded_by=uploader)
                _audit(lead, request.user, 'Document uploaded', key[5:])
        messages.success(request, f'Lead "{lead.name}" created.')
        return redirect('lead_detail', pk=lead.pk)
    data = {
        'advisors': [{'pk': a.pk, 'name': a.get_full_name() or a.username}
                     for a in form.fields['advisor'].queryset],
        'banks': [{'pk': b.pk, 'name': b.name}
                  for b in form.fields['bank'].queryset],
        'sources': SOURCES,
        'partners': [{'pk': p.pk, 'name': p.name} for p in ReferralPartner.objects.filter(status='Active')],
        'init': {},
    }
    return render(request, 'crm/lead_form.html', {
        'form': form, 'title': 'Create Lead', 'submit_label': 'Create Lead',
        'data': data, 'active_nav': 'Leads'})


@login_required
@perm.module_required('Leads')
def lead_detail(request, pk):
    lead = get_object_or_404(visible_leads(request.user), pk=pk)
    documents = lead.documents.all()
    tasks = lead.tasks.all()

    advisor_name = (lead.advisor.get_full_name() or lead.advisor.username) if lead.advisor else 'Unassigned'
    bank_name = lead.bank.name if lead.bank else ''

    lead_js = {
        'id': lead.pk, 'name': lead.name, 'mobile': lead.mobile or '—',
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
    }

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
            't': d.doc_type,
            'm': f'{d.uploaded_by} · {d.created_at.strftime("%d %b %Y")}',
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

    data = {
        'lead': lead_js, 'stageOrder': STAGES,
        'documents': documents_js, 'tasks': tasks_js, 'notes': notes_js,
        'audits': audits_js,
    }
    # own-scope users can't reassign, so don't expose other advisors' names
    advisors = User.objects.none() if perm.is_own_scope(request.user, 'Leads') \
        else User.objects.filter(role=Role.ADVISOR)
    return render(request, 'crm/lead_detail.html', {
        'lead': lead, 'documents': documents, 'tasks': tasks, 'data': data,
        'advisors': advisors,
        'can_edit': perm.can_edit(request.user, 'Leads'),
        'can_delete': perm.can_delete(request.user, 'Leads'), 'active_nav': 'Leads',
    })


@login_required
@perm.module_required('Leads', 'delete')
def lead_delete(request, pk):
    lead = get_object_or_404(visible_leads(request.user), pk=pk)
    if request.method == 'POST':
        name = lead.name
        lead.delete()
        messages.success(request, f'Lead "{name}" deleted.')
        return redirect('lead_list')
    return redirect('lead_detail', pk=pk)


@login_required
@perm.module_required('Leads', 'edit')
@require_POST
def lead_stage_update(request, pk):
    lead = get_object_or_404(visible_leads(request.user), pk=pk)
    stage = request.POST.get('stage', '')
    if stage in dict(Lead.STAGE_CHOICES):
        old = lead.stage
        lead.stage = stage
        if stage == 'Declined':
            lead.lost_reason = request.POST.get('lost_reason', '') or lead.lost_reason
        _apply_disbursed(lead, request.user)
        lead.save()
        if old != stage:
            _audit(lead, request.user, 'Stage changed', 'Stage', old, stage)
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
        qs.delete()
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
    leads = leads.order_by('-created_at')
    resp = HttpResponse(content_type='text/csv')
    resp['Content-Disposition'] = 'attachment; filename="leads.csv"'
    w = csv.writer(resp)
    w.writerow(['ID', 'Name', 'Mobile', 'Email', 'Nationality', 'Property Value',
                'Loan Amount', 'Advisor', 'Bank', 'Source', 'Stage', 'Priority', 'Created'])
    for l in leads:
        w.writerow([l.pk, l.name, l.mobile, l.email, l.nationality, l.property_value,
                    l.loan_amount,
                    (l.advisor.get_full_name() or l.advisor.username) if l.advisor else '',
                    l.bank.name if l.bank else '', l.source, l.stage, l.priority,
                    l.created_at.strftime('%Y-%m-%d')])
    return resp


DISBURSED_STAGES = ['Disbursed', 'Property Transferred']


@login_required
@perm.module_required('Leads')
def lead_pipeline(request):
    base = visible_leads(request.user)
    disbursed_stages = ['Disbursed', 'Property Transferred']

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
        disb = _f(p.leads.filter(stage__in=DISBURSED_STAGES).aggregate(v=Sum('loan_amount'))['v'])
        partners_js.append({
            'n': p.name, 'leads': p.leads.count(),
            'approved': p.leads.filter(stage__in=APPROVED_STAGES).count(),
            'disbursed': p.leads.filter(stage__in=DISBURSED_STAGES).count(),
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
    for l in Lead.objects.order_by('-created_at')[:limit]:
        items.append({'t': f'New lead "{l.name}" received', 'm': l.source,
                      'when': l.created_at})
    for t in Task.objects.filter(status='Completed').order_by('-created_at')[:limit]:
        items.append({'t': f'Task completed: {t.title}',
                      'm': (t.assignee.get_full_name() or t.assignee.username) if t.assignee else '',
                      'when': t.created_at})
    for d in Document.objects.order_by('-created_at')[:limit]:
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
    leads = Lead.objects.all()
    disbursed = leads.filter(stage__in=DISBURSED_STAGES)
    revenue = disbursed.aggregate(s=Sum('loan_amount'))['s'] or 0
    kpis = {
        'revenue': revenue,
        'commission': revenue * 0.006,
        'referral': revenue * 0.003,
        'vat': revenue * 0.006 * 0.05,
        'net': revenue * 0.006 - revenue * 0.003,
        'disbursed_loans': disbursed.count(),
    }
    return render(request, 'crm/finance.html', {'kpis': kpis, 'active_nav': 'Finance'})


@login_required
@perm.module_required('Reports')
def reports(request):
    leads = Lead.objects.all()
    total = leads.count()
    disbursed = leads.filter(stage__in=DISBURSED_STAGES).count()
    kpis = {
        'total_leads': total,
        'disbursed': disbursed,
        'conv': round(disbursed / total * 100) if total else 0,
        'lost': leads.filter(stage='Declined').count(),
        'value': leads.aggregate(s=Sum('loan_amount'))['s'] or 0,
    }
    return render(request, 'crm/reports.html', {'kpis': kpis, 'active_nav': 'Reports'})


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
    }
    return render(request, 'crm/settings.html', {'data': data, 'active_nav': 'SettingsPage'})


@login_required
@perm.module_required('Leads', 'edit')
def lead_edit(request, pk):
    lead = get_object_or_404(visible_leads(request.user), pk=pk)
    # snapshot BEFORE the form binds/validates (is_valid() mutates the instance)
    before = _snapshot(lead)
    form = LeadForm(request.POST or None, request.FILES or None, instance=lead)
    if request.method == 'POST' and form.is_valid():
        form.save()
        if _apply_disbursed(lead, request.user):
            lead.save(update_fields=['disbursed_at'])
        lead.refresh_from_db()
        _audit_diff(lead, request.user, before)
        uploader = request.user.get_full_name() or request.user.username
        for key, f in request.FILES.items():
            if key.startswith('doc::'):
                Document.objects.create(lead=lead, doc_type=key[5:], file=f,
                                        status='Pending Review', uploaded_by=uploader)
                _audit(lead, request.user, 'Document uploaded', key[5:])
        messages.success(request, 'Lead updated.')
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
        'data': data, 'active_nav': 'Leads'})


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
            form.save()
            messages.success(request, 'Task created.')
        else:
            messages.error(request, 'Task title is required.')
    return redirect('task_list')


@login_required
@perm.module_required('Tasks', 'edit')
def task_complete(request, pk):
    t = get_object_or_404(Task, pk=pk)
    t.status = 'Completed'
    t.save()
    messages.success(request, f'Task "{t.title}" marked complete.')
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
        bl = b.lead_set.all() if hasattr(b, 'lead_set') else Lead.objects.filter(bank=b)
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


# ---------- advisors ----------
@login_required
@perm.module_required('Advisors')
def advisor_list(request):
    DISB = ['Disbursed', 'Property Transferred']
    APPROVED_STAGES = ['Pre-Approved', 'Disbursed', 'FOL Signed', 'Under Disbursement']
    advisors = User.objects.filter(role=Role.ADVISOR).annotate(
        lead_count=Count('leads'),
        approved=Count('leads', filter=Q(leads__stage__in=APPROVED_STAGES)),
        disbursed=Count('leads', filter=Q(leads__stage='Disbursed')),
        active_leads=Count('leads', filter=~Q(leads__stage__in=DISB + ['Declined'])))
    rows = []
    for a in advisors:
        rev = float(Lead.objects.filter(advisor=a, stage='Disbursed').aggregate(
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
        'leads': p.leads.count(),
        'approved': p.leads.exclude(stage__in=['Lead Received', 'Documents Pending',
                                               'Documents Complete', 'Logged In',
                                               'Under Review', 'Declined']).count(),
        'disbursed': p.leads.filter(stage__in=DISBURSED_STAGES).count(),
        'revenue': round(_f(p.leads.filter(stage__in=DISBURSED_STAGES)
                            .aggregate(v=Sum('loan_amount'))['v']) * 0.011),
        'status': p.status if p.status in STC_STATUS else 'Active',
        'i': _ini(p.name),
        'created': p.created_at.strftime('%Y-%m-%d'),
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
        partner.save()
        messages.success(request, 'Referral partner created with documents.')
        return redirect('partner_list')
    return render(request, 'crm/partner_form.html', {'form': form, 'active_nav': 'Referral Partners'})


# ---------- documents ----------
@login_required
@perm.module_required('Documents')
def document_list(request):
    docs = Document.objects.select_related('lead', 'lead__advisor', 'verified_by')
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
            'name': (d.doc_type or 'Document').replace(' ', '_') + '.pdf',
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
        doc.save()
        messages.success(request, f'{doc.doc_type} {mapping[action][1]}.')
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
            'leadsCount': Lead.objects.filter(advisor=u).count(),
            'openTasks': Task.objects.filter(assignee=u).exclude(
                status__in=['Completed', 'Cancelled']).count(),
            'completedTasks': Task.objects.filter(assignee=u, status='Completed').count(),
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


# ---------- CSV exports ----------
def _csv(filename, header, rows):
    resp = HttpResponse(content_type='text/csv')
    resp['Content-Disposition'] = f'attachment; filename="{filename}"'
    w = csv.writer(resp)
    w.writerow(header)
    for r in rows:
        w.writerow(r)
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
                  t.created_at.strftime('%Y-%m-%d')] for t in tasks])


@login_required
@perm.module_required('Banks')
def bank_export(request):
    return _csv('banks.csv', ['Name', 'Type', 'Contact', 'Status'],
                [[b.name, b.bank_type, b.contact_person, b.status] for b in Bank.objects.all()])


@login_required
@perm.module_required('Documents')
def document_export(request):
    docs = Document.objects.select_related('lead')
    if perm.is_own_scope(request.user, 'Documents'):
        docs = docs.filter(lead__advisor=request.user)
    return _csv('documents.csv', ['ID', 'Type', 'Lead', 'Status', 'Uploaded By', 'Created'],
                [[d.pk, d.doc_type, d.lead.name, d.status, d.uploaded_by,
                  d.created_at.strftime('%Y-%m-%d')] for d in docs.order_by('-created_at')])


@login_required
@perm.module_required('Advisors')
def advisor_export(request):
    rows = []
    for a in User.objects.filter(role=Role.ADVISOR):
        cnt = Lead.objects.filter(advisor=a).count()
        rows.append([a.get_full_name() or a.username, a.email, a.phone, a.status, cnt])
    return _csv('advisors.csv', ['Name', 'Email', 'Phone', 'Status', 'Assigned Leads'], rows)


@login_required
@perm.module_required('Referral Partners')
def partner_export(request):
    qs = ReferralPartner.objects.all()
    if request.user.role != Role.CEO:
        qs = qs.filter(created_by=request.user)
    return _csv('partners.csv', ['Name', 'Company', 'Type', 'Mobile', 'Email', 'Status'],
                [[p.name, p.company, p.partner_type, p.mobile, p.email, p.status]
                 for p in qs])


@login_required
@perm.module_required('Users')
def user_export(request):
    return _csv('users.csv', ['Name', 'Username', 'Email', 'Phone', 'Role', 'Department', 'Status'],
                [[u.get_full_name() or u.username, u.username, u.email, u.phone,
                  u.role_label, u.department, u.status] for u in User.objects.all()])


@login_required
@perm.module_required('Finance')
def finance_export(request):
    leads = Lead.objects.filter(stage__in=DISBURSED_STAGES).select_related('advisor', 'bank')
    return _csv('finance.csv', ['Lead', 'Loan Amount', 'Advisor', 'Bank', 'Stage'],
                [[l.name, l.loan_amount,
                  (l.advisor.get_full_name() or l.advisor.username) if l.advisor else '',
                  l.bank.name if l.bank else '', l.stage] for l in leads])


@login_required
@perm.module_required('Reports')
def report_export(request):
    rows = []
    for src in SOURCES:
        qs = Lead.objects.filter(source=src)
        rows.append([src, qs.count(), qs.filter(stage__in=DISBURSED_STAGES).count(),
                     qs.filter(stage='Declined').count()])
    return _csv('report.csv', ['Source', 'Total Leads', 'Disbursed', 'Declined'], rows)


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
        messages.success(request, 'Note added.')
    return redirect('lead_detail', pk=pk)


@login_required
@perm.module_required('Documents', 'create')
@require_POST
def lead_document_upload(request, pk):
    lead = get_object_or_404(visible_leads(request.user), pk=pk)
    uploader = request.user.get_full_name() or request.user.username
    doc_type = request.POST.get('doc_type', '').strip() or 'Document'
    n = 0
    for f in request.FILES.getlist('file'):
        Document.objects.create(lead=lead, doc_type=doc_type, file=f,
                                status='Pending Review', uploaded_by=uploader)
        _audit(lead, request.user, 'Document uploaded', doc_type)
        n += 1
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
        return HttpResponse('ok')
    return HttpResponse('err', status=400)


@login_required
@perm.module_required('Settings', 'edit')
@require_POST
def settings_state_save(request):
    import json
    key = request.POST.get('key', '')
    if key not in ('stages', 'sources', 'doc_types', 'notifications'):
        return HttpResponse('err', status=400)
    try:
        value = json.loads(request.POST.get('value', '[]'))
    except ValueError:
        return HttpResponse('err', status=400)
    s, _ = AppSetting.objects.get_or_create(key=key, defaults={'value': value})
    s.value = value
    s.save()
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
        'client': l.name,
        'bankRm': c.bank_rm,
        'mob': l.mobile or '—',
        'loan': float(l.loan_amount or 0),
        'bank': l.bank.name if l.bank else '—',
        'rm': (l.advisor.get_full_name() or l.advisor.username) if l.advisor else '—',
        'slab': float(c.slab or 0),
        'brokerPct': float(c.broker_pct or 0),
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
    return render(request, 'crm/customization.html', {
        'data': {'rows': rows, 'totals': totals},
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
    for field, attr in (('slab', 'slab'), ('broker_pct', 'broker_pct')):
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
    for field in ('bank_rm', 'cp'):
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
    w.writerow(['Month', 'Client Name', 'Bank RM', 'MOB', 'Loan Amount', 'Bank Name',
                'RM Name', 'Slab', 'Actual Revenue', 'VAT', 'With VAT', 'Broker Revenue',
                'Broker Payout', 'Final Revenue', 'CP', 'Status'])
    for c in Customization.objects.select_related('lead', 'lead__advisor', 'lead__bank'):
        r = _cz_row(c)
        w.writerow([r['month'], r['client'], r['bankRm'], r['mob'], r['loan'], r['bank'],
                    r['rm'], r['slab'], round(r['actualRevenue'], 2), round(r['vat'], 2),
                    round(r['withVat'], 2), round(r['brokerRevenue'], 2),
                    round(r['brokerPayout'], 2), round(r['finalRevenue'], 2), r['cp'], r['status']])
    return resp
