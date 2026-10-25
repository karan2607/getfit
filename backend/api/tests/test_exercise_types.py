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
        """
        Holds and unmetered conditioning. Machines moved to CARDIO_MACHINE so
        speed and incline have somewhere to go -- these are the ones that
        must NOT move with them.
        """
        self.assert_classify([
            ('Plank', et.DURATION),
            ('Side Plank', et.DURATION),
            ('Wall Sit', et.DURATION),
            ('Dead Hang', et.DURATION),
            ('Jump Rope', et.DURATION),
            ('Battle Ropes', et.DURATION),
            ('Bear Crawl', et.DURATION),
            ('Isometric Chest Squeeze', et.DURATION),
            ('Running', et.DURATION),
            ('Sprints', et.DURATION),
        ])

    def test_cardio_machines_are_their_own_type(self):
        """
        Reported: "Treadmill should also track speed" and "should also have a
        column to add incline". A machine has a readout, so it gets a type
        whose contract includes speed and incline.
        """
        self.assert_classify([
            ('Treadmill', et.CARDIO_MACHINE),
            ('Running, Treadmill', et.CARDIO_MACHINE),
            ('Incline Treadmill Walk', et.CARDIO_MACHINE),
            ('Treadmill HIIT', et.CARDIO_MACHINE),
            ('Elliptical Trainer', et.CARDIO_MACHINE),
            ('Stationary Bike', et.CARDIO_MACHINE),
            ('Recumbent Bike', et.CARDIO_MACHINE),
            ('Air Bike', et.CARDIO_MACHINE),
            ('Stairmaster', et.CARDIO_MACHINE),
            ('Rowing Machine', et.CARDIO_MACHINE),
        ])

    def test_loaded_carries_are_weight_distance(self):
        """
        Carries are prescribed in DISTANCE ("Farmer's Carry: 40 yards"), which
        had nowhere to be logged -- two feedback items sat escalated for months
        because of it. WEIGHT_DISTANCE keeps duration too, so reclassifying
        does not drop already-logged carry times.
        """
        self.assert_classify([
            ("Farmer's Walk", et.WEIGHT_DISTANCE),
            ("Farmers Carry", et.WEIGHT_DISTANCE),
            ('Yoke Walk', et.WEIGHT_DISTANCE),
            ('Suitcase Carry', et.WEIGHT_DISTANCE),
            ('Rickshaw Carry', et.WEIGHT_DISTANCE),
        ])

    def test_sled_and_prowler_work_is_weight_distance(self):
        """
        Reported: "Sled push should also ask for weight right?" — yes. A sled is
        loaded with plates exactly as a trap bar is loaded for a carry, so
        seconds alone is half the log. These used to classify as DURATION, then
        WEIGHT_DURATION, and are now WEIGHT_DISTANCE -- a sled push is
        prescribed in yards.
        """
        self.assert_classify([
            ('Sled Push', et.WEIGHT_DISTANCE),
            ('Metabolic Finisher: Sled Push', et.WEIGHT_DISTANCE),
            ('Metabolic Finisher: Sled Push (Light)', et.WEIGHT_DISTANCE),
            ('Sled Drag', et.WEIGHT_DISTANCE),
            ('Sled Pull', et.WEIGHT_DISTANCE),
            ('Backward Sled Drag', et.WEIGHT_DISTANCE),
            ('Sled Overhead Backward Walk', et.WEIGHT_DISTANCE),
            ('Prowler Push', et.WEIGHT_DISTANCE),
            ('Prowler', et.WEIGHT_DISTANCE),
            # Plurals: a plan can legitimately say "Sled Pushes".
            ('Sled Pushes', et.WEIGHT_DISTANCE),
            ('Sled Drags', et.WEIGHT_DISTANCE),
        ])

    def test_farmers_hold_is_weight_distance_like_the_walk(self):
        """A farmer's hold is a farmer's walk standing still — still loaded."""
        self.assert_classify([
            ("Farmer's Hold", et.WEIGHT_DISTANCE),
            ('Farmers Hold', et.WEIGHT_DISTANCE),
            ('Suitcase Hold', et.WEIGHT_DISTANCE),
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
        self.assertEqual(et.normalize('weight_reps', 'Treadmill'), et.CARDIO_MACHINE)
        self.assertEqual(et.normalize('weight_reps', 'Push-ups'), et.BODYWEIGHT_REPS)
        self.assertEqual(et.normalize('duration', 'Barbell Bench Press', reps='8-12'),
                         et.WEIGHT_REPS)

    def test_missing_value_falls_back_to_classifier(self):
        self.assertEqual(et.normalize(None, 'Plank'), et.DURATION)
        self.assertEqual(et.normalize('', 'Wall Sit'), et.DURATION)
        self.assertEqual(et.normalize(None, 'Zercher Squat'), et.WEIGHT_REPS)

    def test_invalid_value_falls_back_to_classifier(self):
        for junk in ('cardio', 'reps', 'WEIGHT_REPS', 'time', 42, True, {}):
            self.assertEqual(et.normalize(junk, 'Treadmill'), et.CARDIO_MACHINE)
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

    def assert_contract(self, mtype, supplied, expected_kept):
        """
        Assert the CONTRACT rather than a literal dict.

        These used to compare against a hardcoded 3-key dict, so adding
        speed/incline/distance broke all five at once without any of them
        actually being wrong. Asserting the property means the next metric
        does not break them either.
        """
        out = et.coerce_set_fields(mtype, supplied)
        self.assertEqual(set(out), set(et.ALL_SET_FIELDS),
                         msg='coerce must return every field')
        for field in et.ALL_SET_FIELDS:
            if field in expected_kept:
                self.assertEqual(out[field], expected_kept[field],
                                 msg='%s should keep %s' % (mtype, field))
            else:
                self.assertIsNone(out[field],
                                  msg='%s must null %s' % (mtype, field))

    def test_duration_rejects_weight_and_reps(self):
        self.assert_contract(
            et.DURATION,
            {'weight_kg': 27.216, 'reps_completed': 99, 'duration_seconds': 60},
            {'duration_seconds': 60})

    def test_weight_reps_rejects_duration(self):
        self.assert_contract(
            et.WEIGHT_REPS,
            {'weight_kg': 60.0, 'reps_completed': 10, 'duration_seconds': 999},
            {'weight_kg': 60.0, 'reps_completed': 10})

    def test_weight_duration_keeps_weight_and_time_not_reps(self):
        self.assert_contract(
            et.WEIGHT_DURATION,
            {'weight_kg': 30.0, 'reps_completed': 45, 'duration_seconds': 45},
            {'weight_kg': 30.0, 'duration_seconds': 45})

    def test_bodyweight_rejects_weight(self):
        self.assert_contract(
            et.BODYWEIGHT_REPS,
            {'weight_kg': 50.0, 'reps_completed': 15, 'duration_seconds': None},
            {'reps_completed': 15})

    def test_warmup_rejects_everything_numeric(self):
        self.assert_contract(
            et.WARMUP,
            {'weight_kg': 20, 'reps_completed': 12, 'duration_seconds': 30},
            {})

    def test_cardio_machine_keeps_time_speed_and_incline(self):
        """Reported: "Treadmill should also track speed" / add incline."""
        self.assert_contract(
            et.CARDIO_MACHINE,
            {'duration_seconds': 1500, 'speed_kmh': 5.6, 'incline_pct': 3.5,
             'weight_kg': 99, 'reps_completed': 9},
            {'duration_seconds': 1500, 'speed_kmh': 5.6, 'incline_pct': 3.5})

    def test_weight_distance_keeps_weight_distance_and_time(self):
        """
        Time is in this contract ON PURPOSE. weight_distance takes over from
        weight_duration, and reclassifying runs the contract over existing
        history -- dropping duration_seconds would have nulled 17 already
        logged carry/sled durations in production.
        """
        self.assert_contract(
            et.WEIGHT_DISTANCE,
            {'weight_kg': 40.0, 'distance_m': 27.4, 'duration_seconds': 45,
             'reps_completed': 9},
            {'weight_kg': 40.0, 'distance_m': 27.4, 'duration_seconds': 45})

    def test_every_new_contract_is_a_superset_of_the_one_it_replaces(self):
        """
        The property that makes reclassification lossless BY CONSTRUCTION.

        _step_2_fix_setlog_field_contract nulls any field absent from the new
        contract, so a narrower contract would silently destroy logged history
        the moment an exercise was reclassified.
        """
        self.assertTrue(et.REPLACES, 'REPLACES should not be empty')
        for new_type, old_type in et.REPLACES.items():
            new_fields = set(et.FIELD_CONTRACT[new_type])
            old_fields = set(et.FIELD_CONTRACT[old_type])
            self.assertTrue(
                old_fields <= new_fields,
                msg='%s drops %s from %s -- reclassifying would destroy data'
                    % (new_type, sorted(old_fields - new_fields), old_type))

    def test_floats_are_not_rounded_to_integers(self):
        """Rounding a 3.5% incline to 4, or 5.6 km/h to 6, defeats the point."""
        out = et.coerce_set_fields(
            et.CARDIO_MACHINE, {'speed_kmh': 5.64, 'incline_pct': 3.5,
                                'duration_seconds': 10})
        self.assertAlmostEqual(out['speed_kmh'], 5.64)
        self.assertAlmostEqual(out['incline_pct'], 3.5)

    def test_negative_metrics_floor_at_zero(self):
        out = et.coerce_set_fields(
            et.CARDIO_MACHINE, {'speed_kmh': -3, 'incline_pct': -1,
                                'duration_seconds': 10})
        self.assertEqual(out['speed_kmh'], 0.0)
        self.assertEqual(out['incline_pct'], 0.0)

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
        self.assertGreater(counts.get(et.DURATION, 0), 10, counts)
        self.assertGreater(counts.get(et.BODYWEIGHT_REPS, 0), 30, counts)
        # The machines left DURATION for CARDIO_MACHINE, and the carries/sleds
        # left WEIGHT_DURATION for WEIGHT_DISTANCE, so both new types must be
        # non-empty -- an empty one means a rule stopped matching.
        self.assertGreater(counts.get(et.CARDIO_MACHINE, 0), 5, counts)
        self.assertGreater(counts.get(et.WEIGHT_DISTANCE, 0), 5, counts)
        # The bulk are ordinary lifts; if this collapses, a rule is over-matching.
        self.assertGreater(counts.get(et.WEIGHT_REPS, 0), 500, counts)

    def test_no_stretch_is_typed_as_a_lift(self):
        wrong = [n for n in self.names
                 if 'stretch' in n.lower() and et.classify(n) != et.WARMUP]
        self.assertEqual(wrong, [])

    def test_no_treadmill_or_plank_is_typed_as_a_lift(self):
        """Both are timed work; they just land in different timed types now."""
        wrong = [n for n in self.names
                 if 'treadmill' in n.lower()
                 and et.classify(n) != et.CARDIO_MACHINE]
        self.assertEqual(wrong, [])
        wrong = [n for n in self.names
                 if n.lower() == 'plank' and et.classify(n) != et.DURATION]
        self.assertEqual(wrong, [])
