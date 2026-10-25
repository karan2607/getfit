"""
Canonical exercise measurement-type classification.

This module is the SINGLE source of truth for deciding how an exercise is
measured. Nothing else in the codebase may re-derive this with its own regexes.

Why this exists
---------------
`Exercise.measurement_type` was previously written straight from AI output with
`.get('measurement_type', 'weight_reps')` and read by nothing server-side. A
missing or bogus value silently became 'weight_reps', which is why warmups and
planks kept rendering weight+reps inputs. The server now decides, and the AI only
gets a vote.

The field/unit contract per type is defined in FIELD_CONTRACT and is enforced on
write by `coerce_set_fields`, so a duration exercise can never again put seconds
into `SetLog.weight_kg`.
"""

import re

WEIGHT_REPS = 'weight_reps'
WEIGHT_DURATION = 'weight_duration'
DURATION = 'duration'
WARMUP = 'warmup'
BODYWEIGHT_REPS = 'bodyweight_reps'
# Cardio machines: time is the prescription, but speed and incline are what
# actually describe the effort. Reported as "Treadmill should also track speed"
# and "should also have a column to add incline".
CARDIO_MACHINE = 'cardio_machine'
# Loaded carries and sled work, where the prescription is a DISTANCE. Two
# feedback items sat escalated for months because "30 yards" had nowhere to be
# logged, so the only options were to mis-prescribe it as seconds or drop it.
WEIGHT_DISTANCE = 'weight_distance'

ALL_TYPES = (WEIGHT_REPS, WEIGHT_DURATION, DURATION, WARMUP, BODYWEIGHT_REPS,
             CARDIO_MACHINE, WEIGHT_DISTANCE)

DEFAULT_TYPE = WEIGHT_REPS

# ---------------------------------------------------------------------------
# Read/write contract
# ---------------------------------------------------------------------------
# For each measurement type, which SetLog fields carry meaning. Any field not
# listed is forced to None on write so it can never be charted or fed to the AI.
#
#   weight_kg        -- always kilograms on the wire and in the DB
#   reps_completed   -- integer repetitions
#   duration_seconds -- integer seconds (NEVER stored in weight_kg)
FIELD_CONTRACT = {
    WEIGHT_REPS:     ('weight_kg', 'reps_completed'),
    WEIGHT_DURATION: ('weight_kg', 'duration_seconds'),
    DURATION:        ('duration_seconds',),
    BODYWEIGHT_REPS: ('reps_completed',),
    WARMUP:          (),
    # Both of these are deliberately SUPERSETS of the type they replace --
    # see REPLACES below. That is not a style choice: reclassifying an exercise
    # runs the SetLog field contract over its history, and any field missing
    # from the new contract is NULLED. A `weight + distance` carry would have
    # destroyed 17 already-logged durations in production.
    CARDIO_MACHINE:  ('duration_seconds', 'speed_kmh', 'incline_pct'),
    WEIGHT_DISTANCE: ('weight_kg', 'duration_seconds', 'distance_m'),
}

# Which existing type each new type takes over from. Every new contract must
# contain its predecessor's fields, and a test asserts it, so reclassification
# is lossless by construction rather than by review.
REPLACES = {
    CARDIO_MACHINE: DURATION,
    WEIGHT_DISTANCE: WEIGHT_DURATION,
}

# Stored as floats. Rounding speed to 4 km/h or an incline to 2% would quietly
# destroy the precision these fields exist to capture.
_FLOAT_SET_FIELDS = frozenset((
    'weight_kg', 'speed_kmh', 'incline_pct', 'distance_m'))

ALL_SET_FIELDS = (
    'weight_kg', 'reps_completed', 'duration_seconds',
    # Canonical units, converted for display exactly as weight_kg is:
    #   speed_kmh    km/h, shown as mph for lb users
    #   incline_pct  percent, no conversion
    #   distance_m   metres, shown in the unit the PRESCRIPTION uses (yd/ft)
    'speed_kmh', 'incline_pct', 'distance_m',
)

