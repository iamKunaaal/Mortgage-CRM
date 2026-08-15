from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Runs all scheduled background jobs in one go (for a single cron entry).'

    def handle(self, *args, **opts):
        jobs = ['check_sla', 'check_silence', 'check_doc_expiry',
                'check_integrity', 'check_nurture', 'check_buyouts', 'check_reminders',
                'check_receivables', 'check_ops_validity']
        for job in jobs:
            self.stdout.write(self.style.HTTP_INFO(f'→ {job}'))
            try:
                call_command(job)
            except Exception as ex:
                self.stderr.write(self.style.ERROR(f'  {job} failed: {ex}'))
        self.stdout.write(self.style.SUCCESS('All daily jobs finished.'))
