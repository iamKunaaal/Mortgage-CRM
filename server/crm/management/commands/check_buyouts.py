import datetime
from django.core.management.base import BaseCommand
from django.utils import timezone

from crm.models import Lead, Notification, generate_case_number


class Command(BaseCommand):
    help = ('Buyout engine (PRD §13.4): for every disbursed case whose fixed-rate period ends in '
            '~120 days, auto-create a buyout lead so we win the refinance before a competitor. Run daily.')

    def handle(self, *args, **opts):
        today = timezone.localdate()
        window = today + datetime.timedelta(days=120)
        DISB = ['Disbursed', 'Property Transferred', 'Property Transfer Scheduled', 'Property Transfer']
        src = Lead.objects.filter(is_deleted=False, stage__in=DISB, fol_rate_type='Fixed',
                                  fol_fixed_period_end__isnull=False,
                                  fol_fixed_period_end__lte=window,
                                  fol_fixed_period_end__gte=today)
        created = 0
        for case in src.select_related('advisor', 'client'):
            tag = f'Buyout — {case.name}'
            # avoid duplicates: one open buyout lead per source case
            if Lead.objects.filter(name=tag, is_deleted=False).exclude(stage='Declined').exists():
                continue
            b = Lead.objects.create(
                name=tag, mobile=case.mobile, email=case.email, nationality=case.nationality,
                client=case.client, advisor=case.advisor, source='Referral Partner',
                loan_amount=case.loan_amount, property_value=case.property_value,
                stage='Lead Received', priority='High',
                bank_notes=f'Auto buyout — fixed period ends {case.fol_fixed_period_end}',
                case_number=generate_case_number())
            if case.advisor:
                Notification.objects.create(
                    user=case.advisor, category='lead',
                    text=f'Buyout opportunity — {case.name} fixed rate ends {case.fol_fixed_period_end}',
                    url=f'/leads/{b.pk}/')
            created += 1
        self.stdout.write(self.style.SUCCESS(f'Buyout engine done — {created} buyout lead(s) created.'))
