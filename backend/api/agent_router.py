"""
Which model answers which request.

Routing is DETERMINISTIC and server-side. The alternative -- asking a model
which model should handle something -- adds a call, a failure mode and a
latency hit to answer a question the server already knows, and makes the
routing untestable because it can change its mind between identical requests.

The split
---------
    workouts  -> Claude Haiku 5.5   plans, edits, logging, history, weights
    food      -> Gemini 2.5 Flash   meals, diet, body composition

Measured, not assumed: the repo's eval scored Haiku's only deficit on plan
generation as exercise naming, which `exercise_names.canonical()` rewrites on
write anyway, while it was clean on rep schemes and measurement types -- the
two faults that actually caused damage. See `manage.py eval_plans --live`.

Mixed requests
--------------
"Change my bench press and suggest a high-protein lunch" is BOTH. Routing it
whole sends half the request to a model that cannot serve it, and the usual
result is that half gets dropped silently. So a mixed request is split into
parts, each part routed, and the replies composed into one answer.

Classification is keyword-based on purpose. It decides which model reads the
request, not what the answer is, so a wrong guess costs a less suitable
model rather than a wrong fact -- and the tool layer is identical on both
sides. A learned classifier here would be a second model call to save nothing.
"""

import re

WORKOUT = 'workout'
FOOD = 'food'

# Deliberately narrow and specific. A word that could plausibly belong to
# either domain is left out: "calories" appears in both a treadmill session
# and a meal, so it classifies nothing on its own.
_WORKOUT_WORDS = (
    'workout', 'exercise', 'lift', 'set', 'sets', 'rep', 'reps', 'plan',
    'routine', 'superset', 'squat', 'bench', 'deadlift', 'press', 'curl',
    'row', 'pull', 'push', 'cardio', 'treadmill', 'warmup', 'rest day',
    'session', 'training', 'week', 'program', 'progression', 'weight i',
    'heavier', 'lighter', 'pr', 'personal best', 'form', 'injur', 'gym',
)

_FOOD_WORDS = (
    'meal', 'eat', 'ate', 'food', 'diet', 'breakfast', 'lunch', 'dinner',
    'snack', 'protein shake', 'macro', 'macros', 'carb', 'carbs', 'fat',
    'recipe', 'nutrition', 'calorie intake', 'body fat', 'inbody', 'weigh in',
    'weighed', 'body composition', 'bmi', 'scan',
)

# Sentence-ish separators. A mixed request is usually two clauses, and
# splitting on these keeps each clause intact rather than scattering words.
_SPLIT = re.compile(r'\s*(?:\band\s+(?:also\s+)?|[;.]|\bthen\b|\balso\b)\s*',
                    re.I)


# Built once. Each term is matched on WORD BOUNDARIES, not as a substring:
# plain `in` had 'pr' matching "protein", 'eat' matching "great" and 'row'
# matching "brown", so "suggest a high protein vegetarian dinner" scored as a
# workout request. Multi-word terms keep their internal spacing.
_BOUNDED = {}


def _bounded(words):
    key = id(words)
    if key not in _BOUNDED:
        _BOUNDED[key] = re.compile(
            r'(?<!\w)(?:%s)(?!\w)'
            % '|'.join(re.escape(w) for w in sorted(words, key=len, reverse=True)),
            re.I)
    return _BOUNDED[key]


def _hits(text, words):
    """How many distinct terms appear as whole words."""
    return len(set(_bounded(words).findall(text or '')))


def classify(text):
    """
    The domain one piece of text belongs to.

    Ties and blanks go to WORKOUT: it is the app's primary surface, it is the
    half with the plan-mutation guardrails, and its model is the cheaper of
    the two.
    """
    workout = _hits(text, _WORKOUT_WORDS)
    food = _hits(text, _FOOD_WORDS)
    return FOOD if food > workout else WORKOUT


def split_request(text):
    """
    A request as a list of (domain, text) parts, in the order asked.

    Adjacent parts of the same domain are merged, so an ordinary single-topic
    message comes back as exactly one part and costs one call. Only a request
    that genuinely spans both domains is split.
    """
    if not (text or '').strip():
        return [(WORKOUT, text or '')]

    pieces = [p.strip() for p in _SPLIT.split(text) if p and p.strip()]
    if len(pieces) <= 1:
        return [(classify(text), text)]

    parts = []
    for piece in pieces:
        domain = classify(piece)
        if parts and parts[-1][0] == domain:
            parts[-1] = (domain, '%s. %s' % (parts[-1][1], piece))
        else:
            parts.append((domain, piece))

    # A split that produced one domain is not a mixed request; return the
    # original text so the model sees the sentence the user actually wrote
    # rather than a reassembled approximation of it.
    if len(parts) == 1:
        return [(parts[0][0], text)]
    return parts


def is_mixed(text):
    return len({domain for domain, _ in split_request(text)}) > 1
