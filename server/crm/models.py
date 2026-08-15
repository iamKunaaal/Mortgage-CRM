from decimal import Decimal
from django.contrib.auth.models import AbstractUser
from django.db import models


class Role(models.TextChoices):
    SUPER_ADMIN = 'SUPER_ADMIN', 'Super Admin (IT)'
    CEO = 'CEO', 'CEO / Managing Director'
    SALES_DIRECTOR = 'SALES_DIRECTOR', 'Sales Director'
    TEAM_LEADER = 'TEAM_LEADER', 'Sales Team Leader'
    OPS_MANAGER = 'OPS_MANAGER', 'Mortgage Operations Manager'
    OPS_EXECUTIVE = 'OPS_EXECUTIVE', 'Operations Executive'
    ADVISOR = 'ADVISOR', 'Mortgage Advisor'
    TELECALLER = 'TELECALLER', 'Telecaller / Pre-Sales'
    ACCOUNTANT = 'ACCOUNTANT', 'Accountant / Finance Officer'
    HR_EXECUTIVE = 'HR_EXECUTIVE', 'HR Executive'
    COMPLIANCE = 'COMPLIANCE', 'Compliance Officer'
    MARKETING = 'MARKETING', 'Marketing Executive'
    AUDITOR = 'AUDITOR', 'External Auditor (Read-only)'


class User(AbstractUser):
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.ADVISOR)
    phone = models.CharField(max_length=30, blank=True)
    department = models.CharField(max_length=40, blank=True)
    status = models.CharField(max_length=20, default='Active')
    # reporting hierarchy — a Team Leader sees their reports' cases (Team scope)
    manager = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='reports')
    team = models.CharField(max_length=60, blank=True)
    # assignment engine controls (PRD §9.5)
    daily_lead_cap = models.PositiveIntegerField(default=0)   # 0 = no cap
    out_of_office = models.BooleanField(default=False)
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
    advisor = models.ForeignKey('User', on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='assigned_partners',
                                limit_choices_to={'role': 'ADVISOR'})   # relationship owner
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


class CaseSequence(models.Model):
    """Per-year atomic counter for gap-free, concurrency-safe case numbers."""
    year = models.PositiveIntegerField(unique=True)
    last_seq = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f'{self.year}: {self.last_seq}'


def generate_case_number():
    """Sequential per-year case number, e.g. BITAR-2026-0007.
    Concurrency-safe: the counter row is locked for the duration of the transaction."""
    from django.utils import timezone as _tz
    from django.db import transaction
    year = _tz.now().year
    with transaction.atomic():
        counter, _ = CaseSequence.objects.select_for_update().get_or_create(year=year)
        counter.last_seq += 1
        counter.save(update_fields=['last_seq'])
        seq = counter.last_seq
    # configurable prefix/padding (PRD §22.5) — Settings > numbering
    prefix, pad = 'BITAR', 4
    try:
        row = AppSetting.objects.filter(key='numbering').first()
        if row and isinstance(row.value, dict):
            prefix = (row.value.get('case_prefix') or prefix).strip('-') or prefix
            pad = int(row.value.get('case_pad') or pad)
    except Exception:
        pass
    return f'{prefix}-{year}-{seq:0{pad}d}'


def business_config():
    """Business-hours calendar (PRD §17.4). Defaults: Mon–Fri 9:00–18:00, UAE holidays list."""
    cfg = {'days': [0, 1, 2, 3, 4], 'start': 9, 'end': 18, 'holidays': []}
    try:
        row = AppSetting.objects.filter(key='sla_calendar').first()
        if row and isinstance(row.value, dict):
            for k in ('days', 'start', 'end', 'holidays'):
                if row.value.get(k) not in (None, ''):
                    cfg[k] = row.value[k]
    except Exception:
        pass
    return cfg


def _is_working_day(d, cfg):
    return d.weekday() in cfg['days'] and d.isoformat() not in set(cfg['holidays'])


