"""
Progression advice must never go stale.

Reported: "Maybe because the week hasn't changed and our yesterday changes are
not reflecting cz new plans for the week haven't been generated?" — correct
diagnosis. All 31 personal ExerciseGuide rows in production were written in one
10-second window before the progression fix shipped, and nothing regenerated
them, so the Seated Calf Raise guide still read "Maintain 90.0lb".

The root cause was caching: prepare_week baked history-derived advice into the
same row as stable content (steps/muscles/tips/images). Advice derived from
logged history is wrong the moment a set is logged.

Advice is now computed per request and never cached. These tests assert that a
newly logged set changes the advice immediately, with no prepare_week run.
"""

import datetime as dt
from unittest import mock

from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework.authtoken.models import Token

from api import views, gemini, exercise_types as et
from api.models import (
    User, UserProfile, WorkoutPlan, WorkoutDay, Exercise, WorkoutSession,
    SetLog, ExerciseGuide,
)

KG_PER_LB = 0.453592


class AdviceFreshnessTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='fresh@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)

        self.plan = WorkoutPlan.objects.create(
            user=self.user, title='P', is_active=True, current_week=1)
        self.day = WorkoutDay.objects.create(
            plan=self.plan, day_number=1, week_number=1, name='Legs')
        self.calf = Exercise.objects.create(
            day=self.day, name='Seated Calf Raise', sets=3, reps='15-20',
            measurement_type=et.WEIGHT_REPS)

        # A cached guide holding STABLE content only, already stamped current.
        ExerciseGuide.objects.create(
            user=None, name='Seated Calf Raise',
            data={'steps': ['a', 'b'], 'muscles': ['Calves'], 'tips': ['t'],
                  'category': 'other', 'images': [],
                  'advice_version': views.ADVICE_VERSION})

        self.url = reverse('exercise-guide')

    def log_session(self, day, weight_lb, reps, month=10):
        s = WorkoutSession.objects.create(
            user=self.user, exercise_day=self.day, day_name='Legs')
        WorkoutSession.objects.filter(pk=s.pk).update(
            started_at=timezone.make_aware(dt.datetime(2026, month, day, 10, 0)))
        for n in (1, 2, 3):
            SetLog.objects.create(
                workout_session=s, exercise=self.calf,
                exercise_name='Seated Calf Raise', set_number=n,
                weight_kg=weight_lb * KG_PER_LB, reps_completed=reps,
                is_completed=True)

    def guide(self):
        res = self.client.get(self.url, {'name': 'Seated Calf Raise'})
        self.assertEqual(res.status_code, 200, res.data)
        return res.data

    # -- the headline property ------------------------------------------------

    def test_advice_reflects_a_new_set_with_no_prepare_week_run(self):
        """
        The reported bug: advice only changed when the week was re-prepared.
        """
        self.log_session(1, 50, 20)
        self.log_session(8, 50, 20)
        first = self.guide()
        self.assertIn('Go up to', first['recommended_weight'] or '')

        # An over-ambitious jump, logged right now.
        self.log_session(15, 90, 8)
        second = self.guide()
        self.assertIn('Drop to about 50.0lb', second['recommended_weight'] or '')
        self.assertNotEqual(first['recommended_weight'],
                            second['recommended_weight'])

    def test_advice_is_not_stored_in_the_cached_row(self):
        self.log_session(1, 50, 20)
        self.log_session(8, 50, 20)
        self.guide()
        cached = ExerciseGuide.objects.get(user=None, name='Seated Calf Raise')
        for field in ('recommended_weight', 'recommended_target', 'coaching_note'):
            self.assertNotIn(field, cached.data,
                             msg='%s must not be cached' % field)

    def test_never_repeats_a_weight_the_user_just_failed_at(self):
        """The exact production symptom: 'Maintain 90.0lb' after 9/8/8 reps."""
        self.log_session(1, 50, 20)
        self.log_session(15, 90, 8)
        advice = self.guide()['recommended_weight'] or ''
        self.assertNotIn('Maintain 90', advice)
        self.assertNotIn('Stay at 90', advice)
        self.assertIn('50.0lb', advice)

    # -- correctness of the computed advice -----------------------------------

    def test_inside_the_range_holds_weight(self):
        self.log_session(1, 50, 17)
        self.log_session(8, 50, 17)
        advice = self.guide()
        self.assertIn('Stay at 50.0lb', advice['recommended_weight'])
        self.assertIn('Build to 20 reps', advice['recommended_weight'])

    def test_topping_the_range_adds_weight(self):
        self.log_session(1, 50, 20)
        self.log_session(8, 50, 20)
        advice = self.guide()
        self.assertIn('Go up to', advice['recommended_weight'])

    def test_advice_is_in_the_users_unit(self):
        self.log_session(1, 50, 20)
        self.log_session(8, 50, 20)
        advice = self.guide()
        self.assertEqual(advice['unit'], 'lb')
        self.assertNotIn('kg', advice['recommended_weight'])

    def test_kg_user_gets_kg(self):
        self.user.profile.preferred_unit = 'kg'
        self.user.profile.save()
        self.log_session(1, 50, 20)
        self.log_session(8, 50, 20)
        advice = self.guide()
        self.assertEqual(advice['unit'], 'kg')

    def test_no_history_yields_no_advice_rather_than_a_guess(self):
        advice = self.guide()
        self.assertIsNone(advice['recommended_weight'])
        self.assertIsNone(advice['coaching_note'])

    def test_stable_content_still_served_from_cache(self):
        advice = self.guide()
        self.assertEqual(advice['steps'], ['a', 'b'])
        self.assertEqual(advice['muscles'], ['Calves'])

    def test_measurement_type_is_reported(self):
        self.assertEqual(self.guide()['measurement_type'], et.WEIGHT_REPS)

    # -- per-type advice ------------------------------------------------------

    def test_duration_exercise_gets_a_time_target_not_a_weight(self):
        plank = Exercise.objects.create(
            day=self.day, name='Plank', sets=3, reps='45 sec',
            measurement_type=et.DURATION)
        ExerciseGuide.objects.create(
            user=None, name='Plank',
            data={'steps': [], 'images': [],
                  'advice_version': views.ADVICE_VERSION})
        for d in (1, 8):
            s = WorkoutSession.objects.create(user=self.user, exercise_day=self.day,
                                              day_name='Legs')
            WorkoutSession.objects.filter(pk=s.pk).update(
                started_at=timezone.make_aware(dt.datetime(2026, 10, d, 10, 0)))
            for n in (1, 2, 3):
                SetLog.objects.create(
                    workout_session=s, exercise=plank, exercise_name='Plank',
                    set_number=n, duration_seconds=45, is_completed=True)

        res = self.client.get(self.url, {'name': 'Plank'})
        self.assertIsNone(res.data['recommended_weight'])
        self.assertIn('sec', res.data['recommended_target'])

    def test_warmup_gets_no_advice(self):
        Exercise.objects.create(
            day=self.day, name='Hamstring Stretch', sets=1, reps='30 sec',
            measurement_type=et.WARMUP)
        ExerciseGuide.objects.create(
            user=None, name='Hamstring Stretch',
            data={'steps': [], 'images': [],
                  'advice_version': views.ADVICE_VERSION})
        res = self.client.get(self.url, {'name': 'Hamstring Stretch'})
        self.assertIsNone(res.data['recommended_weight'])
        self.assertIsNone(res.data['recommended_target'])
        self.assertIsNone(res.data['coaching_note'])