# What a progress chart should plot, and in what unit, for each type.
#   metric: which contract field drives the chart
#   unit:   'mass' (convert kg -> user unit), 'seconds', 'reps', or None
CHART_CONTRACT = {
    WEIGHT_REPS:     {'metric': 'weight_kg', 'unit': 'mass'},
    WEIGHT_DURATION: {'metric': 'weight_kg', 'unit': 'mass'},
    DURATION:        {'metric': 'duration_seconds', 'unit': 'seconds'},
    BODYWEIGHT_REPS: {'metric': 'reps_completed', 'unit': 'reps'},
    WARMUP:          {'metric': None, 'unit': None},
    # Cardio progression stays on TIME, matching the existing duration
    # behaviour. Speed and incline are recorded detail, not the progression
    # driver -- revisiting that is a coaching decision to make once there is
    # real speed data to look at.
    CARDIO_MACHINE:  {'metric': 'duration_seconds', 'unit': 'seconds'},
    # A carry progresses on load, like the weight_duration it replaces.
    WEIGHT_DISTANCE: {'metric': 'weight_kg', 'unit': 'mass'},
}


def uses_field(measurement_type, field):
    """True if `field` carries meaning for this measurement type."""
    return field in FIELD_CONTRACT.get(measurement_type, ())


# ---------------------------------------------------------------------------
# Classification rules
# ---------------------------------------------------------------------------
# Ordered (type, include_patterns, exclude_patterns). First rule whose include
# matches and whose exclude does NOT match wins.
#
# Order matters and is deliberate:
#   1. WARMUP          -- "runner's stretch" is a stretch, not a run
#   2. WEIGHT_DURATION -- "farmer's walk" before any generic walk/carry handling
#   3. BODYWEIGHT_REPS -- "push up to side plank" is a rep exercise, not a plank
#   4. DURATION        -- bare "plank", "treadmill", "wall sit"
# Excludes exist because the KB contains loaded variants of bodyweight moves
# ("cable crunch", "ab crunch machine", "dip machine") and rep-based variants of
# words that otherwise imply time ("hang clean" is not a dead hang).

_WARMUP_PATTERNS = [
    r'warm\s?-?\s?up',
    r'\bmobility\b',
    r'\bstretch',
    r'\bfoam roll',
    r'\bactivation\b',
    r'\bcool\s?-?\s?down\b',
]

# Loaded variants that must NOT be treated as bodyweight rep work.
_LOADED = (
    r'\bcable\b|\bmachine\b|\brope\b|\bweighted\b|\bplate\b|\bdumbbell\b|'
    r'\bbarbell\b|\bsmith\b|\bkettlebell\b|\bbosu\b|\bpulldown\b|\bjerk dip\b|'
    r'\bsled\b'
)

