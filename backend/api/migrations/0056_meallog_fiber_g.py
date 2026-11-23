from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0055_feedback_branch'),
    ]

    operations = [
        migrations.AddField(
            model_name='meallog',
            name='fiber_g',
            field=models.FloatField(blank=True, null=True),
        ),
    ]
