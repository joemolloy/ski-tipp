# Generated by Django 2.2.1 on 2019-09-20 10:20

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('skitipp', '0016_auto_20190920_1146'),
    ]

    operations = [
        migrations.AddField(
            model_name='tipppointtally',
            name='is_best_tipp',
            field=models.BooleanField(default=False),
        ),
    ]
