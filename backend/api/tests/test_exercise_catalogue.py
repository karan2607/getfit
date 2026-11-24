"""
The exercise catalogue: the list the plan generator names exercises from.

These tests exist because the bug they guard against was invisible. Plan
generation worked perfectly well while naming exercises out of the model's
own head -- the plans were good, the JSON validated, every test passed. The
only symptom was that 766 of the photo library's 873 folders were unreachable
and 98 named exercises showed no photo, and nothing asserted otherwise.
"""
from django.test import SimpleTestCase

from api import exercise_catalogue as lib
from api import exercise_names
from api.views import _workout_system_prompt


class CatalogueDataTests(SimpleTestCase):
    def test_catalogue_loads_the_whole_library(self):
        self.assertGreater(len(lib.all_names()), 850)

    def test_names_are_display_spelled_not_folder_keys(self):
        # exercise_kb.json is keyed by the lowercased name; the catalogue must
        # carry the readable one or plans fill up with "barbell squat".
        self.assertIn('Barbell Squat', lib.all_names())
        self.assertNotIn('barbell squat', lib.all_names())

    def test_every_catalogue_name_is_case_insensitively_findable(self):
        self.assertIsNotNone(lib.get('barbell squat'))
        self.assertIsNone(lib.get('an exercise nobody has invented'))

    def test_facets_come_through(self):
        facets = lib.get('Barbell Bench Press - Medium Grip')
        self.assertEqual(facets['equipment'], 'barbell')
        self.assertIn('chest', facets['primary'])


class CatalogueTextTests(SimpleTestCase):
    def test_grouped_by_muscle_with_equipment(self):
        text = lib.catalogue_text()
        self.assertIn('CHEST:', text)
        self.assertIn('Barbell Bench Press [barbell]', text)

    def test_stretches_are_left_out_of_a_training_plan(self):
        # 123 stretches crowd out the movements a plan is actually asking for.
        self.assertNotIn('Seated Hamstring', lib.catalogue_text())
        self.assertIn('Seated Hamstring', lib.catalogue_text(plan_only=False))

    def test_a_full_gym_gets_the_whole_catalogue(self):
        # The common case. A filter here would only ever remove good options.
        self.assertIsNone(lib._equipment_filter('full gym'))
        self.assertIsNone(lib._equipment_filter(''))
        self.assertIn('Barbell Bench Press [barbell]',
                      lib.catalogue_text(equipment='full gym'))

    def test_dumbbells_only_drops_the_barbell_work(self):
        text = lib.catalogue_text(equipment='dumbbells and a bench at home')
        self.assertNotIn('Barbell Bench Press', text)
        self.assertIn('Dumbbell Bench Press', text)

    def test_bodyweight_survives_every_equipment_filter(self):
        # "dumbbells only" must not mean "no push-ups".
        self.assertIn('body only', lib._equipment_filter('dumbbells only'))

    def test_unrecognised_equipment_text_does_not_narrow_anything(self):
        # A filter that guessed wrong is worse than no filter: it silently
        # removes exercises the client can actually do.
        self.assertIsNone(lib._equipment_filter('whatever is in the garage'))


class SearchTests(SimpleTestCase):
    def test_muscle_search_includes_secondary_movers(self):
        names = [e['name'] for e in lib.search(muscle='triceps', limit=400)]
        # Bench press is a chest exercise; triceps is a secondary mover, and
        # a triceps search that cannot see a press is useless for a swap.
        self.assertIn('Barbell Bench Press - Medium Grip', names)

    def test_equipment_filter_is_exact(self):
        for found in lib.search(muscle='chest', equipment='cable', limit=50):
            self.assertEqual(found['equipment'], 'cable')

    def test_search_excludes_stretches_by_default(self):
        names = [e['name'] for e in lib.search(muscle='hamstrings', limit=400)]
        self.assertNotIn('Seated Hamstring', names)


class PromptTests(SimpleTestCase):
    def test_generation_prompt_carries_the_catalogue(self):
        prompt = _workout_system_prompt('lb')
        self.assertIn('NAME THEM FROM THE CATALOGUE', prompt)
        self.assertIn('Barbell Bench Press [barbell]', prompt)

    def test_generation_prompt_narrows_to_the_clients_equipment(self):
        prompt = _workout_system_prompt('lb', equipment='dumbbells only')
        self.assertIn('Dumbbell Bench Press', prompt)
        self.assertNotIn('Barbell Bench Press [barbell]', prompt)

    def test_the_catalogue_text_is_not_built_for_chat(self):
        # 6,300 tokens on every chat turn would make a measured latency
        # problem worse, and chat does not name exercises from a catalogue --
        # generation does, once per plan. Asserted on the source rather than
        # by calling the chat builder, which needs a user and a database.
        import inspect
        from api import views
        chat_src = inspect.getsource(views._build_system_prompt)
        self.assertNotIn('catalogue_text', chat_src)


class FoldingTests(SimpleTestCase):
    def test_library_spellings_are_recognised(self):
        # Before the catalogue these were unregistered, so a plan naming one
        # split the user's logged history away from the curated spelling.
        self.assertEqual(exercise_names.canonical('Wide-Grip Lat Pulldown'),
                         'Lat Pulldown (Wide Grip)')

    def test_curated_names_still_win(self):
        for name in exercise_names.CANONICAL_NAMES:
            self.assertEqual(exercise_names.canonical(name), name)

    def test_the_library_spelling_does_not_displace_a_verified_one(self):
        # "Barbell Bench Press - Medium Grip" IS the barbell bench press, and
        # the curated spelling is the one with a human-verified photo. Without
        # the alias the catalogue would offer only the library's wording and
        # the verified name would never appear in a plan again.
        self.assertEqual(
            exercise_names.canonical('Barbell Bench Press - Medium Grip'),
            'Barbell Bench Press')
        self.assertIn('Barbell Bench Press [barbell]', lib.catalogue_text())

    def test_loaded_and_unloaded_stay_separate(self):
        # The library has "Barbell Glute Bridge"; this app has "Glute Bridges".
        # Folding them would merge a loaded movement into a bodyweight one and
        # corrupt the progression history -- the same merge already refused
        # for Walking Lunges.
        self.assertEqual(exercise_names.canonical('Barbell Glute Bridge'),
                         'Barbell Glute Bridge')

    def test_a_library_only_name_folds_onto_itself(self):
        self.assertEqual(
            exercise_names.canonical('Barbell Guillotine Bench Press'),
            'Barbell Guillotine Bench Press')

    def test_an_unknown_name_is_still_left_alone(self):
        self.assertEqual(exercise_names.canonical('Karan Special Press'),
                         'Karan Special Press')