class AdviceVersionTests(APITestCase):
    """The version stamp covers the CACHED (prompt-generated) content."""

    def setUp(self):
        self.user = User.objects.create_user(email='ver@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)
        self.url = reverse('exercise-guide')

    def test_a_guide_below_the_current_version_is_regenerated(self):
        ExerciseGuide.objects.create(
            user=None, name='Barbell Squat',
            data={'steps': ['stale'], 'images': [], 'advice_version': 0})

        def _call(*, system_prompt, user_prompt, **kw):
            return {'steps': ['fresh'], 'muscles': ['Quads'], 'tips': ['t'],
                    'category': 'squat', 'kb_key': None}

        with mock.patch.object(gemini, 'call_gemini_json', _call), \
                mock.patch.object(views, 'call_gemini_json', _call):
            res = self.client.get(self.url, {'name': 'Barbell Squat'})

        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['steps'], ['fresh'])
        cached = ExerciseGuide.objects.get(user=None, name='Barbell Squat')
        self.assertEqual(cached.data['advice_version'], views.ADVICE_VERSION)

    def test_a_current_guide_is_served_without_an_ai_call(self):
        ExerciseGuide.objects.create(
            user=None, name='Barbell Squat',
            data={'steps': ['cached'], 'images': [],
                  'advice_version': views.ADVICE_VERSION})

        def _boom(**kwargs):
            raise AssertionError('must not call the AI for a current guide')

        with mock.patch.object(gemini, 'call_gemini_json', _boom), \
                mock.patch.object(views, 'call_gemini_json', _boom):
            res = self.client.get(self.url, {'name': 'Barbell Squat'})
        self.assertEqual(res.data['steps'], ['cached'])

    def test_newly_generated_guides_are_stamped(self):
        def _call(*, system_prompt, user_prompt, **kw):
            return {'steps': ['s'], 'muscles': ['m'], 'tips': ['t'],
                    'category': 'other', 'kb_key': None}

        with mock.patch.object(gemini, 'call_gemini_json', _call), \
                mock.patch.object(views, 'call_gemini_json', _call):
            self.client.get(self.url, {'name': 'Some New Lift'})
        cached = ExerciseGuide.objects.get(user=None, name='Some New Lift')
        self.assertEqual(cached.data['advice_version'], views.ADVICE_VERSION)

    def test_generated_guides_never_store_advice_fields(self):
        def _call(*, system_prompt, user_prompt, **kw):
            # A model that volunteers advice anyway must not have it persisted.
            return {'steps': ['s'], 'muscles': ['m'], 'tips': ['t'],
                    'category': 'other', 'kb_key': None,
                    'recommended_weight': 'Start 100lb',
                    'coaching_note': 'stale prose'}

        with mock.patch.object(gemini, 'call_gemini_json', _call), \
                mock.patch.object(views, 'call_gemini_json', _call):
            self.client.get(self.url, {'name': 'Another Lift'})
        cached = ExerciseGuide.objects.get(user=None, name='Another Lift')
        self.assertNotIn('recommended_weight', cached.data)
        self.assertNotIn('coaching_note', cached.data)


