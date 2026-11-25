"""
Suggested working weight for one exercise INSTANCE.

Why an instance and not an exercise
-----------------------------------
`_exercise_history` pools every logged set for a name, so "what should I use
for machine chest press?" has one answer regardless of where it sits. That is
the blanket-last-weight problem: the same movement as the LEAD press on an
accessories day and as the third chest movement on a push day are different
jobs, and the honest answers differ by a lot. Pooling them produces a number
that is too heavy in one place and too light in the other, and is wrong in
both.

So a suggestion is computed per (exercise, day, slot), from, in priority order:

  1. slot history -- the same movement at a comparable position (lead lift vs
     later accessory, same preceding same-muscle volume), preferring the same
     kind of day
  2. preceding same-muscle fatigue today, RELATIVE to the fatigue the history
     was logged under, as a discount band
  3. the target rep range against what was last actually performed
  4. day type -- heavy vs accessory, when the history came from the other kind
  5. completion -- all sets done at the top of the range on a fresh slot
     bumps slightly; missed reps pull back

and then, during the session, today's actuals: beating the target on earlier
same-muscle work nudges a later suggestion up, falling short pulls it back.

Advisory only. It is never logged for the user and never overrides a number
they typed; a suggestion that writes itself into history would corrupt the
very data it is computed from.

Deterministic and uncached, for the same reason `_build_exercise_advice` is:
cached advice was still repeating "Maintain 90.0lb" days after the logic that
produced it had been fixed.
"""

import re

from . import exercise_muscles, exercise_names, exercise_types

KG_PER_LB = 0.453592

#: Discount per preceding exercise sharing a primary muscle, and the floor.
#: 10-20% is the band asked for; one preceding movement is the light end and
#: three or more is the heavy end. Below 0.80 the suggestion stops being a
#: working weight, so it clamps.
_FATIGUE_STEP = 0.05
_FATIGUE_FLOOR = 0.80

#: Roughly 2.5% per rep. Going from a 12-rep target to a 15-rep target is more
#: work per set, so the load comes down; the reverse raises it.
_REP_STEP = 0.025

#: All sets done at the top of the range, fresh: a small bump, not a jump.
_BUMP = 1.025
#: Reps missed last time: pull back rather than hold.
_PULLBACK = 0.95
#: Heavy vs accessory day, when the history came from the other kind.
_DAY_TYPE_STEP = 0.05
#: Today's actuals on earlier same-muscle work, per unit of average score.
_LIVE_STEP = 0.05

#: However the factors stack, the answer stays near what was really lifted.
#: Outside this band it is a guess wearing history's clothes.
_FACTOR_MIN = 0.70
_FACTOR_MAX = 1.10

#: Smallest practical jump on most machines and bars.
_INCREMENT = {'lb': 2.5, 'kg': 1.25}

#: How many past sessions of this movement are considered for slot matching.
_SESSIONS_CONSIDERED = 12

WEIGHTED_TYPES = (exercise_types.WEIGHT_REPS, exercise_types.WEIGHT_DURATION,
                  exercise_types.WEIGHT_DISTANCE)

_HEAVY_WORDS = {'heavy', 'strength', 'power', 'max'}
_LIGHT_WORDS = {'accessory', 'accessories', 'light', 'pump', 'volume',
                'cardio', 'abs', 'core', 'conditioning', 'recovery'}


def _primary_muscles(name):
    profile = exercise_muscles.profile_for(name)
    return set(profile['primary']) if profile else set()


def _factor_for(count):
    return max(_FATIGUE_FLOOR, 1.0 - _FATIGUE_STEP * count)


def preceding_count(day_exercises, target_order, target_name):
    """
    How many exercises BEFORE this one today share one of its primary muscles.

    Order is the whole point: three chest movements before this one is a
    different session than three after it.
    """
    mine = _primary_muscles(target_name)
    if not mine:
        return 0
    overlapping = 0
    for other in day_exercises:
        if other.order >= target_order:
            continue
        if other.measurement_type == exercise_types.WARMUP:
            continue
        if _primary_muscles(other.name) & mine:
            overlapping += 1
    return overlapping


def fatigue_factor(day_exercises, target_order, target_name):
    """How much to discount for work already scheduled on the same muscles."""
    return _factor_for(preceding_count(day_exercises, target_order, target_name))


