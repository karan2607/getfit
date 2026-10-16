"""
Classifier tests for api/exercise_types.py.

These lock in the specific misclassifications that kept shipping: warmups and
planks typed as weight+reps, and the near-miss name collisions that a naive
regex gets wrong ("hang clean" is not a dead hang, "cable crunch" is not
bodyweight, "push up to side plank" is not a plank).
"""

import json
import os

from django.test import SimpleTestCase

from api import exercise_types as et


class ClassifyNameTests(SimpleTestCase):
    """Deterministic name -> measurement_type rules."""

    def assert_classify(self, cases):
        failures = []
        for name, expected in cases:
            got = et.classify(name)
            if got != expected:
                failures.append('%r -> %s (expected %s)' % (name, got, expected))
        if failures:
            self.fail('misclassified:\n  ' + '\n  '.join(failures))

    def test_warmup_and_mobility(self):
        self.assert_classify([
            ('Warmup & Mobility', et.WARMUP),
            ('Dynamic Warm-up', et.WARMUP),
            ('Warm up', et.WARMUP),
            ('Hamstring Stretch', et.WARMUP),
            ("Runner's Stretch", et.WARMUP),     # a stretch, not a run
            ('Foam Roll Quads', et.WARMUP),
            ('Hip Mobility Drill', et.WARMUP),
            ('Cool-down', et.WARMUP),
        ])

    def test_duration_work(self):
        self.assert_classify([
            ('Plank', et.DURATION),
            ('Side Plank', et.DURATION),
            ('Wall Sit', et.DURATION),
            ('Dead Hang', et.DURATION),
            ('Treadmill', et.DURATION),
            ('Running, Treadmill', et.DURATION),
            ('Elliptical Trainer', et.DURATION),
            ('Stationary Bike', et.DURATION),
            ('Jump Rope', et.DURATION),
            ('Battle Ropes', et.DURATION),
            ('Bear Crawl', et.DURATION),
            ('Stairmaster', et.DURATION),
            ('Isometric Chest Squeeze', et.DURATION),
        ])

    def test_loaded_carries_are_weight_duration(self):
        self.assert_classify([
            ("Farmer's Walk", et.WEIGHT_DURATION),
            ("Farmers Carry", et.WEIGHT_DURATION),
            ('Yoke Walk', et.WEIGHT_DURATION),
            ('Suitcase Carry', et.WEIGHT_DURATION),
            ('Rickshaw Carry', et.WEIGHT_DURATION),
        ])

    def test_sled_and_prowler_work_is_weight_duration(self):
        """
        Reported: "Sled push should also ask for weight right?" — yes. A sled is
        loaded with plates exactly as a trap bar is loaded for a carry, so
        seconds alone is half the log. These used to classify as DURATION.
        """
        self.assert_classify([
            ('Sled Push', et.WEIGHT_DURATION),
            ('Metabolic Finisher: Sled Push', et.WEIGHT_DURATION),
            ('Metabolic Finisher: Sled Push (Light)', et.WEIGHT_DURATION),
            ('Sled Drag', et.WEIGHT_DURATION),
            ('Sled Pull', et.WEIGHT_DURATION),
            ('Backward Sled Drag', et.WEIGHT_DURATION),
            ('Sled Overhead Backward Walk', et.WEIGHT_DURATION),
            ('Prowler Push', et.WEIGHT_DURATION),
            ('Prowler', et.WEIGHT_DURATION),
            # Plurals: a plan can legitimately say "Sled Pushes".
            ('Sled Pushes', et.WEIGHT_DURATION),
            ('Sled Drags', et.WEIGHT_DURATION),
        ])

    def test_farmers_hold_is_weight_duration_like_the_walk(self):
        """A farmer's hold is a farmer's walk standing still — still loaded."""
        self.assert_classify([
            ("Farmer's Hold", et.WEIGHT_DURATION),
            ('Farmers Hold', et.WEIGHT_DURATION),
            ('Suitcase Hold', et.WEIGHT_DURATION),
        ])

    def test_the_sled_machine_is_not_a_sled_push(self):
        """
        The guard on the fix above. In the exercise library "sled" also names the
        plate-loaded 45-degree machine, so a bare \\bsled\\b rule would have
        reclassified ordinary rep work as a loaded carry.
        """
        self.assert_classify([
            ('Sled Hack Squat', et.WEIGHT_REPS),
            ('Sled 45 Degree Leg Press', et.WEIGHT_REPS),
            ('Sled Overhead Triceps Extension', et.WEIGHT_REPS),
            ('Sled Closer Hand Press', et.WEIGHT_REPS),
        ])

    def test_bodyweight_reps(self):
        self.assert_classify([
            ('Push-ups', et.BODYWEIGHT_REPS),
            ('Pushups', et.BODYWEIGHT_REPS),
            ('Pull-up', et.BODYWEIGHT_REPS),
            ('Chin-up', et.BODYWEIGHT_REPS),
            ('Bench Dips', et.BODYWEIGHT_REPS),
            ('Inverted Row', et.BODYWEIGHT_REPS),
            ('Crunches', et.BODYWEIGHT_REPS),
            ('Hanging Leg Raise', et.BODYWEIGHT_REPS),
            ('Mountain Climbers', et.BODYWEIGHT_REPS),
            ('Bird Dog', et.BODYWEIGHT_REPS),
        ])

    def test_standard_lifts_are_weight_reps(self):
        self.assert_classify([
            ('Barbell Bench Press', et.WEIGHT_REPS),
            ('Dumbbell Shoulder Press', et.WEIGHT_REPS),
            ('Barbell Squat', et.WEIGHT_REPS),
            ('Deadlift', et.WEIGHT_REPS),
            ('Lat Pulldown', et.WEIGHT_REPS),
            ('High-to-Low Cable Crossover', et.WEIGHT_REPS),
            ('Pec Deck Fly Machine', et.WEIGHT_REPS),
            ('Good Morning', et.WEIGHT_REPS),
            ('Barbell Walking Lunge', et.WEIGHT_REPS),
            ('Bodyweight Walking Lunge', et.WEIGHT_REPS),
        ])

    def test_near_miss_collisions(self):
        """The cases a naive regex gets wrong. Each of these is a real KB entry."""
        self.assert_classify([
            # "hang" in a rep lift must not read as a dead hang
            ('Hang Clean', et.WEIGHT_REPS),
            ('Hang Snatch', et.WEIGHT_REPS),
            ('Smith Machine Hang Power Clean', et.WEIGHT_REPS),
            # a push-up that ends in a plank is still a rep exercise
            ('Push Up to Side Plank', et.BODYWEIGHT_REPS),
            # loaded variants of bodyweight movements are weighted
            ('Cable Crunch', et.WEIGHT_REPS),
            ('Ab Crunch Machine', et.WEIGHT_REPS),
            ('Rope Crunch', et.WEIGHT_REPS),
            ('Dip Machine', et.WEIGHT_REPS),
            ('Jerk Dip Squat', et.WEIGHT_REPS),
            ('Weighted Pull-up', et.WEIGHT_REPS),
            # "run"/"walk" inside another movement's name
            ('Chest Push with Run Release', et.WEIGHT_REPS),
        ])

    def test_empty_and_junk_names(self):
        self.assertEqual(et.classify(''), et.DEFAULT_TYPE)
        self.assertEqual(et.classify(None), et.DEFAULT_TYPE)
        self.assertEqual(et.classify('   '), et.DEFAULT_TYPE)


