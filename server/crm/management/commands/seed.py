import random
from django.core.management.base import BaseCommand
from crm.models import User, Bank, Lead, Task, ReferralPartner, Document, Role, STAGES, SOURCES


class Command(BaseCommand):
    help = 'Seed demo users (one per role), banks, leads, tasks, partners, documents.'

    def handle(self, *args, **opts):
        # --- users: one per role, password = "password123" ---
        people = [
            ('ceo', 'Kunal', 'Darji', Role.CEO, 'Management'),
            ('director', 'Sara', 'Lopez', Role.SALES_DIRECTOR, 'Sales'),
            ('ops', 'Priya', 'Nair', Role.OPS_MANAGER, 'Operations'),
            ('advisor', 'Fatima', 'Rahman', Role.ADVISOR, 'Sales'),
            ('accountant', 'Marcus', 'Lee', Role.ACCOUNTANT, 'Finance'),
            ('advisor2', 'Daniel', 'Roy', Role.ADVISOR, 'Sales'),
        ]
        users = {}
        for uname, fn, ln, role, dept in people:
            u, created = User.objects.get_or_create(username=uname, defaults={
                'first_name': fn, 'last_name': ln, 'role': role, 'department': dept,
                'email': f'{uname}@mortgagecrm.ae', 'phone': '+971 50 000 0000', 'status': 'Active',
            })
            u.role = role; u.first_name = fn; u.last_name = ln; u.department = dept
            if role == Role.ADVISOR:
                u.target_calls = 2200; u.target_submissions = 24
                u.target_partners = 10; u.target_disbursement = 2500000
            if uname == 'ceo':
                u.is_staff = True; u.is_superuser = True
            u.set_password('password123')
            u.save()
            users[uname] = u
        self.stdout.write(self.style.SUCCESS('Users ready (login password: password123)'))

        # --- banks ---
        banks = []
        for n, t in [('Emirates NBD', 'Conventional'), ('FAB', 'Conventional'), ('ADCB', 'Conventional'),
                     ('Mashreq', 'Conventional'), ('Dubai Islamic Bank', 'Islamic'), ('HSBC', 'Conventional')]:
            b, _ = Bank.objects.get_or_create(name=n, defaults={'bank_type': t, 'contact_person': 'Bank Desk'})
            banks.append(b)

        # --- referral partners ---
        for n, c in [('Provident RE', 'Provident Real Estate'), ('Betterhomes', 'Betterhomes LLC'),
                     ('Allsopp & Allsopp', 'Allsopp & Allsopp Group')]:
            ReferralPartner.objects.get_or_create(name=n, defaults={
                'company': c, 'organization': c, 'mobile': '+971 4 123 4567',
                'email': f'{n.split()[0].lower()}@partner.ae', 'emirates_id': '784-0000-0000000-0',
                'passport_no': 'A1234567', 'bank_name': 'Emirates NBD', 'account_no': '01234567890',
                'iban': 'AE000000000000000000000', 'partner_type': 'Real Estate Agency'})

        # --- leads ---
        names = ['Omar Khalil', 'Aisha Verma', 'Marcus Client', 'Rania Saeed', 'Sanjay Mehta',
                 'Leila Haddad', 'Karan Shah', 'Nora Aziz', 'Hassan Qadir', 'Elena Petrova',
                 'David Chen', 'Mariam Noor', 'Tom Becker', 'Sofia Rossi', 'Yusuf Ali', 'Anita Desai']
        advisors = [users['advisor'], users['advisor2']]
        active_stages = STAGES[:14]
        if Lead.objects.count() < len(names):
            for i, nm in enumerate(names):
                pv = (8 + (i * 7) % 40) * 100000
                ltv = random.choice([75, 80, 85])
                Lead.objects.create(
                    name=nm, mobile=f'+971 50 {100+i} {4000+i}', email=f'{nm.split()[0].lower()}@mail.com',
                    nationality=random.choice(['UAE', 'India', 'UK', 'Egypt']),
                    property_value=pv, ltv=ltv, loan_amount=round(pv * ltv / 100),
                    advisor=advisors[i % 2], bank=random.choice(banks),
                    source=random.choice(SOURCES), stage=active_stages[i % len(active_stages)],
                    priority=random.choice(['High', 'Medium', 'Low']))

        # --- tasks ---
        titles = ['Collect Salary Certificate', 'Book Property Valuation', 'Follow-up With Bank',
                  'Submit Bank Application', 'Call Customer', 'Request Missing Documents']
        if Task.objects.count() < 10:
            for i, lead in enumerate(Lead.objects.all()[:10]):
                Task.objects.create(title=random.choice(titles), lead=lead, assignee=lead.advisor,
                                    task_type=random.choice(['Documents', 'Bank Follow-up', 'Valuation', 'Customer Call']),
                                    priority=random.choice(['High', 'Medium', 'Low']),
                                    status=random.choice(['Pending', 'In Progress', 'Completed']))

        # --- documents ---
        if Document.objects.count() < 10:
            for lead in Lead.objects.all()[:8]:
                for dt in ['Passport', 'Emirates ID', 'Salary Certificate']:
                    Document.objects.create(lead=lead, doc_type=dt,
                                            status=random.choice(['Verified', 'Pending Review', 'Missing']))

        self.stdout.write(self.style.SUCCESS('Seed complete. Visit / and log in.'))
        self.stdout.write('Logins (password123): ceo, director, ops, advisor, accountant')