def day_type(name):
    """'heavy', 'accessory' or 'standard', from the day's name."""
    words = set(re.findall(r'[a-z]+', (name or '').lower()))
    if words & _HEAVY_WORDS:
        return 'heavy'
    if words & _LIGHT_WORDS:
        return 'accessory'
    return 'standard'


_DAY_RANK = {'accessory': -1, 'standard': 0, 'heavy': 1}


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


def _to_unit(kg, unit):
    return kg / KG_PER_LB if unit == 'lb' else kg


def _round_to_increment(value, unit):
    step = _INCREMENT.get(unit, 2.5)
    return round(round(value / step) * step, 2)


def _past_slots(user, exercise, variants, exclude_session_id):
    """
    One record per recent past session of this movement: what was lifted, how
    it went, and the slot it was lifted in.
    """
    from .models import SetLog
    from .views import _target_rep_range

    mine = _primary_muscles(exercise.name)
    sessions_qs = (
        SetLog.objects
        .filter(workout_session__user=user, exercise_name__in=variants,
                is_completed=True, weight_kg__isnull=False)
        .exclude(workout_session_id=exclude_session_id)
        .order_by('-workout_session__started_at')
        .values_list('workout_session_id', flat=True)
    )
    session_ids = []
    for sid in sessions_qs[:200]:
        if sid not in session_ids:
            session_ids.append(sid)
        if len(session_ids) >= _SESSIONS_CONSIDERED:
            break
    if not session_ids:
        return []

    rows = list(
        SetLog.objects
        .filter(workout_session_id__in=session_ids)
        .select_related('workout_session', 'exercise')
    )
    by_session = {}
    for row in rows:
        by_session.setdefault(row.workout_session_id, []).append(row)

    out = []
    for sid in session_ids:
        logs = by_session.get(sid, [])
        own = [l for l in logs if l.exercise_name in variants]
        done = [l for l in own if l.is_completed and l.weight_kg is not None]
        if not done:
            continue
        # The heaviest completed set: a working weight, not an average
        # dragged down by back-off sets.
        best = max(done, key=lambda l: l.weight_kg or 0)

        # Where it sat: count earlier same-muscle exercises actually done in
        # that session. Unknown when the plan row has since been deleted.
        my_order = best.exercise.order if best.exercise_id else None
        preceding = None
        if my_order is not None and mine:
            seen = set()
            for l in logs:
                if (l.exercise_name in variants or not l.is_completed
                        or not l.exercise_id or l.exercise.order >= my_order
                        or l.exercise.measurement_type == exercise_types.WARMUP):
                    continue
                if l.exercise_name in seen:
                    continue
                if _primary_muscles(l.exercise_name) & mine:
                    seen.add(l.exercise_name)
            preceding = len(seen)

        # Completion: every set of it done, and each at or above the low end
        # of what it was asked for at the time.
        missed = False
        for l in own:
            if not l.is_completed:
                missed = True
                break
            low, _ = _target_rep_range(l.target_reps or exercise.reps or '')
            if low and l.reps_completed is not None and l.reps_completed < low:
                missed = True
                break
        _, best_high = _target_rep_range(best.target_reps or exercise.reps or '')

        out.append({
            'session': best.workout_session,
            'day_name': best.workout_session.day_name,
            'weight_kg': float(best.weight_kg),
            'reps': best.reps_completed,
            'preceding': preceding,
            'missed': missed,
            'topped': bool(best_high and best.reps_completed
                           and best.reps_completed >= best_high),
        })
    return out


def live_factor(day_exercises, exercise, today_logs):
    """
    Adjustment from what has actually happened so far today.

    Scores each completed set of EARLIER same-muscle work against its target:
    above the range +1, inside 0, below -1. The average moves this
    suggestion by up to _LIVE_STEP either way. Returns (factor, note).
    """
    from .views import _target_rep_range

    mine = _primary_muscles(exercise.name)
    if not mine or not today_logs:
        return 1.0, None
    scores = []
    for other in day_exercises:
        if other.order >= exercise.order or other.id == exercise.id:
            continue
        if other.measurement_type not in WEIGHTED_TYPES + (
                exercise_types.BODYWEIGHT_REPS,):
            continue
        if not (_primary_muscles(other.name) & mine):
            continue
        for log in today_logs.get(str(other.id), []):
            reps = log.get('reps_completed')
            if not log.get('is_completed') or reps is None:
                continue
            low, high = _target_rep_range(log.get('target_reps') or other.reps or '')
            if not low:
                continue
            scores.append(1 if reps > high else -1 if reps < low else 0)
    if not scores:
        return 1.0, None
    avg = sum(scores) / len(scores)
    if avg == 0:
        return 1.0, None
    factor = 1.0 + _LIVE_STEP * avg
    note = ('earlier sets today beat their targets, nudged up' if avg > 0
            else 'earlier sets today fell short of their targets, pulled back')
    return factor, note


