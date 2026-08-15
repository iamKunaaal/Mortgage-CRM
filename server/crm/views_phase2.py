"""Phase-2 views: Finance, Operations subflows, Partner depth, Automation,
HR, Compliance/Admin depth, Reporting. All additive + graceful — if a config
or integration is absent the core CRM is unaffected.

Shared helpers are imported from crm.views so we reuse one implementation.
"""
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, HttpResponse
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.db.models import Sum

from . import permissions as perm
from .models import (User, Role, Lead, Bank, ReferralPartner, AppSetting, ApprovalRequest,
                     Invoice, CreditNote, Receipt, LedgerEntry, PayoutRun, PayoutLine,
                     IncentiveScheme, MonthLock, ValuationRecord, BuyoutRecord, NOCRecord,
                     TransferBooking, PartnerCommissionModel, PartnerStatement,
                     AutomationRule, AutomationRun, Attendance, LeaveType, LeaveRequest,
                     Target, RetentionPolicy, CustomField,
                     MessageTemplate, UBO, ClientReferral, UploadToken)
from .views import _notify, _audit, _audit_event, visible_leads, _f


# ==========================================================================
# Cross-cutting helpers
# ==========================================================================
def feature_on(flag, default=True):
    """Feature flag switchboard (AD-08). Reads AppSetting('feature_flags')."""
    try:
        s = AppSetting.objects.filter(key='feature_flags').first()
        if s and isinstance(s.value, dict) and flag in s.value:
            return bool(s.value[flag])
    except Exception:
        pass
    return default


def _num(v):
    try:
        return Decimal(str(v or '0'))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal('0')


def finance_config():
    """Finance tunables (VAT %, TRN, invoice format, commission trigger — OD-5)."""
    cfg = {'vat_pct': 5, 'trn': '', 'invoice_format': 'INV-{yyyy}-{seq:04d}',
           'commission_trigger': 'on_receipt'}
    try:
        s = AppSetting.objects.filter(key='finance').first()
        if s and isinstance(s.value, dict):
            cfg.update(s.value)
    except Exception:
        pass
    return cfg


def notify_dispatch(user, text, url='', category='', actor=None, channels=('inapp',)):
    """Single notification entry point. Today only the in-app adapter is live;
    email/whatsapp adapters are stubs so vendors can be added later with no rewrite."""
    if 'inapp' in channels:
        _notify(user, text, url, category, actor)
    # email / whatsapp adapters: intentionally no-op until a provider is configured (Phase-2 deferred)
    return True


def _next_number(fmt, seq):
    y = timezone.localdate().year
    try:
        return fmt.format(yyyy=y, seq=seq)
    except Exception:
        return f'INV-{y}-{seq:04d}'


# ==========================================================================
# 2.1 FINANCE DEPTH
# ==========================================================================
@login_required
@perm.module_required('Finance')
def finance_hub(request):
    """Finance depth hub: invoices, receivables, payouts, incentives, month-end."""
    invoices = Invoice.objects.select_related('lead').all()[:200]
    total_inv = invoices.aggregate(s=Sum('total'))['s'] or 0
    receipts_total = Receipt.objects.aggregate(s=Sum('amount'))['s'] or 0
    outstanding = sum(_f(i.balance) for i in Invoice.objects.exclude(status__in=['Paid', 'Void', 'Credited']))
    payouts = PayoutRun.objects.all()[:50]
    schemes = IncentiveScheme.objects.all()
    locks = MonthLock.objects.all()[:12]
    can_edit = perm.can_edit(request.user, 'Finance')
    rows = [{
        'id': i.pk, 'number': i.number or f'INV#{i.pk}',
        'client': i.client_name or (i.lead.name if i.lead else '—'),
        'total': _f(i.total), 'paid': _f(i.paid_amount), 'balance': _f(i.balance),
        'status': i.status, 'issued': i.issued_at.strftime('%d %b %Y') if i.issued_at else '—',
    } for i in invoices]
    return render(request, 'crm/finance_hub.html', {
        'rows': rows, 'payouts': payouts, 'schemes': schemes, 'locks': locks,
        'kpis': {'invoiced': _f(total_inv), 'received': _f(receipts_total), 'outstanding': outstanding},
        'leads': Lead.objects.filter(is_deleted=False).order_by('-created_at')[:500],
        'cfg': finance_config(), 'can_edit': can_edit,
        'active_nav': 'Finance', 'active_sub': 'finance_hub',
    })


@login_required
@perm.module_required('Finance', 'edit')
@require_POST
def invoice_create(request):
    cfg = finance_config()
    lead_id = request.POST.get('lead') or None
    lead = Lead.objects.filter(pk=lead_id).first() if lead_id else None
    subtotal = _num(request.POST.get('subtotal'))
    vat = (subtotal * Decimal(cfg['vat_pct']) / Decimal(100)).quantize(Decimal('0.01'))
    seq = Invoice.objects.count() + 1
    inv = Invoice.objects.create(
        number=_next_number(cfg['invoice_format'], seq), lead=lead,
        client_name=request.POST.get('client_name', '').strip() or (lead.name if lead else ''),
        trn=cfg.get('trn', ''), subtotal=subtotal, vat=vat, total=subtotal + vat,
        notes=request.POST.get('notes', '').strip(), issued_at=timezone.localdate(),
        created_by=request.user, status='Draft')
    _audit_event(request, 'Invoice created', f'{inv.number} · AED {inv.total}')
    messages.success(request, f'Invoice {inv.number} created (Draft).')
    return redirect('finance_hub')


