from django.db import migrations

# Names whose cached images were wrong before the lateral-raise aliases existed.
# A standing dumbbell lateral raise was being illustrated with
# "Dumbbell_Lying_Rear_Lateral_Raise" photos -- a lying rear-delt movement.
AFFECTED = ['lateral raise', 'lateral raises', 'side lateral', 'rear delt raise']


def clear_lateral_raise_guides(apps, schema_editor):
    """
    Drop cached guides for lateral-raise variants so they regenerate.

    Scoped to just these names rather than flushing the whole cache, so the rest
    of the cached guides are not needlessly re-generated against the AI.
    """
    ExerciseGuide = apps.get_model('api', 'ExerciseGuide')

    total = 0
    for fragment in AFFECTED:
        qs = ExerciseGuide.objects.filter(name__icontains=fragment)
        count = qs.count()
        if count:
            for name in qs.values_list('name', flat=True).distinct():
                print('  [0031] clearing cached guide: %s' % name)
            qs.delete()
            total += count

    print('[0031] cleared %d cached lateral-raise guide(s)' % total)


def noop(apps, schema_editor):
    """Nothing to restore -- the deleted rows regenerate on demand."""


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0030_feedback'),
    ]

    operations = [
        migrations.RunPython(clear_lateral_raise_guides, noop),
    ]
