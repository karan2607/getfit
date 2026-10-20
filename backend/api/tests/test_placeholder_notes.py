"""
Regression tests for the two reported feedback items.

1. "Still seeing Xlb in how to"
   The plan-generation prompt's JSON schema example literally read
   "Start Xkg. Add Ykg when all sets clean.", so the model copied the
   placeholder into real plans with the unit swapped. 179 exercises in
   production had it. Fixed in three places: the prompt example is now a
   concrete number, the prompt explicitly forbids placeholders, and the write
   path strips them regardless of whether the model complies.

2. "Dumbbell lateral raises how to shows sleeping dumbbell lateral raises"
   Fuzzy matching resolved a standing side-delt raise to
   "dumbbell lying rear lateral raise" -- a lying rear-delt movement.
"""

from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework.authtoken.models import Token

from api import views
from api.models import User, UserProfile, WorkoutPlan, WorkoutDay, Exercise


class StripPlaceholderWeightsTests(APITestCase):
    def test_removes_placeholder_sentences(self):
        cases = [
            ('Start Xlb. Add Ylb when all sets clean.', ''),
            ('Start Xkg. Add Ykg when all sets clean.', ''),
            ('Focus on form. Start Xlb. Add Ylb when clean.', 'Focus on form.'),
            ('Start with Xlb.', ''),
            # "Add 5-10lb" is a real increment and must survive; only the
            # placeholder sentence between them goes.
            ('Use a dip belt. Start with Xlb. Add 5-10lb when clean.',
             'Use a dip belt. Add 5-10lb when clean.'),
            ('Start [weight]lb. Add [increment] when clean.', ''),
            ('Start Xlb', ''),                      # no trailing period
        ]
        for raw, expected in cases:
            self.assertEqual(views._strip_placeholder_weights(raw), expected,
                             msg='input=%r' % raw)

    def test_preserves_real_weights(self):
        """A note with genuine numbers must survive untouched."""
        for keep in (
            'Start 135lb. Add 5lb when all sets are clean.',
            'Start 25lb per hand. Add 5lb once 3x12 is easy.',
            'Increase weight next week by 5-10lb and reset to 8 reps.',
            'Focus on controlled descent and ascent.',
            'Use a light dumbbell and focus on controlled movement.',
        ):
            self.assertEqual(views._strip_placeholder_weights(keep), keep,
                             msg='input=%r' % keep)

    def test_handles_empty_and_none(self):
        self.assertEqual(views._strip_placeholder_weights(''), '')
        self.assertEqual(views._strip_placeholder_weights(None), '')

    def test_is_idempotent(self):
        once = views._strip_placeholder_weights('Focus on form. Start Xlb.')
        twice = views._strip_placeholder_weights(once)
        self.assertEqual(once, twice)

    def test_real_production_notes(self):
        """Verbatim samples pulled from the production database."""
        raw = ('Increase reps from last week. If all sets of 14 reps were easy, '
               'increase weight next week by 5-10lb and reset to 12 reps. Start Xlb.')
        cleaned = views._strip_placeholder_weights(raw)
        self.assertNotIn('Xlb', cleaned)
        self.assertIn('Increase reps from last week.', cleaned)
        self.assertIn('5-10lb', cleaned, 'the real increment must survive')