def add_business_minutes(start, minutes):
    """Return the datetime `minutes` of working time after `start`, honouring the calendar."""
    import datetime as _dt
    from django.utils import timezone as _tz
    cfg = business_config()
    cur = _tz.localtime(start)
    remaining = int(minutes)
    guard = 0
    while remaining > 0 and guard < 100000:
        guard += 1
        day_start = cur.replace(hour=cfg['start'], minute=0, second=0, microsecond=0)
        day_end = cur.replace(hour=cfg['end'], minute=0, second=0, microsecond=0)
        if not _is_working_day(cur.date(), cfg) or cur >= day_end:
            nxt = (cur + _dt.timedelta(days=1)).replace(hour=cfg['start'], minute=0, second=0, microsecond=0)
            cur = nxt
            continue
        if cur < day_start:
            cur = day_start
        avail = int((day_end - cur).total_seconds() // 60)
        if remaining <= avail:
            cur = cur + _dt.timedelta(minutes=remaining)
            remaining = 0
        else:
            remaining -= avail
            cur = (cur + _dt.timedelta(days=1)).replace(hour=cfg['start'], minute=0, second=0, microsecond=0)
    return cur


def business_days_between(start, end):
    """Count whole working days elapsed between two datetimes (for silence rules)."""
    import datetime as _dt
    from django.utils import timezone as _tz
    if end <= start:
        return 0
    cfg = business_config()
    d = _tz.localtime(start).date()
    last = _tz.localtime(end).date()
    n = 0
    while d < last:
        d += _dt.timedelta(days=1)
        if _is_working_day(d, cfg):
            n += 1
    return n


class Client(models.Model):
    """The person (or couple) — permanent anchor of the 360 view and lifecycle (PRD §10.1, §13.1).
    One client accumulates many cases (leads) over the years."""
    LIFECYCLE = [('Lead', 'Lead'), ('Applicant', 'Applicant'), ('Active Client', 'Active Client'),
                 ('Closed Client', 'Closed Client'), ('Advocate', 'Advocate')]
    name = models.CharField(max_length=120)
    mobile = models.CharField(max_length=30, blank=True, db_index=True)
    email = models.EmailField(blank=True, db_index=True)
    nationality = models.CharField(max_length=60, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    employer = models.CharField(max_length=160, blank=True)
    lifecycle = models.CharField(max_length=20, choices=LIFECYCLE, default='Lead')
    # consent per channel (mirrors the lead's, kept at the person level)
    consent_call = models.BooleanField(default=False)
    consent_sms = models.BooleanField(default=False)
    consent_whatsapp = models.BooleanField(default=False)
    consent_email = models.BooleanField(default=False)
    do_not_contact = models.BooleanField(default=False)   # global override (PRD §16.4)
    owner = models.ForeignKey('User', on_delete=models.SET_NULL, null=True, blank=True,
                              related_name='clients')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    @property
    def initials(self):
        return ''.join(w[0] for w in self.name.split()[:2]).upper() or self.name[:2].upper()

    def __str__(self):
        return self.name


class Lead(models.Model):
    PRIORITY = [('High', 'High'), ('Medium', 'Medium'), ('Low', 'Low')]
    STAGE_CHOICES = [(s, s) for s in STAGES]
    SOURCE_CHOICES = [(s, s) for s in SOURCES]

    case_number = models.CharField(max_length=30, blank=True, db_index=True)
    # a case belongs to one client (person); the client anchors the 360 + lifecycle (PRD §10.1)
    client = models.ForeignKey(Client, on_delete=models.SET_NULL, null=True, blank=True,
                               related_name='cases')
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
    custom = models.JSONField(default=dict, blank=True)   # AD-05 custom-field values
    advisor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='leads', limit_choices_to={'role': Role.ADVISOR})
    bank = models.ForeignKey(Bank, on_delete=models.SET_NULL, null=True, blank=True)
    source = models.CharField(max_length=40, choices=SOURCE_CHOICES, default='Website')
    stage = models.CharField(max_length=40, choices=STAGE_CHOICES, default='Lead Received')
    priority = models.CharField(max_length=10, choices=PRIORITY, default='Medium')
    lost_reason = models.CharField(max_length=80, blank=True)
    pipeline_month = models.CharField(max_length=20, blank=True)
    disbursed_at = models.DateField(null=True, blank=True)
    is_draft = models.BooleanField(default=False)
    referral_partner = models.ForeignKey(ReferralPartner, on_delete=models.SET_NULL,
                                         null=True, blank=True, related_name='leads')
    # consent capture (per channel)
    consent_call = models.BooleanField(default=False)
    consent_sms = models.BooleanField(default=False)
    consent_whatsapp = models.BooleanField(default=False)
    consent_email = models.BooleanField(default=False)
    # rule-based lead score (0-100), computed on save
    score = models.PositiveIntegerField(default=0)
    # KYC gate — must be Passed before submitting to a bank
    KYC_STATUS = [('Pending', 'Pending'), ('Passed', 'Passed'), ('Rejected', 'Rejected')]
    kyc_status = models.CharField(max_length=10, choices=KYC_STATUS, default='Pending')
    # AML compliance (PRD §16.2) — screening + risk rating + EDD
    SCREEN = [('Pending', 'Pending'), ('Clear', 'Clear'), ('Hit', 'Hit')]
    RISK = [('Low', 'Low'), ('Medium', 'Medium'), ('High', 'High')]
    sanctions_status = models.CharField(max_length=10, choices=SCREEN, default='Pending')
    pep_status = models.CharField(max_length=10, choices=SCREEN, default='Pending')
    screening_evidence = models.FileField(upload_to='screening/', blank=True, null=True)
    screened_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='screened_leads')
    screened_at = models.DateTimeField(null=True, blank=True)
    risk_rating = models.CharField(max_length=10, choices=RISK, blank=True)
    risk_note = models.CharField(max_length=255, blank=True)
    edd_required = models.BooleanField(default=False)   # true when High risk
    edd_complete = models.BooleanField(default=False)   # source of funds/wealth + senior sign-off done
    # structured EDD (PRD §16.2)
    edd_source_of_funds = models.TextField(blank=True)
    edd_source_of_wealth = models.TextField(blank=True)
    edd_ceo_ack = models.BooleanField(default=False)    # CEO acknowledgment for High risk
    # KYC override (PRD §16.1 — Compliance-only, reason-mandatory, time-boxed, follow-up task)
    kyc_override = models.BooleanField(default=False)
    kyc_override_reason = models.CharField(max_length=255, blank=True)
    kyc_override_until = models.DateField(null=True, blank=True)
    kyc_override_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                        related_name='kyc_overrides')
    # instant eligibility check result (computed on save)
    eligible = models.BooleanField(null=True, blank=True)
    eligibility_note = models.CharField(max_length=255, blank=True)
    # first-contact SLA
    first_contact_due = models.DateTimeField(null=True, blank=True)
    first_contacted_at = models.DateTimeField(null=True, blank=True)
    sla_notified = models.BooleanField(default=False)       # breach notified
    sla_warned = models.BooleanField(default=False)         # 80%-elapsed warning notified (PRD §17.2)
    # nurture pool (PRD §9.6) — real but not now; auto-reactivates on this date
    nurture_until = models.DateField(null=True, blank=True)
    # ops silence escalation — last threshold notified: '' / 'warn' / 'escalate' (PRD §12)
    silence_notified = models.CharField(max_length=10, blank=True)
    # operations workflow (PRD §11–12): handover gate + dual ownership + hold
    handed_over = models.BooleanField(default=False)
    handover_at = models.DateTimeField(null=True, blank=True)
    handover_score = models.PositiveIntegerField(default=0)   # completeness % at handover (rework report)
    ops_owner = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name='ops_cases')   # process owner (banks/valuation/transfer)
    ops_hold = models.BooleanField(default=False)
    hold_reason = models.CharField(max_length=200, blank=True)
    hold_review_date = models.DateField(null=True, blank=True)
    rework_flag = models.BooleanField(default=False)   # set on doc rejection / handover bounce (PRD §11.2)
    # pre-approval capture (PRD §11.6)
    preapproval_validity_end = models.DateField(null=True, blank=True)
    # valuation subflow (PRD §11.7)
    valuer_name = models.CharField(max_length=120, blank=True)
    valuation_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    valuation_date = models.DateField(null=True, blank=True)
    valuation_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    # FOL capture (PRD §11.8) — fixed_period_end powers the buyout engine (§13.4)
    FOL_RATE = [('', '—'), ('Fixed', 'Fixed'), ('Variable', 'Variable')]
    fol_rate_type = models.CharField(max_length=10, choices=FOL_RATE, blank=True)
    fol_fixed_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    fol_fixed_period_end = models.DateField(null=True, blank=True)
    fol_variable_index = models.CharField(max_length=60, blank=True)   # e.g. "EIBOR + 1.5%"
    fol_tenor_years = models.PositiveIntegerField(default=0)
    fol_emi = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    fol_processing_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    fol_offer_validity = models.DateField(null=True, blank=True)
    # signing & insurance (PRD §11.9)
    insurance_provider = models.CharField(max_length=120, blank=True)
    insurance_policy_no = models.CharField(max_length=80, blank=True)
    # transfer & deed (PRD §11.13)
    title_deed_number = models.CharField(max_length=80, blank=True)
    # soft delete (recycle bin) — nothing is ever hard-deleted
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='deleted_leads')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def sla_status(self):
        """first-contact SLA state: contacted / breached / due-soon / on-track / none."""
        if self.first_contacted_at:
            return 'contacted'
        if not self.first_contact_due:
            return 'none'
        from django.utils import timezone as _tz
        now = _tz.now()
        if now > self.first_contact_due:
            return 'breached'
        total = (self.first_contact_due - self.created_at).total_seconds() or 1
        left = (self.first_contact_due - now).total_seconds()
        return 'due-soon' if left / total <= 0.25 else 'on-track'

    def compute_score(self):
        """Simple, admin-tunable-later rule-based score (0-100)."""
        s = 0
        if self.priority == 'High':
            s += 30
        elif self.priority == 'Medium':
            s += 15
        loan = float(self.loan_amount or 0)
        if loan >= 2_000_000:
            s += 30
        elif loan >= 1_000_000:
            s += 20
        elif loan > 0:
            s += 10
        if self.source in ('Referral Partner', 'Website'):
            s += 15
        if self.mobile:
            s += 5
        if self.email:
            s += 5
        if float(self.monthly_income or 0) > 0 or float(self.annual_turnover or 0) > 0:
            s += 10
        return min(100, s)

    @property
    def last_activity_at(self):
        """Most recent touch: last follow-up, else lead update time."""
        fu = self.followups.first()  # ordered -created_at
        return fu.created_at if fu else self.updated_at

    @property
    def silence_status(self):
        """Ops silence rule: warn after 3 days, escalate after 7 days of no activity.
        Only applies to open cases (not Disbursed / Declined / drafts)."""
        if self.is_draft or self.stage in ('Disbursed', 'Property Transferred', 'Declined'):
            return 'closed'
        if self.ops_hold:
            return 'closed'          # PRD §17.4 — clock pauses while On Hold
        from django.utils import timezone as _tz
        days = business_days_between(self.last_activity_at, _tz.now())   # working days (PRD §12)
        if days >= 7:
            return 'escalate'
        if days >= 3:
            return 'warn'
        return 'active'

    @property
    def is_lost(self):
        return self.stage == 'Declined'

    @property
    def initials(self):
        return ''.join(w[0] for w in self.name.split()[:2]).upper()

    def __str__(self):
        return self.name


