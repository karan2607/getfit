from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0010_exercise_guide_cache'),
    ]

    operations = [
        migrations.CreateModel(
            name='MealGuide',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(db_index=True, max_length=255, unique=True)),
                ('data', models.JSONField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'ordering': ['name'],
            },
        ),
    ]
