"""
Canonical exercise names.

The defect: `_exercise_history` matched logged sets on `exercise_name__iexact`,
so two spellings of one movement split a user's history and the coaching advice
saw only part of it. Production had 190 distinct names across `Exercise` and
`SetLog` for roughly 125 real movements -- 88 sets of "Incline Dumbbell Press"
invisible to a plan saying "Dumbbell Incline Press", and a week-5
"T-Bar Row (Chest Supported)" reporting no history despite nine logged sets.

**The negative tests in RefusedMergeTests are the most important ones here.**
Canonicalisation rewrites stored history, so a wrong merge is data corruption.
Every pair in that class is one the F1 name scorer rates 0.80-0.87 -- meaning a
threshold-based implementation would have merged them -- and each is a different
exercise.
"""

from django.test import SimpleTestCase, TestCase

from api import exercise_names as en


class NormalisationTests(SimpleTestCase):
    def test_case_and_whitespace_are_ignored(self):
        self.assertTrue(en.same_movement('leg press', '  Leg   Press '))

    def test_hyphens_split_like_spaces(self):
        """"Single-Arm" and "Single Arm" are the same exercise."""
        self.assertTrue(en.same_movement(
            'Single-Arm Dumbbell Row', 'Single Arm Dumbbell Row'))
        self.assertTrue(en.same_movement(
            'Close-Grip Bench Press', 'Close Grip Bench Press'))
        self.assertTrue(en.same_movement(
            'Chest-Supported T-Bar Row', 'Chest Supported T-Bar Row'))

    def test_parentheses_are_ignored(self):
        self.assertTrue(en.same_movement(
            'Chest-Supported T-Bar Row', 'T-Bar Row (Chest Supported)'))

    def test_word_order_does_not_matter(self):
        for left, right in (
            ('Incline Barbell Press', 'Barbell Incline Press'),
            ('Incline Dumbbell Press', 'Dumbbell Incline Press'),
            ('Barbell Overhead Press', 'Overhead Barbell Press'),
            ('Barbell Front Squat', 'Front Barbell Squats'),
            ('Dumbbell Bent-Over Row', 'Bent-Over Dumbbell Rows'),
            ('Dumbbell Walking Lunges', 'Walking Lunges (Dumbbell)'),
        ):
            self.assertTrue(en.same_movement(left, right),
                            msg='%s != %s' % (left, right))

    def test_plurals_are_folded(self):
        for left, right in (
            ('Barbell Back Squat', 'Barbell Back Squats'),
            ('Seated Calf Raise', 'Seated Calf Raises'),
            ('Russian Twist', 'Russian Twists'),
            ('Bicycle Crunch', 'Bicycle Crunches'),
            ('Push-up', 'Push-ups'),
            ('Dip', 'Dips'),
        ):
            self.assertTrue(en.same_movement(left, right),
                            msg='%s != %s' % (left, right))

    def test_the_plural_fold_does_not_mangle_double_s_words(self):
        """The stemmer trap: a naive trailing-s strip makes "press" -> "pres"."""
        self.assertIn('press', en.tokens('Bench Press'))
        self.assertIn('press', en.tokens('Bench Presses'))
        self.assertTrue(en.same_movement('Bench Press', 'Bench Presses'))

    def test_the_plural_fold_does_not_mangle_raises(self):
        self.assertIn('raise', en.tokens('Lateral Raises'))

    def test_ampersand_becomes_and(self):
        self.assertEqual(en.normalize('Warmup & Mobility'),
                         'warmup and mobility')

    def test_empty_input_is_handled(self):
        self.assertIsNone(en.canonical(None))
        self.assertEqual(en.canonical(''), '')
        self.assertEqual(en.canonical('   '), '')     # blank collapses
        for value in (None, '', '   '):
            self.assertTrue(en.is_canonical(value))

    def test_same_movement_is_false_for_blanks(self):
        self.assertFalse(en.same_movement('', 'Leg Press'))
        self.assertFalse(en.same_movement(None, None))


class DirectionTests(SimpleTestCase):
    """
    The one place word order carries meaning, and it is live in the data:
    `High-to-Low Cable Crossover` and `Cable Crossover (Low-to-High)` have
    identical token SETS and are different exercises.
    """

    def test_reversed_directions_are_different_movements(self):
        self.assertFalse(en.same_movement(
            'High-to-Low Cable Crossover', 'Low-to-High Cable Crossover'))

    def test_direction_survives_reordering_and_parentheses(self):
        self.assertFalse(en.same_movement(
            'High-to-Low Cable Crossover', 'Cable Crossover (Low-to-High)'))
        self.assertTrue(en.same_movement(
            'High-to-Low Cable Crossover', 'Cable Crossover (High-to-Low)'))

    def test_the_direction_is_one_token(self):
        self.assertIn('high-to-low', en.tokens('High-to-Low Cable Crossover'))

    def test_spaced_direction_phrasing_is_also_captured(self):
        self.assertTrue(en.same_movement(
            'High to Low Cable Crossover', 'High-to-Low Cable Crossover'))


