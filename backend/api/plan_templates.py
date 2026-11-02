"""
Plan templates: a way to start training when the AI cannot help.

Why this exists
---------------
Generation is the only route to a plan today, so when it fails the app is a
dead end: onboarding finishes, the dashboard says "no active plan", and every
other screen needs one. The AI being down -- which it was, entirely, for the
whole of a billing lapse -- meant a new user could not use the product at all.

A template is not a worse plan. It is a plan that does not depend on a third
party being up, and for a beginner it is close to what a generated plan would
say anyway.

Contraindications are honoured
------------------------------
A template is filtered against the user's own constraints before it is
offered. Offering someone a barbell-squat template the day after they said
their knee is injured would be worse than offering nothing, because it looks
like the app read their note and disagreed.
"""

from . import exercise_names

#: Templates are stated in canonical names so they land in the library and
#: match history without a rename on write.
TEMPLATES = {
    'beginner_full_body': {
        'name': 'Beginner Full-Body',
        'days_per_week': 4,
        'summary': '4 days a week · machines and dumbbells',
        'description': 'Full-body sessions on machines and dumbbells. '
                       'Low technical demand, easy to repeat week to week.',
        'equipment': 'full gym',
        'days': [
            ('Full Body A', ['Machine Chest Press', 'Lat Pulldown (Neutral Grip)',
                             'Leg Press', 'Dumbbell Lateral Raises', 'Plank']),
            ('Full Body B', ['Machine Shoulder Press', 'Seated Cable Row',
                             'Lying Hamstring Curl', 'Dumbbell Hammer Curl',
                             'Plank']),
            ('Full Body C', ['Incline Dumbbell Press', 'Lat Pulldown (Neutral Grip)',
                             'Leg Extension', 'Cable Triceps Pushdown (Rope Attachment)',
                             'Plank']),
            ('Full Body D', ['Machine Chest Press', 'Seated Cable Row',
                             'Leg Press', 'Dumbbell Lateral Raises', 'Plank']),
        ],
    },
    'upper_lower': {
        'name': 'Upper / Lower',
        'days_per_week': 4,
        'summary': '4 days a week · barbell and machines',
        'description': 'Upper and lower split, twice each. '
                       'Barbell work on the main lifts.',
        'equipment': 'full gym',
        'days': [
            ('Upper A', ['Barbell Bench Press', 'Seated Cable Row',
                         'Machine Shoulder Press', 'Dumbbell Hammer Curl']),
            ('Lower A', ['Barbell Squat', 'Lying Hamstring Curl',
                         'Leg Press', 'Standing Calf Raises (Machine)']),
            ('Upper B', ['Incline Dumbbell Press', 'Lat Pulldown (Neutral Grip)',
                         'Dumbbell Lateral Raises',
                         'Cable Triceps Pushdown (Rope Attachment)']),
            ('Lower B', ['Leg Press', 'Lying Hamstring Curl',
                         'Leg Extension', 'Standing Calf Raises (Machine)']),
        ],
    },
    'machine_focused': {
        'name': 'Machine-Focused',
        'days_per_week': 4,
        'summary': '4 days a week · low back stress',
        'description': 'Machines and supported positions throughout. '
                       'Nothing loaded through a standing spine.',
        'equipment': 'full gym',
        'days': [
            ('Push', ['Machine Chest Press', 'Machine Shoulder Press',
                      'Cable Triceps Pushdown (Rope Attachment)']),
            ('Pull', ['Lat Pulldown (Neutral Grip)', 'Seated Cable Row',
                      'Dumbbell Hammer Curl']),
            ('Legs', ['Leg Press', 'Leg Extension', 'Lying Hamstring Curl',
                      'Standing Calf Raises (Machine)']),
            ('Accessories & Core', ['Dumbbell Lateral Raises', 'Cable Face Pulls',
                                    'Plank']),
        ],
    },
}


def _contraindicated_keys(user):
    """
    Canonical keys the user should not be offered, from their own library.

    Reads `ExerciseLibraryEntry` rather than re-parsing the profile note: the
    note is prose, and a template filter is exactly the sort of thing that
    must not depend on guessing which words appeared in it.
    """
    from .models import ExerciseLibraryEntry

    rows = ExerciseLibraryEntry.objects.filter(user=user, contraindicated=True)
    return {row.canonical_key for row in rows}


def available_templates(user=None):
    """
    The templates on offer, with anything contraindicated already removed.

    A template whose MAIN work is all contraindicated is dropped entirely --
    offering a lower-body plan with the legs taken out is not a plan.
    """
    blocked = _contraindicated_keys(user) if user else set()
    out = []
    for key, template in TEMPLATES.items():
        days = []
        for day_name, exercises in template['days']:
            kept = [
                name for name in exercises
                if ' '.join(sorted(exercise_names.key(name))) not in blocked
            ]
            # A day stripped below two movements is not worth keeping.
            if len(kept) >= 2:
                days.append((day_name, kept))
        if len(days) < 2:
            continue
        out.append(dict(template, key=key, days=days,
                        adjusted=len(days) != len(template['days'])))
    return out


def to_plan_payload(template, duration_weeks=8):
    """
    A template as the plan wire format, ready for the ordinary create path.

    Goes through the SAME endpoint a generated plan does, so measurement types,
    canonical names and the field contract are decided by the server exactly
    as they are for AI output. A separate write path would be a second place
    for those rules to drift.
    """
    return {
        'title': template['name'],
        'description': template['description'],
        'duration_weeks': duration_weeks,
        'generated_by_ai': False,
        'equipment': template.get('equipment', ''),
        'week': [
            {
                'day_number': i + 1,
                'name': day_name,
                'focus': '',
                'is_rest_day': False,
                'exercises': (
                    [{'name': 'Warmup & Mobility', 'sets': 1,
                      'reps': '5-10 min', 'rest_seconds': 0}]
                    + [{'name': name, 'sets': 3, 'reps': '8-12',
                        'rest_seconds': 90} for name in exercises]
                ),
            }
            for i, (day_name, exercises) in enumerate(template['days'])
        ],
    }
