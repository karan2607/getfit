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
            if variant in kb:
                self.assertEqual(
                    target, variant,
                    msg='alias %r shadows the exact KB key of the same name'
                        % variant)

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
        self.assertEqual(res.data['images'], self.kb['glute ham raise'][:2])

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
