"""
Recorded plan state, and the diff that checks a change actually did what was
approved.

Why a recorded snapshot rather than the model's account
------------------------------------------------------
The failure this exists to catch is a plan edit that quietly drops something:
a calf raise that vanished in a v2 update and had to be restored in v3. The
model reporting "done, I replaced the bench press" is not evidence, because
the model is exactly the component that lost the exercise.

So both sides of a change are recorded, and the diff is computed in code from
those recordings. The model proposes; the server applies, re-reads, and says
what actually changed. A change that altered anything outside the approved
draft must NEVER report as success.

Identity
--------
Exercises are keyed by primary key, which is meaningful now that
`views._apply_plan_diff` preserves rows through an edit instead of deleting
and recreating the plan. Before that fix every pk changed on every edit, so a
pk diff would have reported the entire plan as replaced -- which, accurately,
it was.
"""

from . import exercise_names


# Fields whose change is worth reporting. `order` is deliberately absent:
# reordering a day is not a content change, and `_apply_plan_diff` rewrites
# order on every pass to keep composite members contiguous, so including it
# would flag every edit as touching everything.
TRACKED_FIELDS = ('name', 'sets', 'reps', 'rest_seconds', 'notes',
                  'measurement_type', 'composite_id')


def _differs(field, old_value, new_value):
    """
    Whether a field's change is worth reporting.

    `name` is compared through `canonical()` rather than as a string, because
    the server renames on save: a payload saying "Standing Calf Raise" is
    stored as "Standing Calf Raises (Machine)". A string compare reports that
    as an unapproved change on EVERY edit, and a diff that cries wolf every
    time is a diff nobody reads -- which would quietly undo the entire point
    of verifying changes.

    A rename that survives canonicalisation is still reported, because that is
    a real rename the user should see.
    """
    if field == 'name':
        return (exercise_names.canonical(old_value or '')
                != exercise_names.canonical(new_value or ''))
    return old_value != new_value


def snapshot_plan(plan, week_numbers=None):
    """
    The full contents of a plan, or of the weeks named, as plain data.

    Stored on both sides of an applied change (`PlanSnapshot`), so a later
    dispute can be settled without re-asking the user what they approved.
    """
    days = plan.days.all().order_by('week_number', 'day_number')
    if week_numbers:
        days = days.filter(week_number__in=list(week_numbers))

    out = {
        'plan_id': str(plan.id),
        'title': plan.title,
        'weeks': {},
    }
    for day in days:
        week = out['weeks'].setdefault(str(day.week_number), [])
        week.append({
            'day_id': str(day.id),
            'day_number': day.day_number,
            'name': day.name,
            'is_rest_day': day.is_rest_day,
            'exercises': [_exercise_state(ex)
                          for ex in day.exercises.all().order_by('order')],
        })
    return out


def _exercise_state(exercise):
    return {
        'id': str(exercise.id),
        'name': exercise.name,
        'sets': exercise.sets,
        'reps': exercise.reps,
        'rest_seconds': exercise.rest_seconds,
        'notes': exercise.notes,
        'measurement_type': exercise.measurement_type,
        'composite_id': (str(exercise.composite_id)
                         if exercise.composite_id else None),
    }


def _index(snapshot):
    """Every exercise in a snapshot, by pk, with where it sits."""
    out = {}
    for week, days in (snapshot.get('weeks') or {}).items():
        for day in days:
            for exercise in day.get('exercises', []):
                out[exercise['id']] = dict(
                    exercise, week=int(week), day=day['name'],
                    day_number=day['day_number'])
    return out


def diff_snapshots(before, after, approved=None, keep_list=()):
    """
    What changed between two snapshots, and whether it was approved.

    `approved` is the list of changes the user confirmed, each at least
    `{'day': str, 'name': str}`. Anything outside it lands in `unapproved`,
    which the caller must surface plainly rather than reporting success.

    `keep_list` names exercises that must survive the change. A keep-list
    entry that was removed or altered is a FAILURE of the change, not a
    footnote -- it is the specific thing the user asked to be protected.
    """
    old, new = _index(before), _index(after)

    added = [new[i] for i in new.keys() - old.keys()]
    removed = [old[i] for i in old.keys() - new.keys()]
    changed = []
    for pk in old.keys() & new.keys():
        deltas = {f: (old[pk].get(f), new[pk].get(f))
                  for f in TRACKED_FIELDS
                  if _differs(f, old[pk].get(f), new[pk].get(f))}
        if deltas:
            changed.append(dict(new[pk], changes=deltas))

    touched = added + removed + changed
    unapproved = [e for e in touched if not _was_approved(e, approved)]

    wanted = {exercise_names.key(n) for n in keep_list}
    violations = [e for e in removed + changed
                  if exercise_names.key(e['name']) in wanted]

    return {
        'added': _sorted(added),
        'removed': _sorted(removed),
        'changed': _sorted(changed),
        'unapproved': _sorted(unapproved),
        'keep_list_violations': _sorted(violations),
        'clean': not unapproved and not violations,
    }


