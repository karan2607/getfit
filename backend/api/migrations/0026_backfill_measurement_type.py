from django.db import migrations


def backfill(apps, schema_editor):
    """
    Classify every Exercise still sitting on the untrustworthy default.

    Migration 0024 added `measurement_type` as AddField-only with
    default='weight_reps', so every exercise created before it -- including every
    warmup, plank and treadmill block in every existing plan -- was stamped
    'weight_reps'. That is the real reason warmups kept rendering weight+reps
    inputs no matter how many times the frontend was patched.

    Only rows holding the default are touched. A row with a deliberate non-default
    value was set by post-0024 code and is left alone.
    """
    from api import exercise_types

    Exercise = apps.get_model('api', 'Exercise')

    qs = Exercise.objects.filter(measurement_type=exercise_types.DEFAULT_TYPE)
    qs = qs.select_related('day')

    changed = 0
    by_type = {}
    for exercise in qs.iterator():
        day_name = exercise.day.name if exercise.day_id else None
        new_type = exercise_types.normalize(
            None,                      # ignore stored value: it is the suspect default
            exercise.name,
            day_name=day_name,
            reps=exercise.reps,
        )
        if new_type == exercise_types.DEFAULT_TYPE:
            continue
        exercise.measurement_type = new_type
        exercise.save(update_fields=['measurement_type'])
        changed += 1
        by_type[new_type] = by_type.get(new_type, 0) + 1
        print(
            '  [0026] %-45s %-16s (day: %s)'
            % (exercise.name[:45], new_type, (day_name or '-')[:28])
        )

    print('[0026] reclassified %d exercise(s): %s' % (changed, by_type or '{}'))


def noop(apps, schema_editor):
    """
    Irreversible by design.

    The previous values were all the same meaningless default, so there is
    nothing to restore. Reversing is a no-op rather than an error so that
    `migrate api 0024` still works for testing.
    """
    print('[0026] reverse is a no-op (original values were all the default)')


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0025_setlog_duration_seconds'),
    ]

    operations = [
        migrations.RunPython(backfill, noop),
    ]
