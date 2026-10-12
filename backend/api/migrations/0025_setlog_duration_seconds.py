from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Add an explicit duration column to SetLog.

    Until now, timed exercises stored their seconds in `weight_kg` (a float
    kilogram column), which meant a 180-second plank was charted as a 396.8 lb
    personal best and fed to the AI as "180kg". 0027 moves that data here.

    Purely additive -- old code ignores the column.
    """

    dependencies = [
        ('api', '0024_exercise_measurement_type'),
    ]

    operations = [
        migrations.AddField(
            model_name='setlog',
            name='duration_seconds',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
    ]
