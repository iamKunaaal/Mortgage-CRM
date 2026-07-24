from . import permissions as perm
from .models import Lead, Role


def crm_globals(request):
    """Expose role-aware module access + sidebar counts to every template."""
    user = getattr(request, 'user', None)
    allowed = []
    lead_count = 0
    if user and user.is_authenticated:
        allowed = [m for m in perm.MODULES if perm.can_access(user, m)]
        leads = Lead.objects.filter(is_deleted=False)
        if user.role == Role.ADVISOR:
            leads = leads.filter(advisor=user)
        lead_count = leads.count()
    is_ceo = bool(user and user.is_authenticated and user.role == Role.CEO)
    can_view_audit = perm.can_view_audit(user) if (user and user.is_authenticated) else False
    pending_approvals = 0
    if user and user.is_authenticated:
        try:
            from .models import ApprovalRequest
            pending_approvals = ApprovalRequest.objects.filter(
                status='Pending', approver_role=user.role).count()
        except Exception:
            pass
    from django.conf import settings
    esign_enabled = getattr(settings, 'ESIGN_ENABLED', False)
    unread_notifications = user.notifications.filter(read=False).count() \
        if (user and user.is_authenticated) else 0
    from .models import AppSetting
    company_name = ''
    try:
        cs = AppSetting.objects.filter(key='company').first()
        if cs and cs.value:
            company_name = cs.value.get('name', '')
    except Exception:
        pass
    return {'allowed': allowed, 'lead_count': lead_count, 'is_ceo': is_ceo,
            'can_view_audit': can_view_audit, 'pending_approvals': pending_approvals,
            'esign_enabled': esign_enabled, 'unread_notifications': unread_notifications,
            'company_name': company_name}
