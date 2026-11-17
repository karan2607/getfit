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

import inspect

from django.test import SimpleTestCase
from django.urls import reverse
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from api import views
from api.models import ExerciseGuide, User


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

    def test_plain_t_bar_row_is_not_the_lying_version(self):
        key, _ = views._resolve_kb_key('T-Bar Row')
        self.assertIsNotNone(key)
        self.assertNotIn('lying', key)

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


class ReportedImageRegressionTests(SimpleTestCase):
    """
    Two image bugs reported from the queue, both of them mine.

    "Dead bug shows wrong images" — the KB contains `dead bug` verbatim, and
    my own alias to `superman` was overriding that exact match. A dead bug is
    supine opposite-limb; a superman is prone simultaneous. Different exercise,
    different position.

    "Backward walking treadmill has regular treadmill images" — it fuzzy-matched
    `walking, treadmill` at 0.80 and illustrated someone walking forwards.
    """

    def test_dead_bug_resolves_to_its_own_kb_entry(self):
        key, score = views._resolve_kb_key('Dead Bug')
        self.assertEqual(key, 'dead bug')
        self.assertEqual(score, 1.0)

    def test_dead_bug_is_no_longer_aliased(self):
        """An alias must never shadow an exact KB key."""
        self.assertIsNone(views._alias_for('Dead Bug'))

    def test_no_alias_shadows_an_exact_kb_key(self):
        """
        The general form of that bug. If a variant is spelled exactly like a
        KB entry, the alias table must not redirect it somewhere else.
        """
        kb = views._load_exercise_kb()
        for variant, target in views._KB_ALIASES.items():
            if variant not in kb or variant in views.MISLABELLED_KB_KEYS:
                continue
            self.assertEqual(
                target, variant,
                msg='alias %r shadows the exact KB key of the same name. If '
                    'that entry is mislabelled, LOOK at its photos and add it '
                    'to views.MISLABELLED_KB_KEYS with what they show.'
                    % variant)

    def test_every_mislabelled_key_is_actually_redirected(self):
        """
        The exception list is not a place to park a name. A key recorded as
        mislabelled must have an alias sending it somewhere else, or it is
        still serving the wrong photo with a note saying so.
        """
        for key in views.MISLABELLED_KB_KEYS:
            target = views._KB_ALIASES.get(key)
            self.assertIsNotNone(
                target, msg='%r is listed as mislabelled but has no alias' % key)
            self.assertNotEqual(target, key)
            self.assertIn(target, views._load_exercise_kb())

    def test_backward_walking_gets_no_image_rather_than_a_forward_one(self):
        """A wrong illustration is worse than none."""
        key, _score = views._resolve_kb_key('Backward Walking (Treadmill)')
        self.assertIsNone(key)

    def test_forward_treadmill_walking_still_matches(self):
        """The fix must not cost the correct case."""
        self.assertEqual(views._resolve_kb_key('Walking Treadmill')[0],
                         'walking, treadmill')
        self.assertEqual(views._resolve_kb_key('Incline Treadmill Walk')[0],
                         'walking, treadmill')

    def test_a_backward_sled_walk_still_matches_its_own_entry(self):
        """'backward' on both sides is agreement, not a conflict."""
        self.assertEqual(
            views._resolve_kb_key('Sled Overhead Backward Walk')[0],
            'sled overhead backward walk')

    def test_a_treadmill_exercise_never_matches_a_sled_one(self):
        """
        The trap this fix walked into: disqualifying the treadmill match let
        `sled overhead backward walk` through at 0.57 -- a different machine
        and a worse illustration than the one being rejected.
        """
        for name in ('Backward Walking (Treadmill)', 'Backward Treadmill Walk'):
            key, _score = views._resolve_kb_key(name)
            self.assertNotIn('sled', str(key or ''))

    def test_row_is_not_treated_as_equipment(self):
        """
        Adding 'rowing' to the equipment words made 'row' an equipment stem --
        but 'row' is already a MOVEMENT word, and it silently re-ranked every
        row exercise.
        """
        self.assertNotIn(views._kb_stem('row'), views._EQUIPMENT_STEMS)
        self.assertEqual(views._resolve_kb_key('Single Arm Cable Row')[0],
                         'seated one-arm cable pulley rows')

    def test_bird_dog_gets_no_image_rather_than_a_superman(self):
        """A quadruped opposite-limb hold is not a prone superman."""
        self.assertIsNone(views._resolve_kb_key('Bird-Dog')[0])

    def test_superman_itself_still_matches(self):
        self.assertEqual(views._resolve_kb_key('Superman')[0], 'superman')


