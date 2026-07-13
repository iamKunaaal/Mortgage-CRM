from django.contrib.auth.models import AbstractUser
from django.db import models


class Role(models.TextChoices):
    CEO = 'CEO', 'CEO / Managing Director'
    SALES_DIRECTOR = 'SALES_DIRECTOR', 'Sales Director'
    OPS_MANAGER = 'OPS_MANAGER', 'Mortgage Operations Manager'
    ADVISOR = 'ADVISOR', 'Mortgage Advisor'
    ACCOUNTANT = 'ACCOUNTANT', 'Accountant / Finance Officer'


class User(AbstractUser):
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.ADVISOR)
    phone = models.CharField(max_length=30, blank=True)
    department = models.CharField(max_length=40, blank=True)
    status = models.CharField(max_length=20, default='Active')
    # advisor monthly targets (assigned by admin at creation)
    target_calls = models.PositiveIntegerField(default=0)
    target_submissions = models.PositiveIntegerField(default=0)
    target_partners = models.PositiveIntegerField(default=0)
    target_disbursement = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    @property
    def role_label(self):
        return Role(self.role).label

    @property
    def initials(self):
        n = (self.get_full_name() or self.username).split()
        return ''.join(w[0] for w in n[:2]).upper() or self.username[:2].upper()

    def __str__(self):
        return self.get_full_name() or self.username


class Bank(models.Model):
    TYPE_CHOICES = [('Conventional', 'Conventional'), ('Islamic', 'Islamic')]
    name = models.CharField(max_length=120)
    bank_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default='Conventional')
    contact_person = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=20, default='Active')
    commission_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    notes = models.TextField(blank=True)

    def __str__(self):
        return self.name


class ReferralPartner(models.Model):
    name = models.CharField(max_length=120)
    company = models.CharField(max_length=160, blank=True)
    organization = models.CharField(max_length=160, blank=True)
    mobile = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    emirates_id = models.CharField(max_length=40, blank=True)
    passport_no = models.CharField(max_length=40, blank=True)
    bank_name = models.CharField(max_length=120, blank=True)
    account_no = models.CharField(max_length=60, blank=True)
    iban = models.CharField(max_length=60, blank=True)
    partner_type = models.CharField(max_length=60, default='Real Estate Agency')
    status = models.CharField(max_length=20, default='Active')
    agreement = models.FileField(upload_to='partners/', blank=True, null=True)
    kyc_doc = models.FileField(upload_to='partners/', blank=True, null=True)
    created_by = models.ForeignKey('User', on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='referral_partners')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


STAGES = [
    'Lead Received', 'Documents Pending', 'Documents Complete', 'Logged In',
    'Under Review', 'Pre-Approved', 'Valuation', 'Valuation Received',
    'FOL Initiated', 'FOL Issued', 'FOL Signing Fixed', 'FOL Signed',
    'Under Disbursement', 'Disbursed',
    'Property Transfer Scheduled', 'Property Transfer', 'Property Transferred',
    'Declined',
]
SOURCES = ['Google Ads', 'Meta Ads', 'Referral Partner', 'Website', 'Walk-in', 'Cold Calling']


class Lead(models.Model):
    PRIORITY = [('High', 'High'), ('Medium', 'Medium'), ('Low', 'Low')]
    STAGE_CHOICES = [(s, s) for s in STAGES]
    SOURCE_CHOICES = [(s, s) for s in SOURCES]

    name = models.CharField(max_length=120)
    mobile = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    nationality = models.CharField(max_length=60, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    # employment profile
    employer = models.CharField(max_length=160, blank=True)
    employment_type = models.CharField(max_length=40, blank=True)
    monthly_income = models.DecimalField(max_digits=14, decimal_places=2, default=0, null=True, blank=True)
    years_employment = models.DecimalField(max_digits=5, decimal_places=1, default=0, null=True, blank=True)
    industry = models.CharField(max_length=80, blank=True)
    company_name = models.CharField(max_length=160, blank=True)
    annual_turnover = models.DecimalField(max_digits=16, decimal_places=2, default=0, null=True, blank=True)
    business_years = models.DecimalField(max_digits=5, decimal_places=1, default=0, null=True, blank=True)
    property_value = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    property_type = models.CharField(max_length=60, blank=True)
    preferred_area = models.CharField(max_length=120, blank=True)
    ltv = models.PositiveIntegerField(default=80)
    loan_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    bank_notes = models.TextField(blank=True)
    advisor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='leads', limit_choices_to={'role': Role.ADVISOR})
    bank = models.ForeignKey(Bank, on_delete=models.SET_NULL, null=True, blank=True)
    source = models.CharField(max_length=40, choices=SOURCE_CHOICES, default='Website')
    stage = models.CharField(max_length=40, choices=STAGE_CHOICES, default='Lead Received')
    priority = models.CharField(max_length=10, choices=PRIORITY, default='Medium')
    lost_reason = models.CharField(max_length=80, blank=True)
    pipeline_month = models.CharField(max_length=20, blank=True)
    disbursed_at = models.DateField(null=True, blank=True)
    referral_partner = models.ForeignKey(ReferralPartner, on_delete=models.SET_NULL,
                                         null=True, blank=True, related_name='leads')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def is_lost(self):
        return self.stage == 'Declined'

    @property
    def initials(self):
        return ''.join(w[0] for w in self.name.split()[:2]).upper()

    def __str__(self):
        return self.name