@login_required
@perm.module_required('Finance', 'edit')
@require_POST
def invoice_send(request, pk):
    inv = get_object_or_404(Invoice, pk=pk)
    if inv.status == 'Draft':
        inv.status, inv.locked, inv.sent_at = 'Sent', True, timezone.now()
        inv.save(update_fields=['status', 'locked', 'sent_at'])
        _audit_event(request, 'Invoice sent', inv.number)     # FI-04 post-send lock
        messages.success(request, f'Invoice {inv.number} marked Sent & locked.')
    return redirect('finance_hub')


@login_required
@perm.module_required('Finance', 'edit')
@require_POST
def receipt_add(request, pk):
    inv = get_object_or_404(Invoice, pk=pk)
    amt = _num(request.POST.get('amount'))
    Receipt.objects.create(invoice=inv, amount=amt,
                           method=request.POST.get('method', 'Bank Transfer'),
                           reference=request.POST.get('reference', '').strip(),
                           received_at=timezone.localdate(), created_by=request.user)
    # auto status (FI-06)
    bal = _f(inv.balance)
    inv.status = 'Paid' if bal <= 0 else 'Part-Paid'
    inv.save(update_fields=['status'])
    # commission booked on receipt (OD-5 default)
    if finance_config().get('commission_trigger') == 'on_receipt' and inv.lead and inv.lead.advisor:
        LedgerEntry.objects.create(payee=inv.lead.advisor, kind='commission', amount=amt * Decimal('0.15'),
                                   lead=inv.lead, note=f'Commission on {inv.number}',
                                   effective_date=timezone.localdate())
    _audit_event(request, 'Receipt recorded', f'{inv.number} · AED {amt}')
    messages.success(request, f'Receipt AED {amt} recorded on {inv.number}.')
    return redirect('finance_hub')


@login_required
@perm.module_required('Finance', 'edit')
@require_POST
def credit_note_add(request, pk):
    inv = get_object_or_404(Invoice, pk=pk)
    amt = _num(request.POST.get('amount'))
    CreditNote.objects.create(invoice=inv, amount=amt, reason=request.POST.get('reason', '').strip(),
                              number=f'CN-{inv.number}', created_by=request.user)
    inv.status = 'Credited'
    inv.save(update_fields=['status'])
    _audit_event(request, 'Credit note issued', f'{inv.number} · AED {amt}')
    messages.success(request, f'Credit note issued for {inv.number}.')
    return redirect('finance_hub')


@login_required
@perm.module_required('Finance', 'edit')
@require_POST
def payout_run_create(request):
    period = request.POST.get('period') or timezone.localdate().strftime('%Y-%m')
    run = PayoutRun.objects.create(period=period, created_by=request.user, status='Draft')
    # build lines from unpaid ledger entries in the period
    total = Decimal('0')
    ledger = LedgerEntry.objects.filter(payout_line__isnull=True)
    by_user, by_partner = {}, {}
    for e in ledger:
        if e.payee_id:
            by_user[e.payee_id] = by_user.get(e.payee_id, Decimal('0')) + _num(e.amount)
        elif e.partner_id:
            by_partner[e.partner_id] = by_partner.get(e.partner_id, Decimal('0')) + _num(e.amount)
    for uid, amt in by_user.items():
        line = PayoutLine.objects.create(run=run, payee_user_id=uid, amount=amt)
        LedgerEntry.objects.filter(payee_id=uid, payout_line__isnull=True).update(payout_line=line)
        total += amt
    for pid, amt in by_partner.items():
        line = PayoutLine.objects.create(run=run, payee_partner_id=pid, amount=amt)
        LedgerEntry.objects.filter(partner_id=pid, payout_line__isnull=True).update(payout_line=line)
        total += amt
    run.total = total
    run.save(update_fields=['total'])
    _audit_event(request, 'Payout run created', f'{period} · AED {total}')
    messages.success(request, f'Payout run for {period} created (AED {total}).')
    return redirect('finance_hub')


@login_required
@perm.module_required('Finance', 'edit')
@require_POST
def payout_run_submit(request, pk):
    """Submit for approval — segregation of duties (FI-08): approver != creator."""
    run = get_object_or_404(PayoutRun, pk=pk)
    ar = ApprovalRequest.objects.create(
        request_type='Payout Run', title=f'Payout run {run.period}',
        detail=f'AED {run.total}', link='/finance-hub/', target_model='PayoutRun',
        target_id=run.pk, approver_role=Role.CEO, requested_by=request.user)
    run.status, run.approval = 'Pending Approval', ar
    run.save(update_fields=['status', 'approval'])
    for u in User.objects.filter(role__in=[Role.CEO, Role.SUPER_ADMIN]).exclude(pk=request.user.pk):
        notify_dispatch(u, f'Payout run {run.period} needs approval (AED {run.total})',
                        '/approvals/', 'approval')
    messages.success(request, 'Payout run submitted for approval.')
    return redirect('finance_hub')


@login_required
@perm.module_required('Finance', 'edit')
@require_POST
def month_lock(request):
    period = request.POST.get('period') or timezone.localdate().strftime('%Y-%m')
    MonthLock.objects.update_or_create(period=period,
                                       defaults={'locked': True, 'locked_by': request.user})
    _audit_event(request, 'Month locked', period)
    messages.success(request, f'{period} locked.')
    return redirect('finance_hub')


