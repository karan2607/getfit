"""
Movement data, and whether two exercises can be supersetted.

Why this exists
---------------
The prompt has always told the model to use supersets ("metabolic resistance
training: compound movements, supersets, short rest"), but nothing checked
whether a proposed pairing was any good. My own first example was
**bench press + push-ups**, which is a bad superset: both push, both chest,
both compound, so each ruins the other.

`exercise_kb.json` was imported from free-exercise-db for its images, and the
upstream dataset carries three more fields per exercise that were dropped:

    force      push / pull / static
    mechanic   compound / isolation
    primary / secondary muscles

That makes superset validity **computable** rather than a matter of AI
judgement. `exercise_muscles.json` is the same 876 exercises, keyed
identically to `exercise_kb.json`, so the already-audited KB matcher resolves
against it unchanged.

What this module is for, and what it is NOT for
-----------------------------------------------
Measured across all 7,875 pairs of the 126 canonical exercise names in
production, these rules **accept 47%**. `Barbell Bench Press + Dumbbell Hammer
Curl` passes as a legitimate antagonist pair, and nobody programs that.

So this is a good SAFETY FILTER and a poor RECOMMENDER. It rejects the 53% that
are actively harmful; it cannot tell you which of the rest a coach would
actually write. Ranking is `superset_pairs.json`'s job — AI proposes, these
rules verify, and only pairings that survive both are kept.

Honest limitation
-----------------
KB resolution is fuzzy, so the muscle data is approximate:
`Barbell Romanian Deadlift` resolves to `barbell deadlift`. The verdict is
therefore only ever used to **reject**, never to invent a pairing, and it
requires data on both sides. A false rejection costs nothing — there is simply
no superset there, which is fine because not every exercise should be in one.
"""

import json
import logging
import os

from . import exercise_types

logger = logging.getLogger(__name__)

_DATA_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'exercise_muscles.json')

_MUSCLES = None

# Muscles that mean an exercise loads the spine. Used to refuse pairing two
# such movements for someone who has stated a back limitation -- the exact
# profile this app's primary user has.
_SPINAL = frozenset(('lower back', 'middle back', 'traps'))


def _load():
    global _MUSCLES
    if _MUSCLES is None:
        try:
            with open(_DATA_PATH) as handle:
                _MUSCLES = json.load(handle)
            logger.info('exercise_muscles loaded: %d entries', len(_MUSCLES))
        except (IOError, OSError, ValueError) as exc:
            logger.warning('exercise_muscles load failed: %s', exc)
            _MUSCLES = {}
    return _MUSCLES


def profile_for(name):
    """
    Movement facts for an exercise name, or None when unknown.

    Resolution goes through the same KB matcher used for images, which was
    audited to 0 wrong matches across 187 production names. None is returned
    unless BOTH `force` and `primary` are present, so a partial record can
    never produce a confident-looking verdict.
    """
    if not name:
        return None
    from .views import _resolve_kb_key          # avoids a circular import

    key, _score = _resolve_kb_key(str(name))
    if not key:
        return None
    record = _load().get(key.lower())
    if not record or not record.get('force') or not record.get('primary'):
        return None
    return {
        'key': key,
        'force': record['force'],
        'mechanic': record.get('mechanic'),
        'primary': frozenset(record['primary']),
        'secondary': frozenset(record.get('secondary') or []),
        'equipment': record.get('equipment') or '',
    }


# NO station-contention rule, deliberately -- I wrote one and removed it.
#
# "Two exercises needing the same apparatus cannot be supersetted" sounds
# obviously true and is false. It rejected `Barbell Bench Press + Barbell Row`,
# which is the textbook antagonist superset AND one of the examples the
# generation prompt recommends: one barbell serves both, you bench it and then
# pick it up and row it. It also rejected `Leg Extension + Leg Curl`, the other
# recommended example, because both resolve to machines.
#
# Fuzzy KB resolution made it worse: `Leg Extension` matches an exercise-ball
# record, so the rejection reasons were wrong as well as unwanted. A rule that
# rejects the canonical examples the prompt teaches is worse than no rule --
# the model would follow the prompt and be silently ungrouped every time.
#
# What survives is availability, which is the part that was actually asked for:
# don't pair equipment the user does not have.

# What each gym type actually has. 'other' and an absent value are UNKNOWN and
# never rejected -- 122 upstream records say "other" and 77 say nothing, so
# treating unknown as unavailable would reject a sixth of the library.
_EQUIPMENT_AVAILABLE = {
    'bodyweight': frozenset(('body only',)),
    'home gym': frozenset((
        'body only', 'dumbbell', 'kettlebells', 'bands', 'barbell',
        'medicine ball', 'exercise ball', 'foam roll', 'e-z curl bar',
    )),
    # 'full gym' has everything, so it is absent on purpose rather than listed.
}


def equipment_for(name):
    """The equipment an exercise needs, or '' when unknown."""
    profile = profile_for(name)
    return profile['equipment'] if profile else ''


