"""
The exercise-name canonicalisation backfill (upgrade step 5).

This step rewrites a user's real training history, and the local database is
empty, so it cannot be observed against real data before it reaches production.
Synthetic fixtures standing in for the actual production name collisions are
therefore the only way to verify it, and the bar is correspondingly high:

* nothing is lost -- every SetLog row, weight, rep count and session survives
* nothing unrelated is touched
* running it twice changes nothing the second time
* only movements in the explicit registry are merged

The step is written but NOT scheduled to run: `manage.py upgrade_data` is a
deliberate act, and `--dry-run` reports what it would change first.
"""

import datetime as dt

from django.test import TestCase
from django.utils import timezone

from api import data_upgrades, exercise_names as en, exercise_types as et
from api.models import (
    User, UserProfile, WorkoutPlan, WorkoutDay, Exercise, WorkoutSession,
    SetLog,
)


class CanonicalBackfillTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='bf@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        self.plan = WorkoutPlan.objects.create(
            user=self.user, title='P', is_active=True, current_week=1)
        self.day = WorkoutDay.objects.create(
            plan=self.plan, day_number=1, week_number=1, name='Push')

        # The real production shape: the plan names it one way, the history is
        # logged under three other spellings.
        self.exercise = Exercise.objects.create(
            day=self.day, name='Dumbbell Incline Press', sets=3, reps='8-12',
            measurement_type=et.WEIGHT_REPS)
        self.untouched = Exercise.objects.create(
            day=self.day, name='Zercher Squat', sets=3, reps='5',
            measurement_type=et.WEIGHT_REPS, order=1)

        self.logged = {
            'Dumbbell Incline Press': 3,
            'Incline Dumbbell Press': 6,
            'Dumbbell Triceps Extension (Overhead)': 3,
            'Overhead Dumbbell Extension (Seated)': 3,
            'Zercher Squat': 3,
        }
        self.session = WorkoutSession.objects.create(
            user=self.user, exercise_day=self.day, day_name='Push',
            is_completed=True)
        WorkoutSession.objects.filter(pk=self.session.pk).update(
            started_at=timezone.now() - dt.timedelta(days=3))
        number = 0
        for name, count in self.logged.items():
            for _ in range(count):
                number += 1
                SetLog.objects.create(
                    workout_session=self.session, exercise_name=name,
                    set_number=number, weight_kg=40, reps_completed=10,
                    is_completed=True)
        self.total_rows = sum(self.logged.values())

    def run_step(self):
        lines = []
        changed = data_upgrades._step_5_canonicalise_exercise_names(
            self.plan, lines.append)
        return changed, lines

    def names(self):
        return dict(
            (name, SetLog.objects.filter(
                workout_session__user=self.user, exercise_name=name).count())
            for name in set(
                SetLog.objects.filter(workout_session__user=self.user)
                .values_list('exercise_name', flat=True)))

    # -- what it changes -----------------------------------------------------

    def test_the_exercise_row_is_renamed_to_canonical(self):
        self.run_step()
        self.exercise.refresh_from_db()
        self.assertEqual(self.exercise.name, 'Incline Dumbbell Press')

    def test_logged_sets_are_reunited_under_one_name(self):
        self.run_step()
        names = self.names()
        self.assertEqual(names.get('Incline Dumbbell Press'), 9)
        self.assertNotIn('Dumbbell Incline Press', names)

    def test_the_triceps_extension_family_collapses(self):
        self.run_step()
        names = self.names()
        self.assertEqual(names.get('Overhead Dumbbell Triceps Extension'), 6)
        self.assertNotIn('Dumbbell Triceps Extension (Overhead)', names)
        self.assertNotIn('Overhead Dumbbell Extension (Seated)', names)

    def test_it_reports_what_it_changed(self):
        changed, lines = self.run_step()
        self.assertGreater(changed, 0)
        self.assertTrue(any('Incline Dumbbell Press' in line for line in lines))

    # -- what it must NOT change --------------------------------------------

    def test_no_set_log_rows_are_lost_or_created(self):
        self.run_step()
        self.assertEqual(
            SetLog.objects.filter(workout_session__user=self.user).count(),
            self.total_rows)

    def test_logged_values_are_untouched(self):
        self.run_step()
        for log in SetLog.objects.filter(workout_session__user=self.user):
            self.assertEqual(log.weight_kg, 40)
            self.assertEqual(log.reps_completed, 10)
            self.assertTrue(log.is_completed)

    def test_an_unregistered_exercise_is_left_alone(self):
        """Never guesses: no registry entry means no rewrite."""
        self.run_step()
        self.untouched.refresh_from_db()
        self.assertEqual(self.untouched.name, 'Zercher Squat')
        self.assertEqual(self.names().get('Zercher Squat'), 3)

    def test_sessions_survive(self):
        self.run_step()
        self.session.refresh_from_db()
        self.assertTrue(self.session.is_completed)
        self.assertEqual(
            WorkoutSession.objects.filter(user=self.user).count(), 1)

    def test_exercise_rows_are_updated_in_place_not_replaced(self):
        original = self.exercise.pk
        self.run_step()
        self.exercise.refresh_from_db()
        self.assertEqual(self.exercise.pk, original)
        self.assertEqual(Exercise.objects.filter(day=self.day).count(), 2)

    def test_another_users_history_is_untouched(self):
        other = User.objects.create_user(email='bf2@test.local', password='x')
        session = WorkoutSession.objects.create(
            user=other, day_name='Theirs', is_completed=True)
        SetLog.objects.create(
            workout_session=session, exercise_name='Dumbbell Incline Press',
            set_number=1, weight_kg=50, reps_completed=8, is_completed=True)
        self.run_step()
        self.assertEqual(
            SetLog.objects.filter(workout_session__user=other,
                                  exercise_name='Dumbbell Incline Press').count(),
            1)

    # -- idempotence ---------------------------------------------------------

    def test_a_second_run_changes_nothing(self):
        self.run_step()
        before = self.names()
        changed, _ = self.run_step()
        self.assertEqual(changed, 0)
        self.assertEqual(self.names(), before)

    def test_running_it_on_already_canonical_data_is_a_noop(self):
        Exercise.objects.filter(pk=self.exercise.pk).update(
            name='Incline Dumbbell Press')
        SetLog.objects.filter(workout_session__user=self.user).update(
            exercise_name='Incline Dumbbell Press')
        changed, _ = self.run_step()
        self.assertEqual(changed, 0)

    # -- integration with the upgrade runner ---------------------------------

    def test_the_step_is_registered_as_version_five(self):
        label, func = data_upgrades.UPGRADES[5]
        self.assertEqual(func, data_upgrades._step_5_canonicalise_exercise_names)
        self.assertIn('canonical', label)
        self.assertEqual(data_upgrades.LATEST_VERSION, 5)

    def test_upgrade_plan_applies_it_and_bumps_the_version(self):
        self.assertEqual(self.plan.data_version, 0)
        data_upgrades.upgrade_plan(self.plan)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.data_version, data_upgrades.LATEST_VERSION)
        self.exercise.refresh_from_db()
        self.assertEqual(self.exercise.name, 'Incline Dumbbell Press')

    def test_a_plan_already_at_the_latest_version_is_skipped(self):
        WorkoutPlan.objects.filter(pk=self.plan.pk).update(
            data_version=data_upgrades.LATEST_VERSION)
        self.plan.refresh_from_db()
        steps, _ = data_upgrades.upgrade_plan(self.plan)
        self.assertEqual(steps, 0)
        self.exercise.refresh_from_db()
        self.assertEqual(self.exercise.name, 'Dumbbell Incline Press')

    def test_force_rerun_is_idempotent(self):
        data_upgrades.upgrade_plan(self.plan, force=True)
        before = self.names()
        data_upgrades.upgrade_plan(self.plan, force=True)
        self.assertEqual(self.names(), before)

    def test_it_survives_a_plan_with_no_days(self):
        empty = WorkoutPlan.objects.create(
            user=self.user, title='Empty', current_week=1)
        data_upgrades._step_5_canonicalise_exercise_names(empty, lambda m: None)

    def test_orphaned_set_logs_are_still_reunited(self):
        """
        `exercise_name` is denormalised and survives plan deletion, so rows
        with a null `exercise` FK are not reachable by walking the plan's
        exercises. The step is scoped by user for exactly this reason.
        """
        self.assertTrue(
            SetLog.objects.filter(workout_session__user=self.user,
                                  exercise__isnull=True).exists())
        self.run_step()
        self.assertEqual(self.names().get('Incline Dumbbell Press'), 9)


