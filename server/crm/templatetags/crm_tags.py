from django import template
from .. import permissions as perm

register = template.Library()


@register.filter
def can(user, module):
    """{% if user|can:'Leads' %} → True if role may access the module."""
    return perm.can_access(user, module)
