"""
Deterministic routing between the two models.

Asking a model which model should answer costs a call, a failure mode and
latency to decide something the server already knows -- and makes routing
untestable, because it can answer differently for identical requests.

The property that matters most is the MIXED case. "Change my bench press and
suggest a high-protein lunch" is both domains; routing it whole sends half
the request to a model that cannot serve it, and the half that cannot be
served is dropped silently rather than reported.
"""

from django.test import SimpleTestCase

from api.agent_router import FOOD, WORKOUT, classify, is_mixed, split_request


class ClassifyTests(SimpleTestCase):
    def test_workout_requests(self):
        for text in [
            'swap my bench press for machine chest press',
            'how many reps did I do last week',
            'what is my workout today',
            'my lower back hurts, change the deadlift',
            'add a superset to day 2',
            'was that a PR on squats',
        ]:
            self.assertEqual(classify(text), WORKOUT, text)

    def test_food_requests(self):
        for text in [
            'log a protein shake for breakfast',
            'what are my macros today',
            'suggest a high protein vegetarian dinner',
            'how is my body fat trending',
            'read my latest inbody scan',
        ]:
            self.assertEqual(classify(text), FOOD, text)

    def test_an_ambiguous_request_goes_to_workouts(self):
        """
        Workouts is the primary surface, holds the plan-mutation guardrails,
        and is the cheaper model. A tie is not worth a coin flip.
        """
        self.assertEqual(classify('how am I doing'), WORKOUT)
        self.assertEqual(classify(''), WORKOUT)

    def test_calories_alone_decides_nothing(self):
        """
        It belongs to a treadmill session as much as to a meal, so it is
        deliberately absent from both word lists.
        """
        self.assertEqual(classify('calories'), WORKOUT)


class SplitTests(SimpleTestCase):
    def test_a_single_topic_request_is_one_part(self):
        """One part means one model call. The common case must not fan out."""
        parts = split_request('swap my bench press for machine chest press')
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0][0], WORKOUT)

    def test_a_single_topic_part_keeps_the_original_sentence(self):
        """
        The model should see what the user wrote, not a sentence reassembled
        from fragments.
        """
        text = 'I did 3 sets of bench press. Then 3 sets of rows.'
        parts = split_request(text)
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0][1], text)

    def test_a_mixed_request_splits_into_both_domains(self):
        parts = split_request(
            'change my bench press and suggest a high protein lunch')
        self.assertEqual([d for d, _ in parts], [WORKOUT, FOOD])
        self.assertIn('bench', parts[0][1])
        self.assertIn('lunch', parts[1][1])

    def test_order_is_preserved(self):
        parts = split_request('log my lunch and then add a set to squats')
        self.assertEqual([d for d, _ in parts], [FOOD, WORKOUT])

    def test_is_mixed_identifies_both(self):
        self.assertTrue(is_mixed('change my bench press and log my lunch'))
        self.assertFalse(is_mixed('change my bench press and add a warmup'))

    def test_same_domain_clauses_are_merged_not_fanned_out(self):
        """
        Three workout clauses is still one call. Splitting per clause would
        multiply cost for no benefit.
        """
        parts = split_request(
            'add a set to squats and change the bench and remove the curls')
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0][0], WORKOUT)

    def test_blank_input_is_safe(self):
        for text in ('', '   ', None):
            parts = split_request(text)
            self.assertEqual(len(parts), 1)
            self.assertEqual(parts[0][0], WORKOUT)

    def test_routing_is_stable_across_identical_calls(self):
        """The property a model-based router could not give us."""
        text = 'change my bench press and suggest a high protein lunch'
        self.assertEqual(split_request(text), split_request(text))


class WordBoundaryTests(SimpleTestCase):
    """
    Terms match as whole words, never as substrings.

    A plain `in` check had 'pr' matching "protein", 'eat' matching "great" and
    'row' matching "brown" -- so "suggest a high protein vegetarian dinner"
    scored as a workout request and would have been sent to the model that
    cannot answer it.
    """

    def test_protein_does_not_count_as_a_pr(self):
        self.assertEqual(classify('suggest a high protein dinner'), FOOD)

    def test_great_does_not_count_as_eat(self):
        self.assertEqual(classify('that squat felt great'), WORKOUT)

    def test_a_real_pr_still_counts(self):
        self.assertEqual(classify('was that a PR'), WORKOUT)

    def test_multi_word_terms_still_match(self):
        self.assertEqual(classify('log a protein shake'), FOOD)
        self.assertEqual(classify('is tomorrow a rest day'), WORKOUT)