class RepsSignalTests(SimpleTestCase):
    """
    The free-text `reps` field as a secondary signal.

    Must fire for a genuine time prescription but NOT for a rest period --
    "12 (30 sec rest)" is a rep exercise, and treating it as timed would break
    every weighted lift the AI annotates with rest.
    """

    def test_time_prescription_implies_duration(self):
        self.assertEqual(et.classify('Side Bridge', reps='30 sec'), et.DURATION)
        self.assertEqual(et.classify('Mystery Move', reps='3 x 30 sec'), et.DURATION)
        self.assertEqual(et.classify('Unknown Hold Thing', reps='45 seconds'), et.DURATION)
        self.assertEqual(et.classify('Mystery Move', reps='60s'), et.DURATION)
        self.assertEqual(et.classify('Mystery Move', reps='2 min'), et.DURATION)

    def test_rest_periods_do_not_imply_duration(self):
        for reps in ('12 (30 sec rest)', '10 reps, 60 sec rest', '8-12, rest 90 sec',
                     '12 reps then 30 sec', 'AMRAP', '8-12'):
            self.assertEqual(
                et.classify('Barbell Bench Press', reps=reps), et.WEIGHT_REPS,
                msg='reps=%r should not make a bench press timed' % reps)

    def test_name_rules_beat_the_reps_signal(self):
        # An explicit name rule always wins; reps is only consulted when nothing
        # in the name matched. A barbell squat stays weighted even if the AI
        # annotated it with a time.
        self.assertEqual(et.classify('Barbell Squat', reps='30 sec'), et.WEIGHT_REPS)
        self.assertEqual(et.classify('Push-ups', reps='30 sec'), et.BODYWEIGHT_REPS)
        self.assertEqual(et.classify('Plank', reps='12'), et.DURATION)
        # ...but a name with no rule at all still defers to the reps text.
        self.assertEqual(et.classify('Zercher Hold Variation', reps='30 sec'),
                         et.DURATION)

    def test_has_opinion_distinguishes_verdict_from_fallback(self):
        # Both return weight_reps, but only one is a real verdict. normalize()
        # depends on this distinction.
        self.assertTrue(et.has_opinion('Barbell Bench Press'))
        self.assertEqual(et.classify('Barbell Bench Press'), et.WEIGHT_REPS)
        self.assertFalse(et.has_opinion('Zercher Thing'))
        self.assertEqual(et.classify('Zercher Thing'), et.WEIGHT_REPS)


