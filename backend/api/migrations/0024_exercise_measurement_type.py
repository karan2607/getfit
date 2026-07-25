from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0023_workoutplan_current_day_order'),
    ]

    operations = [
        migrations.AddField(
            model_name='exercise',
            name='measurement_type',
            field=models.CharField(
                choices=[
                    ('weight_reps', 'Weight + Reps'),
                    ('weight_duration', 'Weight + Duration'),
                    ('duration', 'Duration only'),
                    ('warmup', 'Warmup/Mobility'),
                    ('bodyweight_reps', 'Bodyweight + Reps'),
                ],
                default='weight_reps',
                max_length=20,
            ),
        ),
    ]
