# Generated by Django 2.2.1 on 2019-09-17 23:28

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('skitipp', '0004_auto_20190918_0119'),
    ]

    operations = [
        migrations.AlterField(
            model_name='tipp',
            name='dnf',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='dnfs', to='skitipp.Racer'),
        ),
    ]