class MetaLead(models.Model):
    """Staging record for a lead captured from Meta Lead Ads. Kept SEPARATE from the main
    Lead pipeline so Meta leads don't appear in All Leads/dashboards — they're reviewed on the
    'Meta Leads' page and (later) converted into a real Lead manually."""
    leadgen_id = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=200, blank=True)
    mobile = models.CharField(max_length=60, blank=True)
    email = models.CharField(max_length=254, blank=True)
    campaign = models.CharField(max_length=200, blank=True)
    form_id = models.CharField(max_length=64, blank=True)
    data = models.JSONField(null=True, blank=True)       # exact question -> answer, as submitted
    created_at = models.DateTimeField(auto_now_add=True)
    converted_lead = models.ForeignKey('Lead', on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name='+')

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.name or self.leadgen_id


class Task(models.Model):
    PRIORITY = [('High', 'High'), ('Medium', 'Medium'), ('Low', 'Low')]
    STATUS = [('Pending', 'Pending'), ('In Progress', 'In Progress'),
              ('Completed', 'Completed'), ('Cancelled', 'Cancelled')]
    TYPE = [('Documents', 'Documents'), ('Bank Follow-up', 'Bank Follow-up'),
            ('Valuation', 'Valuation'), ('Customer Call', 'Customer Call'), ('Follow-up', 'Follow-up'),
            ('FOL', 'FOL'), ('Disbursement', 'Disbursement'), ('Application', 'Application')]

    title = models.CharField(max_length=160)
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, null=True, blank=True, related_name='tasks')
    assignee = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='tasks')
    task_type = models.CharField(max_length=30, choices=TYPE, default='Documents')
    priority = models.CharField(max_length=10, choices=PRIORITY, default='Medium')
    status = models.CharField(max_length=20, choices=STATUS, default='Pending')
    due_date = models.DateField(null=True, blank=True)
    outcome = models.CharField(max_length=200, blank=True)   # captured on completion
    completed_at = models.DateTimeField(null=True, blank=True)
    # soft delete (PRD §16.7 — no hard deletes anywhere)
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='deleted_tasks')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title


