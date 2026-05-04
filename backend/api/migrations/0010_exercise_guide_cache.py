from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0009_meal_day_number'),
    ]

    operations = [
        migrations.CreateModel(
            name='ExerciseGuide',
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
