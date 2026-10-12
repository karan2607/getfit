"""
The set-logging write contract and the read endpoints that feed the charts.

`workout_session_log_set` used to accept whatever the client sent, ignoring the
Exercise it had already loaded. That is how seconds ended up in `weight_kg` for
duration exercises, which then made a 180s plank render as a 396.8 lb personal
best. The history endpoints filtered on `weight_kg` with no type discriminator,
so bodyweight work was invisible entirely.
"""

from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework.authtoken.models import Token

from api import exercise_types as et
from api.models import (
    User, UserProfile, WorkoutPlan, WorkoutDay, Exercise, WorkoutSession, SetLog,
)


class LogSetContractTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='set@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)

        self.plan = WorkoutPlan.objects.create(user=self.user, title='P', is_active=True)
        self.day = WorkoutDay.objects.create(plan=self.plan, day_number=1,
                                             week_number=1, name='Upper Body')
        self.bench = Exercise.objects.create(
            day=self.day, name='Barbell Bench Press', sets=3, reps='8-12',
            measurement_type=et.WEIGHT_REPS)
        self.plank = Exercise.objects.create(
            day=self.day, name='Plank', sets=3, reps='60 sec',
            measurement_type=et.DURATION)
        self.pushup = Exercise.objects.create(
            day=self.day, name='Push-ups', sets=3, reps='15',
            measurement_type=et.BODYWEIGHT_REPS)
        self.carry = Exercise.objects.create(
            day=self.day, name="Farmer's Walk", sets=3, reps='45 sec',
            measurement_type=et.WEIGHT_DURATION)
        self.warm = Exercise.objects.create(
            day=self.day, name='Hamstring Stretch', sets=1, reps='30 sec',
            measurement_type=et.WARMUP)

        self.session = WorkoutSession.objects.create(
            user=self.user, exercise_day=self.day, day_name='Upper Body')
        self.url = reverse('workout-session-log-set', args=[self.session.id])

    def log(self, exercise, **fields):
        payload = {'exercise_id': str(exercise.id), 'set_number': 1}
        payload.update(fields)
        return self.client.post(self.url, payload, format='json')

    def test_duration_exercise_rejects_weight_and_reps(self):
        """A legacy client sending seconds as weight_kg must not corrupt the row."""
        res = self.log(self.plank, weight_kg=27.216, reps_completed=99,
                       duration_seconds=60, is_completed=True)
        self.assertEqual(res.status_code, 200, res.data)
        self.assertIsNone(res.data['weight_kg'])
        self.assertIsNone(res.data['reps_completed'])
        self.assertEqual(res.data['duration_seconds'], 60)
        self.assertEqual(res.data['measurement_type'], et.DURATION)

    def test_weighted_exercise_rejects_duration(self):
        res = self.log(self.bench, weight_kg=60.0, reps_completed=10,
                       duration_seconds=999, is_completed=True)
        self.assertEqual(res.data['weight_kg'], 60.0)
        self.assertEqual(res.data['reps_completed'], 10)
        self.assertIsNone(res.data['duration_seconds'])

    def test_bodyweight_exercise_rejects_weight(self):
        res = self.log(self.pushup, weight_kg=50, reps_completed=15,
                       is_completed=True)
        self.assertIsNone(res.data['weight_kg'])
        self.assertEqual(res.data['reps_completed'], 15)

    def test_loaded_carry_keeps_weight_and_time_but_not_reps(self):
        res = self.log(self.carry, weight_kg=30.0, reps_completed=45,
                       duration_seconds=45, is_completed=True)
        self.assertEqual(res.data['weight_kg'], 30.0)
        self.assertEqual(res.data['duration_seconds'], 45)
        self.assertIsNone(res.data['reps_completed'])

    def test_warmup_rejects_all_numeric_fields_but_stays_completable(self):
        res = self.log(self.warm, weight_kg=20, reps_completed=12,
                       duration_seconds=30, is_completed=True)
        self.assertIsNone(res.data['weight_kg'])
        self.assertIsNone(res.data['reps_completed'])
        self.assertIsNone(res.data['duration_seconds'])
        self.assertTrue(res.data['is_completed'])

    def test_partial_update_preserves_untouched_fields(self):
        self.log(self.bench, weight_kg=60.0, reps_completed=10, is_completed=True)
        res = self.log(self.bench, reps_completed=12)
        self.assertEqual(res.data['weight_kg'], 60.0, 'weight must survive')
        self.assertEqual(res.data['reps_completed'], 12)

    def test_explicit_null_clears_a_field(self):
        """
        The old `!= null` guards made clearing impossible -- you could never undo
        a mistyped weight.
        """
        self.log(self.bench, weight_kg=60.0, reps_completed=10)
        res = self.log(self.bench, weight_kg=None)
        self.assertIsNone(res.data['weight_kg'])
        self.assertEqual(res.data['reps_completed'], 10, 'reps untouched')

    def test_repeated_posts_are_idempotent_not_duplicated(self):
        for _ in range(3):
            self.log(self.bench, weight_kg=60.0, reps_completed=10)
        self.assertEqual(
            SetLog.objects.filter(workout_session=self.session,
                                  exercise=self.bench).count(), 1)

    def test_missing_required_fields_are_rejected(self):
        res = self.client.post(self.url, {'set_number': 1}, format='json')
        self.assertEqual(res.status_code, 400)
        res = self.client.post(self.url, {'exercise_id': str(self.bench.id)},
                               format='json')
        self.assertEqual(res.status_code, 400)

    def test_cannot_log_to_another_users_exercise(self):
        other = User.objects.create_user(email='other@test.local', password='x')
        UserProfile.objects.create(user=other, preferred_unit='kg')
        other_plan = WorkoutPlan.objects.create(user=other, title='Theirs')
        other_day = WorkoutDay.objects.create(plan=other_plan, day_number=1,
                                              week_number=1, name='D')
        other_ex = Exercise.objects.create(day=other_day, name='Squat', sets=3,
                                           reps='5', measurement_type=et.WEIGHT_REPS)
        res = self.log(other_ex, weight_kg=100)
        self.assertEqual(res.status_code, 404)

    def test_cannot_log_to_a_completed_session(self):
        self.session.is_completed = True
        self.session.save()
        res = self.log(self.bench, weight_kg=60.0)
        self.assertEqual(res.status_code, 400)

    def test_unauthenticated_is_rejected(self):
        self.client.credentials()
        res = self.log(self.bench, weight_kg=60.0)
        self.assertIn(res.status_code, (401, 403))


