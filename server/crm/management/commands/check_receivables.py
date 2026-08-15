from django.core.management.base import BaseCommand
from django.utils import timezone

from crm.models import Invoice, Notification, User, Role


class Command(BaseCommand):
    help = 'Receivables aging: remind finance of sent invoices with an outstanding balance (FI-05).'

    def handle(self, *args, **opts):
        today = timezone.localdate()
        finance_users = list(User.objects.filter(role__in=[Role.ACCOUNTANT, Role.CEO], status='Active'))
        n = 0
        for inv in Invoice.objects.filter(status__in=['Sent', 'Part-Paid']).select_related('lead'):
            if inv.balance <= 0:
                continue
            days = (today - inv.issued_at).days if inv.issued_at else 0
            if days in (7, 14, 30) or (days > 30 and days % 30 == 0):
                for u in finance_users:
                    Notification.objects.create(
                        user=u, category='approval',
                        text=f'Invoice {inv.number} outstanding AED {inv.balance:.0f} ({days}d)',
                        url='/finance-hub/')
                    n += 1
        self.stdout.write(self.style.SUCCESS(f'Receivables reminders: {n}'))