class AdviceSeesTheReunitedHistoryTests(TestCase):
    """The point of the backfill, stated as a behaviour."""

    def test_progression_advice_appears_after_the_backfill(self):
        from api import views

        user = User.objects.create_user(email='bf3@test.local', password='x')
        UserProfile.objects.create(user=user, preferred_unit='lb')
        plan = WorkoutPlan.objects.create(user=user, title='P', is_active=True,
                                          current_week=1)
        day = WorkoutDay.objects.create(plan=plan, day_number=1,
                                        week_number=1, name='Pull')
        Exercise.objects.create(
            day=day, name='T-Bar Row (Chest Supported)', sets=3, reps='8-12',
            measurement_type=et.WEIGHT_REPS)

        # Nine sets logged months earlier under the other spelling -- the
        # exact production case.
        for days_ago in (30, 20, 10):
            session = WorkoutSession.objects.create(
                user=user, exercise_day=day, day_name='Pull', is_completed=True)
            WorkoutSession.objects.filter(pk=session.pk).update(
                started_at=timezone.now() - dt.timedelta(days=days_ago))
            for number in (1, 2, 3):
                SetLog.objects.create(
                    workout_session=session,
                    exercise_name='Chest-Supported T-Bar Row',
                    set_number=number, weight_kg=45, reps_completed=12,
                    is_completed=True)

        data_upgrades._step_5_canonicalise_exercise_names(plan, lambda m: None)

        self.assertEqual(
            en.canonical('T-Bar Row (Chest Supported)'),
            'Chest-Supported T-Bar Row')
        logs, _ = views._exercise_history(
            user, 'Chest-Supported T-Bar Row', et.WEIGHT_REPS)
        self.assertEqual(len(logs), 9)
        advice = views._build_exercise_advice(
            user, 'Chest-Supported T-Bar Row', et.WEIGHT_REPS, '8-12', 'lb')
        self.assertIsNotNone(advice['coaching_note'])
        self.assertIn('INCREASE WEIGHT', advice['coaching_note'])
