# Broker slab % (drives broker payout) on Customization

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('crm', '0009_lead_dob_property_type_area_notes'),
    ]

    operations = [
        migrations.AddField(
            model_name='customization',
            name='broker_slab',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=6),
        ),
    ]
