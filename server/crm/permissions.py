"""Role-based scoped permission matrix (mirrors the client's role table)."""
from functools import wraps
from django.core.exceptions import PermissionDenied
from .models import Role

# Modules used for navigation + access control
MODULES = ['Dashboard', 'Leads', 'Tasks', 'Banks', 'Advisors',
           'Referral Partners', 'Documents', 'Finance', 'Reports', 'Users', 'Settings']

# access[role][module] = level string
ACCESS = {
    Role.SUPER_ADMIN: {m: 'Full' for m in MODULES} | {'Dashboard': 'Yes'},
    Role.CEO: {m: 'Full' for m in MODULES} | {'Dashboard': 'Yes'},
    Role.SALES_DIRECTOR: {
        'Dashboard': 'Yes', 'Leads': 'View & Assign', 'Tasks': 'Full', 'Banks': 'View',
        'Advisors': 'Full', 'Referral Partners': 'View', 'Documents': 'View',
        'Finance': 'View', 'Reports': 'Sales Reports', 'Users': 'Limited', 'Settings': 'No',
    },
    Role.TEAM_LEADER: {
        'Dashboard': 'Yes', 'Leads': 'Team Leads', 'Tasks': 'Team Tasks', 'Banks': 'View',
        'Advisors': 'View', 'Referral Partners': 'View', 'Documents': 'View',
        'Finance': 'No', 'Reports': 'Team Reports', 'Users': 'No', 'Settings': 'No',
    },
    Role.OPS_MANAGER: {
        'Dashboard': 'Yes', 'Leads': 'View & Edit', 'Tasks': 'Full', 'Banks': 'Full',
        'Advisors': 'View', 'Referral Partners': 'View', 'Documents': 'Full',
        'Finance': 'View', 'Reports': 'Operations Reports', 'Users': 'No', 'Settings': 'No',
    },
    Role.OPS_EXECUTIVE: {
        'Dashboard': 'Yes', 'Leads': 'View & Edit', 'Tasks': 'Full', 'Banks': 'View',
        'Advisors': 'No', 'Referral Partners': 'View', 'Documents': 'Full',
        'Finance': 'No', 'Reports': 'Operations Reports', 'Users': 'No', 'Settings': 'No',
    },
    Role.ADVISOR: {
        'Dashboard': 'Yes', 'Leads': 'Own Leads Only', 'Tasks': 'Own Tasks', 'Banks': 'View',
        'Advisors': 'No', 'Referral Partners': 'View', 'Documents': 'Upload/Edit Own',
        'Finance': 'No', 'Reports': 'Own Reports', 'Users': 'No', 'Settings': 'No',
    },
    Role.TELECALLER: {
        'Dashboard': 'Yes', 'Leads': 'Own Leads Only', 'Tasks': 'Own Tasks', 'Banks': 'No',
        'Advisors': 'No', 'Referral Partners': 'View', 'Documents': 'No',
        'Finance': 'No', 'Reports': 'Own Reports', 'Users': 'No', 'Settings': 'No',
    },
    Role.ACCOUNTANT: {
        'Dashboard': 'Yes', 'Leads': 'View Only', 'Tasks': 'View', 'Banks': 'View',
        'Advisors': 'No', 'Referral Partners': 'View', 'Documents': 'View',
        'Finance': 'Full', 'Reports': 'Finance Reports', 'Users': 'No', 'Settings': 'No',
    },
    Role.HR_EXECUTIVE: {
        'Dashboard': 'Yes', 'Leads': 'No', 'Tasks': 'View', 'Banks': 'No',
        'Advisors': 'View', 'Referral Partners': 'No', 'Documents': 'No',
        'Finance': 'No', 'Reports': 'HR Reports', 'Users': 'Limited', 'Settings': 'No',
    },
    Role.COMPLIANCE: {
        'Dashboard': 'Yes', 'Leads': 'View Only', 'Tasks': 'View', 'Banks': 'View',
        'Advisors': 'View', 'Referral Partners': 'View', 'Documents': 'View',
        'Finance': 'View', 'Reports': 'Compliance Reports', 'Users': 'No', 'Settings': 'No',
    },
    Role.MARKETING: {
        'Dashboard': 'Yes', 'Leads': 'View Only', 'Tasks': 'Own Tasks', 'Banks': 'No',
        'Advisors': 'No', 'Referral Partners': 'View', 'Documents': 'No',
        'Finance': 'No', 'Reports': 'Marketing Reports', 'Users': 'No', 'Settings': 'No',
    },
    Role.AUDITOR: {
        'Dashboard': 'Yes', 'Leads': 'View Only', 'Tasks': 'View', 'Banks': 'View',
        'Advisors': 'View', 'Referral Partners': 'View', 'Documents': 'View',
        'Finance': 'View', 'Reports': 'All Reports', 'Users': 'No', 'Settings': 'No',
    },
}