_RULES = [
    (WARMUP, _WARMUP_PATTERNS, []),

    # Loaded carries, pushes and holds: an external load is moved or held for a
    # distance/time rather than for reps, so the log needs BOTH weight and time.
    #
    # Sled and prowler work used to sit in the DURATION rule below, which asked
    # only for seconds -- reported as "Sled push should also ask for weight
    # right?". It does: you load plates on a sled exactly as you load a trap bar
    # for a carry, and `_LOADED` already treats "sled" as a loaded implement.
    # A farmer's HOLD is the same movement as a farmer's walk standing still.
    (WEIGHT_DISTANCE, [
        r"\bfarmer'?s?\s+(walk|carry|hold)",
        r"\bfarmer\s?-?\s?s? hold\b",
        r'\bsuitcase\s+(walk|carry|hold)',
        r'\brickshaw\s+carry',
        r'\byoke\s+(walk|carry)',
        r'\bwaiter\s+walk',
        r'\b(overhead|loaded|trap bar)\s+carry',
        # Only the conditioning movements. A bare \bsled\b would be wrong: in
        # the exercise library "sled" also names the plate-loaded 45-degree
        # machine ("sled hack squat", "sled overhead triceps extension"), which
        # is ordinary weight_reps work.
        r'\bsled\s+(overhead\s+)?(backward\s+)?'
        r'(push|pull|drag|walk|sprint)(e?s)?\b',
        r'\bprowler\b',          # only ever the sled; there is no machine
    ], []),

    # Cardio machines. Pulled out of DURATION so speed and incline have
    # somewhere to go. Everything here is a machine with a readout; holds
    # (plank, wall sit) and generic cardio stay DURATION below.
    (CARDIO_MACHINE, [
        r'\btreadmill\b',
        r'\belliptical\b',
        r'\bstairmaster\b',
        r'\bstair\s?(climb|master|mill)',
        r'\bpower stairs\b',
        r'\bstationary\b',
        r'\bair bike\b',
        r'\brecumbent bike\b',
        r'\bspin bike\b',
        r'\bexercise bike\b',
        r'\browing machine\b',
        r'\bski erg\b',
        r'\bassault bike\b',
    ], [r'\bclean\b', r'\bsnatch\b']),

    # Bodyweight rep work. Excludes the loaded/machine variants.
    (BODYWEIGHT_REPS, [
        r'\bpush\s?-?\s?ups?\b',
        r'\bpushups?\b',
        r'\bpull\s?-?\s?ups?\b',
        r'\bpullups?\b',
        r'\bchin\s?-?\s?ups?\b',
        r'\bdips?\b',
        r'\binverted row\b',
        r'\bsit\s?-?\s?ups?\b',
        r'\bcrunch',
        r'\bburpees?\b',
        r'\bmuscle\s?-?\s?ups?\b',
        r'\bpistol squat\b',
        r'\bair squat\b',
        r'\bbodyweight squat\b',
        r'\bhanging (leg|knee) raise\b',
        r'\bhanging pike\b',
        r'\bglute bridge\b',
        r'\bbird\s?-?\s?dog\b',
        r'\bmountain climbers?\b',
    ], [_LOADED]),

    # Time-based work.
    (DURATION, [
        r'\bplank\b',
        r'\bwall sit\b',
        r'\bisometric\b',
        r'\bhold\b',
        # Narrow hang patterns only -- "hang clean"/"hang snatch" are rep lifts.
        r'\bdead hang\b',
        r'\bbar hang\b',
        r'\bpassive hang\b',
        r'\bone handed hang\b',
        # Cardio machines and conditioning.
        r'\bbicycling\b',
        r'\bcycling\b',
        r'\bjump\s?-?\s?rope\b',
        r'\bskipping\b',
        r'\bbattle ropes?\b',
        # Sled, prowler and farmer's holds moved to WEIGHT_DURATION above --
        # they are loaded, so seconds alone is only half the log. Bear crawl
        # stays: it is genuinely bodyweight.
        r'\bbear crawl\b',
        r'\brunning\b',
        r'\bjogging\b',
        r'\bsprints?\b',
        r'\bcardio\b',
        r'\bshadow box',
        r'\bjumping jacks?\b',
    ], [r'\bclean\b', r'\bsnatch\b', r'\bgood morning\b']),

    # Positive rule for ordinary resistance work, checked LAST so the more
    # specific rules above always win.
    #
    # Without this the classifier had no opinion on any standard lift -- it just
    # fell through to the DEFAULT_TYPE -- which meant normalize() would accept
    # the AI calling a barbell bench press 'duration'. Equipment and movement
    # nouns are a strong enough signal to be confident.
    (WEIGHT_REPS, [
        r'\bbarbell\b', r'\bdumbbell\b', r'\bkettlebell\b', r'\bcable\b',
        r'\bmachine\b', r'\bsmith\b', r'\bez[- ]?bar\b', r'\btrap bar\b',
        r'\blandmine\b', r'\bplate\b', r'\bhack\b', r'\bpulldown\b',
        r'\bpulley\b', r'\bbench press\b', r'\bpress\b', r'\bcurl\b',
        r'\bsquat\b', r'\bdeadlift\b', r'\brow\b', r'\bextension\b',
        r'\braise\b', r'\bflye?s?\b', r'\blunge\b', r'\bshrug\b',
        r'\bpushdown\b', r'\bpullover\b', r'\bthruster\b', r'\bhip thrust\b',
        r'\bcrossover\b', r'\bpec deck\b', r'\bleg curl\b', r'\bcalf\b',
    ], []),
]

