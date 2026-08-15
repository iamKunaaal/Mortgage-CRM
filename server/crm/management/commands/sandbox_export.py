import json
from django.core.management.base import BaseCommand
from crm.models import Lead


class Command(BaseCommand):
    help = ('Masked-data sandbox export (AD-09): dump leads with PII masked, for seeding a '
            'training/sandbox environment. Prints JSON to stdout.')

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=500)

    def handle(self, *args, **opts):
        out = []
        for l in Lead.objects.filter(is_deleted=False)[:opts['limit']]:
            out.append({
                'name': (l.name[:1] + '***') if l.name else '',
                'mobile': ('+971*****' + (l.mobile[-2:] if l.mobile else '')),
                'email': ('***@' + l.email.split('@')[-1]) if '@' in (l.email or '') else '',
                'stage': l.stage, 'source': l.source, 'priority': l.priority,
                'loan_amount': float(l.loan_amount or 0),
            })
        self.stdout.write(json.dumps(out, indent=2))
        self.stderr.write(self.style.SUCCESS(f'Masked {len(out)} lead(s).'))
