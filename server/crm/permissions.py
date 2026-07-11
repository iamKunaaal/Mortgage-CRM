"""Role-based scoped permission matrix (mirrors the client's role table)."""
from functools import wraps
from django.core.exceptions import PermissionDenied
from .models import Role

# Modules used for navigation + access control
MODULES = ['Dashboard', 'Leads', 'Tasks', 'Banks', 'Advisors',
           'Referral Partners', 'Documents', 'Finance', 'Reports', 'Users', 'Settings']

# access[role][module] = level string
ACCESS = {
    Role.CEO: {m: 'Full' for m in MODULES} | {'Dashboard': 'Yes'},
    Role.SALES_DIRECTOR: {
        'Dashboard': 'Yes', 'Leads': 'View & Assign', 'Tasks': 'Full', 'Banks': 'View',
        'Advisors': 'Full', 'Referral Partners': 'View', 'Documents': 'View',
        'Finance': 'View', 'Reports': 'Sales Reports', 'Users': 'Limited', 'Settings': 'No',
    },
    Role.OPS_MANAGER: {
        'Dashboard': 'Yes', 'Leads': 'View & Edit', 'Tasks': 'Full', 'Banks': 'Full',
        'Advisors': 'View', 'Referral Partners': 'View', 'Documents': 'Full',
        'Finance': 'View', 'Reports': 'Operations Reports', 'Users': 'No', 'Settings': 'No',
    },
    Role.ADVISOR: {
        'Dashboard': 'Yes', 'Leads': 'Own Leads Only', 'Tasks': 'Own Tasks', 'Banks': 'View',
        'Advisors': 'No', 'Referral Partners': 'View', 'Documents': 'Upload/Edit Own',
        'Finance': 'No', 'Reports': 'Own Reports', 'Users': 'No', 'Settings': 'No',
    },
    Role.ACCOUNTANT: {
        'Dashboard': 'Yes', 'Leads': 'View Only', 'Tasks': 'View', 'Banks': 'View',
        'Advisors': 'No', 'Referral Partners': 'View', 'Documents': 'View',
        'Finance': 'Full', 'Reports': 'Finance Reports', 'Users': 'No', 'Settings': 'No',
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
    return effective_access(user.role).get(module, 'No')


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


def can_access(user, module):
    return level(user, module) not in ('No', '')


def can_edit(user, module):
    lv = level(user, module)
    return lv in ('Full', 'View & Edit', 'View & Assign', 'Upload/Edit Own', 'Limited', 'Yes')


def can_create(user, module):
    lv = level(user, module)
    return lv in ('Full', 'View & Edit', 'Own Leads Only', 'Upload/Edit Own', 'Limited')


def can_delete(user, module):
    # Delete is restricted to CEO only, regardless of module access level.
    return user.is_authenticated and user.role == Role.CEO


def is_own_scope(user, module):
    """True if the role only sees its own records for this module."""
    return 'Own' in level(user, module)


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