class MachineShoulderPressTests(SimpleTestCase):
    """
    "Machine shoulder press shows wrong exercise photos". `smith machine
    overhead shoulder press` contains every word of the name, so the AI --
    told to prefer the most specific such key -- picked a barbell in a Smith
    rack, and the consistency check (modifiers only) let it through.
    """

    KEY = 'machine shoulder (military) press'

    def test_resolves_to_the_machine_entry(self):
        for name in ('Machine Shoulder Press', 'Shoulder Press Machine',
                     'Seated Machine Shoulder Press', 'Machine Overhead Press'):
            self.assertEqual(views._resolve_kb_key(name)[0], self.KEY, msg=name)

    def test_ai_picking_the_smith_key_is_rejected(self):
        self.assertFalse(views._kb_key_is_consistent(
            'Machine Shoulder Press', 'smith machine overhead shoulder press'))
        kb = views._load_exercise_kb()
        self.assertEqual(
            views._images_for_exercise(
                'Machine Shoulder Press', 'smith machine overhead shoulder press'),
            kb[self.KEY][:2])

    def test_smith_key_is_not_offered_to_the_ai(self):
        self.assertEqual(views._get_kb_candidates('Machine Shoulder Press'),
                         [self.KEY])

    def test_the_smith_exercise_still_matches_itself(self):
        self.assertEqual(
            views._resolve_kb_key('Smith Machine Overhead Shoulder Press')[0],
            'smith machine overhead shoulder press')


class WeightedDipTests(SimpleTestCase):
    """
    "Weighted dips also has wrong photos". 'weighted' + 'dip' scored highest
    against "weighted bench dip", so a bar dip was shown as a bench dip.
    """

    KEY = 'dips - chest version'

    def test_bar_dips_resolve_to_a_bar_dip(self):
        for name in ('Weighted Dips', 'Weighted Dip', 'Dips',
                     'Weighted Dips (Belt)'):
            self.assertEqual(views._resolve_kb_key(name)[0], self.KEY, msg=name)

    def test_ai_picking_the_bench_dip_is_rejected(self):
        self.assertFalse(views._kb_key_is_consistent(
            'Weighted Dips', 'weighted bench dip'))
        self.assertEqual(views._get_kb_candidates('Weighted Dips'), [self.KEY])

    def test_bench_dips_still_match_themselves(self):
        self.assertEqual(views._resolve_kb_key('Weighted Bench Dip')[0],
                         'weighted bench dip')
        self.assertEqual(views._resolve_kb_key('Bench Dips')[0], 'bench dips')
        self.assertEqual(views._resolve_kb_key('Dip Machine')[0], 'dip machine')
        self.assertEqual(views._resolve_kb_key('Ring Dips')[0], 'ring dips')


class GluteHamRaiseTests(SimpleTestCase):
    """
    "Glute ham raise exercise shows biceps curls photo". The AI-key check only
    compared flipping modifiers, and neither name has one -- so a curl key with
    no word in common with the exercise was accepted.
    """

    KEY = 'glute ham raise'

    def test_resolves_to_its_own_entry(self):
        for name in ('Glute-Ham Raise (GHR)', 'Glute Ham Raise'):
            self.assertEqual(views._resolve_kb_key(name)[0], self.KEY, msg=name)

    def test_ai_picking_a_biceps_curl_is_rejected(self):
        for curl in ('dumbbell bicep curl', 'barbell curl', 'lying cable curl'):
            self.assertFalse(views._kb_key_is_consistent(
                'Glute-Ham Raise (GHR)', curl), msg=curl)
        kb = views._load_exercise_kb()
        self.assertEqual(
            views._images_for_exercise('Glute-Ham Raise (GHR)',
                                       'dumbbell bicep curl'),
            kb[self.KEY][:2])

    def test_a_related_ai_key_is_still_accepted(self):
        self.assertTrue(views._kb_key_is_consistent(
            'Glute-Ham Raise (GHR)', 'natural glute ham raise'))


