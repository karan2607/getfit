from django.db import migrations


def clear_aliased_guides(apps, schema_editor):
    """
    Drop cached guides for exercises that now have an explicit KB alias.

    Adding the pec-deck aliases fixes which images get *looked up*, but guides
    already cached in the DB keep serving the old wrong pictures (a chest
    exercise illustrated with rear-delt photos). Deleting those rows makes them
    regenerate on next view with the corrected image mapping.

    Only aliased names are touched -- every other cached guide survives, so this
    does not trigger a mass re-generation against the AI.
    """
    from api.views import _KB_ALIASES

    ExerciseGuide = apps.get_model('api', 'ExerciseGuide')

    names = sorted(_KB_ALIASES.keys())
    total = 0
    for name in names:
        # Match the stored name case-insensitively, and also catch the common
        # "<alias> machine"/"<alias> fly" phrasings the AI tends to emit.
        qs = ExerciseGuide.objects.filter(name__icontains=name)
        count = qs.count()
        if count:
            print('  [0029] clearing %d cached guide(s) matching %r' % (count, name))
            qs.delete()
            total += count

    print('[0029] cleared %d cached exercise guide(s)' % total)


def noop(apps, schema_editor):
    """Nothing to restore -- the deleted rows regenerate on demand."""


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0028_exerciseguide_user_workoutplan_data_version_and_more'),
    ]

    operations = [
        migrations.RunPython(clear_aliased_guides, noop),
    ]