def _sorted(entries):
    return sorted(entries, key=lambda e: (e.get('week', 0),
                                          e.get('day_number', 0),
                                          e.get('name', '')))


def _was_approved(entry, approved):
    """
    Whether one touched exercise is covered by the approved draft.

    Matched on canonical identity, never on the spelling the draft happened to
    use -- the draft and the stored row routinely differ ("Dumbbell Lateral
    Raise" vs "Dumbbell Lateral Raises") and a string compare would report
    every approved change as unapproved.

    `approved=None` means no draft was supplied, which is not the same as an
    empty draft: with nothing to check against, nothing can be called
    unapproved. An empty LIST means the opposite -- a draft that approved
    nothing, so everything touched is unapproved.
    """
    if approved is None:
        return True
    entry_key = exercise_names.key(entry.get('name', ''))
    for item in approved:
        # A `replace` draft names the INCOMING exercise in `name` and the
        # outgoing one in `replaces`. Matching only on `name` approved the
        # addition and flagged the removal, so every substitution reported
        # "not as approved" -- the diff crying wolf on the most common edit
        # there is, which is how a diff stops being read.
        candidates = {exercise_names.key(item.get('name', ''))}
        if item.get('replaces'):
            candidates.add(exercise_names.key(item['replaces']))
        if entry_key not in candidates:
            continue
        day = item.get('day')
        if day and day != entry.get('day'):
            continue
        week = item.get('week')
        if week is not None and int(week) != entry.get('week'):
            continue
        return True
    return False


# ---------------------------------------------------------------------------
# Propose / apply
# ---------------------------------------------------------------------------

