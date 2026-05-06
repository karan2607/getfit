from django.db import migrations


def clear_exercise_guides(apps, schema_editor):
    ExerciseGuide = apps.get_model('api', 'ExerciseGuide')
    ExerciseGuide.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0015_healthconnection_sync_token'),
    ]

    operations = [
        migrations.RunPython(clear_exercise_guides, migrations.RunPython.noop),
    ]