class Document(models.Model):
    STATUS = [('Uploaded', 'Uploaded'), ('Pending Review', 'Pending Review'),
              ('Verified', 'Verified'), ('Rejected', 'Rejected'), ('Missing', 'Missing')]
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name='documents')
    name = models.CharField(max_length=160, blank=True)
    doc_type = models.CharField(max_length=60)
    status = models.CharField(max_length=20, choices=STATUS, default='Pending Review')
    uploaded_by = models.CharField(max_length=60, default='Customer')
    verified_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    rejection_reason = models.CharField(max_length=200, blank=True)   # reason code on reject (PRD §11.2)
    file = models.FileField(upload_to='documents/', blank=True, null=True)
    issue_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    expiry_notified = models.CharField(max_length=10, blank=True)   # last threshold notified: '30'/'14'/'7'/'exp'
    version = models.PositiveIntegerField(default=1)
    is_current = models.BooleanField(default=True)   # False = superseded by a newer upload
    # soft delete (PRD §16.7 — no hard deletes anywhere)
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='deleted_documents')
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def expiry_status(self):
        """valid / expiring / expired / none (based on expiry_date)."""
        if not self.expiry_date:
            return 'none'
        from django.utils import timezone as _tz
        days = (self.expiry_date - _tz.localdate()).days
        if days < 0:
            return 'expired'
        if days <= 30:
            return 'expiring'
        return 'valid'

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


class FollowUp(models.Model):
    """A logged follow-up on a case, with an optional next-follow-up date (ops workflow)."""
    CHANNEL = [('Call', 'Call'), ('WhatsApp', 'WhatsApp'), ('Email', 'Email'),
               ('SMS', 'SMS'), ('Meeting', 'Meeting'), ('Other', 'Other')]
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name='followups')
    channel = models.CharField(max_length=20, choices=CHANNEL, default='Call')
    note = models.TextField(blank=True)
    next_date = models.DateField(null=True, blank=True)
    done = models.BooleanField(default=False)
    reminded = models.BooleanField(default=False)   # due-date reminder already sent
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='followups')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'Follow-up ({self.channel}) · {self.lead.name}'


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


