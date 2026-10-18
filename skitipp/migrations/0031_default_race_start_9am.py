import datetime
from zoneinfo import ZoneInfo

from django.db import migrations


def move_midnight_starts_to_9am(apps, schema_editor):
    # Races imported before FIS published a start time were stored at local midnight,
    # which closed tipping the night before. Races start at 9:00 or later.
    RaceEvent = apps.get_model('skitipp', 'RaceEvent')
    zurich = ZoneInfo('Europe/Zurich')
    for race in RaceEvent.objects.filter(finished=False):
        local = race.race_date.astimezone(zurich)
        if local.time() == datetime.time(0, 0):
            race.race_date = local.replace(hour=9)
            race.save(update_fields=['race_date'])


class Migration(migrations.Migration):

    dependencies = [
        ('skitipp', '0030_clean_racer_names'),
    ]

    operations = [
        migrations.RunPython(move_midnight_starts_to_9am, migrations.RunPython.noop),
    ]
