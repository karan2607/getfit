"""
Merged "A + B" exercises, and the chat round-trip that caused them.

Reported (c3d46391): the superset feature "shipped as a fake composite and it
corrupts logging data". The plan held ONE exercise named
"Barbell Bench Press + Push-ups (Superset)" with reps "12 + 10", so there was
one weight column and one reps column for two exercises. The user compensated
by summing -- 95 lb x 22 -- which polluted bench history and recorded no
push-up volume at all.

Root cause was not the card. The chat plan-edit path serialised exercises
WITHOUT superset_group or measurement_type and never mentioned supersets, so
merging was the only move the model had. Two bugs in one:

  1. asked for a superset -> got a merged entry
  2. ANY chat edit silently dropped existing groups and types, because chat
     echoes the complete plan back and could not see those fields

These tests cover both the prompt/serialisation fix and the splitter that
catches a merge if one ever reaches the save path anyway.
"""

import json

from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework.authtoken.models import Token

from api import views, exercise_types as et
from api.models import (
    User, UserProfile, WorkoutPlan, WorkoutDay, Exercise, Composite,
)


class SplitMergedExercisesTests(APITestCase):
    """The splitter in isolation."""

    def split(self, exercises, day_name='Upper Body'):
        return views._split_merged_exercises(day_name, exercises)

    def test_a_merged_pair_becomes_two_grouped_entries(self):
        out, problems = self.split([
            {'name': 'Barbell Bench Press + Push-ups (Superset)',
             'sets': 4, 'reps': '12 + 10', 'rest_seconds': 90,
             'measurement_type': 'weight_reps'},
        ])
        self.assertEqual(len(out), 2)
        self.assertEqual([e['name'] for e in out],
                         ['Barbell Bench Press', 'Push-ups'])
        self.assertEqual([e['reps'] for e in out], ['12', '10'])
        # Same group, so _validate_superset_groups judges it as a pair.
        self.assertEqual(out[0]['superset_group'], out[1]['superset_group'])
        self.assertIsNotNone(out[0]['superset_group'])
        # Equal sets is a group invariant and both halves inherit the one value.
        self.assertEqual([e['sets'] for e in out], [4, 4])
        self.assertTrue(problems)

    def test_the_merged_measurement_type_is_dropped_not_inherited(self):
        """
        THE data-corruption mechanism. One type for two exercises is wrong for
        at least one of them: the merge claimed weight_reps, which gave
        push-ups a weight column it has no business having.
        """
        out, _ = self.split([
            {'name': 'Barbell Bench Press + Push-ups', 'sets': 4,
             'reps': '12 + 10', 'measurement_type': 'weight_reps'},
        ])
        for member in out:
            self.assertNotIn('measurement_type', member)
        # And each half classifies correctly from its own name.
        self.assertEqual(et.classify(out[0]['name']), et.WEIGHT_REPS)
        self.assertEqual(et.classify(out[1]['name']), et.BODYWEIGHT_REPS)

    def test_a_single_rep_value_applies_to_both_halves(self):
        """"Bench Press + Barbell Row: 8-12" means 8-12 each, not 8-12 total."""
        out, _ = self.split([
            {'name': 'Bench Press + Barbell Row', 'sets': 3, 'reps': '8-12'},
        ])
        self.assertEqual([e['reps'] for e in out], ['8-12', '8-12'])

    def test_mismatched_part_counts_are_left_alone(self):
        """
        Conservative on purpose: inventing a prescription is worse than
        reporting one that is awkward to log.
        """
        out, problems = self.split([
            {'name': 'A + B', 'sets': 3, 'reps': '12 + 10 + 8'},
        ])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['name'], 'A + B')
        self.assertTrue(any('cannot split' in p for p in problems))

    def test_ordinary_exercises_are_untouched(self):
        """
        The guard that matters most: real names contain plus signs and
        parentheses. None of these may be split.
        """
        originals = [
            {'name': 'High-to-Low Cable Crossover', 'sets': 3, 'reps': '12'},
            {'name': 'Barbell Bench Press', 'sets': 4, 'reps': '8-12'},
            {'name': 'Push Up to Side Plank', 'sets': 3, 'reps': '10'},
            {'name': 'Metabolic Finisher: Sled Push', 'sets': 3, 'reps': '30 yards'},
            {'name': 'Backward Walking (Treadmill)', 'sets': 1, 'reps': '10 min'},
            # No spaces around the plus -- not the merged shape.
            {'name': 'A+B Machine Row', 'sets': 3, 'reps': '12'},
        ]
        out, problems = self.split([dict(e) for e in originals])
        self.assertEqual(out, originals)
        self.assertEqual(problems, [])

    def test_a_triple_merge_splits_into_three(self):
        out, _ = self.split([
            {'name': 'Curl + Triceps Extension + Lateral Raise',
             'sets': 3, 'reps': '12 + 12 + 15'},
        ])
        self.assertEqual(len(out), 3)
        self.assertEqual(len({e['superset_group'] for e in out}), 1)

    def test_a_fresh_group_number_is_used(self):
        """Must not collide with a group the model already assigned."""
        out, _ = self.split([
            {'name': 'Lat Pulldown', 'sets': 3, 'reps': '12',
             'superset_group': 1},
            {'name': 'Cable Row', 'sets': 3, 'reps': '12',
             'superset_group': 1},
            {'name': 'Curl + Triceps Extension', 'sets': 3, 'reps': '12'},
        ])
        merged = [e for e in out if e['name'] in
                  ('Curl', 'Triceps Extension')]
        self.assertEqual(len(merged), 2)
        self.assertNotEqual(merged[0]['superset_group'], 1)


