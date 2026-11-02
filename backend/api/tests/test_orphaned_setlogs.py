"""
Upgrade step 10: reattaching logged sets orphaned by a plan rebuild.

Where the orphans came from
---------------------------
Until `views._apply_plan_diff` landed, every plan edit deleted the plan's days
and rebuilt them. `SetLog.exercise` is SET_NULL and
`WorkoutSession.exercise_day` is SET_NULL, so each edit detached every logged
set and every session in the plan. Step 9 leaves orphans alone by design, so
they accumulated.

The fix in views.py stops new ones. Step 10 repairs the existing ones.

These tests are mostly about the REFUSALS. A wrong reattachment is worse than
no reattachment: step 9 would afterwards rewrite `exercise_name` to match
whatever exercise step 10 guessed, destroying the evidence that it guessed.
"""

from django.utils import timezone
from django.test import TestCase

from api.data_upgrades import _step_10_reattach_orphaned_setlogs
from api.models import (
    User, WorkoutPlan, WorkoutDay, Exercise, WorkoutSession, SetLog,
)


def _silent(_msg):
    pass


class OrphanedSetLogTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='orphan@test.local', password='x')
        self.plan = WorkoutPlan.objects.create(
            user=self.user, title='P', is_active=True)

    # -- helpers ----------------------------------------------------------

    def day(self, name, week=1, number=1):
        return WorkoutDay.objects.create(
            plan=self.plan, day_number=number, week_number=week, name=name)

    def exercise(self, day, name, order=0):
        return Exercise.objects.create(
            day=day, name=name, sets=3, reps='8-12', order=order,
            measurement_type='weight_reps')

    def session(self, day_name, day=None):
        return WorkoutSession.objects.create(
            user=self.user, exercise_day=day, day_name=day_name,
            is_completed=True, completed_at=timezone.now())

    def orphan(self, session, name, set_number=1):
        return SetLog.objects.create(
            workout_session=session, exercise=None, exercise_name=name,
            set_number=set_number, reps_completed=10, weight_kg=40.0,
            is_completed=True)

    # -- reattaching ------------------------------------------------------

    def test_it_reattaches_when_the_session_kept_its_day(self):
        day = self.day('Upper Push')
        bench = self.exercise(day, 'Barbell Bench Press')
        log = self.orphan(self.session('Upper Push', day), 'Barbell Bench Press')

        self.assertEqual(_step_10_reattach_orphaned_setlogs(self.plan, _silent), 1)
        log.refresh_from_db()
        self.assertEqual(log.exercise_id, bench.id)

    def test_it_reattaches_from_the_denormalised_day_name(self):
        """
        The common case for existing damage: the rebuild nulled the session's
        day too, so only `WorkoutSession.day_name` survives.
        """
        day = self.day('Upper Push')
        bench = self.exercise(day, 'Barbell Bench Press')
        log = self.orphan(self.session('Upper Push', day=None), 'Barbell Bench Press')

        self.assertEqual(_step_10_reattach_orphaned_setlogs(self.plan, _silent), 1)
        log.refresh_from_db()
        self.assertEqual(log.exercise_id, bench.id)

    def test_it_matches_on_canonical_identity_not_spelling(self):
        day = self.day('Upper Push')
        pushdown = self.exercise(day, 'Cable Tricep Pushdowns')
        log = self.orphan(self.session('Upper Push', day), 'Cable Tricep Pushdown')

        self.assertEqual(_step_10_reattach_orphaned_setlogs(self.plan, _silent), 1)
        log.refresh_from_db()
        self.assertEqual(log.exercise_id, pushdown.id)

    # -- refusals ---------------------------------------------------------

    def test_it_refuses_when_the_day_name_spans_several_weeks(self):
        """
        'Upper Push' exists in every week of an 8-week block. With the session's
        day FK gone there is no way to know which week was trained, and
        attaching to the wrong week's row is a guess dressed as data.
        """
        for week in (1, 2):
            self.exercise(self.day('Upper Push', week=week),
                          'Barbell Bench Press')
        log = self.orphan(self.session('Upper Push', day=None), 'Barbell Bench Press')

        self.assertEqual(_step_10_reattach_orphaned_setlogs(self.plan, _silent), 0)
        log.refresh_from_db()
        self.assertIsNone(log.exercise_id)

    def test_it_refuses_a_duplicated_exercise_on_one_day(self):
        """The live plan lists Barbell Bench Press twice on Upper Push."""
        day = self.day('Upper Push')
        self.exercise(day, 'Barbell Bench Press', order=0)
        self.exercise(day, 'Barbell Bench Press', order=3)
        log = self.orphan(self.session('Upper Push', day), 'Barbell Bench Press')

        self.assertEqual(_step_10_reattach_orphaned_setlogs(self.plan, _silent), 0)
        log.refresh_from_db()
        self.assertIsNone(log.exercise_id)

    def test_it_refuses_when_the_set_number_is_already_taken(self):
        """`unique_together = (workout_session, exercise, set_number)`."""
        day = self.day('Upper Push')
        bench = self.exercise(day, 'Barbell Bench Press')
        session = self.session('Upper Push', day)
        SetLog.objects.create(
            workout_session=session, exercise=bench,
            exercise_name='Barbell Bench Press', set_number=1,
            reps_completed=8, weight_kg=50.0, is_completed=True)
        log = self.orphan(session, 'Barbell Bench Press', set_number=1)

        self.assertEqual(_step_10_reattach_orphaned_setlogs(self.plan, _silent), 0)
        log.refresh_from_db()
        self.assertIsNone(log.exercise_id)

    def test_it_ignores_another_plans_session(self):
        other = WorkoutPlan.objects.create(user=self.user, title='Other')
        other_day = WorkoutDay.objects.create(
            plan=other, day_number=1, week_number=1, name='Upper Push')
        day = self.day('Upper Push')
        self.exercise(day, 'Barbell Bench Press')
        log = self.orphan(self.session('Upper Push', other_day), 'Barbell Bench Press')

        self.assertEqual(_step_10_reattach_orphaned_setlogs(self.plan, _silent), 0)
        log.refresh_from_db()
        self.assertIsNone(log.exercise_id)

    def test_it_leaves_logs_that_still_have_an_exercise(self):
        """Idempotence: a healthy log is never considered."""
        day = self.day('Upper Push')
        bench = self.exercise(day, 'Barbell Bench Press')
        session = self.session('Upper Push', day)
        SetLog.objects.create(
            workout_session=session, exercise=bench,
            exercise_name='Barbell Bench Press', set_number=1,
            reps_completed=8, weight_kg=50.0, is_completed=True)

        self.assertEqual(_step_10_reattach_orphaned_setlogs(self.plan, _silent), 0)

    def test_running_it_twice_changes_nothing_the_second_time(self):
        day = self.day('Upper Push')
        self.exercise(day, 'Barbell Bench Press')
        self.orphan(self.session('Upper Push', day), 'Barbell Bench Press')

        self.assertEqual(_step_10_reattach_orphaned_setlogs(self.plan, _silent), 1)
        self.assertEqual(_step_10_reattach_orphaned_setlogs(self.plan, _silent), 0)
