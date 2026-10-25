"""
Regression tests for measurement_type surviving the plan lifecycle.

This is the bug class that kept coming back. Two compounding causes:

  1. Migration 0024 added measurement_type as AddField-only with
     default='weight_reps', so every pre-existing exercise was mistyped. No
     frontend change could fix it.
  2. AI plan edits go through `replacePlan` -- a full PUT that deletes and
     recreates every day. The frontend's PreviewDay type had no
     measurement_type field, so each edit silently stripped the type back to
     the default, re-breaking exercises that had been correct.

These tests assert the type survives creation, a full PUT that omits it, a PUT
that sends it, and an AI payload that lies about it.
"""

from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework.authtoken.models import Token

from api import exercise_types as et
from api.models import (
    User, UserProfile, WorkoutPlan, WorkoutDay, Exercise, WorkoutSession, SetLog,
)


class PlanRoundTripTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='rt@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)

    # -- helpers ----------------------------------------------------------

    def make_plan(self):
        """A plan with one warmup day and one mixed training day."""
        plan = WorkoutPlan.objects.create(user=self.user, title='P', is_active=True)
        warm = WorkoutDay.objects.create(
            plan=plan, day_number=1, week_number=1, name='Warmup & Mobility')
        main = WorkoutDay.objects.create(
            plan=plan, day_number=2, week_number=1, name='Upper Body')
        return plan, warm, main

    def types_by_name(self, plan):
        return {
            ex.name: ex.measurement_type
            for ex in Exercise.objects.filter(day__plan=plan)
        }

    def put_payload(self, include_types):
        """
        A plan-replace payload shaped like what the AI chat flow sends.

        include_types=False reproduces the old frontend, which omitted
        measurement_type entirely.
        """
        def ex(name, sets, reps, mtype):
            out = {'name': name, 'sets': sets, 'reps': reps}
            if include_types:
                out['measurement_type'] = mtype
            return out

        return {
            'title': 'P',
            'days': [
                {
                    'day_number': 1, 'name': 'Warmup & Mobility', 'is_rest_day': False,
                    'exercises': [ex('Hamstring Stretch', 1, '30 sec', 'warmup')],
                },
                {
                    'day_number': 2, 'name': 'Upper Body', 'is_rest_day': False,
                    'exercises': [
                        ex('Barbell Bench Press', 3, '8-12', 'weight_reps'),
                        ex('Plank', 3, '60 sec', 'duration'),
                        ex('Push-ups', 3, '15', 'bodyweight_reps'),
                        ex("Farmer's Walk", 3, '45 sec', 'weight_duration'),
                    ],
                },
            ],
        }

    def expected(self):
        return {
            'Hamstring Stretch': et.WARMUP,
            'Barbell Bench Press': et.WEIGHT_REPS,
            'Plank': et.DURATION,
            'Push-ups': et.BODYWEIGHT_REPS,
            "Farmer's Walk": et.WEIGHT_DISTANCE,
        }

    # -- tests ------------------------------------------------------------

    def test_put_without_measurement_type_still_classifies_correctly(self):
        """
        THE regression. The old frontend omitted measurement_type on every PUT,
        so each AI edit reset everything to 'weight_reps'. The server must now
        re-classify instead of defaulting.
        """
        plan, _, _ = self.make_plan()
        res = self.client.put(
            reverse('workout-plan-detail', args=[plan.id]),
            self.put_payload(include_types=False), format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(self.types_by_name(plan), self.expected())

    def test_put_with_measurement_type_preserves_it(self):
        plan, _, _ = self.make_plan()
        res = self.client.put(
            reverse('workout-plan-detail', args=[plan.id]),
            self.put_payload(include_types=True), format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(self.types_by_name(plan), self.expected())

    def test_repeated_edits_do_not_degrade_types(self):
        """
        Five successive AI edits that omit the field must not drift. The old
        code lost the type on the very first one.
        """
        plan, _, _ = self.make_plan()
        url = reverse('workout-plan-detail', args=[plan.id])
        for i in range(5):
            res = self.client.put(url, self.put_payload(include_types=False),
                                  format='json')
            self.assertEqual(res.status_code, 200, res.data)
            self.assertEqual(self.types_by_name(plan), self.expected(),
                             msg='drifted on edit %d' % (i + 1))

    def test_ai_lying_about_type_is_overridden(self):
        """An AI payload claiming a plank is weight+reps must be corrected."""
        plan, _, _ = self.make_plan()
        payload = {
            'title': 'P',
            'days': [{
                'day_number': 1, 'name': 'Upper Body', 'is_rest_day': False,
                'exercises': [
                    {'name': 'Plank', 'sets': 3, 'reps': '60 sec',
                     'measurement_type': 'weight_reps'},
                    {'name': 'Treadmill', 'sets': 1, 'reps': '20 min',
                     'measurement_type': 'weight_reps'},
                    {'name': 'Push-ups', 'sets': 3, 'reps': '15',
                     'measurement_type': 'weight_duration'},
                ],
            }],
        }
        res = self.client.put(reverse('workout-plan-detail', args=[plan.id]),
                              payload, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(self.types_by_name(plan), {
            'Plank': et.DURATION,
            'Treadmill': et.CARDIO_MACHINE,
            'Push-ups': et.BODYWEIGHT_REPS,
        })

    def test_ai_garbage_type_does_not_reach_the_database(self):
        plan, _, _ = self.make_plan()
        payload = {
            'title': 'P',
            'days': [{
                'day_number': 1, 'name': 'Upper Body', 'is_rest_day': False,
                'exercises': [
                    {'name': 'Plank', 'sets': 3, 'reps': '60 sec',
                     'measurement_type': 'cardio'},
                    {'name': 'Turkish Getup', 'sets': 3, 'reps': '5',
                     'measurement_type': 'NONSENSE'},
                ],
            }],
        }
        res = self.client.put(reverse('workout-plan-detail', args=[plan.id]),
                              payload, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        for ex in Exercise.objects.filter(day__plan=plan):
            self.assertIn(ex.measurement_type, et.ALL_TYPES,
                          msg='%s stored an invalid type' % ex.name)

    def test_warmup_day_forces_warmup_on_every_exercise(self):
        plan, _, _ = self.make_plan()
        payload = {
            'title': 'P',
            'days': [{
                'day_number': 1, 'name': 'Warmup & Mobility', 'is_rest_day': False,
                'exercises': [
                    {'name': 'Plank', 'sets': 1, 'reps': '30 sec'},
                    {'name': 'Barbell Bench Press', 'sets': 1, 'reps': '10'},
                ],
            }],
        }
        res = self.client.put(reverse('workout-plan-detail', args=[plan.id]),
                              payload, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        types = set(self.types_by_name(plan).values())
        self.assertEqual(types, {et.WARMUP})

    def test_serializer_exposes_measurement_type_to_the_client(self):
        """
        Without this the frontend cannot render the right inputs, and the PUT
        round-trip has nothing to echo back.
        """
        plan, _, main = self.make_plan()
        Exercise.objects.create(day=main, name='Plank', sets=3, reps='60 sec',
                                measurement_type=et.DURATION)
        res = self.client.get(reverse('workout-plan-detail', args=[plan.id]))
        self.assertEqual(res.status_code, 200)
        exercises = [e for d in res.data['days'] for e in d['exercises']]
        self.assertTrue(exercises)
        for ex in exercises:
            self.assertIn('measurement_type', ex)
        self.assertEqual(exercises[0]['measurement_type'], et.DURATION)


class PlanValidationTests(APITestCase):
    """
    _validate_plan_json previously checked no exercise-level fields at all, so a
    missing `name` became a KeyError -> 500 during plan creation.
    """

    def setUp(self):
        self.user = User.objects.create_user(email='val@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)
        self.plan = WorkoutPlan.objects.create(user=self.user, title='P')
        self.url = reverse('workout-plan-detail', args=[self.plan.id])

    def test_missing_exercise_name_is_a_400_not_a_500(self):
        res = self.client.put(self.url, {
            'title': 'P',
            'days': [{'day_number': 1, 'name': 'D', 'is_rest_day': False,
                      'exercises': [{'sets': 3, 'reps': '10'}]}],
        }, format='json')
        self.assertEqual(res.status_code, 400, res.data)

    def test_blank_exercise_name_is_rejected(self):
        res = self.client.put(self.url, {
            'title': 'P',
            'days': [{'day_number': 1, 'name': 'D', 'is_rest_day': False,
                      'exercises': [{'name': '   ', 'sets': 3, 'reps': '10'}]}],
        }, format='json')
        self.assertEqual(res.status_code, 400, res.data)

    def test_invalid_sets_is_rejected(self):
        for bad in (0, -1, 'many', None):
            res = self.client.put(self.url, {
                'title': 'P',
                'days': [{'day_number': 1, 'name': 'D', 'is_rest_day': False,
                          'exercises': [{'name': 'Squat', 'sets': bad, 'reps': '10'}]}],
            }, format='json')
            self.assertEqual(res.status_code, 400, msg='sets=%r' % (bad,))

    def test_exercise_not_an_object_is_rejected(self):
        res = self.client.put(self.url, {
            'title': 'P',
            'days': [{'day_number': 1, 'name': 'D', 'is_rest_day': False,
                      'exercises': ['Bench Press']}],
        }, format='json')
        self.assertEqual(res.status_code, 400, res.data)

    def test_rest_day_needs_no_exercises(self):
        res = self.client.put(self.url, {
            'title': 'P',
            'days': [{'day_number': 1, 'name': 'Rest', 'is_rest_day': True}],
        }, format='json')
        self.assertEqual(res.status_code, 200, res.data)


class PlanEditDataLossTests(APITestCase):
    """
    A plan-replace PUT deletes and recreates days, which cascades to set logs.

    These document the actual behaviour so a future change cannot quietly make
    it worse: completed SetLog rows survive (their FK is SET_NULL, not CASCADE)
    and remain attributable via the denormalized exercise_name.
    """

    def setUp(self):
        self.user = User.objects.create_user(email='loss@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)

    def test_logged_sets_survive_a_plan_replace(self):
        plan = WorkoutPlan.objects.create(user=self.user, title='P', is_active=True)
        day = WorkoutDay.objects.create(plan=plan, day_number=1, week_number=1,
                                        name='Upper Body')
        bench = Exercise.objects.create(day=day, name='Barbell Bench Press', sets=3,
                                        reps='8-12', measurement_type=et.WEIGHT_REPS)
        session = WorkoutSession.objects.create(user=self.user, exercise_day=day,
                                                day_name='Upper Body')
        SetLog.objects.create(workout_session=session, exercise=bench,
                              exercise_name='Barbell Bench Press', set_number=1,
                              weight_kg=60.0, reps_completed=10, is_completed=True)

        res = self.client.put(reverse('workout-plan-detail', args=[plan.id]), {
            'title': 'P',
            'days': [{'day_number': 1, 'name': 'Upper Body', 'is_rest_day': False,
                      'exercises': [{'name': 'Barbell Bench Press', 'sets': 3,
                                     'reps': '8-12'}]}],
        }, format='json')
        self.assertEqual(res.status_code, 200, res.data)

        log = SetLog.objects.get(workout_session=session, set_number=1)
        self.assertEqual(log.weight_kg, 60.0)
        self.assertEqual(log.reps_completed, 10)
        self.assertTrue(log.is_completed)
        # The Exercise FK is nulled by the replace, but the name survives.
        self.assertEqual(log.exercise_name, 'Barbell Bench Press')
