import datetime
from django.core.management.base import BaseCommand
from django.utils import timezone

from crm.models import Lead, Task, Notification, User, Role


class Command(BaseCommand):
    help = ('Nightly integrity check (PRD §9.6/LM-12): every open lead must carry a future task. '
            'Flags violators to the advisor and their Team Leader.')

    def handle(self, *args, **opts):
        today = timezone.localdate()
        TERMINAL = ['Disbursed', 'Property Transferred', 'Declined']
        open_leads = Lead.objects.filter(is_deleted=False, is_draft=False,
                                         nurture_until__isnull=True).exclude(stage__in=TERMINAL) \
            .select_related('advisor', 'advisor__manager')
        flagged = 0
        for lead in open_leads:
            has_future = Task.objects.filter(lead=lead, is_deleted=False,
                                             due_date__gte=today).exclude(
                                             status__in=['Completed', 'Cancelled']).exists()
            if has_future:
                continue
            if lead.advisor:
                Notification.objects.create(
                    user=lead.advisor, category='task',
                    text=f'Integrity: "{lead.name}" has no future task — schedule the next action',
                    url=f'/leads/{lead.pk}/')
                if lead.advisor.manager:
                    Notification.objects.create(
                        user=lead.advisor.manager, category='task',
                        text=f'Integrity: "{lead.name}" ({lead.advisor.get_full_name() or lead.advisor.username}) has no future task',
                        url=f'/leads/{lead.pk}/')
            flagged += 1
        self.stdout.write(self.style.SUCCESS(f'Integrity check done — {flagged} lead(s) without a future task.'))
