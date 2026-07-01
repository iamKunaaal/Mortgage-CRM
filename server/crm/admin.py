from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User, Bank, ReferralPartner, Lead, Task, Document


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    list_display = ('username', 'get_full_name', 'role', 'status', 'is_staff')
    list_filter = ('role', 'status', 'is_staff')
    fieldsets = UserAdmin.fieldsets + (
        ('CRM Profile', {'fields': ('role', 'phone', 'department', 'status',
                                    'target_calls', 'target_submissions',
                                    'target_partners', 'target_disbursement')}),
    )


admin.site.register(Bank)
admin.site.register(ReferralPartner)
admin.site.register(Lead)
admin.site.register(Task)
admin.site.register(Document)
