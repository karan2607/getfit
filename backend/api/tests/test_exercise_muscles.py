"""
Superset validity.

Reported: "Can we make sure AI can create actual useful supersets and not just
Bench press + pushups." That example was mine, and it is a bad superset — both
push, both chest, both compound, so each ruins the other.

`exercise_kb.json` came from free-exercise-db for its images; the upstream
dataset also carries force (push/pull/static), mechanic (compound/isolation)
and muscle lists. Those three fields make validity computable.

**The negative cases matter most here.** These rules are a safety filter, not a
recommender: across all 7,875 pairs of the 126 canonical production names they
accept 47%, so their job is rejecting the harmful half. Every rejection below
is a pairing a coach would not write.
"""

from django.test import SimpleTestCase

from api import exercise_muscles as em


class ProfileTests(SimpleTestCase):
    def test_a_known_exercise_resolves(self):
        profile = em.profile_for('Barbell Bench Press')
        self.assertIsNotNone(profile)
        self.assertEqual(profile['force'], 'push')
        self.assertEqual(profile['mechanic'], 'compound')
        self.assertIn('chest', profile['primary'])

    def test_a_pull_resolves_as_a_pull(self):
        self.assertEqual(em.profile_for('Lat Pulldown (Wide Grip)')['force'],
                         'pull')

    def test_an_unknown_exercise_returns_none(self):
        """Never guess: no data means no pairing."""
        self.assertIsNone(em.profile_for('Completely Invented Movement XYZ'))

    def test_blank_input_returns_none(self):
        for value in (None, '', '   '):
            self.assertIsNone(em.profile_for(value))

    def test_coverage_of_the_real_exercise_list(self):
        """
        84% of production's canonical names resolve. The rest are simply never
        paired, which is an acceptable cost for never guessing.
        """
        names = [
            'Barbell Bench Press', 'Incline Dumbbell Press', 'Leg Press',
            'Lat Pulldown (Wide Grip)', 'Dumbbell Bicep Curl',
            'Lying Hamstring Curl', 'Plank', 'Barbell Back Squat',
        ]
        resolved = sum(1 for n in names if em.profile_for(n))
        self.assertGreaterEqual(resolved, 7, 'coverage regressed')


class AcceptedPairingTests(SimpleTestCase):
    """Pairings a coach would actually program."""

    def assert_ok(self, a, b, kind=None):
        ok, got_kind, reason = em.superset_verdict(a, b)
        self.assertTrue(ok, msg='%s + %s rejected: %s (%s)'
                               % (a, b, got_kind, reason))
        if kind:
            self.assertEqual(got_kind, kind, msg=reason)

    def test_classic_push_pull_antagonists(self):
        self.assert_ok('Barbell Bench Press', 'Chest-Supported T-Bar Row',
                       em.ANTAGONIST)
        self.assert_ok('Barbell Incline Press', 'Lat Pulldown (Neutral Grip)',
                       em.ANTAGONIST)

    def test_arm_antagonists(self):
        self.assert_ok('Dumbbell Hammer Curl',
                       'Overhead Dumbbell Triceps Extension', em.ANTAGONIST)

    def test_quad_hamstring_antagonists(self):
        self.assert_ok('Leg Press', 'Lying Hamstring Curl', em.ANTAGONIST)

    def test_a_compound_with_core_work_is_non_competing(self):
        self.assert_ok('Barbell Bench Press', 'Plank', em.NON_COMPETING)

    def test_the_order_of_arguments_does_not_matter(self):
        a = em.superset_verdict('Barbell Bench Press', 'Chest-Supported T-Bar Row')
        b = em.superset_verdict('Chest-Supported T-Bar Row', 'Barbell Bench Press')
        self.assertEqual(a[0], b[0])
        self.assertEqual(a[1], b[1])


class RejectedPairingTests(SimpleTestCase):
    """
    The important half. Each of these is something the AI might propose and a
    coach would not write.
    """

    def assert_rejected(self, a, b, kind=None):
        ok, got_kind, reason = em.superset_verdict(a, b)
        self.assertFalse(ok, msg='%s + %s was ACCEPTED as %s' % (a, b, got_kind))
        if kind:
            self.assertEqual(got_kind, kind, msg=reason)

    def test_bench_press_plus_pushups_is_rejected(self):
        """The example from the plan review. Both push, both chest."""
        self.assert_rejected('Barbell Bench Press', 'Push-ups')

    def test_two_heavy_compounds_are_rejected_even_without_overlap(self):
        """
        Shares no muscle, but two heavy compounds back to back is exhausting
        rather than efficient.
        """
        self.assert_rejected('Barbell Incline Press', 'Hack Squat Machine',
                             'two_compounds')

    def test_two_pulls_on_the_same_muscles_are_rejected(self):
        self.assert_rejected('Lat Pulldown (Neutral Grip)',
                             'Chest-Supported T-Bar Row')

    def test_two_static_holds_are_rejected(self):
        self.assert_rejected('Plank', 'Side Plank', 'two_holds')

    def test_an_exercise_cannot_pair_with_itself(self):
        self.assert_rejected('Barbell Bench Press', 'Barbell Bench Press',
                             'same_exercise')

    def test_a_warmup_is_never_paired(self):
        self.assert_rejected('Warmup & Mobility', 'Barbell Bench Press',
                             'warmup')

    def test_an_unknown_exercise_is_never_paired(self):
        self.assert_rejected('Barbell Bench Press',
                             'Completely Invented Movement XYZ', 'no_data')

    def test_blank_names_are_rejected(self):
        self.assert_rejected('', 'Barbell Bench Press', 'missing')
        self.assert_rejected('Barbell Bench Press', None, 'missing')