class ChestSupportedRowTests(SimpleTestCase):
    """
    "Chest supported rows also has wrong images". Nothing in the names said
    "face-down on a pad", so they matched standing or hanging rows.
    """

    def test_each_variant_resolves_to_a_prone_row(self):
        cases = [
            ('Chest-Supported T-Bar Row', 'lying t-bar row'),
            ('Chest Supported T-Bar Row', 'lying t-bar row'),
            ('T-Bar Row (Chest Supported)', 'lying t-bar row'),
            ('T-Bar Rows (Chest Supported)', 'lying t-bar row'),
            ('Chest-Supported Dumbbell Row', 'dumbbell incline row'),
            ('Chest Supported Row (Dumbbell)', 'dumbbell incline row'),
            ('Chest Supported Machine Row', 'leverage iso row'),
            ('Chest Supported Row (Machine)', 'leverage iso row'),
            ('Chest Supported Rows', 'dumbbell incline row'),
            ('Chest-Supported Row', 'dumbbell incline row'),
        ]
        for name, expected in cases:
            self.assertEqual(views._resolve_kb_key(name)[0], expected, msg=name)

    def test_ai_picking_a_standing_or_hanging_row_is_rejected(self):
        for wrong in ('t-bar row with handle', 'inverted row',
                      'one-arm dumbbell row', 'smith machine bent over row'):
            self.assertFalse(views._kb_key_is_consistent(
                'Chest-Supported T-Bar Row', wrong), msg=wrong)
        kb = views._load_exercise_kb()
        self.assertEqual(
            views._images_for_exercise('Chest-Supported T-Bar Row',
                                       't-bar row with handle'),
            kb['lying t-bar row'][:2])

    def test_unsupported_rows_are_unchanged(self):
        self.assertEqual(views._resolve_kb_key('Inverted Row')[0], 'inverted row')
        self.assertNotEqual(
            views._alias_for('Single Arm Dumbbell Row (Supported)'),
            'dumbbell incline row')


class SingleArmDumbbellRowTests(SimpleTestCase):
    """
    "Single Arm dumbbell row also has wrong images". Any AI key sharing a word
    passed the check, so an upright row or a cable row could illustrate it.
    """

    KEY = 'one-arm dumbbell row'

    def test_each_variant_resolves_to_its_own_entry(self):
        for name in ('Single Arm Dumbbell Row', 'Single-Arm Dumbbell Row',
                     'Single Arm Dumbbell Row (Supported)',
                     'Single Arm Dumbbell Rows', 'One Arm Dumbbell Row',
                     'One-Arm Dumbbell Row'):
            self.assertEqual(views._resolve_kb_key(name)[0], self.KEY, msg=name)

    def test_ai_picking_a_different_row_is_rejected(self):
        for wrong in ('dumbbell one-arm upright row',
                      'kneeling single-arm high pulley row',
                      'seated one-arm cable pulley rows',
                      'bent over two-dumbbell row'):
            self.assertFalse(views._kb_key_is_consistent(
                'Single Arm Dumbbell Row', wrong), msg=wrong)
        kb = views._load_exercise_kb()
        self.assertEqual(
            views._images_for_exercise('Single Arm Dumbbell Row',
                                       'dumbbell one-arm upright row'),
            kb[self.KEY][:2])

    def test_cable_row_is_unchanged(self):
        self.assertEqual(views._resolve_kb_key('Single Arm Cable Row')[0],
                         'seated one-arm cable pulley rows')


