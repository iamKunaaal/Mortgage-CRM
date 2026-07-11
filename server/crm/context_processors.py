from . import permissions as perm
from .models import Lead, Role


def crm_globals(request):
    """Expose role-aware module access + sidebar counts to every template."""
    user = getattr(request, 'user', None)
    allowed = []
    lead_count = 0
    if user and user.is_authenticated:
        allowed = [m for m in perm.MODULES if perm.can_access(user, m)]
        leads = Lead.objects.all()
        if user.role == Role.ADVISOR:
            leads = leads.filter(advisor=user)
        lead_count = leads.count()
    is_ceo = bool(user and user.is_authenticated and user.role == Role.CEO)
    return {'allowed': allowed, 'lead_count': lead_count, 'is_ceo': is_ceo}