class RefusedMergeTests(SimpleTestCase):
    """
    Pairs that must NEVER merge. Each scores 0.80-0.87 on the F1 name scorer
    used for image matching, so a similarity threshold would have collapsed
    them and corrupted real training history.
    """

    PAIRS = (
        # The bug caught during implementation: "side" was treated as a noise
        # word, which silently collapsed Plank (90 logged rows) into Side
        # Plank (80 rows).
        ('Plank', 'Side Plank'),
        # Different movement under a shared equipment word.
        ('Barbell Bench Press', 'Barbell Overhead Press'),
        ('Machine Chest Press', 'Machine Shoulder Press'),
        ('Dumbbell Bench Press', 'Dumbbell Overhead Press'),
        ('Overhead Dumbbell Extension (Seated)', 'Overhead Dumbbell Press (Seated)'),
        ('Cable Face Pulls', 'Cable Pull-Through'),
        # Different grip on the curl changes which head is loaded.
        ('Dumbbell Bicep Curl', 'Dumbbell Hammer Curl'),
        # Different stance.
        ('Dumbbell Goblet Squat', 'Dumbbell Sumo Squat'),
        ('Barbell Back Squat', 'Barbell Front Squat'),
        # Loaded vs bodyweight is not the same exercise.
        ('Glute Bridges', 'Dumbbell Glute Bridge'),
        # Grip variants are loaded differently; blending them would corrupt
        # the very history this module exists to repair.
        ('Seated Cable Row (Wide Grip)', 'Seated Cable Row (Neutral Grip)'),
        ('Lat Pulldown (Wide Grip)', 'Lat Pulldown (Neutral Grip)'),
        # Different bench angle.
        ('Incline Dumbbell Press', 'Flat Dumbbell Press'),
        ('Barbell Bench Press', 'Incline Barbell Press'),
        # Different movement entirely.
        ('Dumbbell Rows', 'Renegade Row (Dumbbell)'),
        ('Leg Press', 'Hack Squat Machine'),
        ('Push-ups', 'Pull-ups'),
        ('Seated Calf Raise', 'Standing Calf Raises (Machine)'),
        ('Russian Twist', 'Bicycle Crunch'),
        # Caught by the production dry-run: an alias here would have rewritten
        # a beginner plan's bodyweight lunges into the loaded variant.
        ('Walking Lunges', 'Dumbbell Walking Lunges'),
    )

    def test_an_explicit_dumbbell_lunge_still_folds(self):
        """"Walking Lunges (Dumbbell)" says dumbbell, so it is the loaded one."""
        self.assertTrue(en.same_movement(
            'Walking Lunges (Dumbbell)', 'Dumbbell Walking Lunges'))

    def test_none_of_these_merge(self):
        for left, right in self.PAIRS:
            self.assertFalse(
                en.same_movement(left, right),
                msg='WRONGLY MERGED %r with %r -> %r'
                    % (left, right, en.canonical(left)))

    def test_an_unregistered_name_is_returned_unchanged(self):
        """Never guesses: a movement with no registry entry is left alone."""
        for name in ('Jefferson Curl', 'Zercher Squat', 'Nordic Curl',
                     'Some Exercise Nobody Has Heard Of'):
            self.assertEqual(en.canonical(name), name)
            self.assertTrue(en.is_canonical(name))