class CachedGuideImageTests(APITestCase):
    """A guide cached before an alias existed must not keep its wrong photos."""

    def setUp(self):
        user = User.objects.create_user(email='img@test.local', password='x')
        token, _ = Token.objects.get_or_create(user=user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)
        self.kb = views._load_exercise_kb()

    def test_cached_smith_photos_are_replaced_on_read(self):
        ExerciseGuide.objects.create(
            user=None, name='Machine Shoulder Press',
            data={'steps': ['a'], 'muscles': ['Shoulders'], 'tips': ['t'],
                  'category': 'press',
                  'images': self.kb['smith machine overhead shoulder press'][:2],
                  'advice_version': views.ADVICE_VERSION})
        res = self.client.get(reverse('exercise-guide'),
                              {'name': 'Machine Shoulder Press'})
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['images'],
                         self.kb['machine shoulder (military) press'][:2])

    def test_cached_curl_photos_on_a_glute_ham_raise_are_replaced_on_read(self):
        ExerciseGuide.objects.create(
            user=None, name='Glute-Ham Raise (GHR)',
            data={'steps': ['a'], 'muscles': ['Hamstrings'], 'tips': ['t'],
                  'category': 'hinge',
                  'images': self.kb['dumbbell bicep curl'][:2],
                  'advice_version': views.ADVICE_VERSION})
        res = self.client.get(reverse('exercise-guide'),
                              {'name': 'Glute-Ham Raise (GHR)'})
        self.assertEqual(res.status_code, 200, res.data)
        # Not 'glute ham raise': that entry's own photos are a GHD back
        # extension. See views.MISLABELLED_KB_KEYS.
        self.assertEqual(res.data['images'],
                         self.kb['natural glute ham raise'][:2])

    def test_cached_upright_row_photos_on_a_single_arm_row_are_replaced(self):
        ExerciseGuide.objects.create(
            user=None, name='Single Arm Dumbbell Row',
            data={'steps': ['a'], 'muscles': ['Back'], 'tips': ['t'],
                  'category': 'pull',
                  'images': self.kb['dumbbell one-arm upright row'][:2],
                  'advice_version': views.ADVICE_VERSION})
        res = self.client.get(reverse('exercise-guide'),
                              {'name': 'Single Arm Dumbbell Row'})
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['images'], self.kb['one-arm dumbbell row'][:2])

    def test_unaliased_cached_images_are_left_alone(self):
        images = self.kb['barbell bench press - medium grip'][:2]
        ExerciseGuide.objects.create(
            user=None, name='Barbell Bench Press',
            data={'steps': ['a'], 'images': images,
                  'advice_version': views.ADVICE_VERSION})
        res = self.client.get(reverse('exercise-guide'),
                              {'name': 'Barbell Bench Press'})
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['images'], images)


class ComparativeAiKeyGuardTests(SimpleTestCase):
    """
    The AI-chosen key is now judged against the matcher, not against zero.

    The old guard accepted any key sharing one stemmed token with the exercise
    name. Measured across the 873-key library that let through a median of 43
    keys per exercise -- and 176 for the worst -- while the matcher picks one.
    So whatever the model chose essentially always won, and a sloppy pick was
    then cached indefinitely.

    These names deliberately have NO alias, so they exercise the margin itself
    rather than the alias shortcut.
    """

    def setUp(self):
        self.kb = views._load_exercise_kb()
        self.assertIsNone(views._alias_for('Seated Cable Row'))
        self.assertIsNone(views._alias_for('Bulgarian Split Squat'))
        self.assertIsNone(views._alias_for('Incline Cable Fly'))

    def test_a_key_well_below_the_matchers_best_is_rejected(self):
        # 'upright cable row' is a SHOULDER exercise and scores 0.87 against
        # the matcher's own 1.20 -- a 0.33 gap. The old guard took it, because
        # 0.87 > 0.
        self.assertFalse(
            views._kb_key_is_consistent('Seated Cable Row', 'upright cable row'))
        self.assertEqual(
            views._images_for_exercise('Seated Cable Row', 'upright cable row'),
            self.kb['seated cable rows'][:2])

    def test_a_key_within_the_margin_is_still_preferred(self):
        # The model is sometimes right where the matcher is wrong, so the
        # margin must not simply hand every decision back to the matcher.
        # 0.82 against the matcher's 0.95 is a 0.13 gap: inside the margin.
        self.assertTrue(views._kb_key_is_consistent(
            'Bulgarian Split Squat', 'split squat with dumbbells'))
        self.assertEqual(
            views._images_for_exercise(
                'Bulgarian Split Squat', 'split squat with dumbbells'),
            self.kb['split squat with dumbbells'][:2])

    def test_the_model_can_still_overrule_a_wrong_matcher(self):
        # The case that rules out a strict "must beat the matcher" guard:
        # 'incline cable flye' (correct) and 'cable incline pushdown' (a
        # TRICEPS exercise) both score exactly 0.72, so score alone cannot
        # separate them and a strict rule would lock in the pushdown.
        self.assertEqual(
            views._kb_score(views._kb_tokenize('Incline Cable Fly'),
                            views._kb_tokenize('incline cable flye')),
            views._kb_score(views._kb_tokenize('Incline Cable Fly'),
                            views._kb_tokenize('cable incline pushdown')))
        self.assertEqual(
            views._images_for_exercise('Incline Cable Fly', 'incline cable flye'),
            self.kb['incline cable flye'][:2])

    def test_a_key_is_rejected_when_the_library_has_no_real_match(self):
        # A name with nothing close in the library holds the AI key to the
        # match threshold itself. All three Haiku runs picked 'backward drag'
        # -- a SLED exercise -- for a treadmill walk; Gemini returned null.
        # Showing no photo is better than showing the wrong machine.
        self.assertFalse(views._kb_key_is_consistent(
            'Backward Walking Treadmill', 'backward drag'))
        self.assertEqual(
            views._images_for_exercise('Backward Walking Treadmill', 'backward drag'),
            [])

    def test_an_unknown_key_is_rejected_rather_than_crashing(self):
        self.assertFalse(
            views._kb_key_is_consistent('Seated Cable Row', 'not a real key'))