class UserPermission(models.Model):
    """Per-user override of the role matrix for a single module (field/module-level security)."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='permission_overrides')
    module = models.CharField(max_length=40)
    level = models.CharField(max_length=20)

    class Meta:
        unique_together = ('user', 'module')

    def __str__(self):
        return f'{self.user} · {self.module} = {self.level}'


class Delegation(models.Model):
    """Temporary access delegation — delegate inherits grantor's data scope for a date window."""
    grantor = models.ForeignKey(User, on_delete=models.CASCADE, related_name='delegations_given')
    delegate = models.ForeignKey(User, on_delete=models.CASCADE, related_name='delegations_received')
    starts = models.DateField()
    ends = models.DateField()
    note = models.CharField(max_length=200, blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    @property
    def is_live(self):
        from django.utils import timezone as _tz
        today = _tz.localdate()
        return self.active and self.starts <= today <= self.ends

    def __str__(self):
        return f'{self.grantor} → {self.delegate} ({self.starts}–{self.ends})'


class AppSetting(models.Model):
    key = models.CharField(max_length=60, unique=True)
    value = models.JSONField(default=dict)

    def __str__(self):
        return self.key


class AuditEvent(models.Model):
    """System-wide audit events not tied to a single lead (logins, exports, config, deletes)."""
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    action = models.CharField(max_length=80)
    detail = models.CharField(max_length=255, blank=True)
    ip = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        # append-only (PRD §16.7 / §8.2): existing audit rows can never be modified
        if self.pk is not None:
            raise PermissionError('Audit events are append-only and cannot be modified.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError('Audit events cannot be deleted — nobody can.')

    def __str__(self):
        return f'{self.user} · {self.action}'


class Notification(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    text = models.CharField(max_length=240)
    url = models.CharField(max_length=200, blank=True)
    category = models.CharField(max_length=40, blank=True)
    read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.user} · {self.text[:40]}'


class CallLog(models.Model):
    """A prospecting call an advisor makes to a NEW lead (not a pipeline lead)."""
    OUTCOME = [('Interested', 'Interested'), ('Not Interested', 'Not Interested'),
               ('No Answer', 'No Answer'), ('Callback', 'Callback'), ('Busy', 'Busy')]
    advisor = models.ForeignKey(User, on_delete=models.CASCADE, related_name='call_logs')
    name = models.CharField(max_length=120, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    outcome = models.CharField(max_length=20, choices=OUTCOME, default='No Answer')
    note = models.TextField(blank=True)
    lead = models.ForeignKey(Lead, on_delete=models.SET_NULL, null=True, blank=True,
                             related_name='call_logs')
    follow_up_date = models.DateField(null=True, blank=True)   # next follow-up from this call (PRD)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'Call by {self.advisor} · {self.outcome}'


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

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise PermissionError('Audit records are append-only and cannot be modified.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError('Audit records cannot be deleted.')

    def __str__(self):
        return f'{self.lead.name} · {self.action} · {self.field}'


class ApprovalRequest(models.Model):
    """Shared approval framework (PRD §17.5): creator ≠ approver, decision log.
    Used for partner activation, fee waivers, payout runs, leave, month reopen, etc."""
    TYPE = [('Partner Activation', 'Partner Activation'), ('Fee Waiver', 'Fee Waiver'),
            ('Payout Run', 'Payout Run'), ('Leave', 'Leave'), ('Month Reopen', 'Month Reopen'),
            ('Template Publish', 'Template Publish'), ('Lost-Reason Override', 'Lost-Reason Override')]
    STATUS = [('Pending', 'Pending'), ('Approved', 'Approved'), ('Rejected', 'Rejected')]
    request_type = models.CharField(max_length=30, choices=TYPE)
    title = models.CharField(max_length=200)
    detail = models.CharField(max_length=255, blank=True)
    link = models.CharField(max_length=200, blank=True)         # where to review the target
    target_model = models.CharField(max_length=40, blank=True)  # e.g. 'ReferralPartner'
    target_id = models.PositiveIntegerField(null=True, blank=True)
    approver_role = models.CharField(max_length=20, blank=True)  # role expected to decide
    status = models.CharField(max_length=10, choices=STATUS, default='Pending')
    requested_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='approval_requests')
    decided_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='approval_decisions')
    comment = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.request_type}: {self.title} ({self.status})'


class ConsentRecord(models.Model):
    """Audit-grade consent event per channel (PRD §16.4): status, source, timestamp, capturer."""
    CHANNEL = [('Call', 'Call'), ('SMS', 'SMS'), ('WhatsApp', 'WhatsApp'), ('Email', 'Email')]
    SOURCE = [('Lead form', 'Lead form'), ('Verbal', 'Verbal'), ('Web form', 'Web form'),
              ('Import', 'Import'), ('Unsubscribe', 'Unsubscribe'), ('Manual', 'Manual')]
    client = models.ForeignKey(Client, on_delete=models.CASCADE, null=True, blank=True, related_name='consent_log')
    lead = models.ForeignKey(Lead, on_delete=models.SET_NULL, null=True, blank=True, related_name='consent_log')
    channel = models.CharField(max_length=12, choices=CHANNEL)
    granted = models.BooleanField(default=True)
    source = models.CharField(max_length=20, choices=SOURCE, default='Manual')
    captured_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.channel} {"granted" if self.granted else "withdrawn"} · {self.source}'


class SuspicionFlag(models.Model):
    """Confidential AML suspicion flag (PRD §16.6). Visible only to Compliance (CEO: existence-only)."""
    STATUS = [('Open', 'Open'), ('Investigating', 'Investigating'), ('Resolved', 'Resolved')]
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, null=True, blank=True, related_name='suspicions')
    client = models.ForeignKey(Client, on_delete=models.CASCADE, null=True, blank=True, related_name='suspicions')
    raised_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name='raised_suspicions')
    reason = models.TextField()
    status = models.CharField(max_length=15, choices=STATUS, default='Open')
    resolution = models.TextField(blank=True)
    goaml_ref = models.CharField(max_length=80, blank=True)   # metadata only if filed
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'Suspicion #{self.pk} ({self.status})'


class DataSubjectRequest(models.Model):
    """PDPL data subject request (PRD §16.5) — Compliance-owned."""
    TYPE = [('Access', 'Access'), ('Correction', 'Correction'), ('Deletion', 'Deletion')]
    STATUS = [('Open', 'Open'), ('In Progress', 'In Progress'), ('Completed', 'Completed'), ('Rejected', 'Rejected')]
    client = models.ForeignKey(Client, on_delete=models.SET_NULL, null=True, blank=True, related_name='dsrs')
    subject_name = models.CharField(max_length=120)
    request_type = models.CharField(max_length=15, choices=TYPE, default='Access')
    status = models.CharField(max_length=15, choices=STATUS, default='Open')
    detail = models.TextField(blank=True)
    raised_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name='raised_dsrs')
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'DSR {self.request_type} · {self.subject_name}'


