# Optional display name on Document (dynamic uploads carry name + type)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('crm', '0010_customization_broker_slab'),
    ]

    operations = [
        migrations.AddField(
            model_name='document',
            name='name',
            field=models.CharField(blank=True, max_length=160),
        ),
    ]
