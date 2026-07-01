from django import forms
from django.contrib.auth.forms import AuthenticationForm
from .models import User, Lead, ReferralPartner, Bank, Task, Role


class StyledMixin:
    """Apply consistent CSS classes to all fields."""
    def _style(self):
        for f in self.fields.values():
            w = f.widget
            cls = 'fld-input'
            if isinstance(w, forms.Select):
                cls = 'fld-input'
            if isinstance(w, forms.CheckboxInput):
                cls = ''
            w.attrs.setdefault('class', cls)


class LoginForm(AuthenticationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].widget.attrs.update({'class': 'fld-input', 'placeholder': 'Username'})
        self.fields['password'].widget.attrs.update({'class': 'fld-input', 'placeholder': 'Password'})


class LeadForm(StyledMixin, forms.ModelForm):
    class Meta:
        model = Lead
        fields = ['name', 'mobile', 'email', 'nationality', 'property_value', 'ltv',
                  'loan_amount', 'advisor', 'bank', 'source', 'stage', 'priority']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style()


class UserForm(StyledMixin, forms.ModelForm):
    password = forms.CharField(required=False, widget=forms.PasswordInput(attrs={'placeholder': 'Set / reset password'}))

    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'username', 'email', 'phone', 'role',
                  'department', 'status', 'target_calls', 'target_submissions',
                  'target_partners', 'target_disbursement']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style()
        self.fields['password'].widget.attrs.update({'class': 'fld-input'})

    def save(self, commit=True):
        user = super().save(commit=False)
        pw = self.cleaned_data.get('password')
        if pw:
            user.set_password(pw)
        elif not user.pk:
            user.set_password('changeme123')
        if commit:
            user.save()
        return user


class TaskForm(StyledMixin, forms.ModelForm):
    class Meta:
        model = Task
        fields = ['title', 'lead', 'assignee', 'task_type', 'priority', 'status', 'due_date']
        widgets = {'due_date': forms.DateInput(attrs={'type': 'date'})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style()
        self.fields['title'].required = True


class BankForm(StyledMixin, forms.ModelForm):
    class Meta:
        model = Bank
        fields = ['name', 'bank_type', 'contact_person', 'status']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style()
        self.fields['name'].required = True


class PartnerForm(StyledMixin, forms.ModelForm):
    class Meta:
        model = ReferralPartner
        fields = ['name', 'mobile', 'email', 'company', 'organization', 'emirates_id',
                  'passport_no', 'bank_name', 'account_no', 'iban', 'partner_type',
                  'status', 'agreement', 'kyc_doc']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style()
        # all detail fields mandatory (per client requirement)
        for name in ['name', 'mobile', 'email', 'company', 'organization', 'emirates_id',
                     'passport_no', 'bank_name', 'account_no', 'iban']:
            self.fields[name].required = True
