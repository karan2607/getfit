"""
Suggested working weight for one exercise INSTANCE.

Why an instance and not an exercise
-----------------------------------
`_exercise_history` pools every logged set for a name, so "what should I use
for machine chest press?" has one answer regardless of where it sits. That is
the blanket-last-weight problem: the same movement as the LEAD press on a push
day and as the third chest movement on an accessories day are different jobs,
and the honest answers differ by a lot. Pooling them produces a number that is
too heavy in one place and too light in the other, and is wrong in both.

So a suggestion is computed per (exercise, day, slot), from:

  1. slot history -- the same movement, in the same kind of day, at a
     comparable position
  2. preceding same-muscle fatigue, as a discount band
  3. the target rep range against what was last actually performed
  4. completion -- missed reps mean hold, not progress

Advisory only. It is never logged for the user and never overrides a number
they typed; a suggestion that writes itself into history would corrupt the
very data it is computed from.

Deterministic and uncached, for the same reason `_build_exercise_advice` is:
cached advice was still repeating "Maintain 90.0lb" days after the logic that
produced it had been fixed.
"""

from . import exercise_muscles, exercise_names, exercise_types

#: Discount per preceding exercise sharing a primary muscle, and the floor.
#: 10-20% is the band asked for; one preceding movement is the light end and
#: three or more is the heavy end. Below 0.80 the suggestion stops being a
#: working weight, so it clamps.
_FATIGUE_STEP = 0.05
_FATIGUE_FLOOR = 0.80

#: Roughly 2.5% per rep. Going from a 12-rep target to a 15-rep target is more
#: work per set, so the load comes down; the reverse raises it.
_REP_STEP = 0.025


def _primary_muscles(name):
    profile = exercise_muscles.profile_for(name)
    return set(profile['primary']) if profile else set()


def fatigue_factor(day_exercises, target_order, target_name):
    """
    How much to discount for work already done on the same muscles today.

    Counts only PRECEDING exercises that share a primary muscle. Order is the
    whole point: three chest movements before this one is a different session
    than three after it.
    """
    mine = _primary_muscles(target_name)
    if not mine:
        return 1.0
    overlapping = 0
    for other in day_exercises:
        if other.order >= target_order:
            continue
        if other.measurement_type == exercise_types.WARMUP:
            continue
        if _primary_muscles(other.name) & mine:
            overlapping += 1
    return max(_FATIGUE_FLOOR, 1.0 - _FATIGUE_STEP * overlapping)


def _slot_matches(log_day_name, day_name):
    """
    Whether a logged set came from the same KIND of day.

    Compared on the day's name because that is what `WorkoutSession.day_name`
    denormalises and what survives a plan edit. Token identity, so "Upper
    Push" and "Upper Push (Heavy)" are not forced apart by a qualifier.
    """
    if not log_day_name or not day_name:
        return False
    a = set(exercise_names.tokens(log_day_name))
    b = set(exercise_names.tokens(day_name))
    if not a or not b:
        return False
    return bool(a & b) and (a <= b or b <= a or len(a & b) >= 2)


def suggest_weight(user, exercise, target_reps=None, unit='lb'):
    """
    A suggested working weight, or None when there is nothing to go on.

    Returns {'weight', 'unit', 'basis', 'confidence'}; `weight` is None when
    no slot history exists, and `basis` says so in words rather than offering
    a number dressed up as history.
    """
    from .models import SetLog
    from .views import _logged_name_variants, _target_rep_range

    blank = {'weight': None, 'unit': unit, 'confidence': 'none',
             'basis': 'no logged sets for this exercise yet -- start '
                      'conservatively and adjust'}

    if exercise.measurement_type not in (exercise_types.WEIGHT_REPS,
                                         exercise_types.WEIGHT_DURATION,
                                         exercise_types.WEIGHT_DISTANCE):
        return dict(blank, basis='this exercise does not carry a weight')

    day = exercise.day
    day_exercises = list(day.exercises.all().order_by('order'))

    logs = list(
        SetLog.objects
        .filter(workout_session__user=user,
                exercise_name__in=_logged_name_variants(user, exercise.name),
                is_completed=True, weight_kg__isnull=False)
        .select_related('workout_session')
        .order_by('-workout_session__started_at')[:60]
    )
    if not logs:
        return blank

    same_slot = [l for l in logs
                 if _slot_matches(l.workout_session.day_name, day.name)]
    used, confidence, source = (
        (same_slot, 'high', 'this slot') if same_slot
        else (logs, 'low', 'this exercise in other sessions'))

    # The heaviest completed set in the most recent session we have for this
    # slot: a working weight, not an average dragged down by back-off sets.
    newest = used[0].workout_session.started_at
    recent = [l for l in used if l.workout_session.started_at == newest]
    best = max(recent, key=lambda l: l.weight_kg or 0)
    base_kg = float(best.weight_kg)
    performed_reps = best.reps_completed

    factor = fatigue_factor(day_exercises, exercise.order, exercise.name)
    notes = []
    if factor < 1.0:
        notes.append('%d%% off for work already done on these muscles today'
                     % round((1 - factor) * 100))

    low, high = _target_rep_range(target_reps or exercise.reps or '')
    if performed_reps and low:
        delta = low - performed_reps
        if delta:
            factor *= max(0.75, 1.0 - _REP_STEP * delta)
            notes.append('target %s reps against %d performed'
                         % (target_reps or exercise.reps, performed_reps))

    # Missed reps mean hold, never progress. Progressing on a set the user
    # could not complete is how a plan becomes undoable.
    if performed_reps and low and performed_reps < low:
        factor = min(factor, 1.0)
        notes.append('last session missed the target, so holding rather than adding')

    weight_kg = round(base_kg * factor, 1)
    display = (round(weight_kg / 0.453592, 1) if unit == 'lb'
               else round(weight_kg, 1))

    basis = 'from %s: %s%s x %s' % (
        source,
        round(base_kg / 0.453592, 1) if unit == 'lb' else round(base_kg, 1),
        unit, performed_reps if performed_reps else '?')
    if notes:
        basis += '; ' + '; '.join(notes)

    return {'weight': display, 'unit': unit, 'confidence': confidence,
            'basis': basis}
