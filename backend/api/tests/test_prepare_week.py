"""
Tests for workout_plan_prepare_week and the progression analysis behind it.

This endpoint was dead code. The @api_view/@permission_classes decorators had
drifted onto `_analyze_progression` defined below them, so the view received a
plain HttpRequest and returned an unrendered DRF Response
(ContentNotRenderedError); separately a datetime was string-sliced with [:10].
It 500'd on every single call, which is why the personalised weight advice never
appeared no matter how many times it was "fixed".

The first test here is the guard against that regression. Gemini is stubbed, so
nothing hits the network.
"""

import datetime as dt
from unittest import mock

from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework.authtoken.models import Token

from api import views, gemini, exercise_types as et
from api.models import (
    User, UserProfile, WorkoutPlan, WorkoutDay, Exercise, WorkoutSession, SetLog,
    ExerciseGuide,
)


def fake_gemini(captured):
    """
    Stub that records the prompt and returns a guide violating the contract for
    every exercise, so we can prove the server strips what does not apply.

    Keyword-only to match the real `call_gemini_json(*, system_prompt, ...)`.
    """
    def _call(*, system_prompt, user_prompt, **kwargs):
        captured['system'] = system_prompt
        captured['user'] = user_prompt
        count = sum(
            1 for line in user_prompt.splitlines()
            if line[:3].strip().rstrip('.').isdigit() and '[' in line
        )
        return [{
            'steps': ['a', 'b', 'c', 'd'],
            'muscles': ['Core'],
            'tips': ['t1', 't2'],
            'category': 'core',
            'kb_key': None,
            'recommended_weight': 'Start 135lb',
            'recommended_target': 'Hold 75 sec',
            'coaching_note': 'Push hard today.',
        } for _ in range(count)]
    return _call


class PrepareWeekTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='pw@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)

        self.plan = WorkoutPlan.objects.create(
            user=self.user, title='P', is_active=True, current_week=1)
        self.day = WorkoutDay.objects.create(
            plan=self.plan, day_number=1, week_number=1, name='Upper Body')
        self.warm_day = WorkoutDay.objects.create(
            plan=self.plan, day_number=2, week_number=1, name='Warmup & Mobility')

        self.bench = Exercise.objects.create(
            day=self.day, name='Barbell Bench Press', sets=3, reps='8-12',
            measurement_type=et.WEIGHT_REPS)
        self.plank = Exercise.objects.create(
            day=self.day, name='Plank', sets=3, reps='60 sec',
            measurement_type=et.DURATION)
        self.pushup = Exercise.objects.create(
            day=self.day, name='Push-ups', sets=3, reps='15',
            measurement_type=et.BODYWEIGHT_REPS)
        self.stretch = Exercise.objects.create(
            day=self.warm_day, name='Hamstring Stretch', sets=1, reps='30 sec',
            measurement_type=et.WARMUP)

        self.url = reverse('workout-plan-prepare-week', args=[self.plan.id])
        self.captured = {}

    def seed_history(self):
        """Two sessions of 3 sets each, enough for the progression analysis."""
        for day_of_month in (1, 8):
            s = WorkoutSession.objects.create(
                user=self.user, exercise_day=self.day, day_name='Upper Body')
            WorkoutSession.objects.filter(pk=s.pk).update(
                started_at=timezone.make_aware(
                    dt.datetime(2026, 1, day_of_month, 10, 0)))
            for n in (1, 2, 3):
                SetLog.objects.create(
                    workout_session=s, exercise=self.bench,
                    exercise_name='Barbell Bench Press', set_number=n,
                    weight_kg=40.0, reps_completed=12, is_completed=True)
                SetLog.objects.create(
                    workout_session=s, exercise=self.plank, exercise_name='Plank',
                    set_number=n, duration_seconds=60, is_completed=True)

    def call(self):
        # Patch the SOURCE module: prepare_week does `from .gemini import
        # call_gemini_json` inside the function body, so patching
        # api.views.call_gemini_json would not reach it and the test would make
        # a real network request.
        stub = fake_gemini(self.captured)
        with mock.patch.object(gemini, 'call_gemini_json', stub), \
                mock.patch.object(views, 'call_gemini_json', stub):
            return self.client.post(self.url, {}, format='json')

    # -- the regression guard ---------------------------------------------

    def test_returns_200_not_500(self):
        """
        The headline regression. Before the decorator fix this raised
        ContentNotRenderedError on every call.
        """
        self.seed_history()
        res = self.call()
        self.assertEqual(res.status_code, 200, getattr(res, 'data', res.content))
        self.assertEqual(res.data['generated'], 4)
        self.assertEqual(res.data['unit'], 'lb')

    def test_works_with_no_history_at_all(self):
        res = self.call()
        self.assertEqual(res.status_code, 200, getattr(res, 'data', res.content))

    def test_requires_authentication(self):
        self.client.credentials()
        res = self.client.post(self.url, {}, format='json')
        self.assertIn(res.status_code, (401, 403))

    def test_cannot_prepare_another_users_plan(self):
        other = User.objects.create_user(email='pw2@test.local', password='x')
        UserProfile.objects.create(user=other, preferred_unit='kg')
        token, _ = Token.objects.get_or_create(user=other)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)
        res = self.call()
        self.assertEqual(res.status_code, 404)

    # -- prompt correctness ------------------------------------------------

    def test_prompt_states_the_users_unit_and_leaks_no_kg(self):
        self.seed_history()
        self.call()
        prompt = self.captured['user']
        self.assertIn('ALL WEIGHTS ARE IN LB', prompt)
        self.assertNotIn('kg', prompt,
                         'an lb user must never be shown kg in the prompt')

    def test_prompt_tags_every_exercise_with_its_type(self):
        self.seed_history()
        self.call()
        prompt = self.captured['user']
        for tag in ('[weight_reps]', '[duration]', '[bodyweight_reps]', '[warmup]'):
            self.assertIn(tag, prompt)

    def test_weighted_history_is_converted_to_display_units(self):
        """40kg stored must reach the AI as 88.2lb, not 40."""
        self.seed_history()
        self.call()
        self.assertIn('88.2lb', self.captured['user'])

    def test_duration_history_is_in_seconds(self):
        self.seed_history()
        self.call()
        prompt = self.captured['user']
        self.assertIn('60s', prompt)
        self.assertIn('INCREASE TARGET', prompt)

    # -- contract enforcement on stored guides -----------------------------

    def test_weighted_exercise_keeps_weight_advice_only(self):
        self.seed_history()
        self.call()
        guide = ExerciseGuide.objects.get(user=self.user, name='Barbell Bench Press')
        self.assertEqual(guide.data['recommended_weight'], 'Start 135lb')
        self.assertIsNone(guide.data['recommended_target'])

    def test_duration_exercise_never_gets_weight_advice(self):
        """This is the "HOW TO shows start Xlb" complaint, at the source."""
        self.seed_history()
        self.call()
        guide = ExerciseGuide.objects.get(user=self.user, name='Plank')
        self.assertIsNone(guide.data['recommended_weight'])
        self.assertEqual(guide.data['recommended_target'], 'Hold 75 sec')

    def test_bodyweight_exercise_never_gets_weight_advice(self):
        self.seed_history()
        self.call()
        guide = ExerciseGuide.objects.get(user=self.user, name='Push-ups')
        self.assertIsNone(guide.data['recommended_weight'])

    def test_warmup_gets_no_advice_of_any_kind(self):
        self.seed_history()
        self.call()
        guide = ExerciseGuide.objects.get(user=self.user, name='Hamstring Stretch')
        self.assertIsNone(guide.data['recommended_weight'])
        self.assertIsNone(guide.data['recommended_target'])
        self.assertIsNone(guide.data['coaching_note'])

    # -- per-user isolation -------------------------------------------------

    def test_guides_are_scoped_to_the_requesting_user(self):
        other = User.objects.create_user(email='pw3@test.local', password='x')
        UserProfile.objects.create(user=other, preferred_unit='kg')
        self.seed_history()
        self.call()
        self.assertEqual(ExerciseGuide.objects.filter(user=self.user).count(), 4)
        self.assertEqual(ExerciseGuide.objects.filter(user=other).count(), 0)

    def test_rerun_updates_rather_than_duplicating(self):
        self.seed_history()
        self.call()
        self.call()
        self.assertEqual(ExerciseGuide.objects.filter(user=self.user).count(), 4)

    def test_guide_endpoint_prefers_personal_then_falls_back_to_generic(self):
        self.seed_history()
        self.call()
        ExerciseGuide.objects.create(user=None, name='Plank',
                                     data={'steps': ['generic'], 'images': []})

        res = self.client.get(reverse('exercise-guide'), {'name': 'Plank'})
        self.assertEqual(res.data.get('recommended_target'), 'Hold 75 sec',
                         'owner must get their personalised guide')

        other = User.objects.create_user(email='pw4@test.local', password='x')
        UserProfile.objects.create(user=other, preferred_unit='kg')
        token, _ = Token.objects.get_or_create(user=other)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)
        res = self.client.get(reverse('exercise-guide'), {'name': 'Plank'})
        self.assertEqual(res.data.get('steps'), ['generic'],
                         'another user must get the shared generic guide, '
                         'never the first user\'s personalised numbers')