class ExerciseHistoryTests(APITestCase):
    """
    History must report the measurement type and filter on the right field, or
    the chart has to guess -- which is how plank seconds became pounds.
    """

    def setUp(self):
        self.user = User.objects.create_user(email='hist@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)

        self.plan = WorkoutPlan.objects.create(user=self.user, title='P')
        self.day = WorkoutDay.objects.create(plan=self.plan, day_number=1,
                                             week_number=1, name='Upper Body')
        self.bench = Exercise.objects.create(
            day=self.day, name='Barbell Bench Press', sets=3, reps='8-12',
            measurement_type=et.WEIGHT_REPS)
        self.plank = Exercise.objects.create(
            day=self.day, name='Plank', sets=3, reps='60 sec',
            measurement_type=et.DURATION)
        self.pushup = Exercise.objects.create(
            day=self.day, name='Push-ups', sets=3, reps='15',
            measurement_type=et.BODYWEIGHT_REPS)

        session = WorkoutSession.objects.create(
            user=self.user, exercise_day=self.day, day_name='Upper Body')
        SetLog.objects.create(workout_session=session, exercise=self.bench,
                              exercise_name='Barbell Bench Press', set_number=1,
                              weight_kg=60.0, reps_completed=10, is_completed=True)
        SetLog.objects.create(workout_session=session, exercise=self.plank,
                              exercise_name='Plank', set_number=1,
                              duration_seconds=180, is_completed=True)
        SetLog.objects.create(workout_session=session, exercise=self.pushup,
                              exercise_name='Push-ups', set_number=1,
                              reps_completed=15, is_completed=True)

    def history(self, name):
        return self.client.get(reverse('workout-exercise-history', args=[name]))

    def test_weighted_history(self):
        res = self.history('Barbell Bench Press')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['measurement_type'], et.WEIGHT_REPS)
        self.assertEqual(res.data['metric'], 'weight_kg')
        self.assertEqual(len(res.data['logs']), 1)

    def test_duration_history_reports_seconds_not_weight(self):
        res = self.history('Plank')
        self.assertEqual(res.data['measurement_type'], et.DURATION)
        self.assertEqual(res.data['metric'], 'duration_seconds')
        row = res.data['logs'][0]
        self.assertEqual(row['duration_seconds'], 180)
        self.assertIsNone(row['weight_kg'],
                          'a plank must never report a weight')

    def test_bodyweight_history_is_visible(self):
        """
        Previously filtered out entirely by `weight_kg__isnull=False`, so
        bodyweight exercises showed "no data logged yet" forever.
        """
        res = self.history('Push-ups')
        self.assertEqual(res.data['measurement_type'], et.BODYWEIGHT_REPS)
        self.assertEqual(res.data['metric'], 'reps_completed')
        self.assertEqual(len(res.data['logs']), 1)
        self.assertEqual(res.data['logs'][0]['reps_completed'], 15)

    def test_history_is_case_insensitive(self):
        self.assertEqual(len(self.history('plank').data['logs']), 1)
        self.assertEqual(len(self.history('PLANK').data['logs']), 1)

    def test_unknown_exercise_returns_empty_not_an_error(self):
        res = self.history('Nonexistent Lift')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['logs'], [])

    def test_history_is_scoped_to_the_requesting_user(self):
        other = User.objects.create_user(email='o2@test.local', password='x')
        UserProfile.objects.create(user=other, preferred_unit='kg')
        token, _ = Token.objects.get_or_create(user=other)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)
        res = self.history('Barbell Bench Press')
        self.assertEqual(res.data['logs'], [])

    def test_exercises_list_carries_type_and_per_type_bests(self):
        res = self.client.get(reverse('workout-exercises-list'))
        self.assertEqual(res.status_code, 200)
        rows = {r['exercise_name']: r for r in res.data}

        self.assertEqual(rows['Plank']['measurement_type'], et.DURATION)
        self.assertEqual(rows['Plank']['best_duration_seconds'], 180)
        self.assertIsNone(rows['Plank']['best_weight_kg'],
                          "a plank's best must not be a weight")

        self.assertEqual(rows['Barbell Bench Press']['measurement_type'],
                         et.WEIGHT_REPS)
        self.assertEqual(rows['Barbell Bench Press']['best_weight_kg'], 60.0)

        self.assertEqual(rows['Push-ups']['measurement_type'], et.BODYWEIGHT_REPS)
        self.assertEqual(rows['Push-ups']['best_reps'], 15)

    def test_orphaned_log_still_reports_a_sensible_type(self):
        """
        SetLog.exercise is SET_NULL, so logs outlive plan edits. The type must
        fall back to the classifier rather than defaulting to weight+reps.
        """
        Exercise.objects.filter(pk=self.plank.pk).delete()
        res = self.history('Plank')
        self.assertEqual(res.data['measurement_type'], et.DURATION)
        self.assertEqual(res.data['logs'][0]['duration_seconds'], 180)