class ApparatusModifierTests(SimpleTestCase):
    """
    Apparatus and body position change the exercise, like direction already did.

    Reported: "Dumbbell hammer curl also got wrong images". A preacher bench
    fixes the upper arm, so a preacher hammer curl is a different movement --
    but 'preacher' cost only a little precision, so
    'preacher hammer dumbbell curl' scored 1.06 and beat the correct
    'hammer curls' at 0.95.
    """

    def test_hammer_curl_resolves_to_a_plain_hammer_curl(self):
        key, _ = views._resolve_kb_key('Dumbbell Hammer Curl')
        self.assertEqual(key, 'hammer curls')

    def test_a_preacher_curl_still_matches_itself(self):
        key, _ = views._resolve_kb_key('Preacher Hammer Dumbbell Curl')
        self.assertEqual(key, 'preacher hammer dumbbell curl')

    def test_a_preacher_key_is_rejected_for_a_plain_curl(self):
        self.assertFalse(views._kb_key_is_consistent(
            'Dumbbell Hammer Curl', 'preacher hammer dumbbell curl'))

    def test_kneeling_is_not_matched_by_a_standing_exercise(self):
        self.assertEqual(
            views._kb_score(views._kb_tokenize('Single Arm Dumbbell Row'),
                            views._kb_tokenize('kneeling single-arm high pulley row')),
            0.0)

    def test_every_library_key_still_resolves_to_itself(self):
        # The modifier set is global, so widening it can silently re-rank
        # unrelated exercises. This is the whole-library regression check:
        # one known-benign miss ('squats - with bands' -> 'squat with bands',
        # the same exercise under another name) and no others.
        kb = views._load_exercise_kb()
        misses = [k for k in kb if views._resolve_kb_key(k)[0] != k]
        # 'squats - with bands' -> 'squat with bands' is the same exercise
        # under another name. A mislabelled key is SUPPOSED to miss itself:
        # that is the point of redirecting it.
        expected = sorted(['squats - with bands'] + list(views.MISLABELLED_KB_KEYS))
        self.assertEqual(sorted(misses), expected)


