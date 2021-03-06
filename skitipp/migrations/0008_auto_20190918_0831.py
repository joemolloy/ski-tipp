# Generated by Django 2.2.1 on 2019-09-18 06:31

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def add_alle_im_ziel(apps, schema_editor):
    Racer = apps.get_model('skitipp', 'Racer')
    Racer.objects.get_or_create(fis_id=0, name='Alle im Ziel')


def remove_alle_im_ziel(apps, schema_editor):
    Racer = apps.get_model('skitipp', 'Racer')
    Racer.objects.filter(fis_id=0, name='Alle im Ziel').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('skitipp', '0007_auto_20190918_0139'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='tipp',
            name='alle_im_ziel',
        ),
        migrations.AlterField(
            model_name='tipp',
            name='race_event',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='tipps', to='skitipp.RaceEvent'),
        ),
        migrations.AlterField(
            model_name='tipp',
            name='tipper',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='tipps', to=settings.AUTH_USER_MODEL),
        ),
        migrations.RunPython(add_alle_im_ziel, remove_alle_im_ziel),

    ]
