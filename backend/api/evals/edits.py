"""
Edit scenarios, scored on scope and completeness.

The golden set is eight frozen plans and every rule in `rules.py` judges
GENERATION. The Haiku switch (e62d0db, 1 Nov) was justified on generation
alone -- n=3 per model, Haiku 0.9797 against Argon 0.9877 -- and plan EDITS
were never evaluated on either model. All four failed restructures of week 8
were edits.

What is scored
--------------
The DRAFT the model proposes, not the plan after applying it. The server
applies a draft faithfully; the question is whether the draft says what the
user asked for. Two properties, because they are the two that failed live:

  scope        every change names the week it belongs to, and that week is
               the one the request implies. A day moved without a week
               moves in every week -- the bug that reordered weeks 6 and 7
               four times.
  completeness every exercise the request named is in the draft, and
               nothing else is. The Day-4 draft dropped four of five pull
               exercises and the verification diff still read clean.

The model is asked for the draft as JSON rather than through the tool, so
one scorer serves both providers. That tests the INSTRUCTIONS -- which is
what changed -- and not the tool plumbing, which has its own tests.
"""

#: Each scenario: the plan the model is shown, what the user asks, and what
#: a correct draft has to contain. `weeks` is the set of weeks every item
#: must name; 0 means the whole programme.
SCENARIOS = [
    {
        'key': 'move_one_day',
        'request': 'Move my Legs day to day 3 this week, nothing else.',
        'current_week': 8,
        'weeks': {8},
        'must_include': [{'action': 'move_day', 'day': 'Legs'}],
        'max_items': 1,
    },
    {
        'key': 'restructure_a_week',
        'request': ('For week 8 only: move Legs to day 3, rename Accessories '
                    'to "Pull + Abs", and rename Full Body to '
                    '"Upper Body Focus".'),
        'current_week': 8,
        'weeks': {8},
        'must_include': [
            {'action': 'move_day', 'day': 'Legs'},
            {'action': 'rename_day', 'day': 'Accessories'},
            {'action': 'rename_day', 'day': 'Full Body'},
        ],
        'max_items': 3,
    },
    {
        'key': 'swap_one_exercise',
        'request': 'Swap Barbell Bench Press for Flat Dumbbell Press on Push.',
        'current_week': 8,
        'weeks': {8},
        'must_include': [{'action': 'replace', 'name': 'Flat Dumbbell Press',
                          'replaces': 'Barbell Bench Press'}],
        'max_items': 1,
    },
    {
        'key': 'whole_programme',
        'request': ('Take Barbell Bench Press out of my plan entirely -- '
                    'every week, not just this one.'),
        'current_week': 8,
        'weeks': {0},
        'must_include': [{'action': 'remove', 'name': 'Barbell Bench Press'}],
        'max_items': 1,
    },
    {
        'key': 'add_several_to_one_day',
        'request': ('Add Barbell Row, Face Pull and Dumbbell Curl to '
                    'Accessories this week.'),
        'current_week': 8,
        'weeks': {8},
        'must_include': [
            {'action': 'add', 'name': 'Barbell Row'},
            {'action': 'add', 'name': 'Face Pull'},
            {'action': 'add', 'name': 'Dumbbell Curl'},
        ],
        'max_items': 3,
    },
]

#: The plan every scenario is run against. Three weeks, so a draft that
#: forgets its scope visibly reaches weeks it was not asked about.
PLAN = {
    'title': 'Test Programme',
    'duration_weeks': 8,
    'weeks': {
        week: [
            {'day_number': 1, 'name': 'Push',
             'exercises': ['Barbell Bench Press', 'Machine Shoulder Press']},
            {'day_number': 2, 'name': 'Legs',
             'exercises': ['Barbell Squat', 'Leg Press']},
            {'day_number': 3, 'name': 'Pull',
             'exercises': ['Lat Pulldown', 'Seated Cable Rows']},
            {'day_number': 4, 'name': 'Accessories',
             'exercises': ['Dumbbell Hammer Curl']},
            {'day_number': 5, 'name': 'Full Body',
             'exercises': ['Kettlebell Swings']},
        ]
        for week in (6, 7, 8)
    },
}


def _key(text):
    from api import exercise_names

    return exercise_names.key(exercise_names.canonical((text or '').strip()))


def _matches(item, expected):
    """Whether one draft item satisfies one expectation."""
    if (item.get('action') or '').lower() != expected['action']:
        return False
    for field in ('name', 'replaces', 'day'):
        if field not in expected:
            continue
        if _key(item.get(field)) != _key(expected[field]):
            return False
    return True


def score_edit(draft, scenario):
    """
    Score one proposed draft. Returns {scope, completeness, findings}.

    Both scores are 0..1 so they average like the generation rules do.
    """
    findings = []
    items = [i for i in (draft or []) if isinstance(i, dict)]

    if not items:
        return {'scope': 0.0, 'completeness': 0.0,
                'findings': ['no draft was produced']}

    wanted_weeks = scenario['weeks']
    in_scope = 0
    for item in items:
        week = item.get('week')
        # A model sometimes answers "8" where the schema says integer. The
        # server copes (`_matching_days` coerces), and what is being scored
        # here is whether the model picked the right WEEK, not whether it
        # picked the right type -- so a numeric string counts.
        if isinstance(week, str) and week.strip().lstrip('-').isdigit():
            week = int(week.strip())
        if week is None:
            findings.append('%s: no week, so it applies to EVERY week'
                            % (item.get('action') or '?'))
            continue
        if week not in wanted_weeks:
            findings.append('%s: week %r, expected %s'
                            % (item.get('action') or '?', week,
                               sorted(wanted_weeks)))
            continue
        in_scope += 1
    scope = in_scope / len(items)

    expected = scenario['must_include']
    found = 0
    for want in expected:
        if any(_matches(item, want) for item in items):
            found += 1
        else:
            findings.append('missing: %s %s' % (
                want['action'], want.get('name') or want.get('day') or ''))
    completeness = found / len(expected)

    # Padding a draft is its own failure: every extra item is a change the
    # user did not ask for and will have to notice on the confirm card.
    extra = len(items) - scenario['max_items']
    if extra > 0:
        findings.append('%d change(s) more than the request needs' % extra)
        completeness *= max(0.0, 1.0 - 0.2 * extra)

    return {'scope': round(scope, 4),
            'completeness': round(completeness, 4),
            'findings': findings}