class Customization(models.Model):
    """CEO-only revenue sheet row derived from a lead (see CRM Ref.xlsx)."""
    lead = models.OneToOneField(Lead, on_delete=models.CASCADE, related_name='customization')
    bank_rm = models.CharField(max_length=120, blank=True)   # Bank RM (col D)
    cp = models.CharField(max_length=120, blank=True)        # Channel Partner (col P)
    slab = models.DecimalField(max_digits=6, decimal_places=4, default=0)   # e.g. 0.01 = 1%
    broker_pct = models.DecimalField(max_digits=5, decimal_places=2, default=80)  # broker revenue % (of loan)
    broker_slab = models.DecimalField(max_digits=6, decimal_places=2, default=0)  # broker payout % (of broker revenue)
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
    def broker_revenue(self):          # Broker Revenue = Loan Amount × broker%
        return self.loan_amount * float(self.broker_pct or 0) / 100

    @property
    def broker_payout(self):           # Broker Payout = Broker Revenue × broker slab%
        return self.broker_revenue * float(self.broker_slab or 0) / 100

    @property
    def final_revenue(self):           # Final Revenue = Actual Revenue − Broker Payout
        return self.actual_revenue - self.broker_payout

    def __str__(self):
        return f'Customization · {self.lead.name}'


class BankQuery(models.Model):
    """A bank query raised on an application (PRD §11.5). Blocks nothing but shows prominently."""
    OWNER_SIDE = [('Advisor', 'Advisor'), ('Ops', 'Ops')]
    STATUS = [('Open', 'Open'), ('Answered', 'Answered'), ('Closed', 'Closed')]
    lead = models.ForeignKey('Lead', on_delete=models.CASCADE, related_name='bank_queries')
    application = models.ForeignKey('BankApplication', on_delete=models.CASCADE, null=True, blank=True,
                                   related_name='queries')
    query_type = models.CharField(max_length=60, blank=True)
    description = models.TextField()
    owner_side = models.CharField(max_length=10, choices=OWNER_SIDE, default='Ops')
    due_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS, default='Open')
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'Query · {self.lead.name} ({self.status})'


class BankApplication(models.Model):
    """One bank submission for a lead/case. A lead can have several in parallel."""
    STATUS = [
        ('Draft', 'Draft'), ('Submitted', 'Submitted'), ('Under Review', 'Under Review'),
        ('Pre-Approved', 'Pre-Approved'), ('Approved', 'Approved'),
        ('Rejected', 'Rejected'), ('Withdrawn', 'Withdrawn'),
    ]
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name='bank_applications')
    bank = models.ForeignKey(Bank, on_delete=models.SET_NULL, null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS, default='Draft')
    reference_no = models.CharField(max_length=60, blank=True)
    requested_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    sanctioned_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    interest_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    rejection_reason = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    decision_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='bank_applications')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.bank} · {self.lead.name} ({self.status})'


class AssignmentRule(models.Model):
    """Ordered lead-assignment rules (PRD §9.5): match on source/loan-size, then act."""
    ACTION = [('round_robin', 'Round-robin (least loaded)'), ('user', 'Specific user')]
    order = models.PositiveIntegerField(default=0)
    name = models.CharField(max_length=120)
    match_source = models.CharField(max_length=40, blank=True)       # blank = any source
    min_loan = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    max_loan = models.DecimalField(max_digits=14, decimal_places=2, default=0)  # 0 = no upper bound
    action = models.CharField(max_length=20, choices=ACTION, default='round_robin')
    action_user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='assignment_rules')
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ['order', 'id']

    def __str__(self):
        return f'{self.order}. {self.name}'


class SavedView(models.Model):
    """A saved list filter (PRD §RP-04) — personal or shared."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='saved_views')
    module = models.CharField(max_length=40, default='Leads')
    name = models.CharField(max_length=120)
    querystring = models.CharField(max_length=500, blank=True)
    shared = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f'{self.module}: {self.name}'


class NotificationPref(models.Model):
    """Per-user notification category mute (PRD §NA-04). Mandatory floor categories can't be muted."""
    MANDATORY = {'sla', 'compliance', 'approval'}   # cannot be muted
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notif_prefs')
    category = models.CharField(max_length=40)
    muted = models.BooleanField(default=False)

    class Meta:
        unique_together = ('user', 'category')

    def __str__(self):
        return f'{self.user} · {self.category} {"muted" if self.muted else "on"}'


# ==========================================================================
# PHASE 2 MODELS — all additive; nothing here is a hard dependency of the
# core CRM. Features read config at runtime and degrade gracefully.
# ==========================================================================

# ---- Finance (FI-01,03-11) -------------------------------------------------
class Invoice(models.Model):
    STATUS = [('Draft', 'Draft'), ('Sent', 'Sent'), ('Part-Paid', 'Part-Paid'),
              ('Paid', 'Paid'), ('Credited', 'Credited'), ('Void', 'Void')]
    number = models.CharField(max_length=30, blank=True, db_index=True)
    lead = models.ForeignKey(Lead, on_delete=models.SET_NULL, null=True, blank=True,
                             related_name='invoices')
    client_name = models.CharField(max_length=200, blank=True)
    trn = models.CharField(max_length=30, blank=True)
    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    vat = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    status = models.CharField(max_length=12, choices=STATUS, default='Draft')
    locked = models.BooleanField(default=False)              # true once Sent (FI-04)
    notes = models.CharField(max_length=255, blank=True)
    issued_at = models.DateField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    @property
    def paid_amount(self):
        return sum((r.amount for r in self.receipts.all()), Decimal('0'))

    @property
    def balance(self):
        return (self.total or Decimal('0')) - self.paid_amount

    def __str__(self):
        return self.number or f'INV#{self.pk}'