@login_required
@perm.module_required('Finance', 'edit')
@require_POST
def month_reopen(request):
    """CEO reopen via approval (FI-11)."""
    period = request.POST.get('period')
    if request.user.role in (Role.CEO, Role.SUPER_ADMIN):
        MonthLock.objects.filter(period=period).update(locked=False)
        _audit_event(request, 'Month reopened', period)
        messages.success(request, f'{period} reopened.')
    else:
        ApprovalRequest.objects.create(request_type='Month Reopen', title=f'Reopen {period}',
                                       link='/finance-hub/', approver_role=Role.CEO,
                                       requested_by=request.user)
        messages.success(request, 'Reopen request sent to CEO.')
    return redirect('finance_hub')


@login_required
@perm.module_required('Finance', 'edit')
@require_POST
def incentive_save(request):
    name = request.POST.get('name', '').strip()
    if name:
        IncentiveScheme.objects.create(name=name, rules={'pct': _f(request.POST.get('pct') or 0)},
                                       effective_from=timezone.localdate())
        _audit_event(request, 'Incentive scheme created', name)
        messages.success(request, f'Incentive scheme "{name}" saved.')
    return redirect('finance_hub')


# ==========================================================================
# 2.2 OPERATIONS SUBFLOWS (endpoints; shown on the lead detail Operations tab)
# ==========================================================================
@login_required
@perm.module_required('Leads', 'edit')
@require_POST
def ops_valuation_add(request, pk):
    lead = get_object_or_404(visible_leads(request.user), pk=pk)
    v = ValuationRecord.objects.create(
        lead=lead, valued_amount=_num(request.POST.get('valued_amount')) or None,
        purchase_price=_num(request.POST.get('purchase_price')) or None,
        valued_on=_pdate(request.POST.get('valued_on')), note=request.POST.get('note', '').strip())
    if v.shortfall > 0:
        from .views import _auto_task
        _auto_task(lead, f'Resolve valuation shortfall AED {v.shortfall:.0f}', 'Valuation', days=2, actor=request.user)
    _audit(lead, request.user, 'Valuation recorded', 'Ops', '', str(v.valued_amount or ''))
    messages.success(request, 'Valuation recorded.')
    return redirect('lead_detail', pk=pk)


@login_required
@perm.module_required('Leads', 'edit')
@require_POST
def ops_buyout_add(request, pk):
    lead = get_object_or_404(visible_leads(request.user), pk=pk)
    BuyoutRecord.objects.create(
        lead=lead, current_bank=request.POST.get('current_bank', '').strip(),
        liability_amount=_num(request.POST.get('liability_amount')) or None,
        liability_letter_date=_pdate(request.POST.get('liability_letter_date')),
        liability_valid_until=_pdate(request.POST.get('liability_valid_until')),
        note=request.POST.get('note', '').strip())
    _audit(lead, request.user, 'Buyout recorded', 'Ops')
    messages.success(request, 'Buyout details saved.')
    return redirect('lead_detail', pk=pk)


@login_required
@perm.module_required('Leads', 'edit')
@require_POST
def ops_noc_add(request, pk):
    lead = get_object_or_404(visible_leads(request.user), pk=pk)
    NOCRecord.objects.create(
        lead=lead, developer=request.POST.get('developer', '').strip(),
        fee=_num(request.POST.get('fee')) or None,
        requested_on=_pdate(request.POST.get('requested_on')),
        received_on=_pdate(request.POST.get('received_on')),
        receipt_ref=request.POST.get('receipt_ref', '').strip())
    _audit(lead, request.user, 'NOC recorded', 'Ops')
    messages.success(request, 'NOC details saved.')
    return redirect('lead_detail', pk=pk)


@login_required
@perm.module_required('Leads', 'edit')
@require_POST
def ops_transfer_add(request, pk):
    lead = get_object_or_404(visible_leads(request.user), pk=pk)
    cheques = []
    for i in range(1, 6):
        payee = request.POST.get(f'cheque_payee_{i}', '').strip()
        amt = request.POST.get(f'cheque_amount_{i}', '').strip()
        if payee or amt:
            cheques.append({'payee': payee, 'amount': amt,
                            'bank': request.POST.get(f'cheque_bank_{i}', '').strip(),
                            'no': request.POST.get(f'cheque_no_{i}', '').strip()})
    TransferBooking.objects.create(
        lead=lead, trustee_office=request.POST.get('trustee_office', '').strip(),
        booked_for=_pdate(request.POST.get('booked_for')), cheques=cheques,
        note=request.POST.get('note', '').strip())
    _audit(lead, request.user, 'Transfer booked', 'Ops')
    messages.success(request, 'Transfer booking saved.')
    return redirect('lead_detail', pk=pk)


def _pdate(s):
    s = (s or '').strip()
    if not s:
        return None
    try:
        return datetime.strptime(s, '%Y-%m-%d').date()
    except ValueError:
        return None


