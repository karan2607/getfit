from django.db import migrations

KG_PER_LB = 0.453592

# Anything longer than this is almost certainly not a real set duration; it gets
# flagged in the audit output rather than silently accepted.
IMPLAUSIBLE_SECONDS = 7200


def migrate_duration_data(apps, schema_editor):
    """
    Move timed-exercise data out of the overloaded `weight_kg` column.

    Two separate repairs:

    1. measurement_type == 'duration'
       Seconds were stored in `weight_kg`. Worse, the frontend write path
       (handleSetField) applied an unconditional lb -> kg conversion to anything
       named `weight_kg`, while the read path skipped the conversion for duration
       exercises. So a user on lb who typed 60 seconds had 27.2 persisted and read
       back 27.2 -- asymmetric corruption. We divide by KG_PER_LB for lb users to
       undo that, then null `weight_kg`.

    2. measurement_type == 'weight_duration'
       Seconds were stored in `reps_completed` (weight_kg legitimately held kg).
       Move the seconds across and null `reps_completed`. No unscaling: the
       reps field was never run through the unit conversion.

    Idempotent: only rows with `duration_seconds IS NULL` are considered, so a
    re-run cannot double-convert.

    Depends on 0026 having corrected `measurement_type` first.
    """
    from api import exercise_types

    SetLog = apps.get_model('api', 'SetLog')

    # Resolve each user's unit once. SetLog -> session -> user -> profile.
    UserProfile = apps.get_model('api', 'UserProfile')
    unit_by_user = dict(
        UserProfile.objects.values_list('user_id', 'preferred_unit')
    )

    qs = (
        SetLog.objects
        .filter(duration_seconds__isnull=True)
        .select_related('exercise', 'workout_session')
    )

    moved_duration = 0
    moved_weight_duration = 0
    skipped = 0
    flagged = []

    for log in qs.iterator():
        # SetLog.exercise is SET_NULL, so fall back to the denormalized name.
        if log.exercise_id and log.exercise is not None:
            mtype = log.exercise.measurement_type
        else:
            mtype = exercise_types.classify(log.exercise_name)

        if mtype == exercise_types.DURATION:
            raw = log.weight_kg
            if raw is None:
                continue
            unit = unit_by_user.get(log.workout_session.user_id, 'lb')
            if unit == 'lb':
                seconds = raw / KG_PER_LB
            else:
                seconds = raw
            seconds = int(round(seconds))
            if seconds < 0:
                seconds = 0

            log.duration_seconds = seconds
            log.weight_kg = None
            log.save(update_fields=['duration_seconds', 'weight_kg'])
            moved_duration += 1

            note = ''
            if seconds > IMPLAUSIBLE_SECONDS:
                note = '  <-- CHECK: implausible duration'
                flagged.append((str(log.id), log.exercise_name, raw, seconds))
            print(
                '  [0027] duration        %-38s %-8s raw=%-10s -> %ss%s'
                % (log.exercise_name[:38], unit, round(raw, 3), seconds, note)
            )

        elif mtype == exercise_types.WEIGHT_DURATION:
            raw = log.reps_completed
            if raw is None:
                continue
            log.duration_seconds = int(raw)
            log.reps_completed = None
            log.save(update_fields=['duration_seconds', 'reps_completed'])
            moved_weight_duration += 1
            print(
                '  [0027] weight_duration %-38s reps=%-6s -> %ss (weight_kg kept)'
                % (log.exercise_name[:38], raw, int(raw))
            )
        else:
            skipped += 1

    print(
        '[0027] moved %d duration row(s), %d weight_duration row(s); '
        '%d row(s) untouched (rep/weight exercises)'
        % (moved_duration, moved_weight_duration, skipped)
    )
    if flagged:
        print('[0027] %d row(s) flagged as implausible -- review these:' % len(flagged))
        for row in flagged:
            print('  [0027]   id=%s %s raw=%s -> %ss' % row)


def reverse(apps, schema_editor):
    """
    Put the seconds back where they came from.

    Restores the pre-migration (wrong) layout so `migrate api 0026` is testable.
    The lb unscaling is re-applied in reverse so a round-trip is lossless apart
    from integer rounding.
    """
    from api import exercise_types

    SetLog = apps.get_model('api', 'SetLog')
    UserProfile = apps.get_model('api', 'UserProfile')
    unit_by_user = dict(UserProfile.objects.values_list('user_id', 'preferred_unit'))

    restored = 0
    qs = (
        SetLog.objects
        .filter(duration_seconds__isnull=False)
        .select_related('exercise', 'workout_session')
    )
    for log in qs.iterator():
        if log.exercise_id and log.exercise is not None:
            mtype = log.exercise.measurement_type
        else:
            mtype = exercise_types.classify(log.exercise_name)

        if mtype == exercise_types.DURATION:
            unit = unit_by_user.get(log.workout_session.user_id, 'lb')
            value = log.duration_seconds
            if unit == 'lb':
                value = value * KG_PER_LB
            log.weight_kg = value
            log.duration_seconds = None
            log.save(update_fields=['weight_kg', 'duration_seconds'])
            restored += 1
        elif mtype == exercise_types.WEIGHT_DURATION:
            log.reps_completed = log.duration_seconds
            log.duration_seconds = None
            log.save(update_fields=['reps_completed', 'duration_seconds'])
            restored += 1

    print('[0027] reverse restored %d row(s)' % restored)


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0026_backfill_measurement_type'),
    ]

    operations = [
        migrations.RunPython(migrate_duration_data, reverse),
    ]