# which sidebar nav key maps to which module
NAV = [
    ('dashboard', 'Dashboard', 'My Dashboard'),
    ('lead_list', 'Leads', 'Leads'),
    ('task_list', 'Tasks', 'Tasks'),
    ('bank_list', 'Banks', 'Banks'),
    ('advisor_list', 'Advisors', 'Advisors'),
    ('partner_list', 'Referral Partners', 'Referral Partners'),
    ('document_list', 'Documents', 'Documents'),
    ('user_list', 'Users', 'User Management'),
    ('role_list', 'Settings', 'Roles & Settings'),
]


def level(user, module):
    if not user.is_authenticated:
        return 'No'
    base = effective_access(user.role).get(module, 'No')
    # per-user override wins over the role matrix
    try:
        from .models import UserPermission
        ov = UserPermission.objects.filter(user=user, module=module).first()
        if ov:
            return ov.level
    except Exception:
        pass
    return base


def user_overrides(user):
    """Dict of {module: level} overrides set for this specific user."""
    try:
        from .models import UserPermission
        return {o.module: o.level for o in UserPermission.objects.filter(user=user)}
    except Exception:
        return {}


def effective_access(role):
    """Static matrix overridden by DB-stored per-role customisations."""
    from .models import RolePermission
    base = dict(ACCESS.get(role, {}))
    try:
        for rp in RolePermission.objects.filter(role=role):
            if rp.module in MODULES:
                base[rp.module] = rp.level
    except Exception:
        pass  # table missing during initial migrate
    return base


# field-level security: which sensitive field-groups a role must NOT see on a lead
HIDDEN_FIELDS = {
    Role.MARKETING: {'financials'},
    Role.HR_EXECUTIVE: {'financials', 'contact'},
    Role.TELECALLER: {'financials'},
}


def hidden_field_groups(user):
    """Set of sensitive field groups ('financials','contact') hidden for this user."""
    if not user.is_authenticated:
        return set()
    return set(HIDDEN_FIELDS.get(user.role, set()))


def can_access(user, module):
    return level(user, module) not in ('No', '')


def can_edit(user, module):
    lv = level(user, module)
    # own-scope (advisor) and team-scope (team leader) roles may edit their in-scope records
    return 'Own' in lv or 'Team' in lv or lv in ('Full', 'View & Edit', 'View & Assign',
                                                  'Upload/Edit Own', 'Limited', 'Yes')


def can_create(user, module):
    lv = level(user, module)
    return lv in ('Full', 'View & Edit', 'Own Leads Only', 'Upload/Edit Own', 'Limited')


def can_delete(user, module):
    # Delete is restricted to CEO only, regardless of module access level.
    return user.is_authenticated and user.role == Role.CEO


# PRD §16.1 — KYC "Passed" requires the Compliance Officer's action, not the advisor's.
KYC_ROLES = (Role.COMPLIANCE, Role.CEO, Role.SUPER_ADMIN)


def can_kyc(user):
    return user.is_authenticated and user.role in KYC_ROLES


# PRD §16.7 — audit trail is readable/exportable by Compliance and the External Auditor,
# in addition to system admins (Settings access).
AUDIT_ROLES = (Role.COMPLIANCE, Role.AUDITOR)


def can_view_audit(user):
    return user.is_authenticated and (user.role in AUDIT_ROLES or can_access(user, 'Settings'))


def is_own_scope(user, module):
    """True if the role only sees its own records for this module."""
    return 'Own' in level(user, module)


def is_team_scope(user, module):
    """True if the role sees its team's records (e.g. Team Leader)."""
    return 'Team' in level(user, module)


def team_member_ids(user):
    """User + everyone reporting to them (direct reports). Used for Team scope."""
    ids = {user.id}
    try:
        ids |= set(user.reports.values_list('id', flat=True))
    except Exception:
        pass
    return ids


def active_grantors(user):
    """Users who have delegated their access to `user` in a live date window."""
    try:
        from .models import Delegation
        from django.utils import timezone as _tz
        today = _tz.localdate()
        return [d.grantor for d in Delegation.objects.filter(
            delegate=user, active=True, starts__lte=today, ends__gte=today).select_related('grantor')]
    except Exception:
        return []


def module_required(module, action='access'):
    """View decorator enforcing module access."""
    checks = {'access': can_access, 'edit': can_edit, 'create': can_create, 'delete': can_delete}

    def deco(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if not checks[action](request.user, module):
                raise PermissionDenied(f"Your role can't {action} {module}.")
            return view(request, *args, **kwargs)
        return wrapper
    return deco
