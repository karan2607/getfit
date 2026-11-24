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


def _day_index(snapshot):
    """Every day in a snapshot, by pk, with its name and position."""
    out = {}
    for week, days in (snapshot.get('weeks') or {}).items():
        for day in days:
            day_id = day.get('day_id')
            if not day_id:
                continue        # a hand-built snapshot with no row behind it
            out[day_id] = {
                'week': int(week), 'name': day['name'],
                'day_number': day['day_number'],
            }
    return out


def _day_diff(before, after):
    """
    Days that were renamed or moved, keyed by the row rather than the slot.

    By pk, because that is the whole point of moving a day's `day_number`
    instead of swapping two days' contents: the row survives, so the diff can
    say "this day moved" rather than reporting both days as rewritten.

    COLLAPSED ACROSS WEEKS. A day move with no week applies to every week, so
    an 8-week plan produced 24 entries for one logical change -- the same
    three sentences eight times -- and the confirmation card became unreadable
    at exactly the size a real programme is. The weeks a change covers are
    carried in `weeks` instead, and `week` stays as the earliest so ordering
    and older readers are unaffected.
    """
    old, new = _day_index(before), _day_index(after)
    grouped = {}
    for day_id in old.keys() & new.keys():
        was, now = old[day_id], new[day_id]
        if was['name'] == now['name'] and was['day_number'] == now['day_number']:
            continue
        key = (was['name'], now['name'], was['day_number'], now['day_number'])
        grouped.setdefault(key, []).append(now['week'])

    out = []
    for (from_name, name, from_position, position), weeks in grouped.items():
        weeks = sorted(set(weeks))
        out.append({
            'week': weeks[0], 'weeks': weeks,
            'from_name': from_name, 'name': name,
            'from_position': from_position, 'position': position,
        })
    return sorted(out, key=lambda d: (d['week'], d['position']))


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
    renames = day_renames(approved)

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
    unapproved = [e for e in touched
                  if not _was_approved(e, approved, renames)]

    wanted = {_identity(n) for n in keep_list}
    violations = [e for e in removed + changed
                  if _identity(e['name']) in wanted]

    return {
        'added': _sorted(added),
        'removed': _sorted(removed),
        'changed': _sorted(changed),
        # Renames and moves. Separate from `changed`, which is about
        # exercises: a day that moved has not had a single exercise touched,
        # and reporting it as 56 changed rows would bury the one fact the
        # user wants confirmed.
        'day_changes': _day_diff(before, after),
        'unapproved': _sorted(unapproved),
        'keep_list_violations': _sorted(violations),
        'clean': not unapproved and not violations,
    }


def _sorted(entries):
    return sorted(entries, key=lambda e: (e.get('week', 0),
                                          e.get('day_number', 0),
                                          e.get('name', '')))


def _identity(name):
    """
    The identity apply itself uses: the token set of the CANONICAL spelling.

    Token-set identity alone is not enough here, because apply rewrites the
    name through the registry on the way in. A draft for "Dumbbell Bench
    Press" is stored as "Flat Dumbbell Press", whose token set is different,
    so the diff reported the user's own confirmed change as unapproved -- on
    every week of the plan. Canonicalising both sides first closes that gap
    without widening what counts as the same movement: an unregistered name
    is returned unchanged and still compared on its own tokens.
    """
    return exercise_names.key(exercise_names.canonical(name or ''))


def _was_approved(entry, approved, renames=None):
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
    entry_key = _identity(entry.get('name', ''))
    for item in approved:
        # A day action approves no exercise change. Its `name` is a day name,
        # and letting it into `candidates` would approve any exercise that
        # happened to share the day's name.
        if (item.get('action') or '').lower() in DAY_ACTIONS:
            continue
        # A `replace` draft names the INCOMING exercise in `name` and the
        # outgoing one in `replaces`. Matching only on `name` approved the
        # addition and flagged the removal, so every substitution reported
        # "not as approved" -- the diff crying wolf on the most common edit
        # there is, which is how a diff stops being read.
        candidates = {_identity(item.get('name', ''))}
        if item.get('replaces'):
            candidates.add(_identity(item['replaces']))
        if entry_key not in candidates:
            continue
        day = item.get('day')
        if day and not _same_day(day, entry.get('day'), renames):
            continue
        week = item.get('week')
        if week is not None and int(week) != entry.get('week'):
            continue
        return True
    return False


