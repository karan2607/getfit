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

    Iterates ALL_SET_FIELDS rather than naming columns, so a field added later
    (speed_kmh, incline_pct, distance_m) is enforced here too. Naming them
    would have meant a stale speed left on an exercise reclassified away from
    cardio_machine was never cleared.
    """
    from .models import SetLog

    fields = exercise_types.ALL_SET_FIELDS
    changed = 0
    for day in plan.days.all():
        for exercise in day.exercises.all():
            logs = SetLog.objects.filter(exercise=exercise)
            for log in logs:
                current = {f: getattr(log, f) for f in fields}
                coerced = exercise_types.coerce_set_fields(
                    exercise.measurement_type, current)
                dropped = [f for f in fields if coerced[f] != current[f]]
                if not dropped:
                    continue
                stdout('    setlog %s (%s/%s): %s' % (
                    str(log.id)[:8], exercise.name[:24],
                    exercise.measurement_type,
                    ', '.join('%s %s -> %s' % (f, current[f], coerced[f])
                              for f in dropped),
                ))
                for field in dropped:
                    setattr(log, field, coerced[field])
                log.save(update_fields=dropped)
                changed += 1
    return changed


def _step_3_strip_placeholder_weights(plan, stdout):
    """
    Remove AI placeholder weights from existing exercise notes.

    The plan-generation prompt's JSON schema example literally read
    "Start Xkg. Add Ykg when all sets clean.", and the model copied that
    placeholder straight into real plans with the unit swapped -- so users saw
    "Start Xlb" in the How-to panel. The prompt and the write path are both
    fixed now; this repairs the notes already stored.

    Only the offending sentence is removed; genuine coaching in the same note
    survives. Idempotent: a note with no placeholder is left untouched.
    """
    from .views import _strip_placeholder_weights

    changed = 0
    for day in plan.days.all():
        for exercise in day.exercises.all():
            if not exercise.notes:
                continue
            cleaned = _strip_placeholder_weights(exercise.notes)
            if cleaned == exercise.notes:
                continue
            stdout('    %s: %r -> %r' % (
                exercise.name[:28], exercise.notes[:60], cleaned[:60]))
            exercise.notes = cleaned
            exercise.save(update_fields=['notes'])
            changed += 1
    return changed


def _step_4_normalize_rep_schemes(plan, stdout):
    """
    Snap arbitrary rep counts in existing plans onto conventional schemes.

    The AI was prescribing numbers like 11, 13, 14 and 17, which read as machine
    output rather than programming. Snapping widens to the bracket that contains
    the original (13 -> "12-15"), so the training stimulus is unchanged and the
    prescription becomes idiomatic.

    Durations, distances, existing ranges and "AMRAP" are left alone.
    Idempotent: an already-conventional value is untouched.
    """
    changed = 0
    for day in plan.days.all():
        for exercise in day.exercises.all():
            fixed = exercise_types.normalize_reps(exercise.reps)
            if fixed == exercise.reps:
                continue
            stdout('    %s: reps %r -> %r' % (
                exercise.name[:32], exercise.reps, fixed))
            exercise.reps = fixed
            exercise.save(update_fields=['reps'])
            changed += 1
    return changed


def _step_5_canonicalise_exercise_names(plan, stdout):
    """
    Rewrite Exercise.name and SetLog.exercise_name to the canonical spelling.

    Why this is a step and not a migration: it rewrites a user's real training
    history, and the local DB is empty so a backfill cannot be observed before
    it reaches production. Running it is therefore a deliberate act
    (`manage.py upgrade_data`), reviewable with `--dry-run` first.

    What it fixes: 190 distinct names existed across Exercise and SetLog for a
    far smaller set of real movements, and history is matched on the name, so
    the progression logic could only ever see part of a user's logged sets.

    Only the explicit registry in `exercise_names` drives this -- no similarity
    scoring -- so every merge is one a human wrote down and can veto.

    Idempotent: a row already canonical is left untouched.

    NOTE on SetLog rows: `exercise_name` is denormalised and deliberately
    survives plan deletion, so rows whose `exercise` FK is null are not reached
    by walking this plan's exercises. They are handled by matching on the name
    for this plan's user, which is why the SetLog pass is scoped by user rather
    than by exercise.
    """
    from .models import SetLog
    from . import exercise_names

    changed = 0
    seen_names = set()

    for day in plan.days.all():
        for exercise in day.exercises.all():
            seen_names.add(exercise.name)
            canonical = exercise_names.canonical(exercise.name)
            if canonical != exercise.name:
                stdout('    exercise %r -> %r' % (exercise.name, canonical))
                exercise.name = canonical
                exercise.save(update_fields=['name'])
                changed += 1
                seen_names.add(canonical)

    # Every spelling this user has logged, not only the ones still referenced
    # by a plan -- the point is to reunite history with the current plan.
    logged = set(
        SetLog.objects
        .filter(workout_session__user=plan.user)
        .values_list('exercise_name', flat=True)
        .distinct()
    )
    for name in sorted(n for n in logged if n):
        canonical = exercise_names.canonical(name)
        if canonical == name:
            continue
        rows = (SetLog.objects
                .filter(workout_session__user=plan.user, exercise_name=name)
                .update(exercise_name=canonical))
        if rows:
            stdout('    %d set log(s) %r -> %r' % (rows, name, canonical))
            changed += rows
    return changed


def _step_6_reclassify_for_new_types(plan, stdout):
    """
    Move existing exercises onto cardio_machine and weight_distance.

    Why a NEW step rather than an edit to step 1: `data_version` gates by
    number, so a plan already stamped at 5 never re-runs step 1 no matter how
    much the classifier changes. That is exactly why `Sled Push` was still
    stored as `duration` months after the classifier was fixed -- the rule
    changed and nothing re-read it. Every future classifier change needs its
    own step number for the same reason.

    Why it also re-runs the contract: steps execute in numeric order, so step 2
    enforces the OLD contract before this step changes the type, leaving the
    sets one deploy behind. Reclassifying and re-coercing in the same step
    closes that window.

    This is safe only because each new contract is a superset of the one it
    replaces (see exercise_types.REPLACES). Were it not, re-coercing right
    after reclassifying would null the 17 logged carry and sled durations
    instead of keeping them.

    Idempotent: both halves write only on a difference.
    """
    changed = _step_1_reclassify_measurement_types(plan, stdout)
    changed += _step_2_fix_setlog_field_contract(plan, stdout)
    return changed


# step number -> (label, callable). Append only.
UPGRADES = {
    1: ('reclassify measurement types', _step_1_reclassify_measurement_types),
    2: ('enforce SetLog field contract', _step_2_fix_setlog_field_contract),
    3: ('strip placeholder weights from notes', _step_3_strip_placeholder_weights),
    4: ('normalize rep schemes', _step_4_normalize_rep_schemes),
    5: ('canonicalise exercise names', _step_5_canonicalise_exercise_names),
    6: ('reclassify for cardio_machine / weight_distance',
        _step_6_reclassify_for_new_types),
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
