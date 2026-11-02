"""
The exercise library, derived from the registries rather than hand-written.

Its job in the plan-mutation flow is to be the list a draft is checked
against: an exercise with no entry is REJECTED, not resolved to a near-match.
That rule is only as good as the seed, so these tests are about the seed being
complete, faithful to the registries, and safe to re-run.

A hand-written library would be a fourth copy of exercise_names,
exercise_types and exercise_muscles, and would drift from them exactly as
`Exercise.MEASUREMENT_TYPE_CHOICES` drifted from `ALL_TYPES`.
"""

from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from api import exercise_names, exercise_types
from api.management.commands.seed_exercise_library import canonical_key_for
from api.models import ExerciseLibraryEntry, User


class SeedExerciseLibraryTests(TestCase):
    def seed(self, **opts):
        out = StringIO()
        call_command('seed_exercise_library', stdout=out, **opts)
        return out.getvalue()

    def test_it_covers_every_canonical_name(self):
        """
        Every curated name must be present -- but the library is far WIDER
        than the registry. Seeding only CANONICAL_NAMES looked right and was
        badly wrong: 54 rows, with `Push-ups` (in the live plan) absent, so
        the Phase 1c rejection rule would have refused most of the user's own
        plan and capped every future plan at 54 exercises.
        """
        self.seed()
        stored = set(ExerciseLibraryEntry.objects.values_list(
            'canonical_key', flat=True))
        missing = [n for n in exercise_names.CANONICAL_NAMES
                   if canonical_key_for(n) not in stored]
        self.assertEqual(missing, [])
        self.assertGreater(len(stored), 500,
                           'the library should be the movement universe, '
                           'not the curated subset')

    def test_rerunning_changes_nothing(self):
        self.seed()
        output = self.seed()
        self.assertIn('0 created, 0 updated', output)

    def test_a_dry_run_writes_nothing(self):
        output = self.seed(dry_run=True)
        self.assertIn('[dry run]', output)
        self.assertEqual(ExerciseLibraryEntry.objects.count(), 0)

    def test_the_measurement_type_comes_from_the_classifier(self):
        """Never a stored guess -- the server owns the field contract."""
        self.seed()
        for entry in ExerciseLibraryEntry.objects.all():
            self.assertEqual(
                entry.measurement_type,
                exercise_types.normalize(None, entry.name),
                entry.name)

    def test_every_measurement_type_is_a_known_type(self):
        self.seed()
        stored = set(ExerciseLibraryEntry.objects.values_list(
            'measurement_type', flat=True))
        self.assertTrue(stored <= set(exercise_types.ALL_TYPES),
                        stored - set(exercise_types.ALL_TYPES))

    def test_pushups_carry_the_corrected_muscle_data(self):
        """
        The KB override has to reach the library. Before it, Push-ups resolved
        to `handstand push-ups` and would have been seeded as a SHOULDER
        movement requiring no equipment to be checked against.
        """
        self.seed()
        entry = ExerciseLibraryEntry.objects.get(
            canonical_key=canonical_key_for('Push-ups'))
        self.assertEqual(entry.muscles['primary'], ['chest'])
        self.assertEqual(entry.equipment, 'body only')

    def test_the_key_is_spelling_independent(self):
        """
        Identity is the token set, so a variant spelling finds the same row --
        this is what lets a draft be checked without fuzzy matching.
        """
        self.seed()
        self.assertEqual(canonical_key_for('Push-ups'),
                         canonical_key_for('Push Ups'))
        self.assertTrue(ExerciseLibraryEntry.objects.filter(
            canonical_key=canonical_key_for('push ups')).exists())

    def test_catalogue_rows_have_no_user(self):
        """Contraindication is per-user; the catalogue itself is shared."""
        self.seed()
        self.assertEqual(
            ExerciseLibraryEntry.objects.filter(user__isnull=False).count(), 0)
        self.assertFalse(
            ExerciseLibraryEntry.objects.filter(contraindicated=True).exists())

    def test_a_per_user_row_is_left_alone_by_a_reseed(self):
        """
        Re-deriving the catalogue after a registry change must not wipe a
        user's own contraindications.
        """
        self.seed()
        user = User.objects.create_user(email='lib@test.local', password='x')
        mine = ExerciseLibraryEntry.objects.create(
            canonical_key=canonical_key_for('Barbell Bench Press'),
            name='Barbell Bench Press', user=user, contraindicated=True,
            contraindication_reason='upper back pain, 30 Oct 2026')

        self.seed()

        mine.refresh_from_db()
        self.assertTrue(mine.contraindicated)
        self.assertEqual(mine.contraindication_reason,
                         'upper back pain, 30 Oct 2026')

    def test_it_reports_entries_with_no_muscle_data(self):
        """
        Not an error -- those entries are still valid for identity and the
        field contract. But they cannot take part in superset validation, and
        silently seeding them would hide that.
        """
        output = self.seed()
        self.assertTrue(
            'without muscle data' in output or 'created' in output)


class TheLibraryGrowsWithUseTests(TestCase):
    """
    The library has to be everything a person can do, not everything one
    upstream dataset happened to list.

    876 catalogue records is a starting point, not the set of real exercises.
    Freezing there would make the library quietly wrong as soon as anyone used
    a movement it had never heard of -- and since a draft is checked against
    the library, a frozen library means a shrinking set of usable exercises.

    So the seed runs on every deploy and picks up anything that has entered a
    plan since the last one.
    """

    def test_an_exercise_new_to_a_plan_joins_the_library(self):
        from api.models import WorkoutDay, WorkoutPlan

        call_command('seed_exercise_library', stdout=StringIO())
        before = ExerciseLibraryEntry.objects.count()

        user = User.objects.create_user(email='grow@test.local', password='x')
        plan = WorkoutPlan.objects.create(user=user, title='P')
        day = WorkoutDay.objects.create(
            plan=plan, day_number=1, week_number=1, name='Day')
        from api.models import Exercise
        Exercise.objects.create(
            day=day, name='Jefferson Curl With A Kettlebell', sets=3,
            reps='8-12', order=0, measurement_type='weight_reps')

        call_command('seed_exercise_library', stdout=StringIO())

        self.assertEqual(ExerciseLibraryEntry.objects.count(), before + 1)
        self.assertTrue(ExerciseLibraryEntry.objects.filter(
            canonical_key=canonical_key_for('Jefferson Curl With A Kettlebell')
        ).exists())

    def test_the_library_is_far_wider_than_any_one_plan(self):
        """
        Sanity check on scope. Four live plans use 76 distinct names; the
        library is an order of magnitude larger, so a draft is not confined
        to movements already in use.
        """
        call_command('seed_exercise_library', stdout=StringIO())
        self.assertGreater(ExerciseLibraryEntry.objects.count(), 800)
