"""
Tests for the plan eval harness.

The harness exists because every quality defect so far was found by a human
using the app. So the bar for these tests is specific: **each rule must be shown
to catch a defect that actually reached production.** A rule that cannot catch a
known bug is not worth having, and a rule that fires on legitimate programming is
worse than no rule at all.

Both directions are therefore tested for every rule — a plan that violates it,
and a plan that looks similar but is legitimate.
"""

import glob
import json
import os

from django.test import SimpleTestCase

from api import exercise_types as et
from api.evals import score_plan
from api.evals.rules import RULES
from api.evals.scorer import format_scorecard
from api.management.commands.eval_plans import FIXTURE_DIR, load_fixtures


def day(day_number=1, name='Push', exercises=(), is_rest_day=False):
    return {'day_number': day_number, 'name': name, 'focus': name,
            'is_rest_day': is_rest_day, 'exercises': list(exercises)}


def ex(name, sets=3, reps='8-12', measurement_type=et.WEIGHT_REPS, notes='',
       **extra):
    out = {'name': name, 'sets': sets, 'reps': reps,
           'measurement_type': measurement_type, 'notes': notes,
           'rest_seconds': 90}
    out.update(extra)
    return out


WARMUP = ex('Warmup & Mobility', sets=1, reps='5-10 min',
            measurement_type=et.WARMUP)


def plan(*weeks, **kw):
    return {'title': kw.get('title', 'Test Plan'), 'weeks': list(weeks)}


def solid_day(day_number=1, name='Push'):
    """A day that should pass every rule."""
    return day(day_number, name, [
        WARMUP,
        ex('Barbell Bench Press', 4, '6-8'),
        ex('Incline Dumbbell Press', 3, '8-12'),
        ex('Cable Crossover', 3, '12-15'),
        ex('Triceps Pushdown', 3, '12-15'),
        ex('Plank', 3, '45 sec', et.DURATION),
    ])


def only(rule_name):
    return tuple(r for r in RULES if r[0] == rule_name)


