from django.core.management.base import BaseCommand
from django.utils import timezone

from crm.models import BuyoutRecord, Notification


class Command(BaseCommand):
    help = 'Ops validity alerts: warn when a buyout liability letter is about to expire (OPS-10).'

    def handle(self, *args, **opts):
        today = timezone.localdate()
        n = 0
        for b in BuyoutRecord.objects.filter(liability_valid_until__isnull=False
                                              ).select_related('lead', 'lead__advisor'):
            days = (b.liability_valid_until - today).days
            if days in (7, 3, 0) and b.lead and b.lead.advisor:
                Notification.objects.create(
                    user=b.lead.advisor, category='task',
                    text=f'Liability letter for "{b.lead.name}" expires in {days}d',
                    url=f'/leads/{b.lead_id}/')
                n += 1
        self.stdout.write(self.style.SUCCESS(f'Ops validity alerts: {n}'))
