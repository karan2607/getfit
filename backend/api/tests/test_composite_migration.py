"""
Migration 0045: `Exercise.superset_group` -> `Composite` rows.

This mutates existing rows including live user plans, and the local database is
empty, so synthetic fixtures are the only way to observe it at all.

What is tested, and how — stated plainly
----------------------------------------
`rest_from_members`, the migration's one judgement call, is imported and tested
DIRECTLY. The rest of the migration cannot be driven from an ordinary test:
0046 drops `superset_group`, so the test database has no such column to write
into, and rebuilding a historical schema would be testing Django's state
machinery rather than this logic.

The second class therefore asserts the INVARIANTS the conversion had to
preserve, against the container shape the migration produces. Those are the
regressions a conversion could plausibly introduce — lost logs, merged days,
rest changing under the user, ungrouping deleting training.

The migration's behaviour on real rows is reported as `upgrade_data`-style
`--dry-run` counts before it is allowed to deploy.
"""

import importlib

from django.test import SimpleTestCase, TestCase

from api.models import (
    User, UserProfile, WorkoutPlan, WorkoutDay, Exercise, Composite,
    WorkoutSession, SetLog,
)

_migration = importlib.import_module(
    'api.migrations.0045_migrate_superset_groups')


class RestSelectionTests(SimpleTestCase):
    """
    The one judgement call in migration 0045.

    `rest_from_members` decides each converted group's rest. It has to
    reproduce what the card rendered BEFORE the container existed — the last
    member's value — because the migration's job is to preserve what the user
    currently sees, not to improve it. Max or first would silently change
    every existing plan.
    """

    def test_the_last_set_value_wins(self):
        self.assertEqual(_migration.rest_from_members([60, 90]), 90)
        self.assertEqual(_migration.rest_from_members([60, 90, 120]), 120)

    def test_a_trailing_none_falls_back_to_the_last_set_value(self):
        self.assertEqual(_migration.rest_from_members([60, None]), 60)
        self.assertEqual(_migration.rest_from_members([60, None, None]), 60)

    def test_no_values_means_no_rest(self):
        self.assertIsNone(_migration.rest_from_members([]))
        self.assertIsNone(_migration.rest_from_members([None, None]))

    def test_zero_is_a_real_value_not_a_missing_one(self):
        """`0` is falsy; a truthiness check here would have skipped it."""
        self.assertEqual(_migration.rest_from_members([90, 0]), 0)

    def test_it_accepts_a_generator(self):
        """The migration passes a genexp, which cannot be reversed in place."""
        self.assertEqual(_migration.rest_from_members(x for x in [30, 45]), 45)


class CompositeInvariantTests(TestCase):
    """
    Every property the old `superset_group` integer had, asserted against the
    container that replaced it.
    """

    def setUp(self):
        self.user = User.objects.create_user(email='comp@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        self.plan = WorkoutPlan.objects.create(
            user=self.user, title='P', current_week=1, is_active=True)
        self.day = WorkoutDay.objects.create(
            plan=self.plan, day_number=1, week_number=1, name='Pull')
        self.other_day = WorkoutDay.objects.create(
            plan=self.plan, day_number=2, week_number=1, name='Push')

        self.a = Exercise.objects.create(
            day=self.day, name='Leg Extension', sets=3, reps='12', order=0,
            rest_seconds=60)
        self.b = Exercise.objects.create(
            day=self.day, name='Leg Curl', sets=3, reps='12', order=1,
            rest_seconds=90)
        self.standalone = Exercise.objects.create(
            day=self.day, name='Deadlift', sets=3, reps='5', order=2,
            rest_seconds=180)

        self.session = WorkoutSession.objects.create(
            user=self.user, exercise_day=self.day, day_name='Pull',
            is_completed=True)
        self.log = SetLog.objects.create(
            workout_session=self.session, exercise=self.a,
            exercise_name='Leg Extension', set_number=1,
            weight_kg=40.0, reps_completed=12, is_completed=True)

    def group(self, members):
        """Build the container shape the migration produces."""
        composite = Composite.objects.create(
            day=members[0].day, order=0,
            rest_seconds=_migration.rest_from_members(
                m.rest_seconds for m in members))
        Exercise.objects.filter(pk__in=[m.pk for m in members]).update(
            composite=composite)
        return composite

    def test_rest_matches_what_the_card_rendered_before(self):
        self.assertEqual(self.group([self.a, self.b]).rest_seconds, 90)

    def test_members_keep_their_own_identity_and_contract(self):
        """
        The property that made the merged "A + B" exercise so damaging. Each
        component is still its own Exercise with its own sets, reps and
        measurement type — so each still logs its own sets.
        """
        self.group([self.a, self.b])
        self.a.refresh_from_db()
        self.b.refresh_from_db()
        self.assertEqual(self.a.composite_id, self.b.composite_id)
        self.assertEqual(
            list(self.a.composite.exercises.order_by('order')
                 .values_list('name', flat=True)),
            ['Leg Extension', 'Leg Curl'])

    def test_logged_sets_are_untouched(self):
        """Grouping moves the structure, never the training data."""
        self.group([self.a, self.b])
        after = SetLog.objects.get(pk=self.log.pk)
        self.assertEqual(after.weight_kg, 40.0)
        self.assertEqual(after.reps_completed, 12)
        self.assertEqual(after.exercise_id, self.a.pk)
        self.assertEqual(SetLog.objects.count(), 1)

    def test_a_standalone_exercise_is_not_grouped(self):
        self.group([self.a, self.b])
        self.standalone.refresh_from_db()
        self.assertIsNone(self.standalone.composite_id)

    def test_two_days_do_not_merge(self):
        """
        The integer was day-local, so two days both using `1` was normal. A
        conversion keyed on the integer alone would have merged them into one
        container spanning two days.
        """
        c = Exercise.objects.create(
            day=self.other_day, name='Barbell Bench Press', sets=3, reps='8',
            order=0)
        d = Exercise.objects.create(
            day=self.other_day, name='Barbell Row', sets=3, reps='8', order=1)
        first = self.group([self.a, self.b])
        second = self.group([c, d])
        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(first.day_id, self.day.pk)
        self.assertEqual(second.day_id, self.other_day.pk)

    def test_ungrouping_never_deletes_training(self):
        """
        SET_NULL, not CASCADE. If deleting a composite cascaded, an ordinary UI
        action would destroy the exercises and their history.
        """
        self.group([self.a, self.b]).delete()
        self.a.refresh_from_db()
        self.b.refresh_from_db()
        self.assertIsNone(self.a.composite_id)
        self.assertIsNone(self.b.composite_id)
        self.assertEqual(Exercise.objects.filter(day=self.day).count(), 3)
        self.assertEqual(SetLog.objects.count(), 1)

    def test_deleting_the_day_removes_its_composites(self):
        """CASCADE here is right: a composite has no meaning without its day."""
        self.group([self.a, self.b])
        self.day.delete()
        self.assertEqual(Composite.objects.count(), 0)

    def test_rest_is_none_when_no_member_had_any(self):
        a = Exercise.objects.create(
            day=self.other_day, name='Crunches', sets=3, reps='15', order=0)
        b = Exercise.objects.create(
            day=self.other_day, name='Plank', sets=3, reps='45 sec', order=1)
        self.assertIsNone(self.group([a, b]).rest_seconds)