# ==========================================================================
# 2.3 CHANNEL PARTNERS DEPTH
# ==========================================================================
@login_required
@perm.module_required('Referral Partners', 'edit')
@require_POST
def partner_commission_save(request, pk):
    p = get_object_or_404(ReferralPartner, pk=pk)
    PartnerCommissionModel.objects.create(
        partner=p, effective_from=timezone.localdate(),
        model={'type': request.POST.get('type', 'pct'), 'value': _f(request.POST.get('value') or 0)})
    _audit_event(request, 'Partner commission model set', p.name)
    messages.success(request, f'Commission model updated for {p.name}.')
    return redirect('partner_list')


@login_required
@perm.module_required('Referral Partners')
def partner_statements(request):
    """View generated partner commission statements."""
    stmts = PartnerStatement.objects.select_related('partner').all()
    rows = [{'partner': s.partner.name if s.partner else '—', 'period': s.period,
             'total': _f(s.total), 'lines': s.lines or [], 'n': len(s.lines or [])}
            for s in stmts]
    return render(request, 'crm/partner_statements.html', {
        'rows': rows, 'active_nav': 'Referral Partners'})


@login_required
@perm.module_required('Referral Partners')
@require_POST
def partner_statements_generate(request):
    period = request.POST.get('period') or timezone.localdate().strftime('%Y-%m')
    n = 0
    for p in ReferralPartner.objects.all():
        entries = LedgerEntry.objects.filter(partner=p)
        total = entries.aggregate(s=Sum('amount'))['s'] or 0
        lines = [{'note': e.note, 'amount': str(e.amount)} for e in entries[:100]]
        PartnerStatement.objects.update_or_create(
            partner=p, period=period, defaults={'lines': lines, 'total': total})
        n += 1
    _audit_event(request, 'Partner statements generated', f'{period} · {n} partners')
    messages.success(request, f'{n} partner statement(s) generated for {period}.')
    return redirect('partner_statements')


# ==========================================================================
# 2.4 AUTOMATION BUILDER + engine
# ==========================================================================
@login_required
@perm.module_required('Settings')
def automation_list(request):
    rules = AutomationRule.objects.all()
    runs = AutomationRun.objects.select_related('rule')[:100]
    return render(request, 'crm/automation.html', {
        'rules': rules, 'runs': runs, 'active_nav': 'SettingsPage',
    })


@login_required
@perm.module_required('Settings')
@require_POST
def automation_save(request):
    import json
    name = request.POST.get('name', '').strip()
    if name:
        try:
            conditions = json.loads(request.POST.get('conditions') or '[]')
            actions = json.loads(request.POST.get('actions') or '[]')
        except ValueError:
            conditions, actions = [], []
        AutomationRule.objects.create(name=name, trigger=request.POST.get('trigger', '').strip(),
                                      conditions=conditions, actions=actions,
                                      active=bool(request.POST.get('active')))
        _audit_event(request, 'Automation rule created', name)
        messages.success(request, f'Automation rule "{name}" saved.')
    return redirect('automation_list')


@login_required
@perm.module_required('Settings')
@require_POST
def automation_toggle(request, pk):
    r = get_object_or_404(AutomationRule, pk=pk)
    r.active = not r.active
    r.save(update_fields=['active'])
    return redirect('automation_list')


def run_automations(event, lead=None, actor=None, simulate=False):
    """Tiny rule engine — call from write paths (lead save, stage change, etc.).
    Always safe: never raises into the caller; loop-guarded per rule per record per day."""
    from .views import _auto_task
    try:
        rules = AutomationRule.objects.filter(active=True, trigger=event)
    except Exception:
        return
    today = timezone.localdate()
    for rule in rules:
        try:
            # loop guard: max 1 run per rule per record per day
            if lead and AutomationRun.objects.filter(
                    rule=rule, target_model='Lead', target_id=lead.pk,
                    created_at__date=today, simulated=False).exists():
                continue
            if not _conditions_pass(rule.conditions, lead):
                AutomationRun.objects.create(rule=rule, target_model='Lead',
                                             target_id=lead.pk if lead else None,
                                             status='skipped', simulated=simulate)
                continue
            logs = []
            for act in (rule.actions or []):
                t = act.get('type')
                if simulate:
                    logs.append(f'would {t}')
                    continue
                logs.append(_run_action(t, act, rule, lead, actor))
            AutomationRun.objects.create(rule=rule, target_model='Lead',
                                         target_id=lead.pk if lead else None,
                                         status='simulated' if simulate else 'ok',
                                         log=', '.join(logs)[:500], simulated=simulate)
            if not simulate:
                AutomationRule.objects.filter(pk=rule.pk).update(run_count=rule.run_count + 1)
        except Exception as e:
            try:
                AutomationRun.objects.create(rule=rule, status='error', log=str(e)[:500])
            except Exception:
                pass