def _same_day(drafted, actual, renames=None):
    """
    Whether a draft's day name and a stored day name are the same day.

    A draft that renames a day and then changes an exercise on it names the
    day by its OLD name -- that is what the user called it. Without the
    rename map every such change reads as unapproved, and the diff would cry
    wolf on exactly the restructure it was asked to make.
    """
    if drafted == actual:
        return True
    renamed = (renames or {}).get(exercise_names.key(drafted or ''))
    return bool(renamed) and (exercise_names.key(renamed)
                              == exercise_names.key(actual or ''))


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
        # A day action's `name` is a DAY name. Running it past the exercise
        # library would warn that "Pull + Abs" is not an exercise, which is
        # true and useless.
        if (item.get('action') or '').lower() in DAY_ACTIONS:
            continue
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

#: Operations on a DAY rather than on an exercise. They are applied to the
#: `WorkoutDay` rows themselves (see `apply_day_structure`), not through the
#: exercise payload, so the rest of the draft vocabulary is unchanged.
RENAME_DAY = 'rename_day'
MOVE_DAY = 'move_day'
DAY_ACTIONS = (RENAME_DAY, MOVE_DAY)

ACTIONS = (ADD, REMOVE, REPLACE, UPDATE) + DAY_ACTIONS


class DraftError(ValueError):
    """A draft the server cannot carry out, stated in terms the user sees."""


# ---------------------------------------------------------------------------
# Day-level operations: rename, and move
# ---------------------------------------------------------------------------
#
# These exist because the draft vocabulary could only ever address an
# exercise. Asked to restructure a week -- "make the pull day the second day,
# and call the accessory day Pull + Abs" -- the agent had no field to put
# that in, so it stopped and asked the user to do it by hand. The schema has
# to be able to express what the user actually asks for, or the model's only
# honest answer is that it cannot help.
#
# They are carried out on the WorkoutDay ROWS, deliberately, and not by
# re-emitting the days in a new order. `views._apply_plan_diff` matches days
# on (week_number, day_number): a payload that swapped two days' contents
# would hand day 2's exercises to day 3's row, every Exercise pk would change,
# and every SetLog would be orphaned from the movement it was performed on --
# the exact destruction `_apply_plan_diff` was written to stop. Moving the
# row's own `day_number` keeps the row, and with it the sessions, the logged
# sets and `last_completed_at`, attached to the work actually done on it.

#: How far out of the way a day is parked while a week is renumbered.
#: `(plan, week_number, day_number)` is unique, so a reshuffle collides with
#: itself halfway through unless every row first moves somewhere nobody is.
_PARK_OFFSET = 1000


def apply_day_structure(plan, draft):
    """
    Carry out a draft's day-level operations, in place, on the plan's rows.

    Runs BEFORE the exercise payload is built, so a draft may rename a day and
    then add exercises to it in the same breath.

    Returns ``(ops, renames)``: what was done, and a map from each renamed
    day's old name onto its new one. The map is what lets the rest of the
    draft go on calling the day what the user called it.
    """
    ops, renames = [], {}
    items = [item for item in draft or []
             if (item.get('action') or '').lower() in DAY_ACTIONS]
    if not items:
        return ops, renames

    for item in items:
        action = (item.get('action') or '').lower()
        wanted = (item.get('day') or '').strip()
        if not wanted:
            raise DraftError('a day change must say which day it applies to')

        rows = _day_rows(plan, wanted, item.get('week'), renames)
        if not rows:
            raise DraftError('no day called %r in this plan' % wanted)

        if action == RENAME_DAY:
            ops.extend(_rename_days(rows, item, renames))
        else:
            ops.extend(_move_days(plan, rows, item))

    return ops, renames


