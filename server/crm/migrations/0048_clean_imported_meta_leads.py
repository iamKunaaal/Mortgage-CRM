from django.db import migrations


# One-time cleanup: historical Meta leads that were backfilled from the CSV export
# carried sheet-only artifacts (a "l:" prefix on the id, a "p:" prefix on the phone,
# and plumbing columns like form_name/campaign_name/Lead Remark in the answers).
# Normalise them so they match the clean shape of live-webhook leads.
PLUMBING_KEYS = {
    'id', 'ad_id', 'adset_id', 'campaign_id', 'form_id', 'form_name',
    'campaign_name', 'is_organic', 'platform', 'created_time', 'ad_name',
    'adset_name', 'lead_status', 'Lead Remark',
}


def clean(apps, schema_editor):
    MetaLead = apps.get_model('crm', 'MetaLead')
    for m in MetaLead.objects.all():
        changed = False
        # leadgen_id: strip a leading "l:"
        if m.leadgen_id and m.leadgen_id.lower().startswith('l:'):
            m.leadgen_id = m.leadgen_id.split(':', 1)[1].strip()
            changed = True
        # mobile: strip a leading "p:"
        if m.mobile and m.mobile.lower().startswith('p:'):
            m.mobile = m.mobile.split(':', 1)[1].strip()
            changed = True
        # data: drop plumbing keys + clean a p:-prefixed phone answer
        if isinstance(m.data, dict):
            newdata = {}
            for k, v in m.data.items():
                if k in PLUMBING_KEYS:
                    changed = True
                    continue
                if k == 'phone_number' and isinstance(v, str) and v.lower().startswith('p:'):
                    v = v.split(':', 1)[1].strip()
                    changed = True
                newdata[k] = v
            if changed:
                m.data = newdata
        if changed:
            m.save(update_fields=['leadgen_id', 'mobile', 'data'])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ('crm', '0047_lead_custom_clientreferral_messagetemplate_ubo_and_more'),
    ]
    operations = [migrations.RunPython(clean, noop)]