class Task(models.Model):
    PRIORITY = [('High', 'High'), ('Medium', 'Medium'), ('Low', 'Low')]
    STATUS = [('Pending', 'Pending'), ('In Progress', 'In Progress'),
              ('Completed', 'Completed'), ('Cancelled', 'Cancelled')]
    TYPE = [('Documents', 'Documents'), ('Bank Follow-up', 'Bank Follow-up'),
            ('Valuation', 'Valuation'), ('Customer Call', 'Customer Call'),
            ('FOL', 'FOL'), ('Disbursement', 'Disbursement'), ('Application', 'Application')]

    title = models.CharField(max_length=160)
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, null=True, blank=True, related_name='tasks')
    assignee = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='tasks')
    task_type = models.CharField(max_length=30, choices=TYPE, default='Documents')
    priority = models.CharField(max_length=10, choices=PRIORITY, default='Medium')
    status = models.CharField(max_length=20, choices=STATUS, default='Pending')
    due_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title


class Document(models.Model):
    STATUS = [('Uploaded', 'Uploaded'), ('Pending Review', 'Pending Review'),
              ('Verified', 'Verified'), ('Rejected', 'Rejected'), ('Missing', 'Missing')]
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name='documents')
    doc_type = models.CharField(max_length=60)
    status = models.CharField(max_length=20, choices=STATUS, default='Pending Review')
    uploaded_by = models.CharField(max_length=60, default='Customer')
    verified_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    file = models.FileField(upload_to='documents/', blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.doc_type} · {self.lead.name}'


class Note(models.Model):
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name='notes')
    author = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'Note on {self.lead.name}'


class LeadSourceState(models.Model):
    name = models.CharField(max_length=40, unique=True)
    active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class RolePermission(models.Model):
    role = models.CharField(max_length=20, choices=Role.choices)
    module = models.CharField(max_length=40)
    level = models.CharField(max_length=20)

    class Meta:
        unique_together = ('role', 'module')

    def __str__(self):
        return f'{self.role} · {self.module} = {self.level}'


class AppSetting(models.Model):
    key = models.CharField(max_length=60, unique=True)
    value = models.JSONField(default=dict)

    def __str__(self):
        return self.key


class LeadAudit(models.Model):
    """Immutable record of who changed what on a lead, when."""
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name='audits')
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    action = models.CharField(max_length=60)          # e.g. 'Field updated', 'Stage changed'
    field = models.CharField(max_length=60, blank=True)
    old_value = models.CharField(max_length=255, blank=True)
    new_value = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.lead.name} · {self.action} · {self.field}'


class Customization(models.Model):
    """CEO-only revenue sheet row derived from a lead (see CRM Ref.xlsx)."""
    lead = models.OneToOneField(Lead, on_delete=models.CASCADE, related_name='customization')
    bank_rm = models.CharField(max_length=120, blank=True)   # Bank RM (col D)
    cp = models.CharField(max_length=120, blank=True)        # Channel Partner (col P)
    slab = models.DecimalField(max_digits=6, decimal_places=4, default=0)   # e.g. 0.01 = 1%
    broker_pct = models.DecimalField(max_digits=5, decimal_places=2, default=80)  # broker revenue %
    vat_override = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)  # None = auto 5%
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-added_at']

    # ---- derived revenue fields (mirror the reference sheet) ----
    @property
    def loan_amount(self):
        return float(self.lead.loan_amount or 0)

    @property
    def actual_revenue(self):          # J = Loan × Slab
        return self.loan_amount * float(self.slab or 0)

    @property
    def vat(self):                     # K = Actual × 5% (or manual override)
        if self.vat_override is not None:
            return float(self.vat_override)
        return self.actual_revenue * 0.05

    @property
    def with_vat(self):                # L = Actual + VAT
        return self.actual_revenue + self.vat

    @property
    def broker_revenue(self):          # M = Actual × broker%
        return self.actual_revenue * float(self.broker_pct or 0) / 100

    @property
    def broker_payout(self):           # N = Actual × (100 − broker%)
        return self.actual_revenue * (100 - float(self.broker_pct or 0)) / 100

    @property
    def final_revenue(self):           # O = Broker Revenue
        return self.broker_revenue

    def __str__(self):
        return f'Customization · {self.lead.name}'
