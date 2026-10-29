"""
The gym type, persisted.

`equipment` ('bodyweight' | 'home gym' | 'full gym') was collected at plan
creation, passed into the week-1 generation prompt as a string, and then thrown
away. Nothing stored it, so:

  * every week generated AFTER week 1 was programmed with no idea what
    equipment existed -- a week-5 plan could prescribe cable machines to
    someone who had answered "bodyweight only"
  * the superset validator could not know either, which matters because a
    superset occupies two stations at once

These tests cover the whole path: generate echoes it into the preview, saving
the preview persists it, and the week prompt carries it.
"""

from unittest.mock import patch

from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework.authtoken.models import Token

from api import views
from api.models import User, UserProfile, WorkoutPlan, WorkoutDay


class AcceptedEquipmentTests(APITestCase):
    """Unrecognised values are dropped, not rejected."""

    def test_the_three_real_options_are_kept(self):
        for value in ('bodyweight', 'home gym', 'full gym'):
            self.assertEqual(views._accepted_equipment(value), value)

    def test_case_and_padding_are_normalised(self):
        self.assertEqual(views._accepted_equipment('  Full Gym '), 'full gym')

    def test_junk_is_dropped_rather_than_raising(self):
        """
        Same call as _accepted_outline: the value only sharpens the prompt and
        the pairing rules, so a bad one should cost that sharpening and never
        the whole plan.
        """
        for value in ('garage', 'FULL_GYM', 42, {}, ['full gym']):
            self.assertEqual(views._accepted_equipment(value), '')

    def test_missing_means_unknown(self):
        self.assertEqual(views._accepted_equipment(None), '')
        self.assertEqual(views._accepted_equipment(''), '')


class EquipmentPersistsOnSaveTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='equip@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)

    def save_plan(self, payload):
        res = self.client.post(reverse('workout-plans'), payload, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        return WorkoutPlan.objects.get(pk=res.data['id'])

    def body(self, **extra):
        payload = {
            'title': 'P',
            'duration_weeks': 8,
            'week': [{'day_number': 1, 'name': 'Push', 'is_rest_day': False,
                      'exercises': [{'name': 'Push-ups', 'sets': 3,
                                     'reps': '15'}]}],
        }
        payload.update(extra)
        return payload

    def test_saving_a_preview_persists_the_gym_type(self):
        plan = self.save_plan(self.body(equipment='home gym'))
        self.assertEqual(plan.equipment, 'home gym')

    def test_a_plan_saved_without_it_is_blank_not_broken(self):
        """Older previews and manual plans carry nothing. Blank = unknown."""
        plan = self.save_plan(self.body())
        self.assertEqual(plan.equipment, '')

    def test_an_unrecognised_value_does_not_block_the_save(self):
        plan = self.save_plan(self.body(equipment='my garage'))
        self.assertEqual(plan.equipment, '')

    def test_it_is_exposed_on_the_api(self):
        plan = self.save_plan(self.body(equipment='bodyweight'))
        res = self.client.get(reverse('workout-plan-detail', args=[plan.id]))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['equipment'], 'bodyweight')