# Secondary signal: Exercise.reps is a free-text CharField ("8-12", "AMRAP",
# "30 sec"). A time unit in there is evidence of a timed exercise -- but only if
# the time IS the prescription. "12 (30 sec rest)" is a rep exercise with a rest
# period, so rest clauses and parentheticals are stripped before testing.
_REPS_TIME_RE = re.compile(
    r'\b\d+\s*(s|sec|secs|second|seconds|min|mins|minute|minutes)\b',
    re.I,
)

# Plain rep counts surviving after the time match means reps are the real unit.
_REPS_COUNT_RE = re.compile(r'\b\d+\s*(x|reps?|-\s*\d+)?\b', re.I)


def _reps_imply_duration(reps):
    """True if the free-text reps field prescribes time rather than repetitions."""
    if not reps:
        return False
    text = str(reps).lower()
    # Drop rest/tempo clauses -- "30 sec rest" says nothing about the exercise.
    text = re.sub(r'\([^)]*\)', ' ', text)
    text = re.sub(r'\b(rest|tempo|hold at top|between sets)\b.*$', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    if not text:
        return False
    if not _REPS_TIME_RE.search(text):
        return False
    # Reject "10 reps then 30 sec" style mixed prescriptions.
    if re.search(r'\breps?\b|\bamrap\b|\bfailure\b', text):
        return False
    return True


def _normalize_name(name):
    """Lowercase and collapse punctuation/whitespace for matching."""
    if not name:
        return ''
    text = str(name).lower()
    text = text.replace('&', ' and ')
    text = re.sub(r"[^a-z0-9'\-\s]", ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def _matches_any(text, patterns):
    for pattern in patterns:
        if re.search(pattern, text):
            return True
    return False


def is_warmup_name(name):
    """True if the name or day-name reads as warmup/mobility work."""
    return _matches_any(_normalize_name(name), _WARMUP_PATTERNS)


def _classify_with_confidence(name, reps=None):
    """
    Classify, and report whether a rule actually matched.

    The confidence flag matters because DEFAULT_TYPE is itself 'weight_reps':
    without it, "a rule confidently says this is a barbell lift" and "nothing
    matched, so assume weight+reps" are indistinguishable, and normalize() would
    let the AI relabel a bench press as 'duration'.

    Returns (measurement_type, matched) where `matched` is True only when a name
    rule or the reps-text signal fired.
    """
    text = _normalize_name(name)
    if not text:
        return DEFAULT_TYPE, False

    for measurement_type, includes, excludes in _RULES:
        if _matches_any(text, includes) and not _matches_any(text, excludes):
            return measurement_type, True

    # No name rule matched. Fall back to the reps-text signal: "3 x 30 sec" on an
    # otherwise unrecognised movement means it is timed, not rep-counted.
    if _reps_imply_duration(reps):
        return DURATION, True

    return DEFAULT_TYPE, False


def classify(name, reps=None):
    """
    Deterministically classify an exercise by name.

    Returns one of ALL_TYPES, falling back to DEFAULT_TYPE ('weight_reps') when
    nothing matches. Use has_opinion() to tell a real verdict from the fallback.
    """
    return _classify_with_confidence(name, reps=reps)[0]


def has_opinion(name, reps=None):
    """True if the deterministic rules actually recognised this exercise."""
    return _classify_with_confidence(name, reps=reps)[1]


def normalize(raw, name, day_name=None, reps=None):
    """
    Coerce a (possibly AI-supplied) measurement_type into a trustworthy value.

    Precedence, strongest first:
      1. A warmup day forces WARMUP, whatever the AI claimed.
      2. A confident deterministic name rule overrides the AI -- the AI is not
         allowed to call a plank 'weight_reps'.
      3. A valid AI value is accepted when the rules have no opinion.
      4. Otherwise fall back to the deterministic classifier.
    """
    if day_name and is_warmup_name(day_name):
        return WARMUP

    if is_warmup_name(name):
        return WARMUP

    deterministic, confident = _classify_with_confidence(name, reps=reps)
    if confident:
        return deterministic

    if raw in ALL_TYPES:
        return raw

    return deterministic


# ---------------------------------------------------------------------------
# Rep-scheme conventions
# ---------------------------------------------------------------------------
#
# Lifters prescribe reps in conventional brackets -- 8-12, 10-12, 15-20 -- or in
# round singles for low-rep strength work. The AI was emitting arbitrary numbers
# like 11, 13, 14, 17, which read as machine output rather than programming.
#
# Bare singles that are idiomatic on their own: low-rep strength work (1-6) and
# the round numbers.
_CONVENTIONAL_SINGLES = {1, 2, 3, 4, 5, 6, 8, 10, 12, 15, 20, 25, 30}

# Ordered brackets. A non-conventional single snaps to the first bracket that
# contains it, so 13 -> "12-15" (which still includes 13).
_REP_BRACKETS = (
    (1, 3, '1-3'),
    (3, 5, '3-5'),
    (6, 8, '6-8'),
    (8, 10, '8-10'),
    (10, 12, '10-12'),
    (12, 15, '12-15'),
    (15, 20, '15-20'),
    (20, 25, '20-25'),
    (25, 30, '25-30'),
)

# Units that mean the number is a duration or distance, not a rep count. These
# must never be snapped -- "30 sec" is not 30 reps.
_NON_REP_UNIT_RE = re.compile(
    r'^\s*(s|sec|secs|second|seconds|m|min|mins|minute|minutes|hr|hour|hours|'
    r'yd|yard|yards|meter|meters|metre|metres|mile|miles|km|cal|cals|calorie|'
    r'calories|breath|breaths)\b',
    re.I,
)


def normalize_reps(raw):
    """
    Snap an arbitrary rep count onto a conventional scheme.

    Only a leading bare integer is considered, and only when it is not already
    conventional and not followed by a time/distance unit:

        "13"           -> "12-15"
        "11 per leg"   -> "10-12 per leg"
        "8-12"         -> unchanged (already a bracket)
        "10"           -> unchanged (conventional single)
        "30 sec"       -> unchanged (a duration)
        "AMRAP"        -> unchanged

    Returns the input unchanged whenever there is nothing idiomatic to fix, so
    this is safe to apply on every write.
    """
    if raw is None:
        return raw
    text = str(raw).strip()
    if not text:
        return text

    match = re.match(r'^(\d+)(.*)$', text)
    if not match:
        return text                      # "AMRAP", "to failure", ...

    number = int(match.group(1))
    remainder = match.group(2)

    # A range, a tempo, or anything with a separator is already deliberate.
    if remainder[:1] in ('-', '–', '/', 'x', 'X', '.', ','):
        return text
    if _NON_REP_UNIT_RE.match(remainder):
        return text
    if number in _CONVENTIONAL_SINGLES:
        return text

    for low, high, label in _REP_BRACKETS:
        if low <= number <= high:
            return (label + remainder) if remainder else label

    return text                          # outside the brackets: leave it alone


def prescription_matches_type(measurement_type, reps):
    """
    Is the free-text `reps` prescription consistent with the measurement type?

    Returns (ok, expected_kind) where expected_kind is 'reps', 'time' or None.

    A rep-typed exercise prescribed in minutes, or a timed one prescribed in
    yards, leaves the user guessing which number goes in which box:

        Burpees [bodyweight_reps] reps="10 min"       -> card says minutes,
                                                         input asks for reps
        Farmer's Carry [weight_duration] reps="40 yards" -> input asks seconds

    Both shipped. The prompt now forbids the mismatch and the UI surfaces any
    that slip through; nothing rewrites the prescription, because a rep count
    cannot be inferred from "10 min" without inventing programming.

    Unparseable prescriptions ("AMRAP", "to failure") are accepted for any type.
    """
    if measurement_type == WARMUP or not reps:
        return True, None

    text = str(reps)
    has_time = bool(re.search(
        r'\b\d+\s*(s|sec|secs|second|seconds|min|mins|minute|minutes|hour|hours)\b'
        r'|\b(sec|secs|second|seconds|min|mins|minute|minutes|hour|hours)\b',
        text, re.I,
    ))
    has_distance = bool(re.search(
        r'\b(yard|yards|yd|feet|ft|metre|metres|meter|meters|mile|miles|km)\b',
        text, re.I,
    ))
    has_reps = bool(re.search(r'\d', text)) and not (has_time or has_distance)

    if measurement_type in (WEIGHT_REPS, BODYWEIGHT_REPS):
        # Must be a rep count. A time or distance is the contradiction.
        return (not (has_time or has_distance)), 'reps'

    if measurement_type == WEIGHT_DISTANCE:
        # A DISTANCE is now the expected prescription, which is the whole
        # point of this type existing. "Farmer's Carry: 40 yards" and
        # "Sled Push: 30 yards" were escalated for months because distance had
        # nowhere to be logged, so the prescription was flagged as a mismatch
        # with no way to fix it. A time is also fine -- carries are legitimately
        # prescribed either way. A bare rep count is not.
        #
        # Bare "m" is read as metres HERE ONLY. It is genuinely ambiguous with
        # minutes, which is why the shared regex above demands "metre"/"meter";
        # but a 20-minute farmer's carry does not exist, so for this type
        # "Sled Push: 20m" is a distance. On CARDIO_MACHINE it stays minutes.
        if re.search(r'\b\d+\s*m\b', text, re.I):
            has_distance = True
        if has_distance or has_time:
            return True, 'distance or time'
        return (not has_reps), 'distance or time'

    if measurement_type in (DURATION, WEIGHT_DURATION, CARDIO_MACHINE):
        # Must be a time. A distance, or a bare rep count, is the contradiction.
        if has_distance:
            return False, 'time'
        # The mirror of the weight_distance case above: bare "m" is ambiguous in
        # general, but a 20-metre plank or treadmill set is not a thing, so here
        # it means minutes. Without this "Treadmill: 20m" reads as a bare rep
        # count and gets flagged with nothing for the user to fix.
        if re.search(r'\b\d+\s*m\b', text, re.I):
            return True, 'time'
        if has_reps:
            return False, 'time'
        return True, 'time'

    return True, None


def coerce_set_fields(measurement_type, values):
    """
    Apply FIELD_CONTRACT to a dict of incoming set values.

    Returns a dict containing ALL of ALL_SET_FIELDS: fields that carry meaning
    for this type keep their (type-coerced) value, and fields that do not are
    forced to None. This is what stops seconds landing in weight_kg.

    `values` keys that are absent are treated as "not supplied" and come back as
    None, so callers should merge against existing state if they want partial
    updates.
    """
    allowed = FIELD_CONTRACT.get(measurement_type, FIELD_CONTRACT[DEFAULT_TYPE])
    out = {}
    for field in ALL_SET_FIELDS:
        if field not in allowed:
            out[field] = None
            continue
        raw = values.get(field)
        if raw is None or raw == '':
            out[field] = None
            continue
        try:
            if field in _FLOAT_SET_FIELDS:
                out[field] = max(0.0, float(raw))
            else:
                out[field] = max(0, int(round(float(raw))))
        except (TypeError, ValueError):
            out[field] = None
    return out
