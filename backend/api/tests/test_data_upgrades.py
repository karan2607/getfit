"""
Tests for the in-place plan upgrade mechanism.

The requirement: "when we roll out a major update, we can transition the data so
nothing is lost and also, changes should be updated in already created workouts.
Shouldn't have to create new ones."

So the hard assertions here are the *non-destructive* ones: set logs keep their
ids and values, sessions survive, and `last_completed_at` is preserved.
"""

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from api import data_upgrades, exercise_types as et
from api.models import (
    User, UserProfile, WorkoutPlan, WorkoutDay, Exercise, WorkoutSession, SetLog,
)


class UpgradePlanTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='up@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')

        self.plan = WorkoutPlan.objects.create(
            user=self.user, title='Legacy Plan', is_active=True)
        self.day = WorkoutDay.objects.create(
            plan=self.plan, day_number=1, week_number=1, name='Upper Body')
        self.day.last_completed_at = timezone.now()
        self.day.save()
        self.completed_marker = self.day.last_completed_at

        # A plan written by code that predates the server-side normalize():
        # everything stamped weight_reps, values in the wrong fields.
        self.plank = Exercise.objects.create(
            day=self.day, name='Plank', sets=3, reps='60 sec',
            measurement_type='weight_reps')
        self.pushup = Exercise.objects.create(
            day=self.day, name='Push-ups', sets=3, reps='15',
            measurement_type='weight_reps')
        self.bench = Exercise.objects.create(
            day=self.day, name='Barbell Bench Press', sets=3, reps='8-12',
            measurement_type='weight_reps')

        self.session = WorkoutSession.objects.create(
            user=self.user, exercise_day=self.day, day_name='Upper Body')
        self.plank_log = SetLog.objects.create(
            workout_session=self.session, exercise=self.plank,
            exercise_name='Plank', set_number=1,
            weight_kg=27.2, duration_seconds=60, is_completed=True)
        self.push_log = SetLog.objects.create(
            workout_session=self.session, exercise=self.pushup,
            exercise_name='Push-ups', set_number=1,
            weight_kg=50.0, reps_completed=15, is_completed=True)
        self.bench_log = SetLog.objects.create(
            workout_session=self.session, exercise=self.bench,
            exercise_name='Barbell Bench Press', set_number=1,
            weight_kg=60.0, reps_completed=10, is_completed=True)
        self.log_ids = set(SetLog.objects.values_list('id', flat=True))

    # -- correctness --------------------------------------------------------

    def test_new_plan_starts_at_version_zero(self):
        fresh = WorkoutPlan.objects.create(user=self.user, title='New')
        self.assertEqual(fresh.data_version, 0)

    def test_upgrade_reclassifies_and_fixes_the_field_contract(self):
        call_command('upgrade_data', verbosity=0)

        self.plank.refresh_from_db()
        self.pushup.refresh_from_db()
        self.bench.refresh_from_db()
        self.assertEqual(self.plank.measurement_type, et.DURATION)
        self.assertEqual(self.pushup.measurement_type, et.BODYWEIGHT_REPS)
        self.assertEqual(self.bench.measurement_type, et.WEIGHT_REPS)

        self.plank_log.refresh_from_db()
        self.assertIsNone(self.plank_log.weight_kg, 'stray weight cleared')
        self.assertEqual(self.plank_log.duration_seconds, 60, 'duration kept')

        self.push_log.refresh_from_db()
        self.assertIsNone(self.push_log.weight_kg, 'bogus weight cleared')
        self.assertEqual(self.push_log.reps_completed, 15, 'reps kept')

    def test_version_is_bumped_to_latest(self):
        call_command('upgrade_data', verbosity=0)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.data_version, data_upgrades.LATEST_VERSION)

    # -- non-destructiveness (the actual requirement) -----------------------

    def test_genuine_data_is_preserved(self):
        call_command('upgrade_data', verbosity=0)
        self.bench_log.refresh_from_db()
        self.assertEqual(self.bench_log.weight_kg, 60.0)
        self.assertEqual(self.bench_log.reps_completed, 10)
        self.assertTrue(self.bench_log.is_completed)

    def test_no_set_logs_are_lost_or_recreated(self):
        call_command('upgrade_data', verbosity=0)
        self.assertEqual(set(SetLog.objects.values_list('id', flat=True)),
                         self.log_ids)

    def test_sessions_and_completion_markers_survive(self):
        call_command('upgrade_data', verbosity=0)
        self.assertTrue(WorkoutSession.objects.filter(pk=self.session.pk).exists())
        self.day.refresh_from_db()
        self.assertEqual(self.day.last_completed_at, self.completed_marker)

    def test_exercise_rows_are_updated_in_place_not_replaced(self):
        original_ids = set(Exercise.objects.filter(day=self.day)
                           .values_list('id', flat=True))
        call_command('upgrade_data', verbosity=0)
        self.assertEqual(
            set(Exercise.objects.filter(day=self.day).values_list('id', flat=True)),
            original_ids,
            'exercises must be edited in place so SetLog FKs stay intact')

    # -- idempotency and safety --------------------------------------------

    def test_dry_run_changes_nothing(self):
        call_command('upgrade_data', '--dry-run', verbosity=0)
        self.plank.refresh_from_db()
        self.plan.refresh_from_db()
        self.assertEqual(self.plank.measurement_type, 'weight_reps')
        self.assertEqual(self.plan.data_version, 0)

    def test_second_run_is_a_noop(self):
        call_command('upgrade_data', verbosity=0)
        call_command('upgrade_data', verbosity=0)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.data_version, data_upgrades.LATEST_VERSION)
        self.assertEqual(SetLog.objects.count(), 3)

    def test_force_rerun_is_idempotent(self):
        call_command('upgrade_data', verbosity=0)
        call_command('upgrade_data', '--force', verbosity=0)
        self.plank_log.refresh_from_db()
        self.bench_log.refresh_from_db()
        self.assertEqual(self.plank_log.duration_seconds, 60)
        self.assertEqual(self.bench_log.weight_kg, 60.0)

    def test_single_plan_targeting(self):
        other = WorkoutPlan.objects.create(user=self.user, title='Untouched')
        other_day = WorkoutDay.objects.create(plan=other, day_number=1,
                                              week_number=1, name='D')
        other_ex = Exercise.objects.create(day=other_day, name='Plank', sets=1,
                                           reps='30 sec',
                                           measurement_type='weight_reps')
        call_command('upgrade_data', '--plan', str(self.plan.id), verbosity=0)

        other_ex.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(other_ex.measurement_type, 'weight_reps',
                         'other plans must be untouched')
        self.assertEqual(other.data_version, 0)

    def test_upgrade_plan_returns_counts(self):
        steps, rows = data_upgrades.upgrade_plan(self.plan)
        self.assertEqual(steps, data_upgrades.LATEST_VERSION)
        self.assertGreater(rows, 0)

    def test_already_current_plan_is_skipped(self):
        self.plan.data_version = data_upgrades.LATEST_VERSION
        self.plan.save()
        steps, rows = data_upgrades.upgrade_plan(self.plan)
        self.assertEqual(steps, 0)
        self.assertEqual(rows, 0)


class UpgradeRegistryTests(TestCase):
    """Guards the registry convention itself."""

    def test_steps_are_contiguous_from_one(self):
        keys = sorted(data_upgrades.UPGRADES)
        self.assertEqual(keys, list(range(1, len(keys) + 1)),
                         'upgrade steps must be contiguous and start at 1')

    def test_latest_version_matches_the_registry(self):
        self.assertEqual(data_upgrades.LATEST_VERSION,
                         max(data_upgrades.UPGRADES))

    def test_every_step_has_a_label_and_a_callable(self):
        for version, entry in data_upgrades.UPGRADES.items():
            label, func = entry
            self.assertIsInstance(label, str, msg='step %s' % version)
            self.assertTrue(callable(func), msg='step %s' % version)

    def test_upgrade_is_safe_on_a_plan_with_no_days(self):
        user = User.objects.create_user(email='empty@test.local', password='x')
        UserProfile.objects.create(user=user, preferred_unit='lb')
        plan = WorkoutPlan.objects.create(user=user, title='Empty')
        steps, rows = data_upgrades.upgrade_plan(plan)
        self.assertEqual(rows, 0)
        plan.refresh_from_db()
        self.assertEqual(plan.data_version, data_upgrades.LATEST_VERSION)