def _rename_days(rows, item, renames):
    new_name = (item.get('name') or '').strip()
    if not new_name:
        raise DraftError('a rename must say what the day becomes')

    out = []
    for day in rows:
        old_name = day.name
        if old_name == new_name:
            continue
        day.name = new_name
        day.save(update_fields=['name'])
        # Recorded both ways round: the user reads "Pull day -> Pull + Abs",
        # and the rest of the draft needs to find the day under either name.
        renames[exercise_names.key(old_name)] = new_name
        out.append({'action': RENAME_DAY, 'week': day.week_number,
                    'from_name': old_name, 'name': new_name,
                    'position': day.day_number})
    return out


def _move_days(plan, rows, item):
    position = item.get('to_position')
    if position is None:
        raise DraftError('a move must say which position the day moves to')
    try:
        position = int(position)
    except (TypeError, ValueError):
        raise DraftError('to_position must be a whole number, 1 for the first '
                         'day of the week')
    if position < 1:
        raise DraftError('to_position counts from 1, not from 0')

    out = []
    for day in rows:
        moved = _move_one_day(plan, day, position)
        if moved:
            out.append(moved)
    return out


def _move_one_day(plan, day, position):
    """Put one day at `position` in its week, shifting the rest around it."""
    siblings = list(plan.days.filter(week_number=day.week_number)
                    .order_by('day_number'))
    before = [d.pk for d in siblings]
    from_position = day.day_number
    park = max(d.day_number for d in siblings) + _PARK_OFFSET

    order = [d for d in siblings if d.pk != day.pk]
    # A position past the end means last, not an error: "move it to the end"
    # is a reasonable thing to ask and clamping is what the user meant.
    index = min(position - 1, len(order))
    order.insert(index, day)
    if [d.pk for d in order] == before:
        return None                     # already there

    for offset, row in enumerate(order, start=1):
        row.day_number = park + offset
        row.save(update_fields=['day_number'])
    for number, row in enumerate(order, start=1):
        row.day_number = number
        row.order = number - 1
        row.save(update_fields=['day_number', 'order'])

    return {'action': MOVE_DAY, 'week': day.week_number, 'name': day.name,
            'from_position': from_position, 'position': day.day_number}


def _day_rows(plan, wanted, week=None, renames=None):
    """
    The day rows a draft item names: one per week unless a week is given.

    Omitting the week means every week, exactly as it does for an exercise
    change -- "put the pull day second" is a statement about the programme,
    not about week 3.
    """
    rows = plan.days.all().order_by('week_number', 'day_number')
    if week is not None:
        rows = rows.filter(week_number=int(week))

    key = exercise_names.key(wanted)
    renamed = (renames or {}).get(key)
    renamed_key = exercise_names.key(renamed) if renamed else None

    out = []
    for day in rows:
        day_key = exercise_names.key(day.name or '')
        if day_key == key:
            out.append(day)
        elif renamed_key and day_key == renamed_key:
            out.append(day)
        elif wanted.lower() in (day.name or '').lower():
            out.append(day)
    return out


def validate_draft(draft, plan=None):
    """
    Refuse a draft now, rather than at the confirm button.

    The HTTP propose endpoint checked the shape of a draft; the agent's
    `propose_plan_change` checked only that the list was not empty. So the
    model could propose against a day it had invented, the server would
    record it, the card would render, and Confirm returned 400 bad_draft --
    exactly the "button that can only fail" that withholding stale drafts
    exists to prevent, reached by a different door.

    Both callers use this. With `plan`, it also checks that every day the
    draft names actually exists, which is the check that was only ever run
    at apply time.

    Raises DraftError, whose message is written for the person reading it.
    """
    if not isinstance(draft, list) or not draft:
        raise DraftError('changes must be a non-empty list')

    for item in draft:
        if not isinstance(item, dict):
            raise DraftError('each change has to be an object')

        action = (item.get('action') or REPLACE).lower()
        if action not in ACTIONS:
            raise DraftError('unknown action %r' % action)

        name = (item.get('name') or '').strip()
        day = (item.get('day') or '').strip()

        if action in DAY_ACTIONS:
            # A day action is addressed by `day`; `name` carries the NEW day
            # name for a rename and nothing at all for a move.
            if not day:
                raise DraftError('a day change has to say which day')
            if action == RENAME_DAY and not name:
                raise DraftError('a rename has to say the new name')
            if action == MOVE_DAY and not item.get('to_position'):
                raise DraftError('a move has to say the new position')
        elif not name:
            raise DraftError('every change needs an exercise name')

    if plan is not None:
        _validate_days_exist(plan, draft)


