from django.db import migrations


def clean_racer_names(apps, schema_editor):
    # Same logic as fis_connector.clean_racer_name, copied so the migration
    # doesn't change if that function does
    Racer = apps.get_model('skitipp', 'Racer')
    for racer in Racer.objects.all():
        lines = [line.strip() for line in racer.name.splitlines() if line.strip()]
        name = ' '.join(lines[0].split()) if lines else ''
        if name and name != racer.name:
            racer.name = name
            racer.save(update_fields=['name'])


class Migration(migrations.Migration):

    dependencies = [
        ('skitipp', '0029_alter_raceevent_kind'),
    ]

    operations = [
        migrations.RunPython(clean_racer_names, migrations.RunPython.noop),
    ]
