from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0056_meallog_fiber_g'),
    ]

    operations = [
        migrations.AddField(
            model_name='setlog',
            name='target_reps',
            field=models.CharField(blank=True, default='', max_length=100),
        ),
    ]
