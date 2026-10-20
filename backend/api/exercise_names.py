"""
Canonical exercise names.

This module is the SINGLE source of truth for deciding whether two exercise
names refer to the same movement. Nothing else may re-derive this.

Why this exists
---------------
`_exercise_history` matches logged sets on `exercise_name__iexact`, so two
spellings of one movement split a user's history in half and the coaching advice
only ever sees one part. In production this was not hypothetical:

    Incline Dumbbell Press                  88 rows
    Dumbbell Incline Press                   3 rows   <- same exercise

    Chest-Supported T-Bar Row               10 rows
    T-Bar Row (Chest Supported)              4 rows   <- same exercise
    Chest Supported T-Bar Row                4 rows   <- same exercise

    Dumbbell Triceps Extension (Overhead)   10 rows
    Dumbbell Overhead Triceps Extension      3 rows   <- same exercise
    Overhead Dumbbell Triceps Extension      3 rows   <- same exercise
    Overhead Dumbbell Extension (Seated)     3 rows   <- same exercise

190 distinct names existed across `Exercise` and `SetLog` for what is a far
smaller set of actual movements. A user topping their rep range on week 5 could
be told they had no history at all, because week 1 spelled it differently.

How it decides -- and what it refuses to do
-------------------------------------------
Two mechanisms, both exact. **No fuzzy scoring.** The F1 name scorer used for
image matching has genuine false positives on this data -- `Barbell Bench Press`
vs `Barbell Overhead Press` score 0.87, as do `Cable Face Pulls` vs
`Cable Pull-Through` and `Machine Chest Press` vs `Machine Shoulder Press`.
Merging any of those would corrupt real training history, so a score threshold
is not an acceptable mechanism here.

1. **Token-set identity.** After normalising case, punctuation and plurals, two
   names with the *same set of words* are the same movement. This is exact set
   equality, not similarity, and it resolves the majority of real collisions:
   word order (`Incline Barbell Press` / `Barbell Incline Press`), punctuation
   (`Single Arm` / `Single-Arm`), parenthetical restatement
   (`Chest-Supported T-Bar Row` / `T-Bar Row (Chest Supported)`) and plurals.

2. **An explicit ALIASES table** for true synonyms that token-set identity
   cannot see (`Hammer Curls` -> `Dumbbell Hammer Curl`), each one hand-written
   and reviewable.

Folding happens ONLY onto a name in the registry below. An unregistered pair of
variants is left alone rather than having a canonical form invented for it: this
module never guesses.

Order-sensitive phrases
-----------------------
Token-set identity has exactly one trap in this domain, and it is live in the
data: `High-to-Low Cable Crossover` and `Cable Crossover (Low-to-High)` have
identical token sets and are DIFFERENT exercises (the cable path is reversed).
`_DIRECTION_RE` therefore collapses `X-to-Y` into one ordered token before
tokenising, so the two never compare equal.
"""

import re

# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

# "high-to-low", "low to mid" -> one ordered token, so reversing the direction
# does not produce the same token set. This is the one place where word order
# carries meaning.
_DIRECTION_RE = re.compile(r'\b(\w+)[\s\-]*to[\s\-]*(\w+)\b', re.I)

# Words that add no identifying information. Dropping them lets
# "Machine Dips (Assisted if needed)" and "Assisted Machine Dips" agree.
#
# Every addition here is load-bearing and must be checked against the real name
# list. "side" was in this set on the first pass and silently collapsed
# `Plank` (90 logged rows) into `Side Plank` (80 rows) -- two different
# exercises -- because it left both with the token set {plank}. A noise word
# that appears in any real exercise NAME is not noise.
_NOISE_WORDS = frozenset((
    'a', 'an', 'the', 'or', 'and', 'if', 'needed', 'optional',
    'variation', 'variations', 'each', 'per',
    'alternating', 'alternate',
))


