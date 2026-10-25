"""
Tests for the data-repair migrations 0026 and 0027.

These matter more than usual: the local dev database is empty, so running
`migrate` locally proves nothing about a backfill -- it reports "0 rows" and
then does all its real work against production in the Render build. These
reconstruct the corrupt state and assert on the outcome.

The RunPython callables are invoked directly against the live app registry. The
model state for the fields they touch is identical to the final state, so this
exercises the real logic.
"""

import importlib

from django.apps import apps as real_apps
from django.test import TestCase

from api import exercise_types as et
from api.models import (
    User, UserProfile, WorkoutPlan, WorkoutDay, Exercise, WorkoutSession, SetLog,
)

KG_PER_LB = 0.453592

m0026 = importlib.import_module('api.migrations.0026_backfill_measurement_type')
m0027 = importlib.import_module('api.migrations.0027_migrate_duration_data')


class BackfillMeasurementTypeTests(TestCase):
    """
    Migration 0026. Migration 0024 was AddField-only with default='weight_reps',
    so every exercise that existed before it was mistyped -- the real reason
    warmups kept rendering weight+reps inputs.
    """

    def setUp(self):
        self.user = User.objects.create_user(email='m26@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        self.plan = WorkoutPlan.objects.create(user=self.user, title='Legacy')
        self.warm_day = WorkoutDay.objects.create(
            plan=self.plan, day_number=1, week_number=1, name='Warmup & Mobility')
        self.main_day = WorkoutDay.objects.create(
            plan=self.plan, day_number=2, week_number=1, name='Upper Body')

    def legacy(self, day, name, reps, stored='weight_reps'):
        """An exercise stamped with the pre-0024 default."""
        return Exercise.objects.create(day=day, name=name, sets=3, reps=reps,
                                       measurement_type=stored)

    def test_backfill_corrects_the_default_stamped_rows(self):
        stretch = self.legacy(self.warm_day, 'Hamstring Stretch', '30 sec')
        plank = self.legacy(self.main_day, 'Plank', '60 sec')
        bench = self.legacy(self.main_day, 'Barbell Bench Press', '8-12')
        carry = self.legacy(self.main_day, "Farmer's Walk", '45 sec')
        pushup = self.legacy(self.main_day, 'Push-ups', '15')

        m0026.backfill(real_apps, None)

        for obj, expected in (
            (stretch, et.WARMUP),
            (plank, et.DURATION),
            (bench, et.WEIGHT_REPS),
            (carry, et.WEIGHT_DISTANCE),
            (pushup, et.BODYWEIGHT_REPS),
        ):
            obj.refresh_from_db()
            self.assertEqual(obj.measurement_type, expected, msg=obj.name)

    def test_deliberate_non_default_values_are_left_alone(self):
        """A post-0024 row that already holds a real value must not be touched."""
        treadmill = self.legacy(self.main_day, 'Treadmill', '20 min',
                                stored=et.DURATION)
        m0026.backfill(real_apps, None)
        treadmill.refresh_from_db()
        self.assertEqual(treadmill.measurement_type, et.DURATION)

    def test_warmup_day_membership_drives_the_type(self):
        """A plank on a warmup day becomes warmup, not duration."""
        plank = self.legacy(self.warm_day, 'Plank', '30 sec')
        m0026.backfill(real_apps, None)
        plank.refresh_from_db()
        self.assertEqual(plank.measurement_type, et.WARMUP)

    def test_is_idempotent(self):
        plank = self.legacy(self.main_day, 'Plank', '60 sec')
        m0026.backfill(real_apps, None)
        m0026.backfill(real_apps, None)
        plank.refresh_from_db()
        self.assertEqual(plank.measurement_type, et.DURATION)

    def test_reverse_is_a_safe_noop(self):
        m0026.noop(real_apps, None)   # must not raise


class MigrateDurationDataTests(TestCase):
    """
    Migration 0027. Duration exercises stored seconds in `weight_kg`, and the
    frontend write path scaled anything named weight_kg by KG_PER_LB while the
    read path did not -- so an lb user's 60s plank was persisted as 27.2 and
    read back as 27.2.
    """

    def build(self, unit):
        user = User.objects.create_user(email='m27-%s@test.local' % unit,
                                        password='x')
        UserProfile.objects.create(user=user, preferred_unit=unit)
        plan = WorkoutPlan.objects.create(user=user, title='P')
        day = WorkoutDay.objects.create(plan=plan, day_number=1, week_number=1,
                                        name='Upper Body')
        ex = {
            'plank': Exercise.objects.create(day=day, name='Plank', sets=3,
                                             reps='60 sec',
                                             measurement_type=et.DURATION),
            'bench': Exercise.objects.create(day=day, name='Barbell Bench Press',
                                             sets=3, reps='8-12',
                                             measurement_type=et.WEIGHT_REPS),
            'carry': Exercise.objects.create(day=day, name="Farmer's Walk", sets=3,
                                             reps='45 sec',
                                             measurement_type=et.WEIGHT_DURATION),
            'pushup': Exercise.objects.create(day=day, name='Push-ups', sets=3,
                                              reps='15',
                                              measurement_type=et.BODYWEIGHT_REPS),
        }
        session = WorkoutSession.objects.create(user=user, exercise_day=day,
                                                day_name='Upper Body')

        # 60 real seconds, as the buggy write path would have stored it.
        raw = 60 * KG_PER_LB if unit == 'lb' else 60
        SetLog.objects.create(workout_session=session, exercise=ex['plank'],
                              exercise_name='Plank', set_number=1,
                              weight_kg=raw, is_completed=True)
        # Genuine 60kg x 10 that must survive untouched.
        SetLog.objects.create(workout_session=session, exercise=ex['bench'],
                              exercise_name='Barbell Bench Press', set_number=1,
                              weight_kg=60.0, reps_completed=10, is_completed=True)
        # Loaded carry: seconds parked in reps_completed, weight genuine.
        SetLog.objects.create(workout_session=session, exercise=ex['carry'],
                              exercise_name="Farmer's Walk", set_number=1,
                              weight_kg=30.0, reps_completed=45, is_completed=True)
        SetLog.objects.create(workout_session=session, exercise=ex['pushup'],
                              exercise_name='Push-ups', set_number=1,
                              reps_completed=15, is_completed=True)
        return {'user': user, 'day': day, 'ex': ex, 'session': session}

    def test_lb_user_seconds_are_unscaled(self):
        fx = self.build('lb')
        m0027.migrate_duration_data(real_apps, None)
        log = SetLog.objects.get(exercise=fx['ex']['plank'])
        self.assertEqual(log.duration_seconds, 60,
                         '27.216 must come back as 60 seconds')
        self.assertIsNone(log.weight_kg)

    def test_kg_user_seconds_are_not_unscaled(self):
        fx = self.build('kg')
        m0027.migrate_duration_data(real_apps, None)
        log = SetLog.objects.get(exercise=fx['ex']['plank'])
        self.assertEqual(log.duration_seconds, 60)
        self.assertIsNone(log.weight_kg)

    def test_genuine_weights_are_untouched(self):
        fx = self.build('lb')
        m0027.migrate_duration_data(real_apps, None)
        log = SetLog.objects.get(exercise=fx['ex']['bench'])
        self.assertEqual(log.weight_kg, 60.0)
        self.assertEqual(log.reps_completed, 10)
        self.assertIsNone(log.duration_seconds)

    def test_loaded_carry_seconds_move_out_of_reps(self):
        fx = self.build('lb')
        m0027.migrate_duration_data(real_apps, None)
        log = SetLog.objects.get(exercise=fx['ex']['carry'])
        self.assertEqual(log.duration_seconds, 45)
        self.assertEqual(log.weight_kg, 30.0, 'carry weight is genuine kg')
        self.assertIsNone(log.reps_completed)

    def test_bodyweight_reps_untouched(self):
        fx = self.build('lb')
        m0027.migrate_duration_data(real_apps, None)
        log = SetLog.objects.get(exercise=fx['ex']['pushup'])
        self.assertEqual(log.reps_completed, 15)
        self.assertIsNone(log.duration_seconds)

    def test_orphaned_log_is_classified_by_name(self):
        """SetLog.exercise is SET_NULL, so some rows have no Exercise to read."""
        fx = self.build('lb')
        orphan = Exercise.objects.create(day=fx['day'], name='Wall Sit', sets=1,
                                         reps='45 sec',
                                         measurement_type=et.DURATION)
        SetLog.objects.create(workout_session=fx['session'], exercise=orphan,
                              exercise_name='Wall Sit', set_number=1,
                              weight_kg=45 * KG_PER_LB, is_completed=True)
        Exercise.objects.filter(pk=orphan.pk).delete()

        m0027.migrate_duration_data(real_apps, None)

        log = SetLog.objects.get(exercise_name='Wall Sit')
        self.assertIsNone(log.exercise_id)
        self.assertEqual(log.duration_seconds, 45)
        self.assertIsNone(log.weight_kg)

    def test_is_idempotent(self):
        fx = self.build('lb')
        m0027.migrate_duration_data(real_apps, None)
        m0027.migrate_duration_data(real_apps, None)
        log = SetLog.objects.get(exercise=fx['ex']['plank'])
        self.assertEqual(log.duration_seconds, 60, 'must not double-convert')

    def test_reverse_round_trips(self):
        fx = self.build('lb')
        m0027.migrate_duration_data(real_apps, None)
        m0027.reverse(real_apps, None)
        log = SetLog.objects.get(exercise=fx['ex']['plank'])
        self.assertIsNone(log.duration_seconds)
        self.assertAlmostEqual(log.weight_kg, 60 * KG_PER_LB, places=4)
        carry = SetLog.objects.get(exercise=fx['ex']['carry'])
        self.assertEqual(carry.reps_completed, 45)

    def test_null_values_are_skipped_not_zeroed(self):
        fx = self.build('lb')
        SetLog.objects.filter(exercise=fx['ex']['plank']).update(weight_kg=None)
        m0027.migrate_duration_data(real_apps, None)
        log = SetLog.objects.get(exercise=fx['ex']['plank'])
        self.assertIsNone(log.duration_seconds,
                          'a missing value must stay missing, not become 0')


class MigrationChainTests(TestCase):
    """The full forward chain must be applied in the test database."""

    def test_new_columns_and_constraints_exist(self):
        from django.db import connection
        with connection.cursor() as cur:
            cols = [c.name for c in connection.introspection.get_table_description(
                cur, 'api_setlog')]
        self.assertIn('duration_seconds', cols)

        with connection.cursor() as cur:
            cols = [c.name for c in connection.introspection.get_table_description(
                cur, 'api_exerciseguide')]
        self.assertIn('user_id', cols)

        with connection.cursor() as cur:
            cols = [c.name for c in connection.introspection.get_table_description(
                cur, 'api_workoutplan')]
        self.assertIn('data_version', cols)

    def test_exercise_guide_is_unique_per_user_not_globally(self):
        from api.models import ExerciseGuide
        a = User.objects.create_user(email='ga@test.local', password='x')
        b = User.objects.create_user(email='gb@test.local', password='x')
        # The same name for two users must coexist -- this was globally unique,
        # which is why one user's advice overwrote everyone else's.
        ExerciseGuide.objects.create(user=a, name='Plank', data={})
        ExerciseGuide.objects.create(user=b, name='Plank', data={})
        ExerciseGuide.objects.create(user=None, name='Plank', data={})
        self.assertEqual(ExerciseGuide.objects.filter(name='Plank').count(), 3)
