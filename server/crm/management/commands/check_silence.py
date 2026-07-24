from django.core.management.base import BaseCommand

from crm.models import Lead, Notification, User, Role


class Command(BaseCommand):
    help = ('Ops silence escalation (PRD §12): warn the advisor at 3 days of no activity, '
            'escalate to the Team Leader + Operations Manager at 7 days. Run on a daily schedule.')

    def _notify(self, user, text, url):
        if user:
            Notification.objects.create(user=user, category='silence', text=text, url=url)

    def handle(self, *args, **opts):
        ops_mgrs = list(User.objects.filter(role=Role.OPS_MANAGER))
        warned = escalated = 0
        open_cases = Lead.objects.filter(is_deleted=False, is_draft=False).exclude(
            stage__in=['Disbursed', 'Property Transferred', 'Declined']).select_related('advisor', 'advisor__manager')
        for lead in open_cases:
            state = lead.silence_status          # 'active' / 'warn' / 'escalate' / 'closed'
            url = f'/leads/{lead.pk}/'
            if state == 'warn' and lead.silence_notified not in ('warn', 'escalate'):
                self._notify(lead.advisor, f'No activity for 3+ days — follow up "{lead.name}"', url)
                lead.silence_notified = 'warn'
                lead.save(update_fields=['silence_notified'])
                warned += 1
            elif state == 'escalate' and lead.silence_notified != 'escalate':
                adv = (lead.advisor.get_full_name() or lead.advisor.username) if lead.advisor else 'Unassigned'
                self._notify(lead.advisor, f'Silent 7+ days — escalate "{lead.name}"', url)
                # escalate up the hierarchy: the advisor's Team Leader + all Ops Managers
                mgr = lead.advisor.manager if lead.advisor else None
                if mgr:
                    self._notify(mgr, f'Escalation — "{lead.name}" ({adv}) silent 7+ days', url)
                for om in ops_mgrs:
                    self._notify(om, f'Escalation — "{lead.name}" ({adv}) silent 7+ days', url)
                lead.silence_notified = 'escalate'
                lead.save(update_fields=['silence_notified'])
                escalated += 1
        self.stdout.write(self.style.SUCCESS(
            f'Silence check done — {warned} warned, {escalated} escalated.'))