class NormalizeTests(SimpleTestCase):
    """
    normalize() is the coercion gate. The AI only gets a vote.

    This is the guard that was missing: a missing or bogus measurement_type used
    to land on 'weight_reps', and the AI could label a plank however it liked.
    """

    def test_warmup_day_forces_warmup(self):
        # Even a plank on a warmup day is warmup work.
        self.assertEqual(
            et.normalize('weight_reps', 'Plank', day_name='Warmup & Mobility'),
            et.WARMUP)
        self.assertEqual(
            et.normalize('duration', 'Cat Cow', day_name='Warm-up'), et.WARMUP)

    def test_confident_name_rule_overrides_the_ai(self):
        self.assertEqual(et.normalize('weight_reps', 'Plank'), et.DURATION)
        self.assertEqual(et.normalize('weight_reps', 'Treadmill'), et.DURATION)
        self.assertEqual(et.normalize('weight_reps', 'Push-ups'), et.BODYWEIGHT_REPS)
        self.assertEqual(et.normalize('duration', 'Barbell Bench Press', reps='8-12'),
                         et.WEIGHT_REPS)

    def test_missing_value_falls_back_to_classifier(self):
        self.assertEqual(et.normalize(None, 'Plank'), et.DURATION)
        self.assertEqual(et.normalize('', 'Wall Sit'), et.DURATION)
        self.assertEqual(et.normalize(None, 'Zercher Squat'), et.WEIGHT_REPS)

    def test_invalid_value_falls_back_to_classifier(self):
        for junk in ('cardio', 'reps', 'WEIGHT_REPS', 'time', 42, True, {}):
            self.assertEqual(et.normalize(junk, 'Treadmill'), et.DURATION)
            self.assertEqual(et.normalize(junk, 'Zercher Squat'), et.WEIGHT_REPS)

    def test_valid_ai_value_accepted_when_rules_have_no_opinion(self):
        # "Turkish Getup" has no equipment or movement noun the rules recognise,
        # so a valid AI value is honoured rather than forced to the default.
        self.assertFalse(et.has_opinion('Turkish Getup'))
        self.assertEqual(et.normalize('bodyweight_reps', 'Turkish Getup'),
                         et.BODYWEIGHT_REPS)
        self.assertEqual(et.normalize('duration', 'Turkish Getup'), et.DURATION)

    def test_recognised_lift_overrides_a_valid_but_wrong_ai_value(self):
        # The counterpart: "Zercher Squat" DOES match a rule, so the AI loses.
        self.assertTrue(et.has_opinion('Zercher Squat'))
        self.assertEqual(et.normalize('bodyweight_reps', 'Zercher Squat'),
                         et.WEIGHT_REPS)