def _validate_days_exist(plan, draft):
    """Every day the draft addresses is a day the plan actually has."""
    snapshot = snapshot_plan(plan)
    weeks = {
        int(week): [
            {'day_number': d['day_number'], 'week_number': int(week),
             'name': d['name']}
            for d in days
        ]
        for week, days in (snapshot.get('weeks') or {}).items()
    }
    if not weeks:
        raise DraftError('this plan has no weeks to change')

    # A draft may rename a day and then address it by its NEW name --
    # "rename Cardio to Pull + Abs, then add a Lat Pulldown to Pull + Abs"
    # is one perfectly ordinary restructure. At apply time the rows have
    # already been renamed by `apply_day_structure`, so the later item
    # resolves; here nothing has been renamed yet, so the new name has to be
    # treated as valid from the moment the rename is declared. Without this
    # the validator rejects the restructure it is supposed to wave through.
    pending_names = {
        (item.get('name') or '').strip()
        for item in draft
        if (item.get('action') or '').lower() == RENAME_DAY
        and (item.get('name') or '').strip()
    }

    renames = day_renames(draft)
    for item in draft:
        day = (item.get('day') or '').strip()
        if not day:
            # A change with no day applies plan-wide; nothing to resolve.
            continue
        if day in pending_names:
            continue
        if not _matching_days(weeks, item, renames):
            raise DraftError('no day called %r in this plan' % item['day'])


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

    # A day the draft renames is addressed by its OLD name everywhere else in
    # that draft -- that is the name the user used and the name the model read
    # out of the plan. `apply_day_structure` has already renamed the row by
    # the time this runs, so without the map every later item in the draft
    # would fail to find its day.
    renames = day_renames(draft)

    touched = set()
    for item in draft or []:
        action = (item.get('action') or REPLACE).lower()
        if action not in ACTIONS:
            raise DraftError('unknown action %r' % action)

        if action in DAY_ACTIONS:
            # Already carried out on the rows. The week is still marked
            # touched so the payload speaks for it and `_apply_plan_diff`
            # reconciles the days it now holds rather than none of them.
            for week, _day in _matching_days(weeks, item, renames):
                touched.add(week)
            continue

        name = (item.get('name') or '').strip()
        if not name:
            raise DraftError('every change needs an exercise name')

        target_days = _matching_days(weeks, item, renames)
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


def day_renames(draft):
    """Old day name (as a key) -> new day name, for every rename in a draft."""
    out = {}
    for item in draft or []:
        if (item.get('action') or '').lower() != RENAME_DAY:
            continue
        old_name = (item.get('day') or '').strip()
        new_name = (item.get('name') or '').strip()
        if old_name and new_name:
            out[exercise_names.key(old_name)] = new_name
    return out


def _matching_days(weeks, item, renames=None):
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
    wanted_key = exercise_names.key(wanted_day) if wanted_day else None
    renamed = (renames or {}).get(wanted_key) if wanted_key else None
    renamed_key = exercise_names.key(renamed) if renamed else None

    out = []
    for week, days in weeks.items():
        if wanted_week is not None and int(wanted_week) != week:
            continue
        for day in days:
            day_key = exercise_names.key(day['name'])
            if not wanted_day:
                out.append((week, day))
            elif day_key == wanted_key:
                out.append((week, day))
            elif renamed_key and day_key == renamed_key:
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