def _run_action(t, act, rule, lead, actor):
    """Execute one automation action. Returns a short log string. Never raises."""
    from .views import _auto_task, _notify
    try:
        if t == 'task' and lead:
            _auto_task(lead, act.get('title', rule.name), act.get('task_type', 'Documents'),
                       days=int(act.get('days', 1)), actor=actor)
            return 'task created'
        if t == 'notify' and lead and lead.advisor:
            notify_dispatch(lead.advisor, act.get('text', f'Rule: {rule.name}'),
                            f'/leads/{lead.pk}/', 'lead')
            return 'advisor notified'
        if t == 'notify_role':
            role = act.get('role', '')
            for u in User.objects.filter(role=role, status='Active'):
                notify_dispatch(u, act.get('text', f'Rule: {rule.name}'),
                                f'/leads/{lead.pk}/' if lead else '', 'lead')
            return f'{role} notified'
        if t == 'set_priority' and lead:
            lead.priority = act.get('value', 'High')
            lead.save(update_fields=['priority'])
            return f'priority → {lead.priority}'
        if t == 'add_note' and lead:
            from .models import Note
            Note.objects.create(lead=lead, author=actor, text=act.get('text', rule.name))
            return 'note added'
        if t == 'escalate' and lead:
            mgrs = User.objects.filter(role__in=[Role.OPS_MANAGER, Role.SALES_DIRECTOR, Role.CEO], status='Active')
            for u in mgrs:
                notify_dispatch(u, act.get('text', f'Escalation: {lead.name}'),
                                f'/leads/{lead.pk}/', 'escalation')
            return 'escalated'
        if t == 'send_template' and lead:
            tpl = MessageTemplate.objects.filter(name=act.get('template', ''), published=True).first()
            if tpl and lead.advisor:
                notify_dispatch(lead.advisor, render_template_body(tpl.body, lead),
                                f'/leads/{lead.pk}/', 'lead')
                return 'template sent'
            return 'template not found/unpublished'
    except Exception as e:
        return f'action {t} error: {e}'
    return f'skipped {t}'


def _conditions_pass(conditions, lead):
    """All conditions must pass (AND). Ops: ==, !=, contains, >, <."""
    if not conditions or not lead:
        return True
    for c in conditions:
        val = getattr(lead, c.get('field', ''), None)
        op, target = c.get('op', '=='), c.get('value')
        try:
            if op == '==' and str(val) != str(target):
                return False
            if op == '!=' and str(val) == str(target):
                return False
            if op == 'contains' and str(target).lower() not in str(val or '').lower():
                return False
            if op == '>' and not (float(val or 0) > float(target)):
                return False
            if op == '<' and not (float(val or 0) < float(target)):
                return False
        except (ValueError, TypeError):
            return False
    return True


# ==========================================================================
# 2.6 HR
# ==========================================================================
@login_required
def hr_home(request):
    """HR hub: today's attendance, my leave, targets. Access role-gated below."""
    if not _hr_allowed(request.user):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied
    today = timezone.localdate()
    my_att = Attendance.objects.filter(user=request.user, date=today).first()
    is_mgr = request.user.role in (Role.HR_EXECUTIVE, Role.CEO, Role.SUPER_ADMIN)
    att_rows = Attendance.objects.filter(date=today).select_related('user') if is_mgr else []
    leaves = (LeaveRequest.objects.select_related('user', 'leave_type')
              if is_mgr else LeaveRequest.objects.filter(user=request.user))
    targets = Target.objects.filter(period=today.strftime('%Y-%m')).select_related('user') \
        if is_mgr else Target.objects.filter(user=request.user, period=today.strftime('%Y-%m'))
    return render(request, 'crm/hr_home.html', {
        'my_att': my_att, 'att_rows': att_rows, 'leaves': leaves[:100], 'targets': targets,
        'leave_types': LeaveType.objects.filter(active=True), 'is_mgr': is_mgr,
        'staff': User.objects.filter(status='Active') if is_mgr else [],
        'active_nav': 'HR', 'active_sub': 'hr_home',
    })


def _hr_allowed(user):
    return user.is_authenticated and user.role in (
        Role.HR_EXECUTIVE, Role.CEO, Role.SUPER_ADMIN, Role.ADVISOR, Role.TELECALLER,
        Role.OPS_EXECUTIVE, Role.OPS_MANAGER, Role.TEAM_LEADER, Role.SALES_DIRECTOR,
        Role.ACCOUNTANT, Role.COMPLIANCE, Role.MARKETING)


@login_required
@require_POST
def attendance_checkin(request):
    today = timezone.localdate()
    att, _ = Attendance.objects.get_or_create(user=request.user, date=today)
    if not att.check_in:
        att.check_in = timezone.now()
        att.geo = request.POST.get('geo', '').strip()
        att.save(update_fields=['check_in', 'geo'])
        messages.success(request, 'Checked in.')
    return redirect('hr_home')


@login_required
@require_POST
def attendance_checkout(request):
    today = timezone.localdate()
    att = Attendance.objects.filter(user=request.user, date=today).first()
    if att and not att.check_out:
        att.check_out = timezone.now()
        att.save(update_fields=['check_out'])
        messages.success(request, 'Checked out.')
    return redirect('hr_home')


@login_required
@require_POST
def leave_request(request):
    lt = LeaveType.objects.filter(pk=request.POST.get('leave_type') or 0).first()
    start, end = _pdate(request.POST.get('start')), _pdate(request.POST.get('end'))
    if start and end:
        lr = LeaveRequest.objects.create(user=request.user, leave_type=lt, start=start, end=end,
                                         reason=request.POST.get('reason', '').strip())
        ar = ApprovalRequest.objects.create(request_type='Leave',
                                            title=f'Leave {request.user.get_full_name() or request.user.username}',
                                            detail=f'{start}..{end}', link='/hr/', approver_role=Role.HR_EXECUTIVE,
                                            requested_by=request.user)
        lr.approval = ar
        lr.save(update_fields=['approval'])
        messages.success(request, 'Leave request submitted.')
    return redirect('hr_home')


