from django.core.management.base import BaseCommand
from django.utils import timezone

from crm.models import FollowUp, Task, Notification


class Command(BaseCommand):
    help = ('Follow-up & task reminders: notify owners of follow-ups and tasks due today or overdue. '
            'Run daily on a schedule.')

    def handle(self, *args, **opts):
        today = timezone.localdate()

        # 1) Follow-up reminders (due today or overdue, not done, not already reminded)
        fu_n = 0
        fus = FollowUp.objects.filter(done=False, reminded=False, next_date__isnull=False,
                                      next_date__lte=today, lead__is_deleted=False).select_related('lead', 'lead__advisor')
        for fu in fus:
            owner = fu.lead.advisor or fu.created_by
            if owner:
                when = 'today' if fu.next_date == today else f'overdue since {fu.next_date:%d %b}'
                Notification.objects.create(
                    user=owner, category='task',
                    text=f'Follow-up {when}: "{fu.lead.name}" ({fu.channel})',
                    url=f'/leads/{fu.lead_id}/')
            fu.reminded = True
            fu.save(update_fields=['reminded'])
            fu_n += 1

        # 2) Task due-today reminders (open tasks with a due date of today)
        tk_n = 0
        for t in Task.objects.filter(is_deleted=False, due_date=today,
                                     assignee__isnull=False).exclude(
                                     status__in=['Completed', 'Cancelled']).select_related('lead', 'assignee'):
            Notification.objects.create(
                user=t.assignee, category='task',
                text=f'Task due today: {t.title}',
                url=f'/leads/{t.lead_id}/' if t.lead_id else '/my-day/')
            tk_n += 1

        self.stdout.write(self.style.SUCCESS(
            f'Reminders sent — {fu_n} follow-up(s), {tk_n} task(s) due today.'))
