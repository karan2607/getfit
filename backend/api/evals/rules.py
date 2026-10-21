"""
Individual plan-quality rules.

Each rule is a function `(plan, context) -> (checked, findings)`:

    checked   how many things it looked at (0 means "not applicable to this
              plan", which scores as neutral rather than as a pass)
    findings  a list of Finding describing what was wrong

A rule must never raise on malformed input. These run against live model output,
which is exactly where a missing key shows up, and a crashing scorer reports
nothing.
"""

import re

from .. import exercise_types
from ..views import _PLACEHOLDER_TOKEN_RE, _PROGRESSION_RULE_RE


# --------------------------------------------------------------------------
# Plan traversal
# --------------------------------------------------------------------------
# Generated plans come in two shapes, both accepted by _validate_plan_json:
# {'weeks': [[day, ...], ...]} for multi-week plans and {'days': [...]} for
# single-week ones. Every rule works off these helpers so none of them has to
# know the difference.

def iter_weeks(plan):
    """
    Yield (week_number, [day, ...]) with week numbers starting at 1.

    Three shapes, matching `views._plan_days`:
      {"week":  [day, ...]}        one week (per-week generation)
      {"weeks": [[day, ...], ...]} multiple weeks (legacy whole-plan)
      {"days":  [day, ...]}        single week, older key
    """
    if not isinstance(plan, dict):
        return
    week = plan.get('week')
    if isinstance(week, list):
        yield 1, [d for d in week if isinstance(d, dict)]
        return
    weeks = plan.get('weeks')
    if isinstance(weeks, list):
        for index, week in enumerate(weeks, start=1):
            yield index, [d for d in (week or []) if isinstance(d, dict)]
        return
    days = plan.get('days')
    if isinstance(days, list):
        yield 1, [d for d in days if isinstance(d, dict)]


def iter_days(plan, include_rest=False):
    for week_number, days in iter_weeks(plan):
        for day in days:
            if include_rest or not day.get('is_rest_day'):
                yield week_number, day


def iter_exercises(plan):
    """Yield (week_number, day, exercise) for every exercise on a training day."""
    for week_number, day in iter_days(plan):
        exercises = day.get('exercises')
        if not isinstance(exercises, list):
            continue
        for exercise in exercises:
            if isinstance(exercise, dict):
                yield week_number, day, exercise


def where(week_number, day, exercise=None):
    label = 'W%sD%s' % (week_number, day.get('day_number', '?'))
    if exercise is not None:
        label += ' %s' % (exercise.get('name') or '<unnamed>')
    return label


def _resolved_type(exercise, day):
    """
    The measurement type the SERVER would store, not what the model claimed.

    Scoring the raw model value would report failures the server already
    silently corrects on write, which is noise rather than signal.
    """
    return exercise_types.normalize(
        exercise.get('measurement_type'),
        exercise.get('name'),
        day_name=day.get('name'),
        reps=exercise.get('reps'),
    )


# --------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------

def warmup_first(plan, context, find):
    """
    Prompt: "Add 'Warmup & Mobility' as the very first exercise on every
    non-rest day."
    """
    checked = 0
    for week_number, day in iter_days(plan):
        exercises = day.get('exercises')
        if not isinstance(exercises, list) or not exercises:
            continue
        checked += 1
        first = exercises[0] if isinstance(exercises[0], dict) else {}
        if not exercise_types.is_warmup_name(first.get('name')):
            find(where(week_number, day),
                 'first exercise is %r, not a warmup block'
                 % (first.get('name') or '<unnamed>'))
    return checked


def rep_scheme_conventional(plan, context, find):
    """
    Prompt: "Real coaches prescribe reps in standard brackets, never arbitrary
    numbers." Reuses the server's own snapper, so a finding here means the
    model emitted something `normalize_reps` would have had to rewrite.
    """
    checked = 0
    for week_number, day, exercise in iter_exercises(plan):
        reps = exercise.get('reps')
        if reps is None or not str(reps).strip():
            continue
        checked += 1
        snapped = exercise_types.normalize_reps(reps)
        if str(snapped) != str(reps):
            find(where(week_number, day, exercise),
                 'reps %r is not a conventional scheme (would snap to %r)'
                 % (str(reps), str(snapped)))
    return checked