class CreateExerciseScrubsNotesTests(APITestCase):
    """The write path is the chokepoint: no placeholder may reach the DB."""

    def setUp(self):
        self.user = User.objects.create_user(email='ph@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)
        self.plan = WorkoutPlan.objects.create(user=self.user, title='P')
        self.day = WorkoutDay.objects.create(plan=self.plan, day_number=1,
                                             week_number=1, name='Upper Body')

    def test_create_exercise_strips_placeholder(self):
        ex = views._create_exercise(self.day, {
            'name': 'Barbell Bench Press', 'sets': 3, 'reps': '8-12',
            'notes': 'Start Xlb. Add Ylb when all sets clean.',
        }, 0)
        self.assertEqual(ex.notes, '')

    def test_create_exercise_keeps_the_form_cue_and_drops_the_weight(self):
        """
        UPDATED. This used to assert "Start 135lb." SURVIVED, because the gate
        only removed PLACEHOLDER weights. That behaviour contradicted the
        prompt ("Do NOT put a starting weight ... in notes") and was reported
        as a bug: a static "Start 135lb" sat next to computed advice saying to
        drop 30lb. A concrete weight is now stripped like a placeholder one.
        """
        ex = views._create_exercise(self.day, {
            'name': 'Barbell Bench Press', 'sets': 3, 'reps': '8-12',
            'notes': 'Pause at the chest. Start 135lb.',
        }, 0)
        self.assertEqual(ex.notes, 'Pause at the chest.')

    def test_plan_replace_strips_placeholders(self):
        """An AI plan edit must not reintroduce them either."""
        res = self.client.put(reverse('workout-plan-detail', args=[self.plan.id]), {
            'title': 'P',
            'days': [{'day_number': 1, 'name': 'Upper Body', 'is_rest_day': False,
                      'exercises': [
                          {'name': 'Barbell Bench Press', 'sets': 3, 'reps': '8-12',
                           'notes': 'Start Xlb. Add Ylb when clean.'},
                          {'name': 'Barbell Squat', 'sets': 3, 'reps': '5',
                           'notes': 'Brace hard. Start 225lb.'},
                      ]}],
        }, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        notes = dict(Exercise.objects.filter(day__plan=self.plan)
                     .values_list('name', 'notes'))
        self.assertEqual(notes['Barbell Bench Press'], '')
        # "Start 225lb." is now stripped too -- see the note above on why.
        self.assertEqual(notes['Barbell Squat'], 'Brace hard.')

    def test_no_exercise_in_db_retains_a_placeholder(self):
        views._create_exercise(self.day, {
            'name': 'A', 'sets': 1, 'reps': '1', 'notes': 'Start Xlb.'}, 0)
        views._create_exercise(self.day, {
            'name': 'B', 'sets': 1, 'reps': '1', 'notes': 'Add Ykg when clean.'}, 1)
        views._create_exercise(self.day, {
            'name': 'C', 'sets': 1, 'reps': '1', 'notes': 'Start 3 x 12 at 100lb.'}, 2)
        # Checked in Python, not via notes__regex: \m and \M are Postgres-only
        # word-boundary syntax and SQLite's REGEXP rejects them.
        leaked = [
            n for n in Exercise.objects.values_list('notes', flat=True)
            if views._PLACEHOLDER_TOKEN_RE.search(n or '')
        ]
        self.assertEqual(leaked, [])
        # "Start 3 x 12 at 100lb." names a concrete weight, so the whole
        # sentence goes. The set notation "3 x 12" is still safe on its own --
        # pinned by test_set_notation_survives in test_notes_progression_rules.
        self.assertEqual(Exercise.objects.get(name='C').notes, '')


class PromptForbidsPlaceholdersTests(APITestCase):
    """
    The prompt itself must not hand the model a placeholder to copy.

    Asserted against the GENERATED prompt rather than the function source, so a
    comment mentioning the old string can't make this pass or fail spuriously.
    """

    def build(self, unit='lb'):
        system, user = views._workout_generate_prompt(
            None, days_per_week=4, duration_weeks=8,
            fitness_goal='build_muscle', experience_level='intermediate',
            equipment='full gym', preferred_unit=unit)
        return system + '\n' + user

    def test_notes_are_coaching_only_with_no_weights(self):
        """
        Weight guidance was removed from notes entirely. It duplicated
        `recommended_weight` — which is computed from the user's own logged
        history and so is strictly better than a plan-time guess — and a static
        "Start 95lb" in notes went stale immediately.
        """
        prompt = self.build()
        self.assertIn('COACHING ONLY, NO WEIGHTS', prompt)
        self.assertIn('Do NOT put a starting weight', prompt)
        self.assertIn('form cue', prompt)

    def test_schema_notes_example_is_a_form_cue_not_a_weight(self):
        for unit in ('lb', 'kg'):
            for line in self.build(unit).splitlines():
                if '"notes":' not in line or 'warmup' in line:
                    continue
                self.assertNotIn('Start ', line,
                                 msg='notes example prescribes a weight: %r' % line)

    def test_warmup_notes_are_still_requested(self):
        """Warmup drill lists are genuinely useful and must survive the change."""
        prompt = self.build()
        self.assertIn('stretches/drills', prompt)
        self.assertIn('Arm circles', prompt)

    def test_no_notes_example_contains_a_placeholder(self):
        """
        The prompt still mentions "Start Xlb" — deliberately, inside the
        FORBIDDEN/BAD instruction. What must never contain one is a `"notes":`
        example, because that is the shape the model copies.
        """
        for unit in ('lb', 'kg'):
            for line in self.build(unit).splitlines():
                if '"notes":' not in line:
                    continue
                self.assertIsNone(
                    views._PLACEHOLDER_TOKEN_RE.search(line),
                    msg='copyable notes example contains a placeholder: %r' % line)


class LateralRaiseImageTests(APITestCase):
    """Correct photos for each lateral-raise variant."""

    def test_standing_dumbbell_lateral_raise_is_not_the_lying_version(self):
        for name in ('Dumbbell Lateral Raises', 'Dumbbell Lateral Raise',
                     'Lateral Raises', 'Side Lateral Raise',
                     'Standing Lateral Raise'):
            top = views._get_kb_candidates(name, top_n=3)[0]
            self.assertEqual(top, 'side lateral raise', msg=name)
            self.assertNotIn('lying', top)
            self.assertNotIn('rear', top)

    def test_machine_and_cable_variants(self):
        self.assertEqual(
            views._get_kb_candidates('Machine Lateral Raise', top_n=2)[0],
            'seated side lateral raise')
        self.assertEqual(
            views._get_kb_candidates('Cable Lateral Raises', top_n=2)[0],
            'cable seated lateral raise')

    def test_rear_lateral_raise_still_maps_to_the_lying_movement(self):
        """The guard: a REAR raise genuinely is the lying/bent-over version."""
        for name in ('Rear Lateral Raise', 'Rear Delt Raise',
                     'Lying Rear Lateral Raise'):
            self.assertEqual(
                views._get_kb_candidates(name, top_n=2)[0],
                'dumbbell lying rear lateral raise', msg=name)

    def test_every_alias_target_exists(self):
        kb = views._load_exercise_kb()
        missing = [(k, v) for k, v in views._KB_ALIASES.items() if v not in kb]
        self.assertEqual(missing, [])