class RuleBehaviourTests(SimpleTestCase):
    """One violating case and one legitimate case per rule."""

    def score(self, plan_json, rule, context=None):
        return score_plan(plan_json, context or {}, rules=only(rule))

    def assert_flags(self, plan_json, rule, context=None):
        card = self.score(plan_json, rule, context)
        self.assertTrue(card.findings,
                        msg='%s should have flagged this plan' % rule)
        return card

    def assert_clean(self, plan_json, rule, context=None):
        card = self.score(plan_json, rule, context)
        self.assertFalse(card.findings,
                         msg='%s wrongly flagged: %s'
                             % (rule, [f.detail for f in card.findings]))
        return card

    # -- prescription_matches_type -------------------------------------------

    def test_burpees_prescribed_in_minutes_is_flagged(self):
        """
        Shipped to production: "Metabolic Finisher: Burpees" typed
        bodyweight_reps with reps="10 min". The card said minutes, the input
        asked for reps, and the user was left guessing.
        """
        card = self.assert_flags(plan([day(1, 'Full', [
            WARMUP, ex('Metabolic Finisher: Burpees', 3, '10-15 min',
                       et.BODYWEIGHT_REPS)])]), 'prescription_matches_type')
        self.assertIn('logged in reps', card.findings[0].detail)

    def test_carry_prescribed_in_yards_is_flagged(self):
        """Also shipped: Farmer's Carry at "40 yards", logged in seconds."""
        self.assert_flags(plan([day(1, 'Full', [
            WARMUP, ex("Metabolic Finisher: Farmer's Carry", 3, '40 yards',
                       et.WEIGHT_DURATION)])]), 'prescription_matches_type')

    def test_amrap_is_accepted_for_rep_work(self):
        self.assert_clean(plan([day(1, 'Full', [
            WARMUP, ex('Pull-ups', 3, 'AMRAP', et.BODYWEIGHT_REPS)])]),
            'prescription_matches_type')

    def test_a_plank_in_seconds_is_accepted(self):
        self.assert_clean(plan([day(1, 'Core', [
            WARMUP, ex('Plank', 3, '45 sec', et.DURATION)])]),
            'prescription_matches_type')

    # -- measurement_type_agrees --------------------------------------------

    def test_a_plank_claimed_as_weight_reps_is_flagged(self):
        self.assert_flags(plan([day(1, 'Core', [
            WARMUP, ex('Plank', 3, '45 sec', et.WEIGHT_REPS)])]),
            'measurement_type_agrees')

    def test_sled_push_claimed_as_duration_is_flagged(self):
        """The most recent feedback: a sled is loaded, so seconds alone is
        half the log."""
        self.assert_flags(plan([day(1, 'Full', [
            WARMUP, ex('Sled Push', 3, '30 sec', et.DURATION)])]),
            'measurement_type_agrees')

    def test_an_unopinionated_name_is_not_second_guessed(self):
        """
        `has_opinion` exists so the classifier's silence is distinguishable
        from its verdict. A name the rules do not recognise must not be scored.
        """
        self.assert_clean(plan([day(1, 'Full', [
            WARMUP, ex('Jefferson Curl Variation', 3, '8-12')])]),
            'measurement_type_agrees')

    # -- warmup_first --------------------------------------------------------

    def test_a_day_starting_on_a_barbell_lift_is_flagged(self):
        self.assert_flags(plan([day(1, 'Push', [
            ex('Barbell Bench Press', 4, '6-8'),
            ex('Cable Crossover', 3, '12-15')])]), 'warmup_first')

    def test_a_day_starting_with_a_warmup_block_passes(self):
        self.assert_clean(plan([solid_day()]), 'warmup_first')

    def test_rest_days_are_not_required_to_have_a_warmup(self):
        card = self.score(plan([day(1, 'Rest', [], is_rest_day=True)]),
                          'warmup_first')
        self.assertEqual(card.by_rule()['warmup_first'].checked, 0)

    # -- session_volume ------------------------------------------------------

    def test_a_thirty_exercise_session_is_flagged(self):
        self.assert_flags(plan([day(1, 'Full', [WARMUP] + [
            ex('Exercise %d' % i, 3, '8-12') for i in range(30)])]),
            'session_volume')

    def test_a_dedicated_cardio_day_is_not_flagged(self):
        """
        The rule's own regression. A beginner fat-loss plan alternates lifting
        with conditioning days of warmup + one 25-minute machine block. The
        first version of this rule flagged all 24 such days in a real plan as
        "only 2 exercises", which was the rule being wrong, not the plan.
        """
        self.assert_clean(plan([day(2, 'Cardio', [
            WARMUP, ex('Incline Treadmill Walk', 1, '25-30 min', et.DURATION)])]),
            'session_volume')

    def test_a_one_exercise_resistance_day_is_still_flagged(self):
        """The conditioning exemption must not swallow a genuinely empty day."""
        self.assert_flags(plan([day(1, 'Push', [
            WARMUP, ex('Barbell Bench Press', 1, '6-8')])]), 'session_volume')

    def test_a_normal_session_passes(self):
        self.assert_clean(plan([solid_day()]), 'session_volume')

    # -- no_banned_names -----------------------------------------------------

    def test_the_crossover_variant_the_prompt_forbids_is_flagged(self):
        """
        The prompt says "'Mid-to-High' does not exist — never use it", and
        production logged data contains it anyway. Evidence that prohibitions
        in prose need a gate.
        """
        self.assert_flags(plan([day(1, 'Push', [
            WARMUP, ex('Cable Crossover (Mid-to-High)', 3, '12-15')])]),
            'no_banned_names')

    def test_the_valid_crossover_variants_pass(self):
        self.assert_clean(plan([day(1, 'Push', [
            WARMUP,
            ex('High-to-Low Cable Crossover', 3, '12-15'),
            ex('Low-to-High Cable Crossover', 3, '12-15'),
            ex('Standing Cable Crossover', 3, '12-15')])]), 'no_banned_names')

    # -- rep_scheme_conventional --------------------------------------------

    def test_an_arbitrary_rep_count_is_flagged(self):
        """Reported: "It suggests doing 11, 13, 16. That's not the gym way"."""
        card = self.assert_flags(plan([day(1, 'Push', [
            WARMUP, ex('Barbell Bench Press', 3, '13')])]),
            'rep_scheme_conventional')
        self.assertIn('12-15', card.findings[0].detail)

    def test_conventional_schemes_pass(self):
        self.assert_clean(plan([day(1, 'Push', [
            WARMUP,
            ex('A', 3, '8-12'), ex('B', 3, '5'), ex('C', 3, 'AMRAP'),
            ex('D', 3, '10-12 per leg')])]), 'rep_scheme_conventional')

    # -- notes_without_placeholder_weights ----------------------------------

    def test_a_placeholder_weight_in_notes_is_flagged(self):
        self.assert_flags(plan([day(1, 'Push', [
            WARMUP, ex('Barbell Bench Press', 3, '6-8',
                       notes='Start Xlb. Pause at the chest.')])]),
            'notes_without_placeholder_weights')

    def test_set_notation_in_notes_is_not_mistaken_for_a_placeholder(self):
        """"3 x 12" must survive -- the regex is case-sensitive for this."""
        self.assert_clean(plan([day(1, 'Push', [
            WARMUP, ex('Barbell Bench Press', 3, '6-8',
                       notes='Work up to 3 x 12 with a 2s pause.')])]),
            'notes_without_placeholder_weights')

    # -- phase_variation -----------------------------------------------------

    def test_eight_identical_weeks_are_flagged(self):
        """Prompt: "If weeks 1 and 5 have identical exercise lists, the plan
        is wrong. This is NON-NEGOTIABLE"."""
        self.assert_flags(plan(*[[solid_day()] for _ in range(8)]),
                          'phase_variation')

    def test_a_plan_that_changes_lineup_at_week_five_passes(self):
        early = [[solid_day()] for _ in range(4)]
        late = [[day(1, 'Push', [
            WARMUP, ex('Barbell Overhead Press', 4, '5'),
            ex('Weighted Dips', 3, '6-8'), ex('Hack Squat Machine', 3, '8-10'),
            ex('Plank', 3, '60 sec', et.DURATION)])] for _ in range(4)]
        self.assert_clean(plan(*(early + late)), 'phase_variation')

    def test_a_four_week_plan_is_not_expected_to_vary(self):
        card = self.score(plan(*[[solid_day()] for _ in range(4)]),
                          'phase_variation')
        self.assertEqual(card.by_rule()['phase_variation'].checked, 0)

    # -- injury_constraints_respected ---------------------------------------

    def test_a_squat_for_a_stated_knee_injury_is_flagged(self):
        self.assert_flags(
            plan([day(1, 'Legs', [WARMUP, ex('Barbell Back Squat', 4, '6-8')])]),
            'injury_constraints_respected',
            context={'injuries': 'Left knee pain under load'})

    def test_the_prompts_own_substitution_passes(self):
        self.assert_clean(
            plan([day(1, 'Legs', [
                WARMUP, ex('Leg Press', 4, '10-12'),
                ex('Seated Hamstring Curl', 3, '12-15'),
                ex('Step-ups', 3, '10-12 per leg')])]),
            'injury_constraints_respected',
            context={'injuries': 'Left knee pain under load'})

    def test_an_explicitly_named_movement_is_honoured(self):
        """
        The real profile notes say "avoid ... overhead press", which no
        body-part trigger catches.
        """
        self.assert_flags(
            plan([day(1, 'Push', [
                WARMUP, ex('Barbell Overhead Press', 4, '5')])]),
            'injury_constraints_respected',
            context={'injuries': 'avoid exercises that strain the upper back '
                                 'like overhead press'})

    def test_a_romanian_deadlift_is_allowed_for_a_back_issue(self):
        """
        The prompt PRESCRIBES the RDL as the substitution for a lower-back
        issue. A rule that banned anything containing "deadlift" would
        contradict the prompt's own injury protocol — and would fire on the
        live plan, which uses exactly this substitution.
        """
        self.assert_clean(
            plan([day(1, 'Legs', [
                WARMUP, ex('Barbell Romanian Deadlift', 3, '8-10'),
                ex('Trap Bar Carry', 3, '40 sec', et.WEIGHT_DURATION)])]),
            'injury_constraints_respected',
            context={'injuries': 'lower back issue, avoid heavy deadlifts'})

    def test_no_stated_injury_means_the_rule_does_not_apply(self):
        card = self.score(
            plan([day(1, 'Legs', [WARMUP, ex('Barbell Back Squat', 4, '6-8')])]),
            'injury_constraints_respected', context={'injuries': ''})
        self.assertEqual(card.by_rule()['injury_constraints_respected'].checked, 0)

    # -- superset_groups_valid ----------------------------------------------

    def test_a_lone_superset_member_is_flagged(self):
        self.assert_flags(plan([day(1, 'Push', [
            WARMUP, ex('Barbell Bench Press', 3, '8-12', superset_group=1),
            ex('Cable Crossover', 3, '12-15')])]), 'superset_groups_valid')

    def test_non_adjacent_superset_members_are_flagged(self):
        self.assert_flags(plan([day(1, 'Push', [
            WARMUP, ex('Barbell Bench Press', 3, '8-12', superset_group=1),
            ex('Leg Press', 3, '10-12'),
            ex('Push-ups', 3, '10-15', et.BODYWEIGHT_REPS, superset_group=1)])]),
            'superset_groups_valid')

    def test_a_valid_pair_passes(self):
        """The shape the user asked for: "chest press + 10 pushups"."""
        self.assert_clean(plan([day(1, 'Push', [
            WARMUP,
            ex('Barbell Bench Press', 3, '8-12', superset_group=1),
            ex('Push-ups', 3, '10-15', et.BODYWEIGHT_REPS, superset_group=1),
            ex('Cable Crossover', 3, '12-15')])]), 'superset_groups_valid')

    def test_a_plan_without_groups_does_not_apply(self):
        card = self.score(plan([solid_day()]), 'superset_groups_valid')
        self.assertEqual(card.by_rule()['superset_groups_valid'].checked, 0)


