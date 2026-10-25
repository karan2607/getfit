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
        self.assertIn('for 2 sessions', res['summary'])

    def test_topping_the_range_once_also_adds_weight(self):
        """
        Double progression: completing every set at the top of the range earns
        the increase. Requiring TWO consecutive such sessions left a real user
        stuck at 100lb since July, repeatedly hitting 4x12 on an 8-12 target and
        never once being told to add load, because a single 10-rep session in
        between reset the streak.
        """
        logs = session(15, 100, [12, 12, 12, 12]) + session(8, 100, [10, 10, 10, 10])
        res = self.analyze(logs)
        self.assertEqual(res['direction'], 'increase')
        self.assertNotIn('for 2 sessions', res['summary'])

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


class RepDescriptionTests(APITestCase):
    """
    Reported: "14 reps at 115.0lb — inside the 12-15 range → HOLD WEIGHT...
    I think 14 might be avg of last session. If so, it should mention that."

    It was the mean, rendered as a bare figure, so 15/14/13 read as though
    every set hit 14.
    """

    def test_uniform_sets_read_as_a_single_number(self):
        self.assertEqual(views._describe_reps([14, 14, 14])[0], '14 reps')

    def test_varying_sets_name_the_actual_numbers(self):
        description, average = views._describe_reps([15, 14, 13])
        self.assertEqual(description, '15/14/13 reps (avg 14)')
        self.assertEqual(average, 14.0)

    def test_the_average_is_still_returned_for_the_threshold_logic(self):
        """The decrease threshold compares against the mean, not the string."""
        self.assertEqual(views._describe_reps([12, 6])[1], 9.0)

    def test_incomplete_sets_are_ignored(self):
        self.assertEqual(views._describe_reps([15, 14, 13, 0])[0],
                         '15/14/13 reps (avg 14)')

    def test_a_single_set_reads_plainly(self):
        self.assertEqual(views._describe_reps([12])[0], '12 reps')

    def test_no_logged_reps_does_not_raise(self):
        self.assertEqual(views._describe_reps([])[0], '0 reps')
        self.assertEqual(views._describe_reps([0, 0])[0], '0 reps')

    def test_the_summary_says_avg_when_sets_differ(self):
        """End to end through _analyze_progression."""
        logs = []
        # The NEWEST session is the one described, so the varying set goes last.
        for day, reps in ((1, [13, 13, 13]), (8, [15, 14, 13])):
            for number, rep in enumerate(reps, start=1):
                logs.append({
                    'workout_session__started_at': dt.datetime(2026, 10, day, 10),
                    'set_number': number, 'weight_kg': 52.16,
                    'reps_completed': rep, 'duration_seconds': None,
                })
        result = views._analyze_progression(
            logs, target_reps=(12, 15), measurement_type='weight_reps',
            unit='lb')
        self.assertIn('avg', result['summary'])
        self.assertIn('15/14/13', result['summary'])
