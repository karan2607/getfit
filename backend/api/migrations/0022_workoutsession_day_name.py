from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0021_healthconnection_last_sync_at'),
    ]

    operations = [
        migrations.AddField(
            model_name='workoutsession',
            name='day_name',
            field=models.CharField(blank=True, max_length=255),
        ),
    ]
