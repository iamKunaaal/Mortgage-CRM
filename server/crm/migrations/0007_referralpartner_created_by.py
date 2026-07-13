# Generated for referral partner ownership scoping

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('crm', '0006_lead_disbursed_at'),
    ]

    operations = [
        migrations.AddField(
            model_name='referralpartner',
            name='created_by',
            field=models.ForeignKey(blank=True, null=True,
                                    on_delete=django.db.models.deletion.SET_NULL,
                                    related_name='referral_partners',
                                    to='crm.user'),
        ),
    ]