class TraversalTests(SimpleTestCase):
    """Both accepted plan shapes, and malformed input."""

    def test_the_single_week_days_shape_is_scored(self):
        card = score_plan({'title': 'T', 'days': [solid_day()]})
        self.assertIsNotNone(card.score)
        self.assertFalse(card.findings)

    def test_the_multi_week_weeks_shape_is_scored(self):
        card = score_plan(plan([solid_day()], [solid_day()]))
        self.assertIsNotNone(card.score)

    def test_junk_input_does_not_raise(self):
        for junk in (None, [], 'plan', 42, {}, {'weeks': 'nope'},
                     {'weeks': [None]}, {'days': [None]},
                     {'weeks': [[{'exercises': 'no'}]]},
                     {'weeks': [[{'exercises': [None, 7]}]]}):
            card = score_plan(junk, label=repr(junk))
            self.assertFalse(
                [f for f in card.findings if 'rule raised' in f.detail],
                msg='a rule raised on %r' % (junk,))

    def test_missing_keys_are_tolerated(self):
        card = score_plan({'weeks': [[{'day_number': 1, 'exercises': [
            {'name': 'Bench Press'}, {}]}]]})
        self.assertFalse([f for f in card.findings if 'rule raised' in f.detail])


class ScoringSemanticsTests(SimpleTestCase):
    def test_a_clean_plan_scores_one(self):
        card = score_plan(plan([solid_day()]))
        self.assertEqual(card.score, 1.0)
        self.assertTrue(card.passed)

    def test_inapplicable_rules_are_excluded_from_the_mean(self):
        """
        A plan for an uninjured user must not score higher merely because the
        injury rule had nothing to look at. Coverage going down must not make
        the number go up.
        """
        card = score_plan(plan([solid_day()]))
        by_rule = card.by_rule()
        self.assertEqual(by_rule['injury_constraints_respected'].checked, 0)
        self.assertIsNone(by_rule['injury_constraints_respected'].score)
        self.assertEqual(card.score, 1.0)

    def test_score_is_the_fraction_of_checks_that_passed(self):
        # Four exercises, one with an arbitrary rep count.
        card = score_plan(plan([day(1, 'Push', [
            WARMUP, ex('A', 3, '8-12'), ex('B', 3, '8-12'),
            ex('C', 3, '13')])]), rules=only('rep_scheme_conventional'))
        result = card.by_rule()['rep_scheme_conventional']
        self.assertEqual((result.checked, result.failed), (4, 1))
        self.assertAlmostEqual(result.score, 0.75)

    def test_weighting_makes_a_blocking_defect_cost_more(self):
        """An unenterable prescription must outweigh an odd-looking rep count."""
        bad_prescription = score_plan(plan([day(1, 'Full', [
            WARMUP, ex('Metabolic Finisher: Burpees', 3, '10-15 min',
                       et.BODYWEIGHT_REPS)])]))
        odd_reps = score_plan(plan([day(1, 'Push', [
            WARMUP, ex('Barbell Bench Press', 3, '13')])]))
        self.assertLess(bad_prescription.score, odd_reps.score)

    def test_a_rule_that_raises_is_reported_not_fatal(self):
        def boom(plan_json, context, find):
            raise RuntimeError('kaboom')

        card = score_plan(plan([solid_day()]),
                          rules=(('boom', boom, 1.0),) + RULES)
        detail = card.by_rule()['boom'].findings[0].detail
        self.assertIn('kaboom', detail)
        self.assertIn('RuntimeError', detail)
        # The other rules still ran.
        self.assertEqual(card.by_rule()['warmup_first'].checked, 1)

    def test_empty_plan_scores_none_rather_than_one(self):
        card = score_plan({'title': 'Empty', 'weeks': []})
        self.assertIsNone(card.score)

    def test_as_dict_round_trips_through_json(self):
        card = score_plan(plan([solid_day()]))
        json.dumps(card.as_dict())        # must not raise

    def test_format_scorecard_renders(self):
        text = format_scorecard(score_plan(plan([solid_day()]), label='x'))
        self.assertIn('x', text)
        self.assertIn('warmup_first', text)


