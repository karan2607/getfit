"""
A prescribed rep range has TWO thresholds.

Reported: "Maintain 130.0lb and focus on engaging your back muscles effectively
- not right advice if I still need to hit 10-12 right?"

`_target_reps_from('10-12')` returned max() = 12, so `hit_all` demanded 12 reps
and the production Lat Pulldown sets of **10 reps** were scored as failures even
though 10 is inside the prescribed range. The advice therefore could not
distinguish "back off" from "you're in the range, add reps".

    >= high  -> topped the range, add weight
    >= low   -> in the range, hold weight and add reps
    <  low   -> below the range, reduce weight
"""

import datetime as dt

from rest_framework.test import APITestCase

from api import views, exercise_types as et

KG_PER_LB = 0.453592


def session(day, weight_lb, reps_list, month=10):
    return [
        {
            'workout_session__started_at': dt.datetime(2026, month, day, 10, 0),
            'set_number': i + 1,
            'weight_kg': weight_lb * KG_PER_LB,
            'reps_completed': reps,
            'duration_seconds': None,
        }
        for i, reps in enumerate(reps_list)
    ]


class TargetRepRangeTests(APITestCase):
    def test_parses_a_range(self):
        self.assertEqual(views._target_rep_range('10-12'), (10, 12))
        self.assertEqual(views._target_rep_range('8-12'), (8, 12))
        self.assertEqual(views._target_rep_range('15-20'), (15, 20))

    def test_single_number_is_a_degenerate_range(self):
        self.assertEqual(views._target_rep_range('15'), (15, 15))

    def test_time_and_distance_prescriptions_have_no_rep_target(self):
        for raw in ('30 sec', '5-10 min', '40 yards', '2 minutes', '1 mile'):
            self.assertEqual(views._target_rep_range(raw), (None, None), msg=raw)

    def test_unparseable_has_no_target(self):
        for raw in ('AMRAP', 'to failure', '', None):
            self.assertEqual(views._target_rep_range(raw), (None, None), msg=raw)

    def test_legacy_helper_still_returns_the_high_end(self):
        self.assertEqual(views._target_reps_from('10-12'), 12)
        self.assertIsNone(views._target_reps_from('30 sec'))


class RangeVerdictTests(APITestCase):
    """Production Lat Pulldown history: 100 lb, target 10-12."""

    def analyze(self, logs, target='10-12'):
        return views._analyze_progression(
            logs,
            target_reps=views._target_rep_range(target),
            measurement_type=et.WEIGHT_REPS,
            unit='lb',
        )

    def test_topping_the_range_twice_adds_weight(self):
        logs = session(15, 100, [12, 12, 12]) + session(9, 100, [12, 12, 12])
        res = self.analyze(logs)
        self.assertEqual(res['direction'], 'increase')
        self.assertIn('INCREASE WEIGHT', res['summary'])

    def test_inside_the_range_is_not_a_failure(self):
        """
        THE reported bug: 10 reps on a 10-12 prescription used to count as a
        miss. It should hold the weight and chase reps.
        """
        logs = session(15, 100, [10, 10, 10]) + session(9, 100, [10, 10, 10])
        res = self.analyze(logs)
        self.assertEqual(res['direction'], 'maintain')
        self.assertIn('inside the 10-12 range', res['summary'])
        self.assertIn('ADD REPS', res['summary'])
        self.assertNotIn('DECREASE', res['summary'])

    def test_below_the_range_decreases(self):
        logs = session(15, 130, [7, 7, 6]) + session(9, 100, [12, 12, 12])
        res = self.analyze(logs)
        self.assertEqual(res['direction'], 'decrease')
        self.assertIn('DECREASE WEIGHT', res['summary'])

    def test_below_the_range_names_the_proven_weight(self):
        logs = session(15, 130, [7, 7, 6]) + session(9, 100, [12, 12, 12])
        res = self.analyze(logs)
        self.assertEqual(res['best_at_target'], 100.0)
        self.assertIn('100.0lb', res['summary'])
        self.assertIn('go back to', res['summary'])

    def test_a_session_inside_the_range_counts_as_proven(self):
        """
        10 reps at 110 lb is inside 10-12, so it is a valid weight to return to.
        Under the old max()-only logic it was discarded as a failure.
        """
        logs = (session(15, 130, [7, 6, 6])
                + session(9, 110, [10, 10, 10]))
        res = self.analyze(logs)
        self.assertEqual(res['direction'], 'decrease')
        self.assertEqual(res['best_at_target'], 110.0)
        self.assertIn('110.0lb', res['summary'])

    def test_mixed_sets_use_the_worst_set(self):
        """One set below the range means the range was not met."""
        logs = session(15, 100, [12, 11, 8]) + session(9, 100, [12, 12, 12])
        res = self.analyze(logs)
        self.assertNotEqual(res['direction'], 'increase')

    def test_single_number_target_behaves_sanely(self):
        logs = session(15, 100, [15, 15, 15]) + session(9, 100, [15, 15, 15])
        res = self.analyze(logs, target='15')
        self.assertEqual(res['direction'], 'increase')
        self.assertIn('15+ reps', res['summary'])

    def test_summary_shows_the_range_not_just_its_max(self):
        logs = session(15, 130, [7, 6, 6]) + session(9, 100, [12, 12, 12])
        res = self.analyze(logs)
        self.assertIn('target 10-12', res['summary'])

    def test_unit_is_respected(self):
        logs = session(15, 130, [7, 6, 6]) + session(9, 100, [12, 12, 12])
        res = self.analyze(logs)
        self.assertNotIn('kg', res['summary'])
        res_kg = views._analyze_progression(
            logs, target_reps=(10, 12), measurement_type=et.WEIGHT_REPS, unit='kg')
        self.assertIn('kg', res_kg['summary'])


class NoRegressionTests(APITestCase):
    """The 15-20 calf-raise case from the previous fix must still behave."""

    def test_calf_raise_over_jump_still_decreases(self):
        logs = (session(7, 90, [9, 8, 8])
                + session(1, 45, [20, 20, 20])
                + session(19, 50, [20, 20, 20], month=8))
        res = views._analyze_progression(
            logs, target_reps=views._target_rep_range('15-20'),
            measurement_type=et.WEIGHT_REPS, unit='lb')
        self.assertEqual(res['direction'], 'decrease')
        self.assertEqual(res['best_at_target'], 50.0)
        self.assertIn('go back to about 50.0lb', res['summary'])

    def test_duration_work_unaffected(self):
        logs = [
            {'workout_session__started_at': dt.datetime(2026, 10, d, 10, 0),
             'set_number': 1, 'weight_kg': None, 'reps_completed': None,
             'duration_seconds': secs}
            for d, secs in ((7, 60), (6, 60), (5, 60))
        ]
        res = views._analyze_progression(
            logs, measurement_type=et.DURATION, unit='lb')
        self.assertEqual(res['direction'], 'increase')

    def test_bodyweight_work_unaffected(self):
        logs = ([dict(d, reps_completed=18) for d in session(15, 0, [18, 18, 18])]
                + [dict(d, reps_completed=15) for d in session(9, 0, [15, 15, 15])])
        for row in logs:
            row['weight_kg'] = None
        res = views._analyze_progression(
            logs, measurement_type=et.BODYWEIGHT_REPS, unit='lb')
        self.assertEqual(res['direction'], 'increase')