class CreditNote(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='credit_notes')
    number = models.CharField(max_length=30, blank=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    reason = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.number or f'CN#{self.pk}'


class Receipt(models.Model):
    METHOD = [('Bank Transfer', 'Bank Transfer'), ('Cheque', 'Cheque'),
              ('Cash', 'Cash'), ('Card', 'Card'), ('Other', 'Other')]
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='receipts')
    amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    method = models.CharField(max_length=20, choices=METHOD, default='Bank Transfer')
    reference = models.CharField(max_length=120, blank=True)
    received_at = models.DateField(null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-received_at', '-created_at']

    def __str__(self):
        return f'Receipt {self.amount} on {self.invoice}'


class LedgerEntry(models.Model):
    """Shared money spine for commission / incentive / clawback / variance (FI-07/09, PM-06)."""
    KIND = [('commission', 'Commission'), ('incentive', 'Incentive'),
            ('clawback', 'Clawback'), ('variance', 'Variance'), ('adjustment', 'Adjustment')]
    payee = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                              related_name='ledger_entries')
    partner = models.ForeignKey(ReferralPartner, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='ledger_entries')
    kind = models.CharField(max_length=20, choices=KIND, default='commission')
    amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    lead = models.ForeignKey(Lead, on_delete=models.SET_NULL, null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)
    effective_date = models.DateField(null=True, blank=True)
    payout_line = models.ForeignKey('PayoutLine', on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='ledger_entries')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-effective_date', '-created_at']

    def __str__(self):
        return f'{self.kind} {self.amount}'


class PayoutRun(models.Model):
    STATUS = [('Draft', 'Draft'), ('Pending Approval', 'Pending Approval'),
              ('Approved', 'Approved'), ('Paid', 'Paid'), ('Cancelled', 'Cancelled')]
    period = models.CharField(max_length=7)                  # YYYY-MM
    status = models.CharField(max_length=20, choices=STATUS, default='Draft')
    total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    approval = models.ForeignKey(ApprovalRequest, on_delete=models.SET_NULL, null=True, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-period', '-created_at']

    def __str__(self):
        return f'Payout {self.period} ({self.status})'


class PayoutLine(models.Model):
    run = models.ForeignKey(PayoutRun, on_delete=models.CASCADE, related_name='lines')
    payee_user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    payee_partner = models.ForeignKey(ReferralPartner, on_delete=models.SET_NULL, null=True, blank=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    reference = models.CharField(max_length=120, blank=True)

    def __str__(self):
        return f'{self.amount} to {self.payee_user or self.payee_partner}'


class IncentiveScheme(models.Model):
    """Per-employee incentive rules (FI-09). rules JSON is interpreted by the finance engine."""
    name = models.CharField(max_length=120)
    rules = models.JSONField(default=dict, blank=True)
    active = models.BooleanField(default=True)
    effective_from = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class MonthLock(models.Model):
    """Finance month-end lock with CEO reopen (FI-11)."""
    period = models.CharField(max_length=7, unique=True)     # YYYY-MM
    locked = models.BooleanField(default=True)
    locked_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    locked_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.period} {"locked" if self.locked else "open"}'


# ---- Operations subflows (OPS-08,10,11,12) ---------------------------------
class ValuationRecord(models.Model):
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name='valuations')
    bank = models.ForeignKey(Bank, on_delete=models.SET_NULL, null=True, blank=True)
    valued_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    purchase_price = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    valued_on = models.DateField(null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def shortfall(self):
        if self.purchase_price and self.valued_amount:
            return max(self.purchase_price - self.valued_amount, Decimal('0'))
        return Decimal('0')

    def __str__(self):
        return f'Valuation {self.valued_amount} for {self.lead_id}'


class BuyoutRecord(models.Model):
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name='buyouts')
    current_bank = models.CharField(max_length=120, blank=True)
    liability_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    liability_letter_date = models.DateField(null=True, blank=True)
    liability_valid_until = models.DateField(null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'Buyout for {self.lead_id}'


class NOCRecord(models.Model):
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name='nocs')
    developer = models.CharField(max_length=160, blank=True)
    fee = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    requested_on = models.DateField(null=True, blank=True)
    received_on = models.DateField(null=True, blank=True)
    receipt_ref = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'NOC for {self.lead_id}'


class TransferBooking(models.Model):
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name='transfers')
    trustee_office = models.CharField(max_length=160, blank=True)
    booked_for = models.DateField(null=True, blank=True)
    cheques = models.JSONField(default=list, blank=True)     # [{payee, amount, bank, no}]
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'Transfer for {self.lead_id}'


# ---- Channel Partners depth (PM-02,06,07) ----------------------------------
class PartnerCommissionModel(models.Model):
    partner = models.ForeignKey(ReferralPartner, on_delete=models.CASCADE, related_name='commission_models')
    model = models.JSONField(default=dict, blank=True)       # {type: pct|slab|flat, value, ...}
    effective_from = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-effective_from']

    def __str__(self):
        return f'Commission model for {self.partner_id}'


class PartnerStatement(models.Model):
    partner = models.ForeignKey(ReferralPartner, on_delete=models.CASCADE, related_name='statements')
    period = models.CharField(max_length=7)                  # YYYY-MM
    lines = models.JSONField(default=list, blank=True)
    total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-period']
        unique_together = ('partner', 'period')

    def __str__(self):
        return f'{self.partner_id} statement {self.period}'