class GuideBatchPairingTests(SimpleTestCase):
    """
    Batch guides are paired by echoed name, not by position.

    zip() pairs positionally, so one missing element shifts every later
    exercise onto its neighbour's guide and drops the last one. Images survive
    that -- the key guard rejects a shifted key -- but steps, muscles and tips
    have no guard at all, so an exercise would be stored with another
    exercise's instructions and nothing would say so.
    """

    def test_pairs_by_name_when_the_model_echoes_it(self):
        names = ['Barbell Squat', 'Face Pull']
        guides = [{'exercise': 'Face Pull', 'muscles': ['Rear delts']},
                  {'exercise': 'Barbell Squat', 'muscles': ['Quads']}]
        paired = dict(views._pair_guides_with_names(names, guides))
        self.assertEqual(paired['Barbell Squat']['muscles'], ['Quads'])
        self.assertEqual(paired['Face Pull']['muscles'], ['Rear delts'])

    def test_a_dropped_element_does_not_shift_the_others(self):
        names = ['Barbell Squat', 'Dumbbell Bicep Curl', 'Face Pull']
        # The model omitted the curl entirely.
        guides = [{'exercise': 'Barbell Squat', 'muscles': ['Quads']},
                  {'exercise': 'Face Pull', 'muscles': ['Rear delts']}]
        paired = dict(views._pair_guides_with_names(names, guides))
        self.assertEqual(paired['Barbell Squat']['muscles'], ['Quads'])
        self.assertEqual(paired['Face Pull']['muscles'], ['Rear delts'])
        # The curl gets NO guide rather than the face pull's. It regenerates
        # on demand; a wrong one would have persisted in the cache.
        self.assertNotIn('Dumbbell Bicep Curl', paired)

    def test_name_matching_tolerates_case_and_punctuation(self):
        names = ['Glute-Ham Raise (GHR)']
        guides = [{'exercise': 'glute ham raise  ghr', 'muscles': ['Hamstrings']}]
        paired = dict(views._pair_guides_with_names(names, guides))
        self.assertIn('Glute-Ham Raise (GHR)', paired)

    def test_falls_back_to_position_when_lengths_agree(self):
        # No echoed names, but one guide per exercise: position cannot be
        # silently wrong here, so the old behaviour is kept.
        names = ['Barbell Squat', 'Face Pull']
        guides = [{'muscles': ['Quads']}, {'muscles': ['Rear delts']}]
        paired = views._pair_guides_with_names(names, guides)
        self.assertEqual(paired[0][0], 'Barbell Squat')
        self.assertEqual(paired[1][1]['muscles'], ['Rear delts'])

    def test_drops_everything_unpairable_when_lengths_disagree(self):
        names = ['Barbell Squat', 'Face Pull', 'Leg Press']
        guides = [{'muscles': ['Quads']}, {'muscles': ['Rear delts']}]
        self.assertEqual(views._pair_guides_with_names(names, guides), [])

    def test_tolerates_junk(self):
        self.assertEqual(views._pair_guides_with_names(['X'], None), [])
        self.assertEqual(views._pair_guides_with_names(['X'], ['not a dict']), [])


class SelfHealingGuideCacheTests(APITestCase):
    """
    A cached guide written under older matching rules gets re-examined on read.

    The repair this replaces only worked for a name somebody had already
    written an alias for. Everything else kept its stored photos forever,
    which is why three migrations exist purely to DELETE rows with bad
    pictures (0029, 0031, 0040) -- the same incident handled by hand, three
    times. A version stamped on the row lets a rules fix heal every affected
    row instead.
    """

    def setUp(self):
        user = User.objects.create_user(email='heal@test.local', password='x')
        token, _ = Token.objects.get_or_create(user=user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)
        self.kb = views._load_exercise_kb()

    def _cache(self, name, images, **extra):
        data = {'steps': ['a'], 'muscles': ['m'], 'tips': ['t'],
                'category': 'other', 'images': images,
                'advice_version': views.ADVICE_VERSION}
        data.update(extra)
        ExerciseGuide.objects.create(user=None, name=name, data=data)

    def _get(self, name):
        res = self.client.get(reverse('exercise-guide'), {'name': name})
        self.assertEqual(res.status_code, 200, res.data)
        return res.data

    def test_a_row_with_no_version_is_re_resolved(self):
        # No alias for this name, so the OLD repair left it alone: a seated
        # cable row kept photos of an upright row, a shoulder exercise.
        self.assertIsNone(views._alias_for('Seated Cable Row'))
        self._cache('Seated Cable Row', self.kb['upright cable row'][:2])
        self.assertEqual(self._get('Seated Cable Row')['images'],
                         self.kb['seated cable rows'][:2])

    def test_a_row_stamped_with_an_older_version_is_re_resolved(self):
        self._cache('Dumbbell Hammer Curl',
                    self.kb['preacher hammer dumbbell curl'][:2],
                    kb_match_version=views.KB_MATCH_VERSION - 1)
        self.assertEqual(self._get('Dumbbell Hammer Curl')['images'],
                         self.kb['hammer curls'][:2])

    def test_a_current_row_is_trusted_and_not_recomputed(self):
        # A good model pick that the matcher would not have made must survive
        # being read back, or every read would undo it.
        self._cache('Incline Cable Fly', self.kb['incline cable flye'][:2],
                    kb_match_version=views.KB_MATCH_VERSION)
        self.assertEqual(self._get('Incline Cable Fly')['images'],
                         self.kb['incline cable flye'][:2])

    def test_a_stale_row_keeps_images_the_current_rules_still_accept(self):
        # Re-resolving must not throw away a good stored choice just because
        # the row is old: the cached key is offered back to today's guard.
        self._cache('Incline Cable Fly', self.kb['incline cable flye'][:2])
        self.assertEqual(self._get('Incline Cable Fly')['images'],
                         self.kb['incline cable flye'][:2])

    def test_a_row_whose_exercise_has_no_match_loses_its_images(self):
        self._cache('Backward Walking Treadmill', self.kb['backward drag'][:2])
        self.assertEqual(self._get('Backward Walking Treadmill')['images'], [])

    def test_newly_written_guides_carry_the_current_version(self):
        # Without the stamp on write, every row would look stale forever and
        # be re-resolved on every single read.
        self.assertGreaterEqual(views.KB_MATCH_VERSION, 1)
        src = inspect.getsource(views)
        self.assertEqual(
            src.count("['kb_match_version'] = KB_MATCH_VERSION"), 3,
            'every path that writes images must stamp the matcher version')