def plan_digest(snapshot):
    """
    A content digest of a plan snapshot, for detecting that it moved on.

    Content rather than a timestamp: not every mutation calls
    `WorkoutPlan.save()` -- the supersets endpoint writes Exercise rows
    directly -- so `updated_at` can sit still while the plan changes
    underneath. `sort_keys` so an equal plan always digests equal.
    """
    import hashlib
    import json

    payload = json.dumps(snapshot, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(payload.encode()).hexdigest()


def library_warnings(draft, user=None):
    """
    Exercises in a draft that the library has never heard of.

    A WARNING, never a rejection. Rejecting would stop the model ever
    introducing a new movement, and plans legitimately introduce new work --
    the library is seeded from one upstream dataset and is not the set of
    exercises a person can do. The entry joins the library on the next deploy.

    What stays banned is the other half: a draft name is never resolved onto
    an existing entry by similarity. That is the actual hazard, and the reason
    this project does exact token-set identity everywhere -- a score threshold
    sent Push-ups to `handstand push-ups` and scored Bench Press 0.87 against
    Overhead Press.
    """
    from .models import ExerciseLibraryEntry

    known = set(ExerciseLibraryEntry.objects
                .filter(models_q_user(user))
                .values_list('canonical_key', flat=True))

    out = []
    seen = set()
    for item in draft or []:
        name = (item.get('name') or '').strip()
        if not name:
            continue
        key = ' '.join(sorted(exercise_names.key(name)))
        if not key or key in known or key in seen:
            continue
        seen.add(key)
        out.append({
            'type': 'unknown_exercise',
            'name': name,
            'message': '%s: new, not in your library' % name,
        })
    return out


def models_q_user(user):
    """Catalogue rows plus this user's own overrides."""
    from django.db.models import Q

    if user is None:
        return Q(user__isnull=True)
    return Q(user__isnull=True) | Q(user=user)


# ---------------------------------------------------------------------------
# Turning a draft into a plan payload
# ---------------------------------------------------------------------------

ADD = 'add'
REMOVE = 'remove'
REPLACE = 'replace'
UPDATE = 'update'
ACTIONS = (ADD, REMOVE, REPLACE, UPDATE)


class DraftError(ValueError):
    """A draft the server cannot carry out, stated in terms the user sees."""


def build_payload(plan, draft, week_numbers=None):
    """
    The plan payload a confirmed draft produces, built from RECORDED STATE.

    The model never re-emits the plan. That was the original failure: chat was
    shown all eight weeks and asked to return all eight, so one dropped line
    in a 56-day reply silently deleted an exercise and its history -- and the
    cost was ~32,000 tokens each way to change one thing.

    Here the model proposes OPERATIONS -- remove this, replace that with this
    -- and the server applies them to the plan it already has. An exercise the
    draft does not mention cannot be dropped, because nothing re-states it.

    Returns a payload in the `days` wire shape, scoped to the weeks touched,
    which `_apply_plan_diff` then reconciles against the live rows.
    """
    from . import exercise_names

    snapshot = snapshot_plan(plan, week_numbers)
    # week -> list of day dicts, each with a mutable exercise list.
    weeks = {
        int(week): [
            {
                'day_number': day['day_number'],
                'week_number': int(week),
                'name': day['name'],
                'is_rest_day': day['is_rest_day'],
                'exercises': [
                    {k: v for k, v in ex.items()
                     if k in ('name', 'sets', 'reps', 'rest_seconds',
                              'notes', 'measurement_type')}
                    for ex in day['exercises']
                ],
            }
            for day in days
        ]
        for week, days in (snapshot.get('weeks') or {}).items()
    }
    if not weeks:
        raise DraftError('this plan has no weeks to change')

    touched = set()
    for item in draft or []:
        action = (item.get('action') or REPLACE).lower()
        if action not in ACTIONS:
            raise DraftError('unknown action %r' % action)

        name = (item.get('name') or '').strip()
        if not name:
            raise DraftError('every change needs an exercise name')

        target_days = _matching_days(weeks, item)
        if not target_days:
            raise DraftError('no day called %r in this plan'
                             % (item.get('day') or '?'))

        for week, day in target_days:
            touched.add(week)
            _apply_one(day, action, item, name, exercise_names)

    return {
        'title': plan.title,
        'description': plan.description or '',
        'duration_weeks': plan.duration_weeks,
        'days': [day for week in sorted(touched) for day in weeks[week]],
    }, touched


def _matching_days(weeks, item):
    """
    Every (week, day) a draft item applies to.

    A draft naming a day but no week applies to that day in EVERY week. That
    is what a user means by "take bench press out" -- not "out of week 4
    only", which would leave it in the other seven and look like the change
    silently failed.
    """
    from . import exercise_names

    wanted_week = item.get('week')
    wanted_day = (item.get('day') or '').strip().lower()
    out = []
    for week, days in weeks.items():
        if wanted_week is not None and int(wanted_week) != week:
            continue
        for day in days:
            if not wanted_day:
                out.append((week, day))
            elif exercise_names.key(day['name']) == exercise_names.key(wanted_day):
                out.append((week, day))
            elif wanted_day in (day['name'] or '').lower():
                out.append((week, day))
    return out


def _apply_one(day, action, item, name, exercise_names):
    """Carry out one draft operation on one day, in place."""
    key = exercise_names.key(name)
    exercises = day['exercises']

    def index_of(target):
        target_key = exercise_names.key(target)
        for i, ex in enumerate(exercises):
            if exercise_names.key(ex['name']) == target_key:
                return i
        return None

    if action == REMOVE:
        i = index_of(name)
        if i is not None:
            exercises.pop(i)
        return

    if action == ADD:
        if index_of(name) is not None:
            return                      # already there; adding twice is a bug
        exercises.append(_new_exercise(item, name))
        return

    if action == UPDATE:
        i = index_of(name)
        if i is None:
            return
        for field in ('sets', 'reps', 'rest_seconds', 'notes'):
            if item.get(field) is not None:
                exercises[i][field] = item[field]
        return

    # REPLACE. `replaces` names the outgoing exercise; without it the draft
    # is an add, because there is nothing to replace.
    outgoing = (item.get('replaces') or '').strip()
    if not outgoing:
        if index_of(name) is None:
            exercises.append(_new_exercise(item, name))
        return
    i = index_of(outgoing)
    if i is None:
        return                          # already gone; nothing to do
    # Keep the slot's prescription unless the draft states otherwise: a
    # substitution is a change of exercise, not of programming.
    existing = exercises[i]
    exercises[i] = {
        'name': name,
        'sets': item.get('sets') or existing.get('sets'),
        'reps': item.get('reps') or existing.get('reps'),
        'rest_seconds': item.get('rest_seconds') or existing.get('rest_seconds'),
        'notes': '',
        'measurement_type': item.get('measurement_type'),
    }
    del key


def _new_exercise(item, name):
    return {
        'name': name,
        'sets': item.get('sets') or 3,
        'reps': item.get('reps') or '8-12',
        'rest_seconds': item.get('rest_seconds'),
        'notes': item.get('notes') or '',
        'measurement_type': item.get('measurement_type'),
    }