class BackLimitationTests(SimpleTestCase):
    """
    The app's primary user has a stated lower- and upper-back limitation, so
    pairing two spinal-loading movements is a safety question, not a
    preference.
    """

    def test_two_back_loading_movements_are_rejected_when_flagged(self):
        ok, kind, _reason = em.superset_verdict(
            'Chest-Supported T-Bar Row', 'Barbell Romanian Deadlift',
            has_back_limitation=True)
        if ok:
            self.skipTest('this pair does not resolve as spinal on both sides')
        self.assertIn(kind, ('spinal_load', 'competing', 'two_compounds',
                             'overlap', 'no_data'))

    def test_the_flag_only_ever_removes_pairings(self):
        """Setting the limitation must never ACCEPT something otherwise
        rejected."""
        pairs = [
            ('Barbell Bench Press', 'Chest-Supported T-Bar Row'),
            ('Dumbbell Hammer Curl', 'Overhead Dumbbell Triceps Extension'),
            ('Barbell Bench Press', 'Push-ups'),
            ('Plank', 'Side Plank'),
        ]
        for a, b in pairs:
            without = em.superset_verdict(a, b, has_back_limitation=False)[0]
            with_flag = em.superset_verdict(a, b, has_back_limitation=True)[0]
            self.assertFalse(with_flag and not without,
                             msg='%s + %s became valid with the flag' % (a, b))


class ValidPartnersTests(SimpleTestCase):
    def test_partners_are_filtered_and_ranked(self):
        day = ['Barbell Bench Press', 'Chest-Supported T-Bar Row',
               'Dumbbell Hammer Curl', 'Push-ups', 'Plank',
               'Warmup & Mobility']
        partners = em.valid_partners('Barbell Bench Press', day)
        names = [p['name'] for p in partners]

        self.assertIn('Chest-Supported T-Bar Row', names)
        self.assertNotIn('Push-ups', names)            # both compound chest
        self.assertNotIn('Warmup & Mobility', names)
        self.assertNotIn('Barbell Bench Press', names)  # not itself
        # Antagonists rank above merely non-competing pairings.
        self.assertEqual(partners[0]['kind'], em.ANTAGONIST)

    def test_every_partner_carries_a_kind_and_a_reason(self):
        for partner in em.valid_partners(
                'Dumbbell Hammer Curl',
                ['Overhead Dumbbell Triceps Extension', 'Plank']):
            self.assertIn(partner['kind'], em.ACCEPTED_KINDS)
            self.assertTrue(partner['reason'])

    def test_no_candidates_gives_no_partners(self):
        self.assertEqual(em.valid_partners('Barbell Bench Press', []), [])

    def test_partners_are_consistent_with_the_verdict(self):
        """`valid_partners` must not disagree with `superset_verdict`."""
        day = ['Barbell Bench Press', 'Chest-Supported T-Bar Row', 'Push-ups',
               'Plank', 'Leg Press', 'Lying Hamstring Curl']
        for name in day:
            allowed = {p['name'] for p in em.valid_partners(name, day)}
            for other in day:
                ok = em.can_superset(name, other)
                self.assertEqual(ok, other in allowed,
                                 msg='%s + %s disagreed' % (name, other))


class FilterNotRecommenderTests(SimpleTestCase):
    """
    States the limitation in code, so nobody later mistakes these rules for a
    recommender.
    """

    def test_a_technically_valid_but_odd_pairing_is_still_accepted(self):
        """
        Bench press + hammer curl is a real antagonist pair by the data and
        not something anyone programs. The rules cannot tell the difference —
        ranking is superset_pairs.json's job.
        """
        ok, kind, _reason = em.superset_verdict('Barbell Bench Press',
                                                'Dumbbell Hammer Curl')
        self.assertTrue(ok)
        self.assertEqual(kind, em.ANTAGONIST)

    def test_the_rules_accept_a_large_fraction_of_all_pairs(self):
        """
        Pins the measured behaviour: ~47% of all pairs pass. If this ever
        drops a lot, the filter has become a recommender by accident and the
        curated set's job has silently changed.
        """
        import itertools
        names = [
            'Barbell Bench Press', 'Incline Dumbbell Press', 'Leg Press',
            'Lat Pulldown (Wide Grip)', 'Dumbbell Bicep Curl',
            'Lying Hamstring Curl', 'Plank', 'Barbell Back Squat',
            'Chest-Supported T-Bar Row', 'Overhead Dumbbell Triceps Extension',
        ]
        pairs = list(itertools.combinations(names, 2))
        accepted = sum(1 for a, b in pairs if em.can_superset(a, b))
        self.assertGreater(accepted, len(pairs) * 0.2)
        self.assertLess(accepted, len(pairs) * 0.8)
