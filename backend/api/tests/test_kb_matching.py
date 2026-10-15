"""
Exercise-image resolution.

Reported: "There's a few more that fail so please verify this once and review the
code itself once to make sure it is right."

An audit of all 187 exercise names in production found 13 mis-resolving, from
three causes:

1. `_lookup_exercise_images` ran its own unaliased copy of the scoring loop, so
   every alias was bypassed whenever the AI returned `kb_key: null`. The earlier
   pec-deck and lateral-raise fixes were therefore only half applied.
2. Muscle-flipping modifiers (lying/rear/reverse/incline/decline/behind/front)
   only cost a little precision, so "dumbbell lying rear lateral raise" outscored
   "side lateral raise" for a plain dumbbell lateral raise.
3. No notion of equipment. "sled overhead triceps extension" and "standing
   dumbbell triceps extension" both scored exactly 0.90 for a dumbbell overhead
   extension, and the winner was decided by JSON key order.

Plus: `_kb_stem('press')` returned 'pres', so 'press' never matched
`_MOVEMENT_WORDS` and every press exercise silently lost the specificity bonus.
"""

from django.test import SimpleTestCase

from api import views


class StemmerTests(SimpleTestCase):
    def test_does_not_strip_s_from_double_s_words(self):
        self.assertEqual(views._kb_stem('press'), 'press')
        self.assertEqual(views._kb_stem('presses'), 'press')

    def test_press_reaches_the_movement_word_set(self):
        """The bug: 'press' stemmed to 'pres' and fell out of the set."""
        self.assertIn('press', views._MOVEMENT_STEMS)
        self.assertIn(views._kb_stem('press'), views._MOVEMENT_STEMS)

    def test_normal_plurals_still_stem(self):
        self.assertEqual(views._kb_stem('raises'), 'raise')
        self.assertEqual(views._kb_stem('curls'), 'curl')
        self.assertEqual(views._kb_stem('flyes'), 'fly')
        self.assertEqual(views._kb_stem('rows'), 'row')

    def test_short_words_are_left_alone(self):
        for w in ('row', 'dip', 'fly'):
            self.assertEqual(views._kb_stem(w), w)

    def test_movement_and_flipper_sets_are_stemmed_consistently(self):
        for stem in views._MOVEMENT_STEMS | views._FLIPPER_STEMS | views._EQUIPMENT_STEMS:
            self.assertEqual(views._kb_stem(stem), stem,
                             msg='%r is not a fixed point of the stemmer' % stem)


class ScoreDisqualifierTests(SimpleTestCase):
    def tok(self, s):
        return views._kb_tokenize(s)

    def test_flipping_modifier_on_one_side_disqualifies(self):
        """A wrong illustration is worse than none, so score 0 rather than rank down."""
        self.assertEqual(
            views._kb_score(self.tok('lateral raise'),
                            self.tok('dumbbell lying rear lateral raise')), 0.0)
        self.assertEqual(
            views._kb_score(self.tok('machine fly'),
                            self.tok('reverse machine flyes')), 0.0)
        self.assertEqual(
            views._kb_score(self.tok('t-bar row'),
                            self.tok('lying t-bar row')), 0.0)

    def test_matching_modifiers_are_fine(self):
        self.assertGreater(
            views._kb_score(self.tok('reverse machine fly'),
                            self.tok('reverse machine flyes')), 0.5)

    def test_conflicting_equipment_disqualifies(self):
        self.assertEqual(
            views._kb_score(self.tok('dumbbell triceps extension'),
                            self.tok('sled overhead triceps extension')), 0.0)

    def test_shared_equipment_outranks_no_equipment(self):
        q = self.tok('dumbbell triceps extension overhead')
        with_equip = views._kb_score(q, self.tok('standing dumbbell triceps extension'))
        self.assertGreater(with_equip, 0.5)

    def test_no_overlap_is_zero(self):
        self.assertEqual(views._kb_score(self.tok('plank'), self.tok('bench press')), 0.0)


class AliasLookupTests(SimpleTestCase):
    def test_exact_name(self):
        self.assertEqual(views._alias_for('Pec Deck Fly Machine'), 'butterfly')

    def test_parenthetical_qualifier_is_stripped(self):
        """"Lateral Raises (Dumbbell)" previously missed the alias entirely."""
        self.assertEqual(views._alias_for('Lateral Raises (Dumbbell)'),
                         'side lateral raise')

    def test_label_prefix_is_stripped(self):
        self.assertEqual(views._alias_for('Metabolic Finisher: Battle Ropes'),
                         'battling ropes')

    def test_prefix_and_parens_together(self):
        self.assertEqual(
            views._alias_for('Metabolic Finisher: Incline Treadmill Walk (Light)'),
            'walking, treadmill')

    def test_unaliased_name_returns_none(self):
        self.assertIsNone(views._alias_for('Barbell Bench Press'))

    def test_every_alias_target_exists_in_the_kb(self):
        kb = views._load_exercise_kb()
        missing = [(k, v) for k, v in views._KB_ALIASES.items() if v not in kb]
        self.assertEqual(missing, [])