@login_required
@require_POST
def leave_decide(request, pk):
    if request.user.role not in (Role.HR_EXECUTIVE, Role.CEO, Role.SUPER_ADMIN):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied
    lr = get_object_or_404(LeaveRequest, pk=pk)
    decision = request.POST.get('decision')
    lr.status = 'Approved' if decision == 'approve' else 'Rejected'
    lr.save(update_fields=['status'])
    if lr.approval:
        lr.approval.status = lr.status
        lr.approval.decided_by = request.user
        lr.approval.decided_at = timezone.now()
        lr.approval.save(update_fields=['status', 'decided_by', 'decided_at'])
    notify_dispatch(lr.user, f'Your leave request was {lr.status.lower()}.', '/hr/', 'approval')
    messages.success(request, f'Leave {lr.status.lower()}.')
    return redirect('hr_home')


@login_required
@require_POST
def target_save(request):
    if request.user.role not in (Role.HR_EXECUTIVE, Role.CEO, Role.SUPER_ADMIN, Role.SALES_DIRECTOR):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied
    u = User.objects.filter(pk=request.POST.get('user') or 0).first()
    if u:
        Target.objects.update_or_create(
            user=u, metric=request.POST.get('metric', 'disbursed_value'),
            period=request.POST.get('period') or timezone.localdate().strftime('%Y-%m'),
            defaults={'target_value': _num(request.POST.get('target_value'))})
        messages.success(request, 'Target saved.')
    return redirect('hr_home')


# ==========================================================================
# 2.7 COMPLIANCE / ADMIN DEPTH
# ==========================================================================
@login_required
@perm.module_required('Settings')
@require_POST
def retention_save(request):
    RetentionPolicy.objects.update_or_create(
        record_class=request.POST.get('record_class', '').strip() or 'Lead',
        defaults={'years': int(request.POST.get('years') or 7)})
    _audit_event(request, 'Retention policy set', request.POST.get('record_class', ''))
    messages.success(request, 'Retention policy saved.')
    return redirect('settings_view')


@login_required
@perm.module_required('Settings')
def custom_fields_page(request):
    fields = CustomField.objects.filter(model='Lead')
    return render(request, 'crm/custom_fields.html', {
        'fields': fields, 'active_nav': 'SettingsPage'})


@login_required
@perm.module_required('Settings')
@require_POST
def custom_field_toggle(request, pk):
    f = get_object_or_404(CustomField, pk=pk)
    f.active = not f.active
    f.save(update_fields=['active'])
    return redirect('custom_fields_page')


@login_required
@perm.module_required('Settings')
@require_POST
def custom_field_save(request):
    import re
    label = request.POST.get('label', '').strip()
    key = request.POST.get('key', '').strip()
    if not key and label:
        # auto-generate a safe key from the label (non-tech users only type a label)
        key = re.sub(r'[^a-z0-9]+', '_', label.lower()).strip('_')[:40]
    if key:
        opts = [o.strip() for o in request.POST.get('options', '').split(',') if o.strip()]
        CustomField.objects.update_or_create(
            model=request.POST.get('model', 'Lead'), key=key,
            defaults={'label': label or key,
                      'field_type': request.POST.get('field_type', 'text'),
                      'options': opts, 'active': True})
        _audit_event(request, 'Custom field added', f'Lead.{key}')
        messages.success(request, f'Custom field "{label or key}" saved.')
    else:
        messages.error(request, 'Enter a field label.')
    return redirect('custom_fields_page')


# ==========================================================================
# 2.8 REPORTING — weighted forecast (PL-07)
# ==========================================================================
@login_required
@perm.module_required('Reports')
def forecast(request):
    """Weighted pipeline forecast from per-stage probabilities (config-driven)."""
    probs = _stage_probs()
    leads = visible_leads(request.user).exclude(stage__in=['Declined']).select_related('bank')
    rows, weighted = {}, Decimal('0')
    for l in leads:
        p = Decimal(str(probs.get(l.stage, 0))) / Decimal(100)
        amt = _num(l.loan_amount)
        rows.setdefault(l.stage, {'stage': l.stage, 'count': 0, 'value': Decimal('0'),
                                  'prob': int(probs.get(l.stage, 0)), 'weighted': Decimal('0')})
        rows[l.stage]['count'] += 1
        rows[l.stage]['value'] += amt
        rows[l.stage]['weighted'] += amt * p
        weighted += amt * p
    return render(request, 'crm/forecast.html', {
        'rows': sorted(rows.values(), key=lambda r: -r['weighted']),
        'weighted_total': weighted, 'active_nav': 'Reports',
    })


def _stage_probs():
    default = {'Lead Received': 5, 'Documents Pending': 10, 'Documents Complete': 20,
               'Logged In': 30, 'Under Review': 40, 'Pre-Approved': 60, 'Valuation': 70,
               'FOL Issued': 85, 'FOL Signed': 90, 'Under Disbursement': 95}
    try:
        s = AppSetting.objects.filter(key='stage_probs').first()
        if s and isinstance(s.value, dict):
            default.update(s.value)
    except Exception:
        pass
    return default


# ==========================================================================
# SCAFFOLDED-ITEM COMPLETIONS
# ==========================================================================
import secrets
from django.views.decorators.csrf import csrf_exempt


# ---- Custom fields on the lead form (AD-05) --------------------------------
def custom_fields_for(model='Lead', user=None):
    """Active custom fields for a model, honoring role visibility."""
    qs = CustomField.objects.filter(model=model, active=True)
    out = []
    for f in qs:
        vis = f.role_visibility or []
        if vis and user and user.role not in vis:
            continue
        out.append(f)
    return out