class PrepareWeekNoLongerCachesAdviceTests(APITestCase):
    def test_prepare_week_stores_stable_content_only(self):
        user = User.objects.create_superuser(email='pw2@test.local', password='x')
        UserProfile.objects.create(user=user, preferred_unit='lb')
        token, _ = Token.objects.get_or_create(user=user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)

        plan = WorkoutPlan.objects.create(user=user, title='P', is_active=True,
                                          current_week=1)
        day = WorkoutDay.objects.create(plan=plan, day_number=1, week_number=1,
                                        name='Legs')
        Exercise.objects.create(day=day, name='Barbell Squat', sets=3,
                                reps='8-12', measurement_type=et.WEIGHT_REPS)

        def _call(*, system_prompt, user_prompt, **kw):
            return [{'steps': ['a'], 'muscles': ['Quads'], 'tips': ['t'],
                     'category': 'squat', 'kb_key': None,
                     'recommended_weight': 'Start 200lb',
                     'coaching_note': 'should not persist'}]

        with mock.patch.object(gemini, 'call_gemini_json', _call), \
                mock.patch.object(views, 'call_gemini_json', _call):
            res = self.client.post(
                reverse('workout-plan-prepare-week', args=[plan.id]), {},
                format='json')

        self.assertEqual(res.status_code, 200, getattr(res, 'data', res.content))
        cached = ExerciseGuide.objects.get(user=user, name='Barbell Squat')
        self.assertEqual(cached.data['steps'], ['a'])
        self.assertEqual(cached.data['advice_version'], views.ADVICE_VERSION)
        for field in ('recommended_weight', 'recommended_target', 'coaching_note'):
            self.assertNotIn(field, cached.data)