class AnalyzeProgressionTests(APITestCase):
    """
    `_analyze_progression` is an internal helper, not a view. It previously
    carried view decorators (breaking both itself and prepare_week) and
    hardcoded kg while the profile default is lb.
    """

    def logs(self, values, field, days=(1, 8)):
        out = []
        for day in days:
            for _ in range(3):
                out.append({
                    'workout_session__started_at': dt.datetime(2026, 1, day, 10, 0),
                    'set_number': 1,
                    'weight_kg': values if field == 'weight_kg' else None,
                    'reps_completed': values if field == 'reps_completed' else 10,
                    'duration_seconds': values if field == 'duration_seconds' else None,
                })
        return out

    def test_is_callable_as_a_plain_function(self):
        """It must not be a DRF view -- prepare_week calls it with a list."""
        result = views._analyze_progression([], measurement_type=et.WEIGHT_REPS)
        self.assertIsInstance(result, dict)

    def test_weighted_summary_uses_the_users_unit(self):
        res = views._analyze_progression(
            self.logs(40.0, 'weight_kg'), target_reps=10,
            measurement_type=et.WEIGHT_REPS, unit='lb')
        self.assertEqual(res['direction'], 'increase')
        self.assertIn('88.2lb', res['summary'])
        self.assertNotIn('kg', res['summary'])

    def test_weighted_summary_in_kg_for_a_kg_user(self):
        res = views._analyze_progression(
            self.logs(40.0, 'weight_kg'), target_reps=10,
            measurement_type=et.WEIGHT_REPS, unit='kg')
        self.assertIn('40', res['summary'])
        self.assertIn('kg', res['summary'])

    def test_duration_progression_is_in_seconds(self):
        res = views._analyze_progression(
            self.logs(60, 'duration_seconds'),
            measurement_type=et.DURATION, unit='lb')
        self.assertEqual(res['direction'], 'increase')
        self.assertIn('sec', res['summary'])
        self.assertNotIn('lb', res['summary'],
                         'a plank must never be described in pounds')

    def test_bodyweight_progression_is_in_reps(self):
        logs = (self.logs(15, 'reps_completed', days=(1,))
                + self.logs(18, 'reps_completed', days=(8,)))
        res = views._analyze_progression(
            logs, measurement_type=et.BODYWEIGHT_REPS, unit='lb')
        self.assertEqual(res['direction'], 'increase')
        self.assertIn('reps', res['summary'])

    def test_warmup_never_produces_advice(self):
        res = views._analyze_progression(
            self.logs(60, 'duration_seconds'),
            measurement_type=et.WARMUP, unit='lb')
        self.assertIsNone(res['direction'])
        self.assertIsNone(res['summary'])

    def test_too_little_history_produces_no_advice(self):
        res = views._analyze_progression(
            self.logs(40.0, 'weight_kg', days=(1,))[:2],
            measurement_type=et.WEIGHT_REPS, unit='lb')
        self.assertIsNone(res['direction'])

    def test_session_date_handles_datetimes(self):
        """The old code sliced a datetime with [:10] -> TypeError."""
        self.assertEqual(
            views._session_date(dt.datetime(2026, 3, 4, 9, 0)), '2026-03-04')
        self.assertEqual(views._session_date(None), '')

    def test_target_reps_parsing(self):
        self.assertEqual(views._target_reps_from('8-12'), 12)
        self.assertEqual(views._target_reps_from('15'), 15)
        self.assertIsNone(views._target_reps_from('30 sec'))
        self.assertIsNone(views._target_reps_from('AMRAP'))
        self.assertIsNone(views._target_reps_from(None))

    def test_unit_conversion_helpers(self):
        self.assertEqual(views._to_display_weight(40.0, 'lb'), 88.2)
        self.assertEqual(views._to_display_weight(40.0, 'kg'), 40.0)
        self.assertIsNone(views._to_display_weight(None, 'lb'))

    def test_user_unit_defaults_to_lb(self):
        user = User.objects.create_user(email='nounit@test.local', password='x')
        # No profile at all -- must not raise.
        self.assertEqual(views._user_unit(user), 'lb')
        UserProfile.objects.create(user=user, preferred_unit='kg')
        user.refresh_from_db()
        self.assertEqual(views._user_unit(user), 'kg')


class KbAliasTests(APITestCase):
    """
    Image-library aliases. "Pec Deck Fly Machine" fuzzy-matched
    "reverse machine flyes" (shared machine/flye tokens), so a CHEST exercise
    was illustrated with REAR-DELT photos.
    """

    def test_pec_deck_resolves_to_the_chest_machine(self):
        for name in ('Pec Deck', 'Pec Deck Fly Machine', 'Machine Chest Fly',
                     'Pec Fly Machine'):
            self.assertEqual(views._get_kb_candidates(name, top_n=3)[0],
                             'butterfly', msg=name)

    def test_reverse_fly_still_resolves_to_rear_delt(self):
        for name in ('Reverse Fly Machine', 'Rear Delt Machine', 'Reverse Pec Deck'):
            self.assertEqual(views._get_kb_candidates(name, top_n=3)[0],
                             'reverse machine flyes', msg=name)

    def test_unaliased_names_still_match_normally(self):
        got = views._get_kb_candidates('Dumbbell Fly', top_n=3)
        self.assertIn('dumbbell flyes', got)

    def test_every_alias_target_exists_in_the_kb(self):
        kb = views._load_exercise_kb()
        missing = [(k, v) for k, v in views._KB_ALIASES.items() if v not in kb]
        self.assertEqual(missing, [], 'alias points at a non-existent KB key')