def suggest_weight(user, exercise, target_reps=None, unit='lb',
                   day_exercises=None, today_logs=None,
                   exclude_session_id=None):
    """
    A suggested working weight, or no number when there is nothing to go on.

    Returns {'weight', 'weight_kg', 'unit', 'basis', 'confidence'}; `weight`
    is None when no history exists, and `basis` says so in words rather than
    offering a number dressed up as history.

    `today_logs` maps exercise id -> this session's sets so far (dicts with
    reps_completed / target_reps / is_completed), for the live adjustment.
    """
    from .views import _logged_name_variants, _target_rep_range

    blank = {'weight': None, 'weight_kg': None, 'unit': unit,
             'confidence': 'none',
             'basis': 'no logged sets for this exercise yet -- start '
                      'conservatively and adjust'}

    if exercise.measurement_type not in WEIGHTED_TYPES:
        return dict(blank, basis='this exercise does not carry a weight')

    day = exercise.day
    if day_exercises is None:
        day_exercises = list(day.exercises.all().order_by('order'))

    variants = _logged_name_variants(user, exercise.name)
    past = _past_slots(user, exercise, variants, exclude_session_id)
    if not past:
        return blank

    today_prec = preceding_count(day_exercises, exercise.order, exercise.name)
    today_fresh = today_prec == 0

    def ranked(item):
        idx, p = item
        day_match = _slot_matches(p['day_name'], day.name)
        hist = p['preceding']
        if hist is None and day_match:
            hist = today_prec        # same day, unknown position: assume same
        pos_match = (hist == 0) == today_fresh if hist is not None else False
        gap = abs(hist - today_prec) if hist is not None else 9
        return (not pos_match, not day_match, gap, idx), day_match, pos_match, hist

    scored = [ranked(item) for item in enumerate(past)]
    key, day_match, pos_match, hist_prec = min(scored, key=lambda s: s[0])
    chosen = past[key[-1]]

    if pos_match and day_match:
        confidence, source = 'high', 'this slot'
    elif pos_match or day_match:
        confidence, source = 'medium', 'a similar slot'
    else:
        confidence, source = 'low', 'this exercise in other sessions'

    base_kg = chosen['weight_kg']
    performed = chosen['reps']
    factor = 1.0
    notes = []

    # 2. Fatigue, relative to what the history was already lifted under. A
    # push-day 100 lb logged after bench and incline is already a fatigued
    # number; discounting it again would double count.
    hist_for_fatigue = hist_prec if hist_prec is not None else 0
    fatigue = _factor_for(today_prec) / _factor_for(hist_for_fatigue)
    if abs(fatigue - 1.0) > 1e-9:
        factor *= fatigue
        notes.append('%s%d%% for %s same-muscle work before it today'
                     % ('-' if fatigue < 1 else '+',
                        round(abs(1 - fatigue) * 100),
                        'more' if fatigue < 1 else 'less'))

    # 3. Target reps against what was last performed at that weight.
    target = target_reps or exercise.reps or ''
    low, high = _target_rep_range(target)
    rep_cut = False
    if performed and low:
        if performed < low:
            factor *= max(0.75, 1.0 - _REP_STEP * (low - performed))
            rep_cut = True
            notes.append('target %s reps against %d performed' % (target, performed))
        elif performed > high:
            factor *= min(1.10, 1.0 + _REP_STEP * (performed - high))
            notes.append('target %s reps against %d performed' % (target, performed))

    # 4. Day type, only when the history came from the other kind of day.
    shift = _DAY_RANK[day_type(day.name)] - _DAY_RANK[day_type(chosen['day_name'])]
    if shift:
        factor *= 1.0 + _DAY_TYPE_STEP * shift
        notes.append('%s day vs %s day history'
                     % (day_type(day.name), day_type(chosen['day_name'])))

    # 5. Completion trend. Progressing on a set the user could not complete is
    # how a plan becomes undoable, so missed reps pull back.
    if chosen['missed']:
        if not rep_cut:
            factor *= _PULLBACK
        notes.append('last session missed the target, so pulling back')
    elif chosen['topped'] and today_fresh and not rep_cut:
        factor *= _BUMP
        notes.append('every set done at the top of the range last time')

    # Today's actuals.
    live, live_note = live_factor(day_exercises, exercise, today_logs or {})
    if live_note:
        factor *= live
        notes.append(live_note)

    factor = min(_FACTOR_MAX, max(_FACTOR_MIN, factor))
    # Assisted machines log negative load; scaling a negative number inverts
    # every adjustment, so those get the last weight as-is.
    raw = base_kg * factor if base_kg > 0 else base_kg
    base_display = _round_to_increment(_to_unit(base_kg, unit), unit)
    display = _round_to_increment(_to_unit(raw, unit), unit)

    # A percentage that rounds away is the same as no advice.
    #
    # The bump is 2.5%, and the smallest plate change is 2.5 lb. On a 50 lb
    # calf raise that is 1.25 lb, which rounds straight back to 50 -- so a
    # client who topped the range was told to stay, forever, on every light
    # movement. Below roughly 100 lb the adjustment is always smaller than
    # the equipment allows, which is precisely where it matters most.
    #
    # So an intent to move becomes one increment. Only the direction is
    # taken from the factor; the size is what the equipment can express.
    if base_kg > 0:
        step = _INCREMENT.get(unit, 2.5)
        if factor > 1.0 and display <= base_display:
            display = _round_to_increment(base_display + step, unit)
        elif factor < 1.0 and display >= base_display:
            display = _round_to_increment(max(base_display - step, step), unit)

    # Never above a weight the target reps have actually been met at, when
    # the last attempt missed them.
    #
    # The percentage pullback alone answered 75 lb to a client who had just
    # managed 8 reps of a prescribed 15-20 at 90 lb, having done 20 at 50.
    # 75 will not produce 15 reps either; 50 is the only weight in the
    # history that has. Evidence beats arithmetic, and a floor is not
    # applied when the history has none to offer.
    if chosen['missed'] and base_kg > 0:
        proven = [p['weight_kg'] for p in past if not p['missed']]
        if proven:
            ceiling = _round_to_increment(_to_unit(max(proven), unit), unit)
            if display > ceiling:
                display = ceiling
                notes.append('last weight the target reps were actually met at')

    weight_kg = display * KG_PER_LB if unit == 'lb' else display

    basis = 'from %s: %s%s x %s' % (
        source, round(_to_unit(base_kg, unit), 1), unit,
        performed if performed else '?')
    if notes:
        basis += '; ' + '; '.join(notes)

    return {'weight': display, 'weight_kg': round(weight_kg, 3), 'unit': unit,
            'confidence': confidence, 'basis': basis}


