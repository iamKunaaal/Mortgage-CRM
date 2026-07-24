from django.core.management.base import BaseCommand
from django.utils import timezone

from crm.models import Document, Notification


class Command(BaseCommand):
    help = 'Notify advisors of documents expiring in 30/14/7 days or already expired (run on a schedule).'

    def handle(self, *args, **opts):
        today = timezone.localdate()
        docs = Document.objects.filter(expiry_date__isnull=False).select_related('lead', 'lead__advisor')
        sent = 0
        for d in docs:
            if not d.lead.advisor:
                continue
            days = (d.expiry_date - today).days
            # decide threshold; only notify once per threshold crossing
            threshold = None
            if days < 0:
                threshold = 'exp'
            elif days <= 7:
                threshold = '7'
            elif days <= 14:
                threshold = '14'
            elif days <= 30:
                threshold = '30'
            if threshold and d.expiry_notified != threshold:
                label = 'has expired' if threshold == 'exp' else f'expires in {days} day(s)'
                Notification.objects.create(
                    user=d.lead.advisor, category='document',
                    text=f'Document "{d.name or d.doc_type}" for {d.lead.name} {label}',
                    url=f'/leads/{d.lead.pk}/')
                d.expiry_notified = threshold
                d.save(update_fields=['expiry_notified'])
                sent += 1
        self.stdout.write(self.style.SUCCESS(f'Doc expiry check done — {sent} alert(s) sent.'))