def _fold_plural(word):
    """
    Singularise conservatively.

    The same trap as the KB stemmer: a naive trailing-`s` strip turns "press"
    into "pres" and "raises" into "rais". Only strip when the result is still a
    plausible stem.
    """
    if len(word) > 4 and word.endswith('ies'):
        return word[:-3] + 'y'
    if len(word) > 4 and word.endswith('sses'):
        return word[:-2]
    if len(word) > 3 and word.endswith('es') and word[:-2].endswith(
            ('ch', 'sh', 'x', 'z')):
        return word[:-2]
    # Threshold is 3, not 4, so "ups" -> "up" and "dips" -> "dip": the
    # push-up/dip family is always written both ways.
    if (len(word) > 2 and word.endswith('s')
            and not word.endswith(('ss', 'us', 'is'))):
        return word[:-1]
    return word


# Placeholder that survives punctuation stripping, so the ordered direction
# token can be rebuilt after every other hyphen has been split on.
_DIR_SENTINEL = '\x00'


def normalize(name):
    """
    Lowercase, de-punctuate and collapse whitespace. Word order preserved.

    Hyphens are split, not kept: "Single-Arm Dumbbell Row" and "Single Arm
    Dumbbell Row" are the same exercise and must produce the same words. The
    only exception is a direction phrase, which is protected first -- see
    _DIRECTION_RE.
    """
    if not name:
        return ''
    text = str(name).lower().replace('&', ' and ')
    text = _DIRECTION_RE.sub(
        lambda m: '%s%sto%s%s' % (m.group(1), _DIR_SENTINEL,
                                  _DIR_SENTINEL, m.group(2)), text)
    text = re.sub(r"[^a-z0-9%s\s]" % _DIR_SENTINEL, ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text.replace(_DIR_SENTINEL, '-')


def tokens(name):
    """Identifying words, plural-folded, noise dropped."""
    out = []
    for word in normalize(name).split():
        if '-to-' in word:
            out.append(word)                 # ordered direction token, as-is
            continue
        if word in _NOISE_WORDS:
            continue
        out.append(_fold_plural(word))
    return out


def key(name):
    """
    The identity of a movement: its set of identifying words.

    Set, not sequence -- so word order and punctuation do not matter, while a
    different word (press vs extension, bench vs overhead) always does.
    """
    return frozenset(tokens(name))


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------
# The authoritative spelling for every movement that had colliding variants in
# production. Any name whose token set matches one of these resolves to it.
#
# Where two spellings were equally defensible the more frequent one in real
# data won, so the backfill rewrites fewer rows and the UI keeps showing what
# the user is used to.

CANONICAL_NAMES = (
    # -- chest ------------------------------------------------------------
    'Barbell Bench Press',
    'Incline Barbell Press',
    'Close-Grip Bench Press',
    'Flat Dumbbell Press',
    'Incline Dumbbell Press',
    'Dumbbell Floor Press',
    'Machine Chest Press',
    'Pec Deck Fly Machine',
    'High-to-Low Cable Crossover',
    'Low-to-High Cable Crossover',
    'Standing Cable Crossover',
    # -- back -------------------------------------------------------------
    'Lat Pulldown (Wide Grip)',
    'Lat Pulldown (Neutral Grip)',
    'Seated Cable Row (Neutral Grip)',
    'Seated Cable Row (Wide Grip)',
    'Chest-Supported T-Bar Row',
    'Dumbbell Bent-Over Row',
    'Single-Arm Dumbbell Row',
    'Cable Face Pulls',
    'Cable Pull-Through',
    # -- shoulders --------------------------------------------------------
    'Barbell Overhead Press',
    'Dumbbell Overhead Press',
    'Machine Shoulder Press',
    'Dumbbell Lateral Raises',
    'Cable Lateral Raise',
    'Resistance Band Pull-Apart',
    # -- arms -------------------------------------------------------------
    'Dumbbell Bicep Curl',
    'Dumbbell Hammer Curl',
    'Cable Bicep Curls (Straight Bar Attachment)',
    'Overhead Dumbbell Triceps Extension',
    'Cable Triceps Pushdown (Rope Attachment)',
    'Machine Dips (Assisted)',
    # -- legs -------------------------------------------------------------
    'Barbell Back Squat',
    'Barbell Front Squat',
    'Dumbbell Goblet Squat',
    'Dumbbell Sumo Squat',
    'Leg Press',
    'Hack Squat Machine',
    'Barbell Romanian Deadlift',
    'Dumbbell Romanian Deadlift',
    'Lying Hamstring Curl',
    'Glute-Ham Raise (GHR)',
    'Seated Calf Raise',
    'Standing Calf Raises (Machine)',
    'Dumbbell Walking Lunges',
    'Barbell Glute Bridge',
    'Glute Bridges',
    'Kettlebell Swings',
    # -- core -------------------------------------------------------------
    'Cable Ab Crunches',
    'Russian Twist',
    'Bicycle Crunch',
    'Mountain Climbers',
    'Plank',
    'Side Plank',
)

# True synonyms: same movement, DIFFERENT words, so token-set identity cannot
# see them. Keys are matched after normalisation, so case, punctuation, plurals
# and word order are already handled -- one entry covers every such spelling.
#
# Every entry here is a judgement call that can be read and vetoed. Nothing is
# inferred by similarity.
ALIASES = {
    # An intensity annotation is not a different exercise, and keeping them
    # apart is what hides a user's loading history from the progression logic.
    'cable face pull heavy': 'Cable Face Pulls',
    'face pull': 'Cable Face Pulls',
    'lat pulldown neutral grip heavy': 'Lat Pulldown (Neutral Grip)',
    'heavy leg press': 'Leg Press',
    'hack squat machine heavy leg press': 'Hack Squat Machine',
    'standing calf raise': 'Standing Calf Raises (Machine)',

    # Hammer curls are dumbbell work unless stated otherwise.
    'hammer curl': 'Dumbbell Hammer Curl',
    # A supinating curl IS the standard dumbbell curl.
    'dumbbell bicep curl supinating': 'Dumbbell Bicep Curl',
    'cable bicep curl': 'Cable Bicep Curls (Straight Bar Attachment)',
    'cable bicep curl straight bar ez bar attachment':
        'Cable Bicep Curls (Straight Bar Attachment)',

    # The overhead dumbbell triceps extension family. Five spellings in
    # production, including one that omits "triceps" and one that notes the
    # single dumbbell -- all the same movement.
    'overhead dumbbell extension seated': 'Overhead Dumbbell Triceps Extension',
    'dumbbell overhead tricep extension single dumbbell':
        'Overhead Dumbbell Triceps Extension',

    'rope triceps pushdown': 'Cable Triceps Pushdown (Rope Attachment)',
    'triceps pushdown rope attachment': 'Cable Triceps Pushdown (Rope Attachment)',
    'triceps pushdown rope bar': 'Cable Triceps Pushdown (Rope Attachment)',
    'cable triceps pushdown rope': 'Cable Triceps Pushdown (Rope Attachment)',

    # Bench press naming. "Chest press" on a flat bench with a barbell is the
    # bench press; the MACHINE chest press is a different exercise and is NOT
    # aliased here.
    'flat barbell press': 'Barbell Bench Press',
    'barbell chest press flat bench': 'Barbell Bench Press',
    'close grip barbell bench press': 'Close-Grip Bench Press',
    'dumbbell bench press': 'Flat Dumbbell Press',

    'pec deck fly': 'Pec Deck Fly Machine',
    # Judgement call, flag if you disagree: the prompt states the only valid
    # crossover variants are High-to-Low, Low-to-High and Standing, yet
    # "Mid-to-High" (17 rows) and "Low-to-Mid" (10 rows) are both in the data.
    # Both are UPWARD cable paths, so both resolve to Low-to-High rather than
    # being left as two more orphan names.
    'cable crossover low-to-mid': 'Low-to-High Cable Crossover',
    'cable crossover mid-to-high': 'Low-to-High Cable Crossover',

    # "Leg curl" and "hamstring curl" are the same machine. The ambiguous
    # "(Lying or Seated)" spellings resolve to lying, which is what the user
    # has 48 rows of.
    'lying leg curl': 'Lying Hamstring Curl',
    'hamstring curl lying seated': 'Lying Hamstring Curl',
    'leg curl seated lying': 'Lying Hamstring Curl',
    'glute ham raise lying hamstring curl': 'Glute-Ham Raise (GHR)',

    'dumbbell romanian deadlift rdl': 'Dumbbell Romanian Deadlift',
    'romanian deadlift rdl': 'Barbell Romanian Deadlift',

    'goblet squat': 'Dumbbell Goblet Squat',
    'goblet squat bodyweight light dumbbell': 'Dumbbell Goblet Squat',

    'light band pull apart': 'Resistance Band Pull-Apart',
    'kettlebell swing american russian': 'Kettlebell Swings',
    'cable crunch': 'Cable Ab Crunches',
    'single arm dumbbell row supported': 'Single-Arm Dumbbell Row',
    'dumbbell row': 'Dumbbell Bent-Over Row',
}

# Deliberately NOT aliased, each one a pair the F1 scorer rates 0.80-0.87 and
# which a threshold-based merge would have collapsed:
#
#   Barbell Bench Press        vs  Barbell Overhead Press     (0.87)
#   Machine Chest Press        vs  Machine Shoulder Press     (0.87)
#   Cable Face Pulls           vs  Cable Pull-Through         (0.87)
#   Dumbbell Bicep Curl        vs  Dumbbell Hammer Curl       (0.87)
#   Dumbbell Bench Press       vs  Dumbbell Overhead Press    (0.87)
#   Overhead Dumbbell Extension vs Overhead Dumbbell Press    (0.80)
#   Dumbbell Goblet Squat      vs  Dumbbell Sumo Squat        (0.87)
#   Glute Bridges              vs  Dumbbell Glute Bridge      (0.80)
#   Seated Cable Row (Wide)    vs  Seated Cable Row (Neutral) (1.00)
#   Walking Lunges             vs  Dumbbell Walking Lunges    (0.95)
#
# That last one was briefly aliased and then removed: the dry-run showed it
# would rewrite a BEGINNER plan's bodyweight "Walking Lunges" into the loaded
# variant, which is the same unloaded-vs-loaded merge already refused above for
# Glute Bridges. "Walking Lunges (Dumbbell)" still folds into the loaded name
# by token-set identity, which is correct -- it says dumbbell.
#
# Grip variants stay separate on purpose: a wide-grip and a close-grip row are
# loaded differently, so blending them would corrupt the progression history
# this module exists to repair.


def _build_index():
    index = {}
    for name in CANONICAL_NAMES:
        index[key(name)] = name
    for variant, target in ALIASES.items():
        if target not in CANONICAL_NAMES:
            raise ValueError(
                'ALIASES target %r is not in CANONICAL_NAMES' % target)
        index.setdefault(key(variant), target)
    return index


_BY_KEY = _build_index()


def canonical(name):
    """
    The canonical spelling of `name`, or `name` unchanged when unrecognised.

    Never guesses: a movement with no registry entry is returned as-is, so a
    new exercise is simply not folded rather than being merged into whatever
    looked closest.
    """
    if not name:
        return name
    text = str(name).strip()
    if not text:
        return text
    return _BY_KEY.get(key(text), text)


def is_canonical(name):
    """True if `name` is already the canonical spelling of its movement."""
    if not name:
        return True
    return canonical(name) == str(name).strip()


def same_movement(left, right):
    """
    True if two names refer to the same movement.

    Compares IDENTITY, not the canonical display string, so it also answers
    correctly for movements with no registry entry: "Zercher Squat" and
    "Squat (Zercher)" are the same exercise even though neither has a canonical
    spelling on file.

    This is deliberately more permissive than `canonical()`, and the split is
    the point. Deciding what to *call* something requires a human-written
    registry entry. Deciding whether two names are the *same thing* needs only
    exact token-set identity, so history reunification works for exercises
    nobody has curated yet.
    """
    if not left or not right:
        return False
    return key(canonical(left)) == key(canonical(right))


def variants_of(name):
    """
    Every registered spelling that resolves to the same movement as `name`.

    Used by the history lookup so a query can match rows still stored under an
    old spelling, and by the backfill to report what it would rewrite.
    """
    target = canonical(name)
    out = {target}
    for variant, mapped in ALIASES.items():
        if mapped == target:
            out.add(variant)
    return out