def suggest_for_session(session, unit='lb', actuals=None):
    """
    Suggestions for every exercise instance of a session's day, keyed by
    exercise id.

    Built from the full planned sequence and from today's sets so far: the
    stored ones, overlaid with `actuals` -- what the client has typed but not
    yet saved. Nothing here is written; this only reads.
    """
    day = session.exercise_day
    if day is None:
        return {}
    day_exercises = list(day.exercises.all().order_by('order'))

    today = {}
    for log in session.set_logs.all():
        if not log.exercise_id:
            continue
        today.setdefault(str(log.exercise_id), {})[log.set_number] = {
            'reps_completed': log.reps_completed,
            'target_reps': log.target_reps,
            'is_completed': log.is_completed,
        }
    for a in actuals or []:
        if not isinstance(a, dict) or not a.get('exercise_id'):
            continue
        try:
            set_number = int(a.get('set_number'))
            reps = a.get('reps_completed')
            reps = int(reps) if reps not in (None, '') else None
        except (TypeError, ValueError):
            continue
        slot = today.setdefault(str(a['exercise_id']), {}).setdefault(set_number, {})
        if 'reps_completed' in a:
            slot['reps_completed'] = reps
        if 'is_completed' in a:
            slot['is_completed'] = bool(a['is_completed'])
    today_logs = {k: list(v.values()) for k, v in today.items()}

    out = {}
    for ex in day_exercises:
        if ex.measurement_type not in WEIGHTED_TYPES:
            continue
        out[str(ex.id)] = suggest_weight(
            session.user, ex, unit=unit, day_exercises=day_exercises,
            today_logs=today_logs, exclude_session_id=session.id)
    return out
