from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0008_exercise_reps_max_length'),
    ]

    operations = [
        migrations.AddField(
            model_name='meal',
            name='day_number',
            field=models.PositiveIntegerField(default=1),
        ),
    ]
