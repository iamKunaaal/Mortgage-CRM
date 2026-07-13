# Employment profile fields on Lead

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('crm', '0007_referralpartner_created_by'),
    ]

    operations = [
        migrations.AddField(model_name='lead', name='employer',
                            field=models.CharField(blank=True, max_length=160)),
        migrations.AddField(model_name='lead', name='employment_type',
                            field=models.CharField(blank=True, max_length=40)),
        migrations.AddField(model_name='lead', name='monthly_income',
                            field=models.DecimalField(decimal_places=2, default=0, max_digits=14, null=True, blank=True)),
        migrations.AddField(model_name='lead', name='years_employment',
                            field=models.DecimalField(decimal_places=1, default=0, max_digits=5, null=True, blank=True)),
        migrations.AddField(model_name='lead', name='industry',
                            field=models.CharField(blank=True, max_length=80)),
        migrations.AddField(model_name='lead', name='company_name',
                            field=models.CharField(blank=True, max_length=160)),
        migrations.AddField(model_name='lead', name='annual_turnover',
                            field=models.DecimalField(decimal_places=2, default=0, max_digits=16, null=True, blank=True)),
        migrations.AddField(model_name='lead', name='business_years',
                            field=models.DecimalField(decimal_places=1, default=0, max_digits=5, null=True, blank=True)),
    ]