class CanonicalMergeTests(SimpleTestCase):
    """The real collisions from production, each with its logged row counts."""

    def test_incline_dumbbell_press(self):
        """88 rows vs 3 rows, same exercise."""
        self.assertEqual(en.canonical('Dumbbell Incline Press'),
                         'Incline Dumbbell Press')

    def test_the_t_bar_row_family(self):
        for variant in ('T-Bar Row (Chest Supported)',
                        'T-Bar Rows (Chest Supported)',
                        'Chest Supported T-Bar Row'):
            self.assertEqual(en.canonical(variant), 'Chest-Supported T-Bar Row')

    def test_the_overhead_triceps_extension_family(self):
        """Five spellings in production, one of which omits "triceps"."""
        for variant in ('Dumbbell Triceps Extension (Overhead)',
                        'Dumbbell Overhead Triceps Extension',
                        'Overhead Dumbbell Extension (Seated)',
                        'Dumbbell Overhead Tricep Extension (single dumbbell)'):
            self.assertEqual(en.canonical(variant),
                             'Overhead Dumbbell Triceps Extension')

    def test_the_rope_pushdown_family(self):
        for variant in ('Cable Triceps Pushdown (Rope)',
                        'Rope Triceps Pushdown',
                        'Triceps Pushdown (Rope Attachment)',
                        'Triceps Pushdown (Rope or Bar)'):
            self.assertEqual(en.canonical(variant),
                             'Cable Triceps Pushdown (Rope Attachment)')

    def test_intensity_annotations_do_not_create_a_new_exercise(self):
        """
        Keeping "(Heavy)" separate is exactly what hid loading history from
        the progression logic.
        """
        self.assertEqual(en.canonical('Heavy Leg Press'), 'Leg Press')
        self.assertEqual(en.canonical('Cable Face Pulls (Heavy)'),
                         'Cable Face Pulls')
        self.assertEqual(en.canonical('Lat Pulldown (Neutral Grip, Heavy)'),
                         'Lat Pulldown (Neutral Grip)')

    def test_hammer_curls_default_to_dumbbell(self):
        self.assertEqual(en.canonical('Hammer Curls'), 'Dumbbell Hammer Curl')

    def test_a_supinating_curl_is_the_standard_curl(self):
        self.assertEqual(en.canonical('Dumbbell Bicep Curl (Supinating)'),
                         'Dumbbell Bicep Curl')

    def test_leg_curl_and_hamstring_curl_are_the_same_machine(self):
        for variant in ('Lying Leg Curl', 'Hamstring Curl (Lying or Seated)',
                        'Leg Curls (Seated or Lying)'):
            self.assertEqual(en.canonical(variant), 'Lying Hamstring Curl')

    def test_flat_barbell_press_is_the_bench_press(self):
        self.assertEqual(en.canonical('Flat Barbell Press'),
                         'Barbell Bench Press')
        self.assertEqual(en.canonical('Barbell Chest Press (Flat Bench)'),
                         'Barbell Bench Press')

    def test_the_machine_chest_press_is_not_folded_into_the_bench_press(self):
        """A different exercise, despite the alias above."""
        self.assertEqual(en.canonical('Machine Chest Press'),
                         'Machine Chest Press')


class RegistryIntegrityTests(SimpleTestCase):
    def test_canonical_is_idempotent(self):
        for name in list(en.CANONICAL_NAMES) + list(en.ALIASES):
            once = en.canonical(name)
            self.assertEqual(en.canonical(once), once, msg=name)

    def test_every_canonical_name_is_already_canonical(self):
        for name in en.CANONICAL_NAMES:
            self.assertTrue(en.is_canonical(name), msg=name)

    def test_no_two_canonical_names_collide(self):
        seen = {}
        for name in en.CANONICAL_NAMES:
            identity = en.key(name)
            self.assertNotIn(
                identity, seen,
                msg='%r and %r have the same identity' % (name, seen.get(identity)))
            seen[identity] = name

    def test_every_alias_target_is_a_canonical_name(self):
        """_build_index raises on a typo; this states the invariant."""
        for variant, target in en.ALIASES.items():
            self.assertIn(target, en.CANONICAL_NAMES, msg=variant)

    def test_no_alias_points_at_itself(self):
        for variant, target in en.ALIASES.items():
            self.assertNotEqual(en.key(variant), en.key(target), msg=variant)

    def test_variants_of_includes_the_canonical_name(self):
        self.assertIn('Leg Press', en.variants_of('Heavy Leg Press'))


