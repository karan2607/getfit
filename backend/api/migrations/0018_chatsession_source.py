from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0017_clear_exercise_guide_cache'),
    ]

    operations = [
        migrations.AddField(
            model_name='chatsession',
            name='source',
            field=models.CharField(
                choices=[('main', 'Main'), ('embedded', 'Embedded')],
                default='main',
                max_length=20,
            ),
        ),
    ]