class FieldContractTests(SimpleTestCase):
    """coerce_set_fields enforces which fields may hold a value per type."""

    def test_duration_rejects_weight_and_reps(self):
        out = et.coerce_set_fields(et.DURATION, {
            'weight_kg': 27.216, 'reps_completed': 99, 'duration_seconds': 60})
        self.assertEqual(out, {
            'weight_kg': None, 'reps_completed': None, 'duration_seconds': 60})

    def test_weight_reps_rejects_duration(self):
        out = et.coerce_set_fields(et.WEIGHT_REPS, {
            'weight_kg': 60.0, 'reps_completed': 10, 'duration_seconds': 999})
        self.assertEqual(out, {
            'weight_kg': 60.0, 'reps_completed': 10, 'duration_seconds': None})

    def test_weight_duration_keeps_weight_and_time_not_reps(self):
        out = et.coerce_set_fields(et.WEIGHT_DURATION, {
            'weight_kg': 30.0, 'reps_completed': 45, 'duration_seconds': 45})
        self.assertEqual(out, {
            'weight_kg': 30.0, 'reps_completed': None, 'duration_seconds': 45})

    def test_bodyweight_rejects_weight(self):
        out = et.coerce_set_fields(et.BODYWEIGHT_REPS, {
            'weight_kg': 50.0, 'reps_completed': 15, 'duration_seconds': None})
        self.assertEqual(out, {
            'weight_kg': None, 'reps_completed': 15, 'duration_seconds': None})

    def test_warmup_rejects_everything_numeric(self):
        out = et.coerce_set_fields(et.WARMUP, {
            'weight_kg': 20, 'reps_completed': 12, 'duration_seconds': 30})
        self.assertEqual(out, {
            'weight_kg': None, 'reps_completed': None, 'duration_seconds': None})

    def test_integers_are_coerced_and_floored_at_zero(self):
        out = et.coerce_set_fields(et.DURATION, {'duration_seconds': '60.7'})
        self.assertEqual(out['duration_seconds'], 61)
        out = et.coerce_set_fields(et.DURATION, {'duration_seconds': -5})
        self.assertEqual(out['duration_seconds'], 0)

    def test_blank_and_unparseable_become_none(self):
        for bad in ('', None, 'abc', [], {}):
            out = et.coerce_set_fields(et.WEIGHT_REPS, {'weight_kg': bad})
            self.assertIsNone(out['weight_kg'], msg='weight_kg=%r' % (bad,))

    def test_every_type_has_a_contract_and_a_chart_spec(self):
        for mtype in et.ALL_TYPES:
            self.assertIn(mtype, et.FIELD_CONTRACT)
            self.assertIn(mtype, et.CHART_CONTRACT)
            # Every contract field must be a real SetLog field.
            for field in et.FIELD_CONTRACT[mtype]:
                self.assertIn(field, et.ALL_SET_FIELDS)
            metric = et.CHART_CONTRACT[mtype]['metric']
            if metric is not None:
                self.assertIn(
                    metric, et.FIELD_CONTRACT[mtype],
                    msg='%s charts %s but does not record it' % (mtype, metric))

    def test_unknown_type_degrades_to_the_default_contract(self):
        out = et.coerce_set_fields('nonsense', {'weight_kg': 10, 'reps_completed': 5})
        self.assertEqual(out['weight_kg'], 10)
        self.assertEqual(out['reps_completed'], 5)


class KnowledgeBaseSanityTests(SimpleTestCase):
    """
    Guards against a future rule change silently reclassifying the whole KB.

    Not asserting exact counts (rules may legitimately improve) -- asserting that
    the obvious categories stay populated and that nothing has collapsed.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            'exercise_kb.json')
        with open(path) as fh:
            kb = json.load(fh)
        cls.names = list(kb.keys()) if isinstance(kb, dict) else list(kb)

    def test_kb_loads(self):
        self.assertGreater(len(self.names), 500)

    def test_each_category_is_populated(self):
        counts = {}
        for name in self.names:
            t = et.classify(name)
            counts[t] = counts.get(t, 0) + 1
        # Every stretch in the KB should be warmup; there are ~50 of them.
        self.assertGreater(counts.get(et.WARMUP, 0), 30, counts)
        self.assertGreater(counts.get(et.DURATION, 0), 15, counts)
        self.assertGreater(counts.get(et.BODYWEIGHT_REPS, 0), 30, counts)
        self.assertGreater(counts.get(et.WEIGHT_DURATION, 0), 2, counts)
        # The bulk are ordinary lifts; if this collapses, a rule is over-matching.
        self.assertGreater(counts.get(et.WEIGHT_REPS, 0), 500, counts)

    def test_no_stretch_is_typed_as_a_lift(self):
        wrong = [n for n in self.names
                 if 'stretch' in n.lower() and et.classify(n) != et.WARMUP]
        self.assertEqual(wrong, [])

    def test_no_treadmill_or_plank_is_typed_as_a_lift(self):
        wrong = [n for n in self.names
                 if ('treadmill' in n.lower() or n.lower() == 'plank')
                 and et.classify(n) != et.DURATION]
        self.assertEqual(wrong, [])