class GluteHamRaiseTests(SimpleTestCase):
    """
    The one case where the KB's own label is the problem.

    "glute ham raise" is an exact name match AND the wrong exercise: both its
    photos show a GHD back extension -- hips hinged over the pad, hands behind
    the head -- which is a lower-back movement, not a knee-flexion one. No
    amount of better matching fixes a wrong label, which is why this is an
    alias and not a threshold tweak.

    Reported twice: once as "Glute ham raise excercise shows biceps curls
    photo", and again after the first fix pointed it at the mislabelled entry.
    """

    def test_every_spelling_lands_on_the_entry_that_shows_the_movement(self):
        for name in ('Glute-Ham Raise (GHR)', 'Glute Ham Raise',
                     'Glute-Ham Raise', 'Glute Ham Raises', 'GHR'):
            key, _score = views._resolve_kb_key(name)
            self.assertEqual(key, 'natural glute ham raise', name)

    def test_it_never_resolves_to_the_mislabelled_entry(self):
        for name in ('Glute-Ham Raise (GHR)', 'Glute Ham Raise', 'GHR',
                     'Nordic Curl', 'Nordic Hamstring Curl'):
            key, _score = views._resolve_kb_key(name)
            self.assertNotEqual(key, 'glute ham raise', name)

    def test_a_nordic_curl_is_not_a_biceps_exercise(self):
        """It scored highest against BARBELL CURL -- the Lying Hamstring Curl
        failure again, on a different name."""
        for name in ('Nordic Curl', 'Nordic Curls', 'Nordic Ham Curl',
                     'Nordic Hamstring Curl', 'Nordic Hamstring Curls'):
            key, _score = views._resolve_kb_key(name)
            self.assertEqual(key, 'natural glute ham raise', name)

    def test_the_images_served_are_the_natural_ghr_photos(self):
        images = views._images_for_exercise('Glute-Ham Raise (GHR)')
        self.assertTrue(images)
        for url in images:
            self.assertIn('Natural_Glute_Ham_Raise', url)
            self.assertNotIn('/Glute_Ham_Raise/', url)

    def test_the_floor_variant_is_left_alone(self):
        """A floor GHR is its own entry and a different setup."""
        key, _score = views._resolve_kb_key('Floor Glute-Ham Raise')
        self.assertEqual(key, 'floor glute-ham raise')

    def test_the_version_was_bumped_so_cached_rows_re_resolve(self):
        """
        An alias change that does not bump this heals nothing: every guide
        already cached keeps the images it was written with. That is what the
        three delete-the-bad-rows migrations were doing by hand.
        """
        self.assertGreaterEqual(views.KB_MATCH_VERSION, 2)
