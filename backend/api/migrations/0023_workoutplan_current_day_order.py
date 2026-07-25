from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0022_workoutsession_day_name'),
    ]

    operations = [
        migrations.AddField(
            model_name='workoutplan',
            name='current_day_order',
            field=models.PositiveIntegerField(default=0),
        ),
    ]