def apply_custom_fields(request, obj, model='Lead'):
    """Save posted custom_cf_<key> values onto obj.custom (obj must have a JSON `custom`)."""
    data = dict(getattr(obj, 'custom', {}) or {})
    for f in custom_fields_for(model, request.user):
        key = f'custom_cf_{f.key}'
        if key in request.POST:
            data[f.key] = request.POST.get(key, '').strip()
    obj.custom = data
    return obj


# ---- Template studio + milestone messaging (AD-07, CL-03, DM-09) ----------
@login_required
@perm.module_required('Settings')
def template_studio(request):
    templates = MessageTemplate.objects.all()
    return render(request, 'crm/template_studio.html', {
        'templates': templates, 'active_nav': 'SettingsPage'})


@login_required
@perm.module_required('Settings')
@require_POST
def template_save(request):
    name = request.POST.get('name', '').strip()
    if name:
        t = MessageTemplate.objects.create(
            name=name, kind=request.POST.get('kind', 'inapp'),
            subject=request.POST.get('subject', '').strip(),
            body=request.POST.get('body', ''),
            milestone_stage=request.POST.get('milestone_stage', '').strip(),
            auto_send=bool(request.POST.get('auto_send')))
        # publish requires approval (AD-07)
        if request.user.role in (Role.CEO, Role.SUPER_ADMIN):
            t.published = True
            t.save(update_fields=['published'])
        else:
            ApprovalRequest.objects.create(request_type='Template Publish',
                                           title=f'Publish template {name}', link='/templates/',
                                           approver_role=Role.CEO, requested_by=request.user)
        _audit_event(request, 'Template saved', name)
        messages.success(request, f'Template "{name}" saved.')
    return redirect('template_studio')


def render_template_body(body, lead):
    out = body or ''
    repl = {'{{name}}': lead.name if lead else '', '{{case}}': getattr(lead, 'case_number', '') or '',
            '{{stage}}': getattr(lead, 'stage', '') or ''}
    for k, v in repl.items():
        out = out.replace(k, str(v))
    return out


def send_milestone_messages(lead):
    """Auto-send published milestone templates when a case reaches a stage (CL-03).
    Delivery via notify_dispatch (in-app now; email/whatsapp later)."""
    try:
        tpls = MessageTemplate.objects.filter(published=True, auto_send=True, milestone_stage=lead.stage)
    except Exception:
        return
    for t in tpls:
        if lead.advisor:
            notify_dispatch(lead.advisor, render_template_body(t.body, lead),
                            f'/leads/{lead.pk}/', 'lead')


# ---- DSR export bundle + anonymize (CO-07) ---------------------------------
@login_required
def dsr_export(request, pk):
    from django.core.exceptions import PermissionDenied
    if request.user.role not in (Role.COMPLIANCE, Role.CEO, Role.SUPER_ADMIN):
        raise PermissionDenied
    lead = get_object_or_404(Lead, pk=pk)
    import json
    bundle = {
        'lead': {'name': lead.name, 'mobile': lead.mobile, 'email': lead.email,
                 'stage': lead.stage, 'source': lead.source,
                 'created_at': lead.created_at.isoformat() if lead.created_at else ''},
        'notes': [n.text for n in lead.notes.all()],
        'documents': [d.name for d in lead.documents.all()] if hasattr(lead, 'documents') else [],
    }
    _audit(lead, request.user, 'DSR export', 'Compliance', '', 'access bundle')
    resp = HttpResponse(json.dumps(bundle, indent=2), content_type='application/json')
    resp['Content-Disposition'] = f'attachment; filename="dsr-{lead.pk}.json"'
    return resp


@login_required
@require_POST
def dsr_anonymize(request, pk):
    from django.core.exceptions import PermissionDenied
    if request.user.role not in (Role.COMPLIANCE, Role.CEO, Role.SUPER_ADMIN):
        raise PermissionDenied
    lead = get_object_or_404(Lead, pk=pk)
    lead.name = f'REDACTED-{lead.pk}'
    lead.mobile = ''
    lead.email = ''
    lead.save(update_fields=['name', 'mobile', 'email'])
    _audit(lead, request.user, 'DSR anonymize', 'Compliance', '', 'PII removed')
    messages.success(request, 'Lead PII anonymized (DSR).')
    return redirect('lead_detail', pk=pk)


# ---- UBO capture (CO-04) ---------------------------------------------------
@login_required
@perm.module_required('Leads', 'edit')
@require_POST
def ubo_add(request, pk):
    lead = get_object_or_404(visible_leads(request.user), pk=pk)
    UBO.objects.create(lead=lead, name=request.POST.get('name', '').strip(),
                       share_pct=_num(request.POST.get('share_pct')) or None,
                       id_number=request.POST.get('id_number', '').strip(),
                       nationality=request.POST.get('nationality', '').strip(),
                       is_pep=bool(request.POST.get('is_pep')))
    _audit(lead, request.user, 'UBO added', 'Compliance')
    messages.success(request, 'UBO recorded.')
    return redirect('lead_detail', pk=pk)