# ---- Automation (NA-05,06) -------------------------------------------------
class AutomationRule(models.Model):
    name = models.CharField(max_length=120)
    trigger = models.CharField(max_length=60)                # event key, e.g. 'lead.stage_changed'
    conditions = models.JSONField(default=list, blank=True)  # [{field, op, value}]
    actions = models.JSONField(default=list, blank=True)     # [{type, ...}]
    active = models.BooleanField(default=True)
    run_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class AutomationRun(models.Model):
    rule = models.ForeignKey(AutomationRule, on_delete=models.CASCADE, related_name='runs')
    target_model = models.CharField(max_length=40, blank=True)
    target_id = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(max_length=20, default='ok')   # ok|error|simulated|skipped
    log = models.CharField(max_length=500, blank=True)
    simulated = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


# ---- HR (HR-02..09) --------------------------------------------------------
class Attendance(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='attendances')
    date = models.DateField(db_index=True)
    check_in = models.DateTimeField(null=True, blank=True)
    check_out = models.DateTimeField(null=True, blank=True)
    geo = models.CharField(max_length=120, blank=True)       # "lat,lng"
    selfie = models.CharField(max_length=255, blank=True)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        unique_together = ('user', 'date')
        ordering = ['-date']

    def __str__(self):
        return f'{self.user} {self.date}'


class LeaveType(models.Model):
    name = models.CharField(max_length=60)
    days_per_year = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class LeaveRequest(models.Model):
    STATUS = [('Pending', 'Pending'), ('Approved', 'Approved'), ('Rejected', 'Rejected')]
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='leave_requests')
    leave_type = models.ForeignKey(LeaveType, on_delete=models.SET_NULL, null=True, blank=True)
    start = models.DateField()
    end = models.DateField()
    reason = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=10, choices=STATUS, default='Pending')
    approval = models.ForeignKey(ApprovalRequest, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.user} leave {self.start}..{self.end}'


class Target(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='targets')
    metric = models.CharField(max_length=40)                 # e.g. 'disbursed_value', 'leads'
    period = models.CharField(max_length=7)                  # YYYY-MM
    target_value = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    class Meta:
        unique_together = ('user', 'metric', 'period')

    def __str__(self):
        return f'{self.user} {self.metric} {self.period}'


# ---- Compliance / Admin depth (CO-08, AD-05) -------------------------------
class RetentionPolicy(models.Model):
    record_class = models.CharField(max_length=60, unique=True)   # e.g. 'Lead', 'Document'
    years = models.PositiveIntegerField(default=7)                # OD-6 default
    active = models.BooleanField(default=True)

    def __str__(self):
        return f'{self.record_class}: {self.years}y'


class CustomField(models.Model):
    TYPES = [('text', 'Text'), ('number', 'Number'), ('date', 'Date'),
             ('select', 'Select'), ('checkbox', 'Checkbox')]
    model = models.CharField(max_length=40)                  # e.g. 'Lead'
    key = models.CharField(max_length=40)
    label = models.CharField(max_length=120)
    field_type = models.CharField(max_length=12, choices=TYPES, default='text')
    options = models.JSONField(default=list, blank=True)
    role_visibility = models.JSONField(default=list, blank=True)  # roles that can see; [] = all
    active = models.BooleanField(default=True)

    class Meta:
        unique_together = ('model', 'key')

    def __str__(self):
        return f'{self.model}.{self.key}'


# ==========================================================================
# PHASE 2 — scaffolded items completion
# ==========================================================================
class MessageTemplate(models.Model):
    """Template studio + milestone messaging + doc templates (AD-07, CL-03, DM-09)."""
    KIND = [('inapp', 'In-app'), ('email', 'Email'), ('whatsapp', 'WhatsApp'), ('doc', 'Document')]
    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=12, choices=KIND, default='inapp')
    subject = models.CharField(max_length=200, blank=True)
    body = models.TextField(blank=True)                      # supports {{name}}, {{case}}, {{stage}}
    milestone_stage = models.CharField(max_length=40, blank=True)   # auto-send when a case hits this stage
    auto_send = models.BooleanField(default=False)
    published = models.BooleanField(default=False)
    approval = models.ForeignKey(ApprovalRequest, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class UBO(models.Model):
    """Ultimate beneficial owner for corporate borrowers (CO-04)."""
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name='ubos')
    name = models.CharField(max_length=160)
    share_pct = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    id_number = models.CharField(max_length=60, blank=True)
    nationality = models.CharField(max_length=60, blank=True)
    is_pep = models.BooleanField(default=False)
    screened = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.name} ({self.share_pct}%)'


class ClientReferral(models.Model):
    """Client referral capture + advocacy (CL-06)."""
    referrer_lead = models.ForeignKey(Lead, on_delete=models.SET_NULL, null=True, blank=True,
                                      related_name='referrals_made')
    referred_name = models.CharField(max_length=160)
    referred_mobile = models.CharField(max_length=60, blank=True)
    note = models.CharField(max_length=255, blank=True)
    converted_lead = models.ForeignKey(Lead, on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name='+')
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.referred_name} (by {self.referrer_lead_id})'


class UploadToken(models.Model):
    """Secure tokenized client upload link (DM-08)."""
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name='upload_tokens')
    token = models.CharField(max_length=64, unique=True, db_index=True)
    expires_at = models.DateTimeField()
    used_count = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def is_valid(self):
        from django.utils import timezone as _tz
        return _tz.now() < self.expires_at

    def __str__(self):
        return f'UploadToken for {self.lead_id}'
