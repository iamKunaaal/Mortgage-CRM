from django.core.management.base import BaseCommand
from django.utils import timezone

from crm.models import Lead, Task, Notification


class Command(BaseCommand):
    help = ('Nurture reactivation (PRD §9.6/LM-16): when a nurtured lead reaches its reactivation '
            'date, auto-create a follow-up task, notify the advisor, and clear the nurture flag.')

    def handle(self, *args, **opts):
        today = timezone.localdate()
        due = Lead.objects.filter(is_deleted=False, nurture_until__isnull=False,
                                  nurture_until__lte=today).select_related('advisor')
        n = 0
        for lead in due:
            if lead.advisor:
                Task.objects.create(title=f'Re-engage (nurture) — {lead.name}', lead=lead,
                                    assignee=lead.advisor, task_type='Customer Call',
                                    priority='Medium', status='Pending', due_date=today)
                Notification.objects.create(user=lead.advisor, category='lead',
                                            text=f'Nurtured lead "{lead.name}" is due for re-engagement',
                                            url=f'/leads/{lead.pk}/')
            lead.nurture_until = None
            lead.save(update_fields=['nurture_until'])
            n += 1
        self.stdout.write(self.style.SUCCESS(f'Nurture check done — {n} lead(s) reactivated.'))
