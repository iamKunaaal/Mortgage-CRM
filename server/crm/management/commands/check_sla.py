from django.core.management.base import BaseCommand
from django.utils import timezone

from crm.models import Lead, Notification, User, Role


class Command(BaseCommand):
    help = 'Notify advisors + management of first-contact SLA breaches (run on a schedule/cron).'

    def handle(self, *args, **opts):
        now = timezone.now()
        # PRD §17.2 — warn at 80% of the SLA elapsed, before breach
        warn_open = Lead.objects.filter(is_deleted=False, is_draft=False,
                                        first_contacted_at__isnull=True, sla_warned=False,
                                        sla_notified=False, first_contact_due__gte=now)
        warned = 0
        for lead in warn_open.select_related('advisor'):
            total = (lead.first_contact_due - lead.created_at).total_seconds()
            elapsed = (now - lead.created_at).total_seconds()
            if total > 0 and elapsed / total >= 0.8 and lead.advisor:
                Notification.objects.create(
                    user=lead.advisor, category='sla',
                    text=f'SLA warning — contact "{lead.name}" soon (80% of time used)',
                    url=f'/leads/{lead.pk}/')
                lead.sla_warned = True
                lead.save(update_fields=['sla_warned'])
                warned += 1

        breached = Lead.objects.filter(is_deleted=False, is_draft=False,
                                       first_contacted_at__isnull=True,
                                       first_contact_due__lt=now, sla_notified=False)
        # PRD §9.5: escalate to the advisor's Team Leader at breach + Sales Director/CEO oversight
        managers = list(User.objects.filter(role__in=[Role.CEO, Role.SALES_DIRECTOR]))
        n = 0
        for lead in breached.select_related('advisor', 'advisor__manager'):
            if lead.advisor:
                Notification.objects.create(
                    user=lead.advisor, category='sla',
                    text=f'SLA breached — "{lead.name}" not contacted in time',
                    url=f'/leads/{lead.pk}/')
            adv_name = (lead.advisor.get_full_name() or lead.advisor.username) if lead.advisor else 'Unassigned'
            recipients = list(managers)
            if lead.advisor and lead.advisor.manager:   # the advisor's Team Leader
                recipients.append(lead.advisor.manager)
            seen = set()
            for mgr in recipients:
                if mgr.pk in seen or (lead.advisor and mgr.pk == lead.advisor.pk):
                    continue
                seen.add(mgr.pk)
                Notification.objects.create(
                    user=mgr, category='sla',
                    text=f'SLA breached — "{lead.name}" ({adv_name}) not contacted in time',
                    url=f'/leads/{lead.pk}/')
            lead.sla_notified = True
            lead.save(update_fields=['sla_notified'])
            n += 1
        self.stdout.write(self.style.SUCCESS(
            f'SLA check done — {warned} warned (80%), {n} breach(es) escalated.'))