def prescription_matches_type(plan, context, find):
    """
    Prompt: "THE REPS FIELD MUST MATCH THE MEASUREMENT TYPE."

    This is the rule that would have caught the Farmer's Carry "40 yards" and
    Burpees "10 min" defects, both of which reached production.
    """
    checked = 0
    for week_number, day, exercise in iter_exercises(plan):
        reps = exercise.get('reps')
        if reps is None or not str(reps).strip():
            continue
        measurement_type = _resolved_type(exercise, day)
        if measurement_type == exercise_types.WARMUP:
            continue
        checked += 1
        ok, expected = exercise_types.prescription_matches_type(
            measurement_type, reps)
        if not ok:
            find(where(week_number, day, exercise),
                 '[%s] prescribed %r but this type is logged in %s'
                 % (measurement_type, str(reps), expected))
    return checked


def measurement_type_agrees(plan, context, find):
    """
    The model's measurement_type must agree with the name-based classifier
    wherever the classifier is confident.

    Only confident verdicts are scored: `has_opinion` exists precisely because
    DEFAULT_TYPE == WEIGHT_REPS once made "confident verdict" and "no opinion"
    indistinguishable.
    """
    checked = 0
    for week_number, day, exercise in iter_exercises(plan):
        name = exercise.get('name')
        reps = exercise.get('reps')
        claimed = exercise.get('measurement_type')
        if not name or not claimed:
            continue
        if exercise_types.is_warmup_name(day.get('name')):
            continue
        if not exercise_types.has_opinion(name, reps):
            continue
        checked += 1
        verdict = exercise_types.classify(name, reps)
        if claimed != verdict:
            find(where(week_number, day, exercise),
                 'claimed %r but the name classifies as %r'
                 % (claimed, verdict))
    return checked


def phase_variation(plan, context, find):
    """
    Prompt: "Exercises MUST change meaningfully between phases... This is
    NON-NEGOTIABLE. If weeks 1 and 5 have identical exercise lists, the plan is
    wrong."

    Compared per day_number, since day 1 of week 1 and day 1 of week 5 are the
    same slot in the split.
    """
    lineups = {}
    for week_number, day in iter_days(plan):
        exercises = day.get('exercises')
        if not isinstance(exercises, list):
            continue
        names = tuple(
            str(e.get('name', '')).strip().lower()
            for e in exercises
            if isinstance(e, dict)
            and not exercise_types.is_warmup_name(e.get('name'))
        )
        if names:
            lineups.setdefault(day.get('day_number'), {})[week_number] = names

    checked = 0
    for day_number, by_week in sorted(
            lineups.items(), key=lambda kv: (kv[0] is None, kv[0])):
        if len(by_week) < 5:
            continue          # too short for a second phase to be expected
        checked += 1
        if len(set(by_week.values())) == 1:
            find('day %s' % day_number,
                 'identical exercise lineup in all %d weeks — no phase '
                 'variation' % len(by_week))
    return checked


# Observed sane bounds, taken from the plan this app actually generates today
# (9-10 exercises and 27-32 sets per training day, warmup included). These are
# wide on purpose: the rule exists to catch a 30-exercise session, not to
# second-guess a coach's choice between 8 and 10 exercises.
MAX_EXERCISES_PER_DAY = 14
MIN_EXERCISES_PER_DAY = 3
MAX_SETS_PER_DAY = 45
MIN_SETS_PER_DAY = 8


def _is_conditioning_day(exercises):
    """
    True when the day's only real work is timed conditioning.

    A dedicated cardio day -- warmup plus a single 25-minute treadmill or bike
    block -- is legitimate programming, and the generator produces them on
    beginner fat-loss plans. The first version of the volume rule below flagged
    all 24 such days in one plan as "only 2 exercises", which was the rule
    being wrong rather than the plan.
    """
    working = [e for e in exercises
               if isinstance(e, dict)
               and not exercise_types.is_warmup_name(e.get('name'))]
    if not working:
        return False
    return all(
        exercise_types.classify(e.get('name'), e.get('reps'))
        in (exercise_types.DURATION, exercise_types.WEIGHT_DURATION)
        for e in working
    )