class GoldenSetTests(SimpleTestCase):
    """
    Regression lock on the checked-in fixtures, which are real generated plans
    captured from production spanning 2026-05 to 2026-10.

    Scores are asserted as floors, not equalities: a change that makes a plan
    score BETTER should not fail the suite, but a regression must.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cases = load_fixtures()
        cls.cards = {label: score_plan(p, c, label=label)
                     for label, p, c in cls.cases}

    def test_the_fixtures_are_present_and_well_formed(self):
        self.assertEqual(len(self.cases), 8)
        for path in glob.glob(os.path.join(FIXTURE_DIR, '*.json')):
            with open(path) as handle:
                doc = json.load(handle)
            self.assertIn('plan', doc)
            self.assertIn('context', doc)
            self.assertIn('label', doc)

    def test_the_two_most_recent_plans_are_clean(self):
        """
        plan07 and plan08 were generated after this year's prompt and
        classifier fixes landed. They are the proof the fixes worked, and the
        guard against regressing them.
        """
        for label in ('plan07', 'plan08'):
            card = self.cards[label]
            self.assertEqual(card.score, 1.0,
                             msg='%s regressed: %s' % (
                                 label, [f.detail for f in card.findings][:5]))

    def test_the_harness_rediscovers_the_burpees_defect(self):
        """Filed as feedback and fixed in the prompt; the eval must see it."""
        details = [f.detail for f in self.cards['plan05'].findings]
        self.assertTrue(
            any('logged in reps' in d for d in details),
            msg='expected a prescription mismatch in plan05, got %s' % details[:5])

    def test_the_harness_rediscovers_the_banned_crossover_name(self):
        found = any(
            'banned name' in f.detail
            for card in self.cards.values() for f in card.findings)
        self.assertTrue(found)

    def test_older_plans_score_worse_than_newer_ones(self):
        """
        The headline claim this harness is meant to support: plan quality has
        measurably improved. If this ever inverts, either the rules or the
        generator changed in a way worth knowing about.
        """
        old = min(self.cards[l].score for l in ('plan01', 'plan02', 'plan04'))
        new = min(self.cards[l].score for l in ('plan07', 'plan08'))
        self.assertLess(old, new)

    def test_mean_score_floor(self):
        scores = [c.score for c in self.cards.values() if c.score is not None]
        self.assertGreaterEqual(sum(scores) / len(scores), 0.95)

    def test_every_rule_is_exercised_by_the_golden_set(self):
        """
        A rule that never gets to check anything across eight real plans is
        either dead or misspecified. Superset and canonical-name rules are
        exempt until WS3/WS4 land.
        """
        pending = {'superset_groups_valid', 'canonical_exercise_names'}
        for name, _, _ in RULES:
            if name in pending:
                continue
            checked = sum(c.by_rule()[name].checked for c in self.cards.values())
            self.assertGreater(checked, 0, msg='%s checked nothing' % name)