def _equipment_problem(a, b, gym):
    """
    Equipment the user does not have, or None when the pair is fine.

    Split out of superset_verdict so the ordering of the existing muscle rules
    is left exactly as it was -- this adds a dimension rather than reshuffling
    one.

    `gym` is a WorkoutPlan.equipment value ('bodyweight' | 'home gym' |
    'full gym'); anything else, including '', means unknown and skips the
    availability half entirely.
    """
    a_equip, b_equip = a['equipment'], b['equipment']
    available = _EQUIPMENT_AVAILABLE.get(gym)
    if available is not None:
        missing = sorted({
            equip for equip in (a_equip, b_equip)
            if equip and equip not in available
            # Unknown values are not "missing", they are unverified.
            and equip != 'other'
        })
        if missing:
            return ('unavailable',
                    'needs %s, which a %s does not have'
                    % (' and '.join(missing), gym))
    return None


# Verdict kinds, best first.
ANTAGONIST = 'antagonist'
NON_COMPETING = 'non_competing'
EXHAUST = 'exhaust'

ACCEPTED_KINDS = (ANTAGONIST, NON_COMPETING, EXHAUST)


def superset_verdict(name_a, name_b, has_back_limitation=False, gym=''):
    """
    May these two be supersetted? Returns (ok, kind, reason).

    `kind` is one of ACCEPTED_KINDS when ok, otherwise a short rejection code
    suitable for logging and for showing in the manual pairing picker.

    `gym` is the plan's equipment value. It adds one rejection -- equipment the
    user does not have -- on top of the muscle rules, which are unchanged.
    Omitting it reproduces the previous behaviour exactly, which is what every
    pre-existing caller gets.
    """
    if not name_a or not name_b:
        return False, 'missing', 'an exercise name is missing'
    if str(name_a).strip().lower() == str(name_b).strip().lower():
        return False, 'same_exercise', 'an exercise cannot pair with itself'

    # A warmup block is not training volume; pairing it is meaningless.
    for name in (name_a, name_b):
        if exercise_types.is_warmup_name(name):
            return False, 'warmup', 'warmup blocks are not supersetted'

    a = profile_for(name_a)
    b = profile_for(name_b)
    if a is None or b is None:
        unknown = name_a if a is None else name_b
        return False, 'no_data', 'no movement data for %r' % str(unknown)

    # Two timed holds back to back save no time and just exhaust the user.
    if a['force'] == 'static' and b['force'] == 'static':
        return False, 'two_holds', 'two static holds save no time'

    overlap = a['primary'] & b['primary']
    both_compound = a['mechanic'] == 'compound' and b['mechanic'] == 'compound'
    antagonist = (
        not overlap
        and a['force'] != b['force']
        and 'static' not in (a['force'], b['force'])
    )

    if overlap and both_compound:
        return (False, 'competing',
                'both compound on %s' % ', '.join(sorted(overlap)))

    # Two compounds are only acceptable as a true antagonist pair. Bench press
    # + hack squat shares no muscle but is still two heavy compounds back to
    # back, which is systemically exhausting rather than efficient.
    if both_compound and not antagonist:
        return (False, 'two_compounds',
                'two compound lifts that are not an antagonist pair')

    # After the muscle rules deliberately: when a pair is wrong on BOTH counts,
    # "both push, both chest" is the more useful thing to tell the user than
    # "you don't own a cable machine".
    problem = _equipment_problem(a, b, gym)
    if problem:
        return (False,) + problem

    if has_back_limitation:
        a_spinal = bool((a['primary'] | a['secondary']) & _SPINAL)
        b_spinal = bool((b['primary'] | b['secondary']) & _SPINAL)
        if a_spinal and b_spinal:
            return (False, 'spinal_load',
                    'both load the back, and a back limitation was stated')

    if antagonist:
        return (True, ANTAGONIST,
                '%s vs %s, no shared primary muscle'
                % (a['force'], b['force']))

    if not (a['primary'] | a['secondary']) & (b['primary'] | b['secondary']):
        return True, NON_COMPETING, 'no shared muscles at all'

    if overlap and {a['mechanic'], b['mechanic']} == {'compound', 'isolation'}:
        return (True, EXHAUST,
                'same muscle (%s) with one isolation movement'
                % ', '.join(sorted(overlap)))

    shared = (a['primary'] | a['secondary']) & (b['primary'] | b['secondary'])
    return False, 'overlap', 'shared muscles: %s' % ', '.join(sorted(shared))


def can_superset(name_a, name_b, has_back_limitation=False, gym=''):
    ok, _kind, _reason = superset_verdict(
        name_a, name_b, has_back_limitation, gym)
    return ok


def valid_partners(name, candidates, has_back_limitation=False, gym=''):
    """
    Which of `candidates` may pair with `name`, best kind first.

    Used by the manual picker (so a bad pairing is not reachable by hand) and
    by the prompt builder (so the model picks from a list rather than guessing).
    """
    out = []
    for candidate in candidates:
        ok, kind, reason = superset_verdict(
            name, candidate, has_back_limitation, gym)
        if ok:
            out.append({'name': candidate, 'kind': kind, 'reason': reason})
    order = {kind: index for index, kind in enumerate(ACCEPTED_KINDS)}
    out.sort(key=lambda item: (order.get(item['kind'], 99), item['name']))
    return out