def session_volume(plan, context, find):
    """
    A session has to be physically completable in one gym visit.

    The ceiling applies to every day. The floor applies only to resistance
    days, because conditioning days are legitimately short.
    """
    checked = 0
    for week_number, day in iter_days(plan):
        exercises = day.get('exercises')
        if not isinstance(exercises, list) or not exercises:
            continue
        checked += 1
        count = len(exercises)
        total_sets = 0
        for exercise in exercises:
            if not isinstance(exercise, dict):
                continue
            try:
                total_sets += int(exercise.get('sets') or 0)
            except (TypeError, ValueError):
                pass

        # Ceiling: always meaningful. A 30-exercise session is wrong whatever
        # the day is for. One finding per day is enough.
        if count > MAX_EXERCISES_PER_DAY:
            find(where(week_number, day),
                 '%d exercises in one session (max %d)'
                 % (count, MAX_EXERCISES_PER_DAY))
        elif total_sets > MAX_SETS_PER_DAY:
            find(where(week_number, day),
                 '%d total sets in one session (max %d)'
                 % (total_sets, MAX_SETS_PER_DAY))
        elif not _is_conditioning_day(exercises):
            if count < MIN_EXERCISES_PER_DAY:
                find(where(week_number, day),
                     'only %d exercises on a resistance day' % count)
            elif total_sets < MIN_SETS_PER_DAY:
                find(where(week_number, day),
                     'only %d total sets on a resistance day' % total_sets)
    return checked


def notes_without_placeholder_weights(plan, context, find):
    """
    Prompt: "no 'Start ...', no placeholder weights of any kind." Weight
    recommendations are generated per week from logged history; a static
    "Start 95lb" in notes competes with that and goes stale immediately.
    """
    checked = 0
    for week_number, day, exercise in iter_exercises(plan):
        notes = exercise.get('notes')
        if not notes:
            continue
        checked += 1
        match = _PLACEHOLDER_TOKEN_RE.search(str(notes))
        if match:
            find(where(week_number, day, exercise),
                 'notes contain the placeholder %r' % match.group(0))
    return checked


def notes_without_progression_rules(plan, context, find):
    """
    Notes must not carry a week-over-week progression rule or a concrete
    weight.

    Reported as "Getting different advice in diff places": one card showed
    notes saying "Add 2.5-5lb when all sets completed" directly above computed
    advice saying "DECREASE WEIGHT. Drop to about 55lb". A frozen rule does not
    just go stale, it contradicts the live recommendation.
    """
    checked = 0
    for week_number, day, exercise in iter_exercises(plan):
        notes = exercise.get('notes')
        if not notes:
            continue
        checked += 1
        match = _PROGRESSION_RULE_RE.search(str(notes))
        if match:
            find(where(week_number, day, exercise),
                 'notes carry a static progression rule (%r) that competes '
                 'with the computed advice' % match.group(0).strip())
    return checked


# Prompt: "INJURY PROTOCOL (highest priority — overrides all other decisions):
# Any stated injury or limitation is a HARD constraint." Each entry is
# (what the user said, what must not be programmed, why).
INJURY_CONSTRAINTS = (
    (r'\bknee', r'\b(barbell\s+)?(back\s+|front\s+)?squat\b|\blunge',
     'knee — prompt says substitute leg press / hamstring curl / step-ups'),
    (r'\b(lower\s+)?back|\blumbar|\bspine|\bspinal|\bdisc\b',
     r'\bconventional deadlift\b|\bgood morning\b',
     'lower back — prompt says Romanian or trap bar, no heavy spinal loading'),
    (r'\bshoulder|\brotator',
     r'\b(barbell|military)\s+(overhead\s+)?press\b|\bbehind the neck\b',
     'shoulder — prompt says cable/machine alternatives'),
    (r'\bwrist|\belbow', r'\bbarbell curl\b|\bskull ?crusher',
     'wrist/elbow — prompt says dumbbell or cable equivalents'),
    # Users also name the movement directly ("avoid ... overhead press"), which
    # no body-part trigger catches. Honour the explicit statement.
    (r'overhead press|military press',
     r'\b(barbell|military|dumbbell|seated)?\s*overhead press\b|\bmilitary press\b',
     'a movement the user explicitly asked to avoid'),
)