class MergedExerciseSavePathTests(APITestCase):
    """End to end through the plan PUT, which is what chat output goes through."""

    def setUp(self):
        self.user = User.objects.create_user(email='merge@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)
        self.plan = WorkoutPlan.objects.create(user=self.user, title='P')

    def put(self, exercises):
        res = self.client.put(
            reverse('workout-plan-detail', args=[self.plan.id]), {
                'title': 'P',
                'days': [{'day_number': 1, 'name': 'Upper Body',
                          'is_rest_day': False, 'exercises': exercises}],
            }, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        day = WorkoutDay.objects.get(plan=self.plan, day_number=1)
        return list(day.exercises.order_by('order'))

    def test_the_reported_plan_becomes_two_loggable_exercises(self):
        """
        The exact reported row. Bench + push-ups is a REJECTED pairing (both
        push, both chest), so the group does not survive -- but both halves do,
        and that is the point: two separate loggable exercises beats one
        unloggable merged entry.
        """
        saved = self.put([
            {'name': 'Barbell Warmup', 'sets': 1, 'reps': '5 min'},
            {'name': 'Overhead Press', 'sets': 3, 'reps': '8'},
            {'name': 'Barbell Bench Press + Push-ups (Superset)',
             'sets': 4, 'reps': '12 + 10', 'rest_seconds': 90,
             'measurement_type': 'weight_reps'},
        ])
        names = [e.name for e in saved]
        self.assertIn('Barbell Bench Press', names)
        self.assertIn('Push-ups', names)
        self.assertNotIn('Barbell Bench Press + Push-ups (Superset)', names)

        bench = next(e for e in saved if e.name == 'Barbell Bench Press')
        pushup = next(e for e in saved if e.name == 'Push-ups')
        self.assertEqual(bench.reps, '12')
        self.assertEqual(pushup.reps, '10')
        # Each half now has its OWN field contract -- push-ups get no weight
        # column, which is what made the summed 22-rep logging possible.
        self.assertEqual(bench.measurement_type, et.WEIGHT_REPS)
        self.assertEqual(pushup.measurement_type, et.BODYWEIGHT_REPS)
        self.assertNotIn('weight_kg',
                         et.FIELD_CONTRACT[pushup.measurement_type])

    def test_a_valid_merged_pairing_survives_as_a_real_group(self):
        """An antagonist pair splits AND keeps the group."""
        saved = self.put([
            {'name': 'Hamstring Stretch', 'sets': 1, 'reps': '30 sec'},
            {'name': 'Barbell Squat', 'sets': 3, 'reps': '5'},
            {'name': 'Leg Extension + Leg Curl', 'sets': 3, 'reps': '12'},
        ])
        groups = [e.composite_id for e in saved
                  if e.name in ('Leg Extension', 'Leg Curl')]
        self.assertEqual(len(groups), 2)
        self.assertIsNotNone(groups[0])
        self.assertEqual(groups[0], groups[1])

    def test_a_plan_with_no_merges_round_trips_unchanged(self):
        saved = self.put([
            {'name': 'Barbell Bench Press', 'sets': 4, 'reps': '8-12'},
            {'name': 'High-to-Low Cable Crossover', 'sets': 3, 'reps': '12-15'},
        ])
        self.assertEqual([e.name for e in saved],
                         ['Barbell Bench Press', 'High-to-Low Cable Crossover'])


class ChatPlanContextTests(APITestCase):
    """
    The round-trip fix. Chat echoes the COMPLETE plan back, so any field the
    context omits is destroyed on every edit.
    """

    def setUp(self):
        self.user = User.objects.create_user(email='chatctx@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        self.plan = WorkoutPlan.objects.create(user=self.user, title='Grouped')
        self.day = WorkoutDay.objects.create(
            plan=self.plan, day_number=1, week_number=1, name='Pull')
        self.composite = Composite.objects.create(
            day=self.day, order=0, rest_seconds=75)
        Exercise.objects.create(
            day=self.day, name='Deadlift', sets=3, reps='5', order=0,
            measurement_type=et.WEIGHT_REPS)
        Exercise.objects.create(
            day=self.day, name='Lat Pulldown', sets=3, reps='12', order=1,
            measurement_type=et.WEIGHT_REPS, composite=self.composite)
        Exercise.objects.create(
            day=self.day, name='Plank', sets=3, reps='45 sec', order=2,
            measurement_type=et.DURATION, composite=self.composite)

    def context(self, day_id=None):
        return views._build_system_prompt(
            self.user, plan_id=str(self.plan.id),
            day_id=str(day_id) if day_id else None)

    def plan_json(self, prompt):
        """Pull the plan JSON block back out of the assembled prompt."""
        start = prompt.index('CURRENT WORKOUT PLAN')
        block = prompt[prompt.index('```json', start) + 7:]
        return json.loads(block[:block.index('```')])

    def test_superset_groups_are_visible_to_the_editor(self):
        """Without this the model cannot preserve what it cannot see."""
        doc = self.plan_json(self.context())
        exercises = doc['weeks'][0][0]['exercises']
        by_name = {e['name']: e for e in exercises}
        self.assertEqual(by_name['Lat Pulldown']['superset_group'],
                         by_name['Plank']['superset_group'])
        # Standalone exercises omit the key entirely, matching what the
        # generation prompt asks the model to produce.
        self.assertNotIn('superset_group', by_name['Deadlift'])

    def test_measurement_types_are_visible_to_the_editor(self):
        doc = self.plan_json(self.context())
        by_name = {e['name']: e
                   for e in doc['weeks'][0][0]['exercises']}
        self.assertEqual(by_name['Plank']['measurement_type'], et.DURATION)
        self.assertEqual(by_name['Deadlift']['measurement_type'], et.WEIGHT_REPS)

    def test_the_editor_is_told_the_superset_rules(self):
        """
        The prompt half of the fix. The rules used to live only in
        _workout_system_prompt, which chat does not use.
        """
        prompt = self.context()
        self.assertIn('superset_group', prompt)
        self.assertIn('NEVER merge two exercises into one entry', prompt)
        self.assertIn('bench press + push-ups is wrong', prompt)
        self.assertIn('PRESERVE BOTH VERBATIM', prompt)

    def test_the_single_day_listing_shows_the_group(self):
        """
        Clicking Edit on one day gives the model a TEXT listing of that day,
        not the JSON -- so the group has to be visible there too.
        """
        prompt = self.context(day_id=self.day.id)
        self.assertIn('[superset_group 1]', prompt)

    def test_one_copy_of_the_rules(self):
        """
        Generation and chat must share the constant. Two copies drift, and a
        drifted copy is how the model ends up with rules the server does not
        enforce.
        """
        self.assertIn(views.SUPERSET_PROMPT_RULES,
                      views._workout_system_prompt('lb'))
        self.assertIn(views.SUPERSET_PROMPT_RULES, self.context())


class SplitExistingMergedRowsTests(APITestCase):
    """
    Upgrade step 8: repairing merged rows ALREADY stored in a plan.

    I claimed the Composite migration would do this. It did not — 0045
    converted superset_group integers into containers and never touched merged
    names, and the save-path splitter only fires when a plan is written. The
    reported plan still had eight merged rows, one per week, each with a
    different rep split from the progression.

    The only thing that could actually hurt here is logged history, so that is
    what most of these assert.
    """

    def setUp(self):
        self.user = User.objects.create_user(email='split8@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        self.plan = WorkoutPlan.objects.create(
            user=self.user, title='P', current_week=1, is_active=True)
        self.day = WorkoutDay.objects.create(
            plan=self.plan, day_number=1, week_number=1,
            name='Upper Body Push Focus')

        self.warm = Exercise.objects.create(
            day=self.day, name='Warmup & Mobility', sets=1, reps='5 min',
            order=0, measurement_type=et.WARMUP)
        self.merged = Exercise.objects.create(
            day=self.day, name='Barbell Bench Press + Push-ups (Superset)',
            sets=4, reps='12 + 10', rest_seconds=90, order=1,
            measurement_type=et.WEIGHT_REPS)
        self.after = Exercise.objects.create(
            day=self.day, name='Lateral Raise', sets=3, reps='12-15', order=2,
            measurement_type=et.WEIGHT_REPS)

        # The bench sets the user had already corrected by hand to 12 reps.
        from api.models import WorkoutSession, SetLog
        self.session = WorkoutSession.objects.create(
            user=self.user, exercise_day=self.day,
            day_name='Upper Body Push Focus', is_completed=True)
        self.logs = [
            SetLog.objects.create(
                workout_session=self.session, exercise=self.merged,
                exercise_name='Barbell Bench Press + Push-ups (Superset)',
                set_number=n, weight_kg=43.1, reps_completed=12,
                is_completed=True)
            for n in (1, 2, 3, 4)
        ]
        self.log_ids = {log.id for log in self.logs}

    def run_step(self):
        from api import data_upgrades
        lines = []
        changed = data_upgrades._step_8_split_merged_exercises(
            self.plan, lines.append)
        return changed, lines

    def names(self):
        return [e.name for e in self.day.exercises.order_by('order')]

    # -- the repair ----------------------------------------------------------

    def test_the_merged_row_becomes_two_loggable_exercises(self):
        self.run_step()
        self.assertEqual(
            self.names(),
            ['Warmup & Mobility', 'Barbell Bench Press', 'Push-ups',
             'Lateral Raise'])

    def test_each_half_keeps_its_own_prescription(self):
        self.run_step()
        bench = self.day.exercises.get(name='Barbell Bench Press')
        push = self.day.exercises.get(name='Push-ups')
        self.assertEqual(bench.reps, '12')
        self.assertEqual(push.reps, '10')
        self.assertEqual(bench.sets, 4)
        self.assertEqual(push.sets, 4)

    def test_each_half_gets_its_own_field_contract(self):
        """
        The whole point. Push-ups must stop having a weight column — a value
        typed there is discarded by the server.
        """
        self.run_step()
        push = self.day.exercises.get(name='Push-ups')
        self.assertEqual(push.measurement_type, et.BODYWEIGHT_REPS)
        self.assertNotIn('weight_kg', et.FIELD_CONTRACT[push.measurement_type])
        bench = self.day.exercises.get(name='Barbell Bench Press')
        self.assertEqual(bench.measurement_type, et.WEIGHT_REPS)

    def test_the_halves_are_adjacent_and_the_order_stays_contiguous(self):
        self.run_step()
        orders = list(self.day.exercises.order_by('order')
                      .values_list('order', flat=True))
        self.assertEqual(orders, [0, 1, 2, 3])

    # -- logged history, which is the only part that could hurt --------------

    def test_the_first_half_keeps_the_existing_row_and_its_logs(self):
        """
        Same primary key, so every SetLog stays attached. Those ARE bench sets.
        """
        original_pk = self.merged.pk
        self.run_step()
        bench = self.day.exercises.get(name='Barbell Bench Press')
        self.assertEqual(bench.pk, original_pk)
        from api.models import SetLog
        self.assertEqual(
            SetLog.objects.filter(exercise=bench).count(), 4)

    def test_no_set_log_is_created_deleted_or_repointed(self):
        from api.models import SetLog
        self.run_step()
        self.assertEqual({l.id for l in SetLog.objects.all()}, self.log_ids)
        for log in SetLog.objects.all():
            self.assertEqual(log.weight_kg, 43.1)
            self.assertEqual(log.reps_completed, 12)
            self.assertTrue(log.is_completed)

    def test_the_new_half_has_no_invented_history(self):
        """
        Push-up volume was never recorded. Inventing it would be worse than
        leaving it absent.
        """
        from api.models import SetLog
        self.run_step()
        push = self.day.exercises.get(name='Push-ups')
        self.assertEqual(SetLog.objects.filter(exercise=push).count(), 0)

    # -- restraint -----------------------------------------------------------

    def test_a_day_with_nothing_merged_is_untouched(self):
        other = WorkoutDay.objects.create(
            plan=self.plan, day_number=2, week_number=1, name='Pull')
        a = Exercise.objects.create(day=other, name='Lat Pulldown', sets=3,
                                    reps='12', order=0)
        self.run_step()
        a.refresh_from_db()
        self.assertEqual(a.name, 'Lat Pulldown')
        self.assertEqual(other.exercises.count(), 1)

    def test_a_second_run_changes_nothing(self):
        self.run_step()
        before = self.names()
        changed, _ = self.run_step()
        self.assertEqual(changed, 0)
        self.assertEqual(self.names(), before)

    def test_it_handles_each_weeks_different_rep_split(self):
        """
        The real plan's eight rows each split differently because the reps
        progress: "10 + 10", "5 + 5-8", "6-8 + 8-12".
        """
        for week, reps, expected in (
            (2, '10-12 + 10', ['10-12', '10']),
            (3, '5 + 5-8', ['5', '5-8']),
            (4, '6-8 + 8-12', ['6-8', '8-12']),
        ):
            day = WorkoutDay.objects.create(
                plan=self.plan, day_number=1, week_number=week, name='Push')
            Exercise.objects.create(
                day=day, name='Barbell Bench Press + Push-ups (Superset)',
                sets=4, reps=reps, order=0,
                measurement_type=et.WEIGHT_REPS)
        self.run_step()
        for week, _reps, expected in (
            (2, None, ['10-12', '10']),
            (3, None, ['5', '5-8']),
            (4, None, ['6-8', '8-12']),
        ):
            day = WorkoutDay.objects.get(plan=self.plan, week_number=week,
                                         day_number=1)
            got = [e.reps for e in day.exercises.order_by('order')]
            self.assertEqual(got, expected, msg='week %d' % week)

    def test_it_is_registered_as_step_eight(self):
        from api import data_upgrades
        label, func = data_upgrades.UPGRADES[8]
        self.assertEqual(func, data_upgrades._step_8_split_merged_exercises)
        self.assertIn('merged', label)


class RealignLoggedNamesTests(APITestCase):
    """
    Upgrade step 9. Step 8 renamed the Exercise rows and left the logs behind.

    `SetLog.exercise_name` is denormalised and history matches on NAME, so the
    reported plan ended up with bench history split across two names and the
    logged sets invisible to the coaching for the exercise they belong to.
    """

    def setUp(self):
        from api.models import WorkoutSession, SetLog
        self.user = User.objects.create_user(email='realign@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        self.plan = WorkoutPlan.objects.create(
            user=self.user, title='P', current_week=1, is_active=True)
        self.day = WorkoutDay.objects.create(
            plan=self.plan, day_number=1, week_number=1, name='Push')
        # Post-step-8 state: the row is renamed, the logs still say the old name.
        self.bench = Exercise.objects.create(
            day=self.day, name='Barbell Bench Press', sets=4, reps='12',
            order=0, measurement_type=et.WEIGHT_REPS)
        self.session = WorkoutSession.objects.create(
            user=self.user, exercise_day=self.day, day_name='Push',
            is_completed=True)
        self.logs = [
            SetLog.objects.create(
                workout_session=self.session, exercise=self.bench,
                exercise_name='Barbell Bench Press + Push-ups (Superset)',
                set_number=n, weight_kg=43.1, reps_completed=12,
                is_completed=True)
            for n in (1, 2, 3, 4)
        ]
        # An orphan: no exercise FK. Deliberately survives plan deletion.
        self.orphan = SetLog.objects.create(
            workout_session=self.session, exercise=None,
            exercise_name='Some Deleted Exercise', set_number=9,
            weight_kg=20.0, reps_completed=10, is_completed=True)

    def run_step(self):
        from api import data_upgrades
        lines = []
        changed = data_upgrades._step_9_realign_logged_names(
            self.plan, lines.append)
        return changed, lines

    def test_stale_logged_names_are_realigned(self):
        changed, _ = self.run_step()
        self.assertEqual(changed, 4)
        from api.models import SetLog
        names = set(SetLog.objects.filter(exercise=self.bench)
                    .values_list('exercise_name', flat=True))
        self.assertEqual(names, {'Barbell Bench Press'})

    def test_the_history_is_reunited_under_one_name(self):
        """
        The user-visible point: those sets become part of bench history instead
        of a separate one-session entry the coaching never sees.
        """
        from api.models import SetLog
        before = SetLog.objects.filter(
            exercise_name='Barbell Bench Press').count()
        self.assertEqual(before, 0)
        self.run_step()
        after = SetLog.objects.filter(
            exercise_name='Barbell Bench Press').count()
        self.assertEqual(after, 4)

    def test_logged_values_are_untouched(self):
        from api.models import SetLog
        self.run_step()
        for log in SetLog.objects.filter(exercise=self.bench):
            self.assertEqual(log.weight_kg, 43.1)
            self.assertEqual(log.reps_completed, 12)
            self.assertTrue(log.is_completed)

    def test_no_rows_are_created_or_deleted(self):
        from api.models import SetLog
        before = set(SetLog.objects.values_list('id', flat=True))
        self.run_step()
        self.assertEqual(set(SetLog.objects.values_list('id', flat=True)),
                         before)

    def test_an_orphaned_log_is_left_alone(self):
        """
        `exercise_name` outliving its plan is the DESIGN, not a defect. With no
        FK there is nothing to realign against, and rewriting it would destroy
        history on purpose.
        """
        self.run_step()
        self.orphan.refresh_from_db()
        self.assertEqual(self.orphan.exercise_name, 'Some Deleted Exercise')
        self.assertIsNone(self.orphan.exercise_id)

    def test_a_log_already_matching_is_untouched(self):
        from api.models import SetLog
        SetLog.objects.filter(exercise=self.bench).update(
            exercise_name='Barbell Bench Press')
        changed, _ = self.run_step()
        self.assertEqual(changed, 0)

    def test_a_second_run_changes_nothing(self):
        self.run_step()
        changed, _ = self.run_step()
        self.assertEqual(changed, 0)

    def test_another_users_logs_are_untouched(self):
        from api.models import WorkoutSession, SetLog
        other = User.objects.create_user(email='realign2@test.local', password='x')
        session = WorkoutSession.objects.create(
            user=other, day_name='Theirs', is_completed=True)
        theirs = SetLog.objects.create(
            workout_session=session, exercise=None,
            exercise_name='Barbell Bench Press + Push-ups (Superset)',
            set_number=1, weight_kg=50.0, reps_completed=8, is_completed=True)
        self.run_step()
        theirs.refresh_from_db()
        self.assertEqual(theirs.exercise_name,
                         'Barbell Bench Press + Push-ups (Superset)')

    def test_it_is_registered_as_step_nine(self):
        from api import data_upgrades
        label, func = data_upgrades.UPGRADES[9]
        self.assertEqual(func, data_upgrades._step_9_realign_logged_names)
        self.assertIn('logged names', label)

    def test_step_eight_and_nine_together_fix_the_reported_plan(self):
        """
        End to end: a merged row with logs becomes two loggable exercises AND
        the history lands under the right name. Step 8 alone left it split.
        """
        from api import data_upgrades
        from api.models import SetLog
        day = WorkoutDay.objects.create(
            plan=self.plan, day_number=2, week_number=1, name='Push2')
        merged = Exercise.objects.create(
            day=day, name='Barbell Bench Press + Push-ups (Superset)',
            sets=4, reps='12 + 10', order=0, measurement_type=et.WEIGHT_REPS)
        SetLog.objects.create(
            workout_session=self.session, exercise=merged,
            exercise_name='Barbell Bench Press + Push-ups (Superset)',
            set_number=5, weight_kg=43.1, reps_completed=12, is_completed=True)

        data_upgrades._step_8_split_merged_exercises(self.plan, lambda _m: None)
        data_upgrades._step_9_realign_logged_names(self.plan, lambda _m: None)

        self.assertEqual(
            [e.name for e in day.exercises.order_by('order')],
            ['Barbell Bench Press', 'Push-ups'])
        log = SetLog.objects.get(set_number=5)
        self.assertEqual(log.exercise_name, 'Barbell Bench Press')
        self.assertEqual(log.reps_completed, 12)
