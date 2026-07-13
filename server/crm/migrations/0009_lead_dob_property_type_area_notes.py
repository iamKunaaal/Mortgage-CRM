# DOB, property type, preferred area, bank notes on Lead

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('crm', '0008_lead_employment_fields'),
    ]

    operations = [
        migrations.AddField(model_name='lead', name='date_of_birth',
                            field=models.DateField(null=True, blank=True)),
        migrations.AddField(model_name='lead', name='property_type',
                            field=models.CharField(blank=True, max_length=60)),
        migrations.AddField(model_name='lead', name='preferred_area',
                            field=models.CharField(blank=True, max_length=120)),
        migrations.AddField(model_name='lead', name='bank_notes',
                            field=models.TextField(blank=True)),
    ]