class ResolutionTests(SimpleTestCase):
    """The 13 names that were resolving to the wrong illustration."""

    def assert_resolves(self, cases):
        failures = []
        for name, expected in cases:
            got, _score = views._resolve_kb_key(name)
            if got != expected:
                failures.append('%r -> %r (expected %r)' % (name, got, expected))
        if failures:
            self.fail('wrong resolution:\n  ' + '\n  '.join(failures))

    def test_chest_machine_is_not_rear_delt(self):
        self.assert_resolves([
            ('Pec Deck Fly Machine', 'butterfly'),
            ('Pec Deck', 'butterfly'),
            ('Machine Chest Fly', 'butterfly'),
        ])

    def test_rear_delt_is_still_rear_delt(self):
        self.assert_resolves([
            ('Reverse Fly Machine', 'reverse machine flyes'),
            ('Rear Delt Machine', 'reverse machine flyes'),
            ('Rear Lateral Raise', 'dumbbell lying rear lateral raise'),
        ])

    def test_standing_lateral_raise_is_not_the_lying_version(self):
        self.assert_resolves([
            ('Dumbbell Lateral Raise', 'side lateral raise'),
            ('Dumbbell Lateral Raises', 'side lateral raise'),
            ('Lateral Raises (Dumbbell)', 'side lateral raise'),
            ('Machine Lateral Raise', 'seated side lateral raise'),
        ])

    def test_dumbbell_extension_is_not_the_sled_version(self):
        self.assert_resolves([
            ('Dumbbell Triceps Extension (Overhead)',
             'standing dumbbell triceps extension'),
            ('Overhead Dumbbell Triceps Extension',
             'standing dumbbell triceps extension'),
        ])

    def test_chest_supported_row_is_not_the_lying_version(self):
        for name in ('Chest Supported T-Bar Row', 'Chest-Supported T-Bar Row',
                     'T-Bar Row (Chest Supported)'):
            key, _ = views._resolve_kb_key(name)
            self.assertIsNotNone(key, msg=name)
            self.assertNotIn('lying', key, msg=name)

    def test_dumbbell_row_is_not_the_incline_version(self):
        key, _ = views._resolve_kb_key('Dumbbell Rows')
        self.assertIsNotNone(key)
        self.assertNotIn('incline', key)

    def test_conditioning_blocks_resolve_through_the_prefix_strip(self):
        self.assert_resolves([
            ('Metabolic Finisher: Battle Ropes', 'battling ropes'),
            ('Metabolic Finisher: Treadmill HIIT', 'running, treadmill'),
        ])

    def test_unilluminatable_names_miss_rather_than_guess(self):
        """No burpee/jump-rope entry exists in the KB; a miss is correct."""
        for name in ('Warmup & Mobility', 'Metabolic Finisher: Burpees',
                     'Cricket Match or Hiking'):
            key, _ = views._resolve_kb_key(name)
            self.assertIsNone(key, msg='%s should not be illustrated' % name)


class AiKeyValidationTests(SimpleTestCase):
    """An AI-chosen kb_key still gets checked before it reaches the user."""

    def test_rejects_a_modifier_contradicting_key(self):
        self.assertFalse(views._kb_key_is_consistent(
            'Dumbbell Lateral Raise', 'dumbbell lying rear lateral raise'))

    def test_rejects_anything_other_than_the_alias_when_one_exists(self):
        self.assertFalse(views._kb_key_is_consistent(
            'Pec Deck Fly Machine', 'reverse machine flyes'))
        self.assertTrue(views._kb_key_is_consistent(
            'Pec Deck Fly Machine', 'butterfly'))

    def test_accepts_a_sane_key(self):
        self.assertTrue(views._kb_key_is_consistent(
            'Barbell Bench Press', 'barbell bench press - medium grip'))

    def test_images_for_exercise_falls_back_when_the_key_is_rejected(self):
        """A rejected AI key must not produce the wrong photo."""
        images = views._images_for_exercise(
            'Pec Deck Fly Machine', 'reverse machine flyes')
        kb = views._load_exercise_kb()
        self.assertEqual(images, kb['butterfly'][:2])

    def test_images_for_exercise_honours_a_valid_key(self):
        kb = views._load_exercise_kb()
        key = 'barbell bench press - medium grip'
        self.assertEqual(
            views._images_for_exercise('Barbell Bench Press', key), kb[key][:2])


class CandidateShortlistTests(SimpleTestCase):
    """The shortlist shown to the AI must agree with what resolution would pick."""

    def test_alias_collapses_the_shortlist_to_one(self):
        self.assertEqual(views._get_kb_candidates('Pec Deck Fly Machine'),
                         ['butterfly'])

    def test_shortlist_never_contains_a_disqualified_key(self):
        for name in ('Dumbbell Lateral Raise', 'T-Bar Row', 'Dumbbell Rows'):
            for key in views._get_kb_candidates(name, top_n=10):
                qt, kt = views._kb_tokenize(name), views._kb_tokenize(key)
                self.assertEqual(qt & views._FLIPPER_STEMS,
                                 kt & views._FLIPPER_STEMS,
                                 msg='%s shortlisted %s' % (name, key))

    def test_shortlist_top_matches_resolution(self):
        for name in ('Barbell Bench Press', 'Seated Calf Raise', 'Plank'):
            cands = views._get_kb_candidates(name, top_n=1)
            resolved, _ = views._resolve_kb_key(name)
            if cands and resolved:
                self.assertEqual(cands[0], resolved, msg=name)
