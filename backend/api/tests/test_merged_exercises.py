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
