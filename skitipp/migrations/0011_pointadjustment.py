# Generated by Django 2.2.1 on 2019-09-19 06:12

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('skitipp', '0010_raceevent_short_name'),
    ]

    operations = [
        migrations.CreateModel(
            name='PointAdjustment',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('reason', models.TextField(max_length=200)),
                ('created', models.DateTimeField(auto_now_add=True)),
                ('points', models.FloatField(help_text='(+/-)')),
                ('tipper', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='points_adjustments', to=settings.AUTH_USER_MODEL)),
            ],
        ),
    ]