# Deliberately NOT derived from the stated text by pattern-matching movement
# nouns. "Avoid back heavy exercises like Deadlifts" must not flag a Romanian
# deadlift: the prompt prescribes the RDL as the SUBSTITUTION for a lower-back
# issue, so a naive "ban anything containing 'deadlift'" rule would contradict
# the prompt's own injury protocol. The table above encodes that distinction.


def injury_constraints_respected(plan, context, find):
    """
    The highest-priority rule in the prompt, so the one most worth measuring.

    Scored only when the request actually stated an injury; a plan for an
    uninjured user reports 0 checks and stays neutral.
    """
    stated = str((context or {}).get('injuries') or '').lower()
    if not stated.strip():
        return 0

    active = [(banned, reason) for trigger, banned, reason in INJURY_CONSTRAINTS
              if re.search(trigger, stated, re.I)]
    if not active:
        return 0

    checked = 0
    for week_number, day, exercise in iter_exercises(plan):
        name = str(exercise.get('name') or '')
        if not name:
            continue
        checked += 1
        for banned, reason in active:
            if re.search(banned, name, re.I):
                find(where(week_number, day, exercise),
                     'contraindicated for stated %s' % reason)
                break
    return checked


# Names the prompt explicitly forbids. "Cable Crossover (Mid-to-High)" is in
# production logged data despite the prompt saying "'Mid-to-High' does not
# exist — never use it", which is the evidence that prohibitions in prose do
# not hold and need a gate.
BANNED_NAME_PATTERNS = (
    (r'\bmid[\s-]*to[\s-]*high\b', 'the prompt states this variant does not exist'),
    (r'\blow[\s-]*to[\s-]*mid\b', 'the prompt states this variant does not exist'),
)


def no_banned_names(plan, context, find):
    checked = 0
    for week_number, day, exercise in iter_exercises(plan):
        name = str(exercise.get('name') or '')
        if not name:
            continue
        checked += 1
        for pattern, reason in BANNED_NAME_PATTERNS:
            if re.search(pattern, name, re.I):
                find(where(week_number, day, exercise),
                     'banned name: %s' % reason)
                break
    return checked


def canonical_exercise_names(plan, context, find):
    """
    Every name must already be the canonical spelling of its movement.

    This is the rule that measures the defect behind "it's not being extremely
    smart": `_exercise_history` matches on `exercise_name__iexact`, so two
    spellings of one movement split a user's logged history in half and the
    coaching advice only ever sees one part.

    Depends on `exercise_names`, which arrives in WS3. Until then this reports
    0 checks and scores neutral rather than silently passing.
    """
    try:
        from .. import exercise_names
    except ImportError:
        return 0

    checked = 0
    for week_number, day, exercise in iter_exercises(plan):
        name = exercise.get('name')
        if not name:
            continue
        checked += 1
        resolved = exercise_names.canonical(name)
        if resolved != name:
            find(where(week_number, day, exercise),
                 'non-canonical name; canonical form is %r' % resolved)
    return checked


def superset_groups_valid(plan, context, find):
    """
    A superset group must have at least two members and they must be adjacent
    in `order`, otherwise the UI cannot render the pair as one block.

    Scored only on plans that use groups at all.
    """
    checked = 0
    for week_number, day in iter_days(plan):
        exercises = day.get('exercises')
        if not isinstance(exercises, list):
            continue
        groups = {}
        for position, exercise in enumerate(exercises):
            if not isinstance(exercise, dict):
                continue
            group = exercise.get('superset_group')
            if group is None:
                continue
            groups.setdefault(group, []).append(position)
        if not groups:
            continue
        checked += 1
        for group, positions in sorted(groups.items(), key=lambda kv: str(kv[0])):
            if len(positions) < 2:
                find(where(week_number, day),
                     'superset group %r has only one member' % group)
            elif positions != list(range(positions[0], positions[0] + len(positions))):
                find(where(week_number, day),
                     'superset group %r members are not adjacent (positions %s)'
                     % (group, positions))
    return checked


