# Generated by Django 2.2.1 on 2019-09-18 21:27

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('skitipp', '0009_tipppointtally'),
    ]

    operations = [
        migrations.AddField(
            model_name='raceevent',
            name='short_name',
            field=models.CharField(default='', max_length=10),
            preserve_default=False,
        ),
    ]