class HistoryReunificationTests(TestCase):
    """
    End to end: the advice must see history logged under an older spelling.

    This is the user-visible payoff, and the thing that was broken.
    """

    def setUp(self):
        import datetime as dt
        from django.utils import timezone
        from api.models import (
            User, UserProfile, WorkoutPlan, WorkoutDay, Exercise,
            WorkoutSession, SetLog,
        )
        from api import exercise_types as et

        self.user = User.objects.create_user(email='canon@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        plan = WorkoutPlan.objects.create(user=self.user, title='P',
                                          is_active=True, current_week=1)
        self.day = WorkoutDay.objects.create(plan=plan, day_number=1,
                                             week_number=1, name='Push')
        self.exercise = Exercise.objects.create(
            day=self.day, name='Incline Dumbbell Press', sets=3, reps='8-12',
            measurement_type=et.WEIGHT_REPS)

        # History logged under the OTHER spelling, as production has it.
        for days_ago in (21, 14, 7):
            session = WorkoutSession.objects.create(
                user=self.user, exercise_day=self.day, day_name='Push',
                is_completed=True)
            WorkoutSession.objects.filter(pk=session.pk).update(
                started_at=timezone.now() - dt.timedelta(days=days_ago))
            for set_number in (1, 2, 3):
                SetLog.objects.create(
                    workout_session=session,
                    exercise_name='Dumbbell Incline Press',
                    set_number=set_number, weight_kg=40, reps_completed=10,
                    is_completed=True)

    def test_history_is_found_across_the_spelling_change(self):
        from api import views, exercise_types as et
        logs, _ = views._exercise_history(
            self.user, 'Incline Dumbbell Press', et.WEIGHT_REPS)
        self.assertEqual(len(logs), 9)

    def test_advice_is_produced_from_the_reunited_history(self):
        from api import views, exercise_types as et
        advice = views._build_exercise_advice(
            self.user, 'Incline Dumbbell Press', et.WEIGHT_REPS, '8-12', 'lb')
        self.assertIsNotNone(advice['coaching_note'])

    def test_an_unrelated_exercise_does_not_pick_up_the_history(self):
        from api import views, exercise_types as et
        logs, _ = views._exercise_history(
            self.user, 'Barbell Overhead Press', et.WEIGHT_REPS)
        self.assertEqual(logs, [])

    def test_new_sets_are_stored_under_the_canonical_name(self):
        from rest_framework.test import APIClient
        from rest_framework.authtoken.models import Token
        from api.models import WorkoutSession, SetLog
        from django.urls import reverse

        token, _ = Token.objects.get_or_create(user=self.user)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)
        session = WorkoutSession.objects.create(
            user=self.user, exercise_day=self.day, day_name='Push')

        res = client.post(
            reverse('workout-session-log-set', args=[session.id]),
            {'exercise_id': str(self.exercise.id), 'set_number': 1,
             'weight_kg': 42, 'reps_completed': 10, 'is_completed': True},
            format='json')
        self.assertEqual(res.status_code, 200, getattr(res, 'data', res.content))
        log = SetLog.objects.get(workout_session=session, set_number=1)
        self.assertEqual(log.exercise_name, 'Incline Dumbbell Press')

    def test_a_variant_named_exercise_row_still_logs_canonically(self):
        """Plans created before canonicalisation carry variant names."""
        from rest_framework.test import APIClient
        from rest_framework.authtoken.models import Token
        from api.models import Exercise, WorkoutSession, SetLog
        from api import exercise_types as et
        from django.urls import reverse

        legacy = Exercise.objects.create(
            day=self.day, name='Dumbbell Incline Press', sets=3, reps='8-12',
            measurement_type=et.WEIGHT_REPS, order=5)
        token, _ = Token.objects.get_or_create(user=self.user)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)
        session = WorkoutSession.objects.create(
            user=self.user, exercise_day=self.day, day_name='Push')

        res = client.post(
            reverse('workout-session-log-set', args=[session.id]),
            {'exercise_id': str(legacy.id), 'set_number': 1,
             'weight_kg': 42, 'reps_completed': 10, 'is_completed': True},
            format='json')
        self.assertEqual(res.status_code, 200, getattr(res, 'data', res.content))
        log = SetLog.objects.get(workout_session=session, set_number=1)
        self.assertEqual(log.exercise_name, 'Incline Dumbbell Press')


class CreationTests(TestCase):
    def test_generated_exercises_are_stored_canonically(self):
        from api import views
        from api.models import User, WorkoutPlan, WorkoutDay

        user = User.objects.create_user(email='canon2@test.local', password='x')
        plan = WorkoutPlan.objects.create(user=user, title='P', current_week=1)
        day = WorkoutDay.objects.create(plan=plan, day_number=1,
                                        week_number=1, name='Push')
        exercise = views._create_exercise(
            day, {'name': 'Dumbbell Incline Press', 'sets': 3, 'reps': '8-12'}, 0)
        self.assertEqual(exercise.name, 'Incline Dumbbell Press')

    def test_an_unregistered_name_survives_creation_unchanged(self):
        from api import views
        from api.models import User, WorkoutPlan, WorkoutDay

        user = User.objects.create_user(email='canon3@test.local', password='x')
        plan = WorkoutPlan.objects.create(user=user, title='P', current_week=1)
        day = WorkoutDay.objects.create(plan=plan, day_number=1,
                                        week_number=1, name='Push')
        exercise = views._create_exercise(
            day, {'name': 'Zercher Squat', 'sets': 3, 'reps': '5'}, 0)
        self.assertEqual(exercise.name, 'Zercher Squat')