def program_outline_tiles_the_program(plan, context, find):
    """
    The phase outline must cover every week exactly once.

    Only scored when an outline is present, so the eight historical fixtures
    (which predate outlines) report 0 checks and stay neutral.
    """
    if not isinstance(plan, dict):
        return 0
    outline = plan.get('program_outline')
    if outline is None:
        return 0
    from ..views import validate_program_outline
    duration = plan.get('duration_weeks') or (context or {}).get('duration_weeks')
    checked = 1
    for problem in validate_program_outline(outline, duration):
        find('program_outline', problem)
    return checked


def exercise_notes_map_resolves(plan, context, find):
    """
    Every key in `exercise_notes` must name an exercise that is actually in
    the payload.

    A key nobody matches is a silently dropped form cue -- the model wrote
    coaching that never reaches the user. Scored only when the map is present.
    """
    if not isinstance(plan, dict):
        return 0
    notes = plan.get('exercise_notes')
    if not isinstance(notes, dict) or not notes:
        return 0

    from .. import exercise_names
    present = {
        exercise_names.canonical(str(exercise.get('name') or ''))
        for _week, _day, exercise in iter_exercises(plan)
    }
    present.discard('')
    checked = 0
    for key in notes:
        checked += 1
        if exercise_names.canonical(str(key)) not in present:
            find('exercise_notes', 'key %r matches no exercise in the payload'
                 % key)
    return checked


def single_week_payload(plan, context, find):
    """
    A per-week payload must contain exactly one week.

    Scored only for payloads that declare themselves single-week (a `week`
    key), so whole-plan fixtures report 0 checks rather than all failing for
    being the old shape.
    """
    if not isinstance(plan, dict) or not isinstance(plan.get('week'), list):
        return 0
    if plan.get('weeks'):
        find('payload', 'has both `week` and `weeks`; which is authoritative?')
    days = [d for d in plan['week'] if isinstance(d, dict)]
    if not days:
        find('payload', '`week` contains no days')
    numbers = [d.get('day_number') for d in days]
    if len(set(numbers)) != len(numbers):
        find('payload', 'duplicate day_number within the week: %s' % numbers)
    return 1


# Ordered worst-consequence-first, which is also the order findings are
# reported in. `weight` scales a rule's contribution to the overall score:
# an unenterable prescription blocks the user, an unconventional rep scheme
# merely reads oddly.
RULES = (
    ('prescription_matches_type', prescription_matches_type, 3.0),
    ('injury_constraints_respected', injury_constraints_respected, 3.0),
    ('measurement_type_agrees', measurement_type_agrees, 2.0),
    ('canonical_exercise_names', canonical_exercise_names, 2.0),
    ('session_volume', session_volume, 2.0),
    ('warmup_first', warmup_first, 1.0),
    ('phase_variation', phase_variation, 1.0),
    ('rep_scheme_conventional', rep_scheme_conventional, 1.0),
    ('notes_without_placeholder_weights', notes_without_placeholder_weights, 1.0),
    # Weighted 2.0: a note contradicting the live advice actively misleads,
    # rather than merely reading oddly.
    ('notes_without_progression_rules', notes_without_progression_rules, 2.0),
    ('no_banned_names', no_banned_names, 1.0),
    ('superset_groups_valid', superset_groups_valid, 1.0),
    # Per-week generation. Each reports 0 checks on the whole-plan fixtures,
    # which predate the format, so they stay neutral on history.
    ('program_outline_tiles_the_program', program_outline_tiles_the_program, 2.0),
    ('exercise_notes_map_resolves', exercise_notes_map_resolves, 1.0),
    ('single_week_payload', single_week_payload, 1.0),
)