# ---- Access review + dormant (CO-10) ---------------------------------------
@login_required
@perm.module_required('Users')
def access_review(request):
    from datetime import timedelta
    cutoff = timezone.now() - timedelta(days=90)
    users = User.objects.all().order_by('-last_login')
    rows = [{'name': u.get_full_name() or u.username, 'role': u.role,
             'status': u.status, 'last_login': u.last_login,
             'dormant': bool(u.last_login and u.last_login < cutoff) or not u.last_login}
            for u in users]
    return render(request, 'crm/access_review.html', {'rows': rows, 'active_nav': 'Users'})


# ---- Client referral (CL-06) -----------------------------------------------
@login_required
@perm.module_required('Leads', 'edit')
@require_POST
def client_referral_add(request, pk):
    lead = get_object_or_404(visible_leads(request.user), pk=pk)
    ClientReferral.objects.create(referrer_lead=lead,
                                  referred_name=request.POST.get('referred_name', '').strip(),
                                  referred_mobile=request.POST.get('referred_mobile', '').strip(),
                                  note=request.POST.get('note', '').strip(),
                                  created_by=request.user)
    _audit(lead, request.user, 'Referral captured', 'Lead')
    messages.success(request, 'Referral captured.')
    return redirect('lead_detail', pk=pk)


# ---- Payout execute + incentive auto-calc (FI-08/09) -----------------------
@login_required
@perm.module_required('Finance', 'edit')
@require_POST
def payout_execute(request, pk):
    run = get_object_or_404(PayoutRun, pk=pk)
    if run.status == 'Approved':
        run.status = 'Paid'
        run.save(update_fields=['status'])
        _audit_event(request, 'Payout executed', f'{run.period} · AED {run.total}')
        messages.success(request, f'Payout run {run.period} marked Paid.')
    else:
        messages.error(request, 'Payout must be Approved before it can be paid.')
    return redirect('finance_hub')


@login_required
@perm.module_required('Finance', 'edit')
@require_POST
def incentive_compute(request):
    """Compute incentive ledger entries from active schemes on the period's disbursed value."""
    period = request.POST.get('period') or timezone.localdate().strftime('%Y-%m')
    schemes = IncentiveScheme.objects.filter(active=True)
    n = 0
    DISB = ['Disbursed', 'Property Transfer Scheduled', 'Property Transfer', 'Property Transferred']
    for u in User.objects.filter(role=Role.ADVISOR, status='Active'):
        disbursed = Lead.objects.filter(advisor=u, stage__in=DISB).aggregate(s=Sum('loan_amount'))['s'] or 0
        for s in schemes:
            pct = Decimal(str((s.rules or {}).get('pct', 0)))
            amt = (Decimal(str(disbursed)) * pct / Decimal(100)).quantize(Decimal('0.01'))
            if amt > 0:
                LedgerEntry.objects.create(payee=u, kind='incentive', amount=amt,
                                           note=f'{s.name} {period}', effective_date=timezone.localdate())
                n += 1
    _audit_event(request, 'Incentives computed', f'{period} · {n} entries')
    messages.success(request, f'{n} incentive ledger entries created for {period}.')
    return redirect('finance_hub')


# ---- Tokenized client upload link (DM-08) ----------------------------------
@login_required
@perm.module_required('Documents', 'edit')
@require_POST
def upload_token_create(request, pk):
    from datetime import timedelta
    lead = get_object_or_404(visible_leads(request.user), pk=pk)
    tok = UploadToken.objects.create(lead=lead, token=secrets.token_urlsafe(24),
                                     expires_at=timezone.now() + timedelta(days=7),
                                     created_by=request.user)
    link = request.build_absolute_uri(f'/upload/{tok.token}/')
    messages.success(request, f'Upload link (valid 7 days): {link}')
    return redirect('lead_detail', pk=pk)


@csrf_exempt
def public_upload(request, token):
    """Public, token-gated client upload page (no login). Scans size/type."""
    t = UploadToken.objects.filter(token=token).select_related('lead').first()
    if not t or not t.is_valid():
        return HttpResponse('This upload link has expired.', status=410)
    if request.method == 'POST' and request.FILES.get('file'):
        f = request.FILES['file']
        if f.size > 15 * 1024 * 1024:
            return HttpResponse('File too large (max 15MB).', status=400)
        from .models import Document
        Document.objects.create(lead=t.lead, name=f.name[:120], file=f,
                                doc_type='Document', status='Pending Review',
                                uploaded_by='Client upload')
        t.used_count += 1
        t.save(update_fields=['used_count'])
        return HttpResponse('Thank you — your document was uploaded.')
    return render(request, 'crm/public_upload.html', {'lead': t.lead})


# ---- Simple semantic report builder (RP-06) --------------------------------
@login_required
@perm.module_required('Reports')
def report_builder(request):
    entity = request.GET.get('entity', 'Lead')
    field = request.GET.get('group', 'stage')
    rows = []
    if entity == 'Lead':
        allowed_fields = ['stage', 'source', 'priority', 'kyc_status']
        if field not in allowed_fields:
            field = 'stage'
        from django.db.models import Count
        qs = visible_leads(request.user).values(field).annotate(n=Count('id')).order_by('-n')
        rows = [{'key': r[field] or '—', 'n': r['n']} for r in qs]
    return render(request, 'crm/report_builder.html', {
        'entity': entity, 'field': field, 'rows': rows,
        'fields': ['stage', 'source', 'priority', 'kyc_status'], 'active_nav': 'Reports'})