class GeneratedPreviewCarriesEquipmentTests(APITestCase):
    """
    The link that was missing. The frontend posts the preview object straight
    back on save, so echoing the value into the generate response persists it
    with no client change at all.
    """

    def setUp(self):
        self.user = User.objects.create_user(email='equipgen@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)

    def generate(self, **extra):
        fake_plan = {
            'title': 'Generated',
            'duration_weeks': 8,
            'week': [{'day_number': 1, 'name': 'Push', 'is_rest_day': False,
                      'exercises': [{'name': 'Push-ups', 'sets': 3,
                                     'reps': '15'}]}],
        }
        payload = {'days_per_week': 4, 'duration_weeks': 8}
        payload.update(extra)
        # Patched at the SOURCE, not on api.views: the generate view
        # re-imports call_gemini_json locally, so a patch on the views module
        # binding is simply not the function that gets called.
        with patch('api.gemini.call_gemini_json', return_value=dict(fake_plan)):
            res = self.client.post(reverse('workout-plan-generate'),
                                   payload, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        return res.data

    def test_the_preview_echoes_the_gym_type_back(self):
        data = self.generate(equipment='home gym')
        self.assertEqual(data['equipment'], 'home gym')

    def test_nothing_is_added_when_none_was_sent(self):
        self.assertNotIn('equipment', self.generate())


class WeekPromptCarriesEquipmentTests(APITestCase):
    """
    THE bug this column exists to fix. Week 1 always knew the gym type; every
    later week was generated blind because the answer was never stored.
    """

    def setUp(self):
        self.user = User.objects.create_user(email='equipwk@test.local', password='x')
        self.profile = UserProfile.objects.create(
            user=self.user, preferred_unit='lb')
        self.plan = WorkoutPlan.objects.create(
            user=self.user, title='P', duration_weeks=8,
            equipment='bodyweight')
        WorkoutDay.objects.create(
            plan=self.plan, day_number=1, week_number=1, name='Push')

    def prompt(self):
        system, user = views._week_generate_prompt(
            self.plan, 5, self.profile, preferred_unit='lb')
        return system + '\n' + user

    def test_week_five_is_told_the_gym_type(self):
        self.assertIn('Equipment available: bodyweight', self.prompt())

    def test_an_unknown_gym_type_adds_no_line(self):
        """Blank must not produce "Equipment available: " with nothing after."""
        WorkoutPlan.objects.filter(pk=self.plan.pk).update(equipment='')
        self.plan.refresh_from_db()
        self.assertNotIn('Equipment available:', self.prompt())

    def test_week_one_generation_still_states_it(self):
        """The half that already worked must keep working."""
        _system, user = views._workout_generate_prompt(
            self.profile, days_per_week=4, duration_weeks=8,
            equipment='home gym', preferred_unit='lb')
        self.assertIn('Equipment: home gym', user)


class EquipmentAvailabilityVerdictTests(APITestCase):
    """
    The pairing rule: don't superset equipment the user does not have.

    There is deliberately NO station-contention rule. I wrote one -- "two
    exercises needing the same apparatus cannot be supersetted" -- and removed
    it, because it rejected BOTH canonical examples the generation prompt
    recommends. Those rejections are the regression tests below.
    """

    def verdict(self, a, b, gym=''):
        from api import exercise_muscles as em
        return em.superset_verdict(a, b, gym=gym)

    def test_the_prompts_own_examples_are_accepted_at_a_full_gym(self):
        """
        THE regression. The prompt teaches "bench press + row" and
        "leg extension + leg curl" as model antagonist pairs. A rule that
        rejects them is worse than no rule: the model follows the prompt and
        gets silently ungrouped every time.
        """
        for a, b in (('Barbell Bench Press', 'Barbell Row'),
                     ('Leg Extension', 'Leg Curl')):
            ok, kind, reason = self.verdict(a, b, gym='full gym')
            self.assertTrue(ok, msg='%s + %s rejected: %s' % (a, b, reason))
            self.assertEqual(kind, 'antagonist')

    def test_two_barbell_lifts_are_fine_because_one_barbell_serves_both(self):
        """You bench it, then pick it up and row it. This is how it is done."""
        self.assertTrue(self.verdict(
            'Barbell Bench Press', 'Barbell Row', gym='full gym')[0])

    def test_a_bodyweight_user_is_not_offered_barbell_work(self):
        ok, kind, reason = self.verdict(
            'Barbell Bench Press', 'Barbell Row', gym='bodyweight')
        self.assertFalse(ok)
        self.assertEqual(kind, 'unavailable')
        self.assertIn('barbell', reason)

    def test_a_home_gym_is_not_offered_machine_work(self):
        ok, kind, _reason = self.verdict(
            'Leg Extension', 'Leg Curl', gym='home gym')
        self.assertFalse(ok)
        self.assertEqual(kind, 'unavailable')

    def test_bodyweight_pairs_pass_for_a_bodyweight_user(self):
        self.assertTrue(self.verdict('Push-ups', 'Plank', gym='bodyweight')[0])

    def test_an_unknown_gym_type_changes_nothing(self):
        """
        Every pre-existing caller omits `gym`, and every plan created before
        the column existed has ''. Both must behave exactly as before.
        """
        from api import exercise_muscles as em
        for a, b in (('Barbell Bench Press', 'Barbell Row'),
                     ('Barbell Bench Press', 'Push-ups'),
                     ('Push-ups', 'Plank')):
            self.assertEqual(em.superset_verdict(a, b),
                             em.superset_verdict(a, b, gym=''),
                             msg='%s + %s' % (a, b))

    def test_muscle_rejections_still_win_over_equipment_ones(self):
        """
        Ordering matters for what the user is told: "both push, both chest" is
        more useful than "you don't own that".
        """
        ok, kind, _reason = self.verdict(
            'Barbell Bench Press', 'Push-ups', gym='bodyweight')
        self.assertFalse(ok)
        self.assertEqual(kind, 'two_compounds')

    def test_unknown_equipment_is_never_treated_as_missing(self):
        """
        122 upstream records say "other" and 77 say nothing. Treating unknown
        as unavailable would reject a sixth of the library on a false premise.
        """
        from api import exercise_muscles as em
        a = {'equipment': 'other', 'primary': frozenset(), 'secondary': frozenset()}
        b = {'equipment': '', 'primary': frozenset(), 'secondary': frozenset()}
        self.assertIsNone(em._equipment_problem(a, b, 'bodyweight'))


class EquipmentKnowledgeBaseTests(APITestCase):
    """The regenerated KB. The import must be additive, not a rewrite."""

    def setUp(self):
        from api import exercise_muscles as em
        self.kb = em._load()

    def test_the_record_count_is_unchanged(self):
        self.assertEqual(len(self.kb), 876)

    def test_most_records_now_carry_equipment(self):
        """77 upstream records have no equipment value; the rest must."""
        with_equip = sum(1 for v in self.kb.values() if v.get('equipment'))
        self.assertGreater(with_equip, 750)

    def test_the_required_fields_are_still_present(self):
        """
        profile_for returns None without BOTH force and primary, so a
        regenerated file that lost either would silently disable every
        superset verdict rather than fail loudly.
        """
        usable = sum(1 for v in self.kb.values()
                     if v.get('force') and v.get('primary'))
        self.assertGreater(usable, 700)

    def test_equipment_values_are_from_the_known_vocabulary(self):
        from api import exercise_muscles as em
        known = set()
        for available in em._EQUIPMENT_AVAILABLE.values():
            known |= set(available)
        # Everything a full gym has, plus the unknown marker.
        known |= {'machine', 'cable', 'other'}
        unknown = {v['equipment'] for v in self.kb.values()
                   if v.get('equipment')} - known
        self.assertEqual(unknown, set(), msg='unmapped equipment values')


class InferGymTypeTests(APITestCase):
    """
    Inferring the gym type from what a plan prescribes.

    Needed because the answer was collected and discarded until the column
    existed, so every pre-existing plan has a blank one -- and blank means
    week 2+ is generated with no idea what the user has.

    The bar: conservative. A wrong answer strips equipment the user actually
    has; no answer just means "assume full gym", which is the behaviour from
    before the column existed.
    """

    def infer(self, names):
        from api import exercise_muscles as em
        return em.infer_gym_type(names)

    def test_cable_or_machine_work_means_a_full_gym(self):
        """Nobody has a cable tower at home."""
        self.assertEqual(self.infer(['Cable Crossover', 'Push-ups']), 'full gym')
        self.assertEqual(self.infer(['Leg Press', 'Plank']), 'full gym')

    def test_free_weights_alone_mean_a_home_gym(self):
        self.assertEqual(
            self.infer(['Barbell Bench Press', 'Dumbbell Curl', 'Push-ups']),
            'home gym')

    def test_body_only_work_means_bodyweight(self):
        self.assertEqual(self.infer(['Push-ups', 'Pull-up', 'Plank']),
                         'bodyweight')

    def test_no_recognisable_equipment_means_no_answer(self):
        """
        The important restraint. An unresolvable plan is NOT evidence of a
        bodyweight plan -- it is no evidence at all, and '' already means
        "assume full gym".
        """
        # Genuinely unresolvable. Note 'Zercher Thing' would NOT work here:
        # the fuzzy matcher correctly resolves it to 'zercher squats'
        # (barbell), which is the matcher doing its job, not noise.
        self.assertEqual(self.infer(['Qqqq Zzzz', 'Flibberty Wotsit']), '')
        self.assertEqual(self.infer([]), '')
        self.assertEqual(self.infer(None), '')

    def test_the_catch_all_equipment_value_is_not_evidence(self):
        """122 upstream records say 'other', which says nothing about a gym."""
        from api import exercise_muscles as em
        others = [n for n in ('Jump Rope', 'Battle Ropes')
                  if em.equipment_for(n) == 'other']
        if others:
            self.assertEqual(self.infer(others), '')


class EquipmentBackfillStepTests(APITestCase):
    """Upgrade step 7, which writes the inferred value to existing plans."""

    def setUp(self):
        self.user = User.objects.create_user(email='bfeq@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')

    def build(self, names, equipment=''):
        from api.models import Exercise
        plan = WorkoutPlan.objects.create(
            user=self.user, title='P', current_week=1, equipment=equipment)
        day = WorkoutDay.objects.create(
            plan=plan, day_number=1, week_number=1, name='Full')
        for order, name in enumerate(names):
            Exercise.objects.create(day=day, name=name, sets=3, reps='10',
                                    order=order)
        return plan

    def run_step(self, plan):
        from api import data_upgrades
        lines = []
        changed = data_upgrades._step_7_infer_plan_equipment(plan, lines.append)
        plan.refresh_from_db()
        return changed, lines

    def test_a_blank_plan_gets_the_inferred_value(self):
        plan = self.build(['Cable Crossover', 'Lat Pulldown'])
        changed, _ = self.run_step(plan)
        self.assertEqual(changed, 1)
        self.assertEqual(plan.equipment, 'full gym')

    def test_an_existing_answer_is_never_overwritten(self):
        """
        THE invariant. A real answer from the user beats an inference from the
        exercise list, always -- even when they disagree.
        """
        plan = self.build(['Cable Crossover', 'Lat Pulldown'],
                          equipment='bodyweight')
        changed, _ = self.run_step(plan)
        self.assertEqual(changed, 0)
        self.assertEqual(plan.equipment, 'bodyweight')

    def test_an_unclear_plan_is_left_blank(self):
        plan = self.build(['Qqqq Zzzz', 'Flibberty Wotsit'])
        changed, lines = self.run_step(plan)
        self.assertEqual(changed, 0)
        self.assertEqual(plan.equipment, '')
        self.assertTrue(any('left blank' in line for line in lines))

    def test_a_second_run_changes_nothing(self):
        plan = self.build(['Cable Crossover', 'Lat Pulldown'])
        self.run_step(plan)
        changed, _ = self.run_step(plan)
        self.assertEqual(changed, 0)
        self.assertEqual(plan.equipment, 'full gym')

    def test_it_is_registered_as_step_seven(self):
        from api import data_upgrades
        label, func = data_upgrades.UPGRADES[7]
        self.assertEqual(func, data_upgrades._step_7_infer_plan_equipment)
        self.assertIn('equipment', label)

    def test_the_whole_runner_applies_it(self):
        from django.core.management import call_command
        plan = self.build(['Cable Crossover', 'Lat Pulldown'])
        call_command('upgrade_data', verbosity=0)
        plan.refresh_from_db()
        self.assertEqual(plan.equipment, 'full gym')
