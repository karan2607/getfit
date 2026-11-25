"""
The edit scorer, which decides whether a model's draft was any good.

Scored deterministically and tested here, so the only thing a live run adds
is the model's answer -- not an argument about what counts as correct.
"""
from django.test import SimpleTestCase

from api.evals import edits


class ScopeScoreTests(SimpleTestCase):
    scenario = edits.SCENARIOS[0]          # move Legs, week 8 only

    def test_a_scoped_change_scores_one(self):
        result = edits.score_edit(
            [{'action': 'move_day', 'day': 'Legs', 'week': 8,
              'to_position': 3}], self.scenario)
        self.assertEqual(result['scope'], 1.0)
        self.assertEqual(result['findings'], [])

    def test_an_unscoped_change_scores_zero_and_says_why(self):
        """The live bug: no week means every week."""
        result = edits.score_edit(
            [{'action': 'move_day', 'day': 'Legs', 'to_position': 3}],
            self.scenario)
        self.assertEqual(result['scope'], 0.0)
        self.assertIn('EVERY week', result['findings'][0])

    def test_the_wrong_week_scores_zero(self):
        result = edits.score_edit(
            [{'action': 'move_day', 'day': 'Legs', 'week': 6,
              'to_position': 3}], self.scenario)
        self.assertEqual(result['scope'], 0.0)
        self.assertIn('week 6', result['findings'][0])

    def test_scope_is_the_fraction_of_items_that_got_it_right(self):
        scenario = edits.SCENARIOS[1]      # the three-part restructure
        result = edits.score_edit([
            {'action': 'move_day', 'day': 'Legs', 'week': 8, 'to_position': 3},
            {'action': 'rename_day', 'day': 'Accessories', 'week': 8,
             'name': 'Pull + Abs'},
            {'action': 'rename_day', 'day': 'Full Body',
             'name': 'Upper Body Focus'},
        ], scenario)
        self.assertAlmostEqual(result['scope'], 2 / 3, places=3)

    def test_week_zero_is_right_when_the_user_means_the_programme(self):
        scenario = next(s for s in edits.SCENARIOS
                        if s['key'] == 'whole_programme')
        result = edits.score_edit(
            [{'action': 'remove', 'name': 'Barbell Bench Press', 'week': 0}],
            scenario)
        self.assertEqual(result['scope'], 1.0)

    def test_a_week_number_is_wrong_when_the_user_means_the_programme(self):
        scenario = next(s for s in edits.SCENARIOS
                        if s['key'] == 'whole_programme')
        result = edits.score_edit(
            [{'action': 'remove', 'name': 'Barbell Bench Press', 'week': 8}],
            scenario)
        self.assertEqual(result['scope'], 0.0)


class CompletenessScoreTests(SimpleTestCase):
    scenario = next(s for s in edits.SCENARIOS
                    if s['key'] == 'add_several_to_one_day')

    def test_all_three_present_scores_one(self):
        result = edits.score_edit([
            {'action': 'add', 'name': n, 'day': 'Accessories', 'week': 8}
            for n in ('Barbell Row', 'Face Pull', 'Dumbbell Curl')
        ], self.scenario)
        self.assertEqual(result['completeness'], 1.0)

    def test_the_day_four_case_scores_a_third(self):
        """One of three delivered is not a clean change."""
        result = edits.score_edit(
            [{'action': 'add', 'name': 'Barbell Row', 'day': 'Accessories',
              'week': 8}], self.scenario)
        self.assertAlmostEqual(result['completeness'], 1 / 3, places=3)
        self.assertEqual(len(result['findings']), 2)

    def test_a_spelling_difference_is_not_a_miss(self):
        """Names are compared on identity, as everything else is."""
        result = edits.score_edit([
            {'action': 'add', 'name': 'Barbell Rows', 'day': 'Accessories',
             'week': 8},
            {'action': 'add', 'name': 'Face Pulls', 'day': 'Accessories',
             'week': 8},
            {'action': 'add', 'name': 'Dumbbell Curls', 'day': 'Accessories',
             'week': 8},
        ], self.scenario)
        self.assertEqual(result['completeness'], 1.0)

    def test_padding_the_draft_costs_marks(self):
        result = edits.score_edit([
            {'action': 'add', 'name': n, 'day': 'Accessories', 'week': 8}
            for n in ('Barbell Row', 'Face Pull', 'Dumbbell Curl',
                      'Lateral Raise', 'Crunches')
        ], self.scenario)
        self.assertLess(result['completeness'], 1.0)
        self.assertIn('more than the request needs', result['findings'][-1])

    def test_an_empty_draft_scores_nothing_on_both(self):
        result = edits.score_edit([], self.scenario)
        self.assertEqual(result['scope'], 0.0)
        self.assertEqual(result['completeness'], 0.0)


class ScenarioShapeTests(SimpleTestCase):
    def test_every_scenario_is_runnable(self):
        for scenario in edits.SCENARIOS:
            self.assertTrue(scenario['request'])
            self.assertTrue(scenario['must_include'])
            self.assertTrue(scenario['weeks'])
            self.assertGreaterEqual(scenario['max_items'],
                                    len(scenario['must_include']))

    def test_the_plan_spans_several_weeks(self):
        """
        A single-week fixture cannot show a scope failure: with one week,
        "every week" and "week 8" look the same.
        """
        self.assertGreaterEqual(len(edits.PLAN['weeks']), 2)


class WeekTypeTests(SimpleTestCase):
    """
    A model sometimes answers "8" where the schema says integer.

    The server copes, so the scorer should measure whether the right WEEK
    was chosen rather than whether the right type was. Observed from
    claude-haiku-5-5 in the first comparison run.
    """
    scenario = edits.SCENARIOS[0]

    def test_a_numeric_string_week_is_accepted(self):
        result = edits.score_edit(
            [{'action': 'move_day', 'day': 'Legs', 'week': '8',
              'to_position': 3}], self.scenario)
        self.assertEqual(result['scope'], 1.0)

    def test_a_non_numeric_week_is_still_wrong(self):
        result = edits.score_edit(
            [{'action': 'move_day', 'day': 'Legs', 'week': 'this one',
              'to_position': 3}], self.scenario)
        self.assertEqual(result['scope'], 0.0)
