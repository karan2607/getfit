"""
In-place data upgrades for already-created workout plans.

Why this exists
---------------
Schema migrations fix columns; they do not fix the *content* of plans a user
already created. Before this, correcting an existing plan meant a destructive
full-plan PUT that wiped `last_completed_at` and nulled `SetLog.exercise` FKs --
which is why "roll out an update" previously meant "ask the user to recreate
their plan and lose their history".

How it works
------------
Each upgrade is a numbered step. `WorkoutPlan.data_version` records the highest
step already applied to that plan, so running the upgrade is cheap and safe to
repeat. Steps MUST be idempotent: assume they may run twice.

Adding a future upgrade
-----------------------
1. Append a new `_step_N` function below.
2. Register it in UPGRADES with the next integer.
3. Deploy. `manage.py upgrade_data` runs in the Render build after `migrate`.

Never renumber or edit an existing step -- plans already carry its version.
"""

from . import exercise_types


def _step_1_reclassify_measurement_types(plan, stdout):
    """
    Re-run the canonical classifier over every exercise in the plan.

    Catches two cases the 0026 migration cannot:
      - exercises added to a plan by an AI chat edit between deploys, which may
        have been written before the server-side normalize() existed
      - exercises whose stored type contradicts a confident name rule (a plank
        tagged weight_reps), regardless of whether it is the default value

    Idempotent: writes only when the computed type differs from the stored one.
    """
    changed = 0
    for day in plan.days.all():
        for exercise in day.exercises.all():
            correct = exercise_types.normalize(
                exercise.measurement_type,
                exercise.name,
                day_name=day.name,
                reps=exercise.reps,
            )
            if correct != exercise.measurement_type:
                stdout('    %s: %s -> %s' % (
                    exercise.name[:40], exercise.measurement_type, correct))
                exercise.measurement_type = correct
                exercise.save(update_fields=['measurement_type'])
                changed += 1
    return changed


def _step_2_fix_setlog_field_contract(plan, stdout):
    """
    Force every SetLog under this plan onto its exercise's field contract.

    Clears values parked in fields that do not apply to the measurement type
    (e.g. a weight on a bodyweight exercise, reps on a plank). 0027 moved the
    duration data; this catches anything written by a client that predates the
    server-side coercion in workout_session_log_set.

    Idempotent: a row already conforming is left untouched.
    """
    from .models import SetLog

    changed = 0
    for day in plan.days.all():
        for exercise in day.exercises.all():
            logs = SetLog.objects.filter(exercise=exercise)
            for log in logs:
                coerced = exercise_types.coerce_set_fields(
                    exercise.measurement_type,
                    {
                        'weight_kg': log.weight_kg,
                        'reps_completed': log.reps_completed,
                        'duration_seconds': log.duration_seconds,
                    },
                )
                if (coerced['weight_kg'] == log.weight_kg
                        and coerced['reps_completed'] == log.reps_completed
                        and coerced['duration_seconds'] == log.duration_seconds):
                    continue
                stdout('    setlog %s (%s/%s): w=%s r=%s d=%s -> w=%s r=%s d=%s' % (
                    str(log.id)[:8], exercise.name[:24], exercise.measurement_type,
                    log.weight_kg, log.reps_completed, log.duration_seconds,
                    coerced['weight_kg'], coerced['reps_completed'],
                    coerced['duration_seconds'],
                ))
                log.weight_kg = coerced['weight_kg']
                log.reps_completed = coerced['reps_completed']
                log.duration_seconds = coerced['duration_seconds']
                log.save(update_fields=[
                    'weight_kg', 'reps_completed', 'duration_seconds'])
                changed += 1
    return changed


# step number -> (label, callable). Append only.
UPGRADES = {
    1: ('reclassify measurement types', _step_1_reclassify_measurement_types),
    2: ('enforce SetLog field contract', _step_2_fix_setlog_field_contract),
}

LATEST_VERSION = max(UPGRADES) if UPGRADES else 0


def upgrade_plan(plan, stdout=lambda msg: None, force=False):
    """
    Apply every upgrade step this plan has not seen yet.

    Returns (steps_applied, rows_changed). Pass force=True to re-run all steps
    regardless of `data_version` (they are idempotent).
    """
    start = 0 if force else plan.data_version
    steps_applied = 0
    rows_changed = 0

    for version in sorted(UPGRADES):
        if version <= start:
            continue
        label, func = UPGRADES[version]
        stdout('  step %d (%s)' % (version, label))
        rows_changed += func(plan, stdout)
        steps_applied += 1

    if plan.data_version < LATEST_VERSION:
        plan.data_version = LATEST_VERSION
        plan.save(update_fields=['data_version'])

    return steps_applied, rows_changed
