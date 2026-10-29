"""
Superset groups: schema, validation, and the pairing endpoints.

The design constraint that keeps this cheap: **SetLog is untouched**. Each
member still logs its own sets, so the field contract, `coerce_set_fields`,
progression and history all behave exactly as they do for a standalone
exercise. These tests assert that explicitly, because it is the property that
makes the feature safe.

Invalid groups are UNGROUPED rather than rejected: a bad pairing should cost
the superset, not the workout. Same call already made for prescription
mismatches and the program outline.
"""

from django.urls import reverse
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from api import views, exercise_types as et
from api.models import (
    User, UserProfile, WorkoutPlan, WorkoutDay, Exercise, Composite,
    WorkoutSession, SetLog,
)


def ex(name, sets=3, reps='8-12', measurement_type=et.WEIGHT_REPS, **extra):
    out = {'name': name, 'sets': sets, 'reps': reps,
           'measurement_type': measurement_type}
    out.update(extra)
    return out


WARMUP = ex('Warmup & Mobility', sets=1, reps='5-10 min',
            measurement_type=et.WARMUP)


class GroupValidationTests(APITestCase):
    """`_validate_superset_groups` — the gate the AI's output passes through."""

    def check(self, exercises, back=False):
        cleaned, problems = views._validate_superset_groups(
            'Push', list(exercises), back)
        return cleaned, problems

    def groups_in(self, exercises):
        return {e.get('superset_group') for e in exercises
                if e.get('superset_group') is not None}

    def test_a_valid_antagonist_pair_survives(self):
        exercises = [
            WARMUP,
            ex('Barbell Back Squat'),                       # first working
            ex('Barbell Bench Press', superset_group=1),
            ex('Chest-Supported T-Bar Row', superset_group=1),
        ]
        cleaned, problems = self.check(exercises)
        self.assertEqual(problems, [], msg=problems)
        self.assertEqual(self.groups_in(cleaned), {1})

    def test_bench_press_plus_pushups_is_ungrouped(self):
        """The bad pairing, rejected by the movement data."""
        exercises = [
            WARMUP, ex('Barbell Back Squat'),
            ex('Barbell Bench Press', superset_group=1),
            ex('Push-ups', measurement_type=et.BODYWEIGHT_REPS,
               superset_group=1),
        ]
        cleaned, problems = self.check(exercises)
        self.assertTrue(problems)
        self.assertEqual(self.groups_in(cleaned), set())

    def test_a_single_member_group_is_ungrouped(self):
        exercises = [WARMUP, ex('Barbell Back Squat'),
                     ex('Barbell Bench Press', superset_group=1)]
        cleaned, problems = self.check(exercises)
        self.assertTrue(any('only one member' in p for p in problems))
        self.assertEqual(self.groups_in(cleaned), set())

    def test_non_adjacent_members_are_ungrouped(self):
        exercises = [
            WARMUP, ex('Barbell Back Squat'),
            ex('Barbell Bench Press', superset_group=1),
            ex('Leg Press'),
            ex('Chest-Supported T-Bar Row', superset_group=1),
        ]
        cleaned, problems = self.check(exercises)
        self.assertTrue(any('not adjacent' in p for p in problems))
        self.assertEqual(self.groups_in(cleaned), set())

    def test_the_first_working_exercise_is_never_in_a_group(self):
        """The primary lift is done fresh and heavy."""
        exercises = [
            WARMUP,
            ex('Barbell Bench Press', superset_group=1),
            ex('Chest-Supported T-Bar Row', superset_group=1),
        ]
        cleaned, problems = self.check(exercises)
        self.assertTrue(any('first working' in p for p in problems))
        self.assertEqual(self.groups_in(cleaned), set())

    def test_mismatched_set_counts_are_ungrouped(self):
        """A superset is N rounds of both members."""
        exercises = [
            WARMUP, ex('Barbell Back Squat'),
            ex('Barbell Bench Press', sets=3, superset_group=1),
            ex('Chest-Supported T-Bar Row', sets=4, superset_group=1),
        ]
        cleaned, problems = self.check(exercises)
        self.assertTrue(any('mismatched set counts' in p for p in problems))
        self.assertEqual(self.groups_in(cleaned), set())

    def test_a_warmup_in_a_group_is_ungrouped(self):
        exercises = [
            ex('Barbell Back Squat'),
            ex('Warmup & Mobility', sets=3, measurement_type=et.WARMUP,
               superset_group=1),
            ex('Chest-Supported T-Bar Row', sets=3, superset_group=1),
        ]
        cleaned, problems = self.check(exercises)
        self.assertTrue(problems)
        self.assertEqual(self.groups_in(cleaned), set())

    def test_more_than_two_groups_keeps_only_two(self):
        """"Not everything has to be a superset" — enforced, not suggested."""
        exercises = [
            WARMUP, ex('Barbell Back Squat'),
            ex('Barbell Bench Press', superset_group=1),
            ex('Chest-Supported T-Bar Row', superset_group=1),
            ex('Dumbbell Bicep Curl', superset_group=2),
            ex('Overhead Dumbbell Triceps Extension', superset_group=2),
            ex('Leg Press', superset_group=3),
            ex('Lying Hamstring Curl', superset_group=3),
        ]
        cleaned, problems = self.check(exercises)
        self.assertTrue(any('superset groups, max 2' in p for p in problems))
        self.assertEqual(len(self.groups_in(cleaned)), 2)

    def test_one_bad_group_does_not_lose_a_good_one(self):
        exercises = [
            WARMUP, ex('Barbell Back Squat'),
            ex('Barbell Bench Press', superset_group=1),
            ex('Push-ups', measurement_type=et.BODYWEIGHT_REPS,
               superset_group=1),
            ex('Dumbbell Bicep Curl', superset_group=2),
            ex('Overhead Dumbbell Triceps Extension', superset_group=2),
        ]
        cleaned, problems = self.check(exercises)
        self.assertTrue(problems)
        self.assertEqual(self.groups_in(cleaned), {2})

    def test_a_day_with_no_groups_is_returned_untouched(self):
        exercises = [WARMUP, ex('Barbell Bench Press')]
        cleaned, problems = self.check(exercises)
        self.assertEqual(problems, [])
        self.assertEqual(cleaned, exercises)

    def test_junk_members_do_not_raise(self):
        views._validate_superset_groups('Push', [None, 7, WARMUP], False)


class PlanCreationTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='ss@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)

    def create(self, exercises):
        res = self.client.post(reverse('workout-plans'), {
            'title': 'P', 'duration_weeks': 1,
            'week': [{'day_number': 1, 'name': 'Push', 'is_rest_day': False,
                      'exercises': exercises}],
        }, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        return WorkoutPlan.objects.get(pk=res.data['id'])

    def test_a_valid_group_is_persisted(self):
        plan = self.create([
            WARMUP, ex('Barbell Back Squat'),
            ex('Barbell Bench Press', superset_group=1),
            ex('Chest-Supported T-Bar Row', superset_group=1),
        ])
        grouped = plan.days.first().exercises.exclude(composite=None)
        self.assertEqual(grouped.count(), 2)
        # One container shared by both members, which is what the integer meant.
        self.assertEqual(len({g.composite_id for g in grouped}), 1)

    def test_a_rejected_group_is_saved_ungrouped(self):
        """The workout survives; only the pairing is lost."""
        plan = self.create([
            WARMUP, ex('Barbell Back Squat'),
            ex('Barbell Bench Press', superset_group=1),
            ex('Push-ups', measurement_type=et.BODYWEIGHT_REPS,
               superset_group=1),
        ])
        day = plan.days.first()
        self.assertEqual(day.exercises.count(), 4)
        self.assertEqual(day.exercises.exclude(composite=None).count(), 0)

    def test_the_serializer_exposes_the_group(self):
        plan = self.create([
            WARMUP, ex('Barbell Back Squat'),
            ex('Barbell Bench Press', superset_group=1),
            ex('Chest-Supported T-Bar Row', superset_group=1),
        ])
        res = self.client.get(reverse('workout-plan-detail', args=[plan.id]))
        exercises = res.data['days'][0]['exercises']
        self.assertIn('composite_id', exercises[0])
        ids = [e['composite_id'] for e in exercises]
        # Standalone exercises carry nothing; the two members share one
        # container id. The value is opaque to the client -- it only ever
        # compares members, which is all the grouping needs.
        self.assertEqual(ids[:2], [None, None])
        self.assertIsNotNone(ids[2])
        self.assertEqual(ids[2], ids[3])
        # And the group's own rest is sent, which is the whole reason the
        # container exists: the card used to read the last member's value.
        rests = [e['composite_rest_seconds'] for e in exercises]
        self.assertEqual(rests[2], rests[3])


class SetLoggingUnaffectedTests(APITestCase):
    """
    The property that makes this cheap: a superset member logs exactly like a
    standalone exercise.
    """

    def setUp(self):
        self.user = User.objects.create_user(email='ss2@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)
        plan = WorkoutPlan.objects.create(user=self.user, title='P',
                                          duration_weeks=1, current_week=1)
        self.day = WorkoutDay.objects.create(plan=plan, day_number=1,
                                             week_number=1, name='Push')
        self.composite = Composite.objects.create(
            day=self.day, order=0, rest_seconds=90)
        self.a = Exercise.objects.create(
            day=self.day, name='Barbell Bench Press', sets=3, reps='8-12',
            measurement_type=et.WEIGHT_REPS, order=0, composite=self.composite)
        self.b = Exercise.objects.create(
            day=self.day, name='Push-ups', sets=3, reps='10-15',
            measurement_type=et.BODYWEIGHT_REPS, order=1, composite=self.composite)
        self.session = WorkoutSession.objects.create(
            user=self.user, exercise_day=self.day, day_name='Push')

    def log(self, exercise, **fields):
        payload = {'exercise_id': str(exercise.id), 'set_number': 1}
        payload.update(fields)
        return self.client.post(
            reverse('workout-session-log-set', args=[self.session.id]),
            payload, format='json')

    def test_each_member_logs_independently(self):
        self.assertEqual(self.log(self.a, weight_kg=60, reps_completed=10,
                                  is_completed=True).status_code, 200)
        self.assertEqual(self.log(self.b, reps_completed=12,
                                  is_completed=True).status_code, 200)
        self.assertEqual(SetLog.objects.filter(
            workout_session=self.session).count(), 2)

    def test_the_field_contract_still_applies_per_member(self):
        """A bodyweight member must not accept a weight just because its
        partner does."""
        self.log(self.b, weight_kg=99, reps_completed=12, is_completed=True)
        log = SetLog.objects.get(exercise=self.b)
        self.assertIsNone(log.weight_kg)
        self.assertEqual(log.reps_completed, 12)

    def test_progression_advice_is_unchanged_for_a_member(self):
        advice = views._build_exercise_advice(
            self.user, 'Barbell Bench Press', et.WEIGHT_REPS, '8-12', 'lb')
        self.assertEqual(
            set(advice), {'recommended_weight', 'recommended_target',
                          'coaching_note'})


class SupersetEndpointTests(APITestCase):
    """Manual pairing. The picker must not be able to create a bad group."""

    def setUp(self):
        self.user = User.objects.create_user(email='ss3@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb',
                                   personal_notes='')
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)
        plan = WorkoutPlan.objects.create(user=self.user, title='P',
                                          duration_weeks=1, current_week=1)
        self.day = WorkoutDay.objects.create(plan=plan, day_number=1,
                                             week_number=1, name='Push')
        names = [
            ('Warmup & Mobility', et.WARMUP),
            ('Barbell Back Squat', et.WEIGHT_REPS),      # first working
            ('Barbell Bench Press', et.WEIGHT_REPS),
            ('Chest-Supported T-Bar Row', et.WEIGHT_REPS),
            ('Dumbbell Bicep Curl', et.WEIGHT_REPS),
            ('Overhead Dumbbell Triceps Extension', et.WEIGHT_REPS),
            ('Push-ups', et.BODYWEIGHT_REPS),
        ]
        self.ex = {}
        for order, (name, mtype) in enumerate(names):
            self.ex[name] = Exercise.objects.create(
                day=self.day, name=name, sets=3, reps='8-12',
                measurement_type=mtype, order=order)

    def post(self, groups):
        return self.client.post(
            reverse('workout-day-supersets', args=[self.day.id]),
            {'groups': groups}, format='json')

    def ids(self, *names):
        return [str(self.ex[n].id) for n in names]

    def test_a_valid_pair_is_accepted_and_made_adjacent(self):
        res = self.post([self.ids('Barbell Bench Press',
                                  'Chest-Supported T-Bar Row')])
        self.assertEqual(res.status_code, 200, res.data)
        grouped = list(self.day.exercises.exclude(composite=None)
                       .order_by('order'))
        self.assertEqual(len(grouped), 2)
        self.assertEqual(grouped[1].order - grouped[0].order, 1)

    def test_a_bad_pair_is_refused_with_the_reason(self):
        res = self.post([self.ids('Barbell Bench Press', 'Push-ups')])
        self.assertEqual(res.status_code, 400)
        self.assertIn('not a good superset', res.data['detail'])

    def test_the_first_working_exercise_is_refused(self):
        res = self.post([self.ids('Barbell Back Squat',
                                  'Dumbbell Bicep Curl')])
        self.assertEqual(res.status_code, 400)
        self.assertIn('fresh', res.data['detail'])

    def test_a_single_member_group_is_refused(self):
        res = self.post([self.ids('Barbell Bench Press')])
        self.assertEqual(res.status_code, 400)

    def test_an_exercise_cannot_be_in_two_groups(self):
        res = self.post([
            self.ids('Barbell Bench Press', 'Chest-Supported T-Bar Row'),
            self.ids('Barbell Bench Press', 'Dumbbell Bicep Curl'),
        ])
        self.assertEqual(res.status_code, 400)
        self.assertIn('two groups', res.data['detail'])

    def test_more_than_the_cap_is_refused(self):
        res = self.post([
            self.ids('Barbell Bench Press', 'Chest-Supported T-Bar Row'),
            self.ids('Dumbbell Bicep Curl',
                     'Overhead Dumbbell Triceps Extension'),
            self.ids('Push-ups', 'Barbell Bench Press'),
        ])
        self.assertEqual(res.status_code, 400)

    def test_an_empty_list_clears_all_grouping(self):
        self.post([self.ids('Barbell Bench Press', 'Chest-Supported T-Bar Row')])
        self.assertEqual(
            self.day.exercises.exclude(composite=None).count(), 2)
        res = self.post([])
        self.assertEqual(res.status_code, 200)
        self.assertEqual(
            self.day.exercises.exclude(composite=None).count(), 0)

    def test_an_exercise_from_another_day_is_refused(self):
        other_plan = WorkoutPlan.objects.create(user=self.user, title='Q',
                                                duration_weeks=1)
        other_day = WorkoutDay.objects.create(plan=other_plan, day_number=1,
                                              week_number=1, name='Pull')
        stranger = Exercise.objects.create(
            day=other_day, name='Lat Pulldown (Wide Grip)', sets=3,
            reps='8-12', measurement_type=et.WEIGHT_REPS)
        res = self.post([[str(self.ex['Barbell Bench Press'].id),
                          str(stranger.id)]])
        self.assertEqual(res.status_code, 400)
        self.assertIn('not on this day', res.data['detail'])

    def test_another_users_day_is_404(self):
        other = User.objects.create_user(email='ss4@test.local', password='x')
        token, _ = Token.objects.get_or_create(user=other)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)
        self.assertEqual(self.post([]).status_code, 404)

    def test_a_malformed_body_is_a_400(self):
        res = self.client.post(
            reverse('workout-day-supersets', args=[self.day.id]),
            {'groups': 'nope'}, format='json')
        self.assertEqual(res.status_code, 400)

    def test_nothing_is_written_when_validation_fails(self):
        """Atomic: a refused request must not reorder or partly group."""
        before = list(self.day.exercises.order_by('order')
                      .values_list('id', 'order', 'composite_id'))
        self.post([self.ids('Barbell Bench Press', 'Push-ups')])
        after = list(self.day.exercises.order_by('order')
                     .values_list('id', 'order', 'composite_id'))
        self.assertEqual(before, after)


class SupersetOptionsTests(APITestCase):
    """What the picker is allowed to show."""

    def setUp(self):
        self.user = User.objects.create_user(email='ss5@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb',
                                   personal_notes='')
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)
        plan = WorkoutPlan.objects.create(user=self.user, title='P',
                                          duration_weeks=1, current_week=1)
        self.day = WorkoutDay.objects.create(plan=plan, day_number=1,
                                             week_number=1, name='Push')
        for order, (name, mtype) in enumerate([
            ('Warmup & Mobility', et.WARMUP),
            ('Barbell Back Squat', et.WEIGHT_REPS),
            ('Barbell Bench Press', et.WEIGHT_REPS),
            ('Chest-Supported T-Bar Row', et.WEIGHT_REPS),
            ('Push-ups', et.BODYWEIGHT_REPS),
        ]):
            Exercise.objects.create(day=self.day, name=name, sets=3,
                                    reps='8-12', measurement_type=mtype,
                                    order=order)

    def options(self):
        res = self.client.get(
            reverse('workout-day-superset-options', args=[self.day.id]))
        self.assertEqual(res.status_code, 200, res.data)
        return res.data

    def test_only_acceptable_partners_are_offered(self):
        data = self.options()
        by_name = {e['name']: e for e in data['exercises']}
        bench = by_name['Barbell Bench Press']
        partners = {p['name'] for p in bench['partners']}
        self.assertIn('Chest-Supported T-Bar Row', partners)
        self.assertNotIn('Push-ups', partners)

    def test_warmups_and_the_first_lift_are_not_offered(self):
        data = self.options()
        listed = {e['name'] for e in data['exercises']}
        self.assertNotIn('Warmup & Mobility', listed)
        self.assertNotIn('Barbell Back Squat', listed)
        for entry in data['exercises']:
            names = {p['name'] for p in entry['partners']}
            self.assertNotIn('Warmup & Mobility', names)
            self.assertNotIn('Barbell Back Squat', names)

    def test_each_partner_carries_a_kind_and_a_reason(self):
        for entry in self.options()['exercises']:
            for partner in entry['partners']:
                self.assertTrue(partner['kind'])
                self.assertTrue(partner['reason'])
                self.assertIn('same_sets', partner)

    def test_the_cap_is_reported(self):
        self.assertEqual(self.options()['max_groups'],
                         views.MAX_SUPERSET_GROUPS_PER_DAY)

    def test_every_offered_pair_is_actually_postable(self):
        """The picker and the writer must not disagree."""
        data = self.options()
        for entry in data['exercises']:
            for partner in entry['partners']:
                if not partner['same_sets']:
                    continue
                res = self.client.post(
                    reverse('workout-day-supersets', args=[self.day.id]),
                    {'groups': [[entry['id'], partner['id']]]}, format='json')
                self.assertEqual(
                    res.status_code, 200,
                    msg='%s + %s was offered but refused: %s'
                        % (entry['name'], partner['name'], res.data))

    def test_requires_auth(self):
        self.client.credentials()
        res = self.client.get(
            reverse('workout-day-superset-options', args=[self.day.id]))
        self.assertIn(res.status_code, (401, 403))


class BackLimitationTests(APITestCase):
    """The user's stated back problem must narrow what is offered."""

    def test_the_profile_note_is_detected(self):
        user = User.objects.create_user(email='ss6@test.local', password='x')
        UserProfile.objects.create(
            user=user, preferred_unit='lb',
            personal_notes='I am dealing with some lower back and upper back issue.')
        self.assertTrue(views._has_back_limitation(user))

    def test_an_empty_profile_is_not_flagged(self):
        user = User.objects.create_user(email='ss7@test.local', password='x')
        UserProfile.objects.create(user=user, preferred_unit='lb',
                                   personal_notes='')
        self.assertFalse(views._has_back_limitation(user))

    def test_a_user_with_no_profile_does_not_raise(self):
        user = User.objects.create_user(email='ss8@test.local', password='x')
        self.assertFalse(views._has_back_limitation(user))


class GroupSizeTests(APITestCase):
    """
    Trisets and giant sets. 3+ members already worked server-side -- the
    pairwise verdict loop never assumed two -- so what was missing was an upper
    bound and a picker that could build one.

    Bigger groups are something the USER or the agent builds deliberately. The
    generation prompt still teaches pairs only, so the model does not invent
    them.
    """

    def setUp(self):
        self.user = User.objects.create_user(email='sssize@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb',
                                   personal_notes='')
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)
        plan = WorkoutPlan.objects.create(user=self.user, title='P',
                                          duration_weeks=1, current_week=1)
        self.day = WorkoutDay.objects.create(plan=plan, day_number=1,
                                             week_number=1, name='Full')
        # Deliberately non-competing so pairwise checks pass and the SIZE rule
        # is what the assertions are actually measuring.
        for order, (name, mtype) in enumerate([
            ('Warmup & Mobility', et.WARMUP),
            ('Barbell Back Squat', et.WEIGHT_REPS),       # first working
            ('Dumbbell Bicep Curl', et.WEIGHT_REPS),
            ('Overhead Dumbbell Triceps Extension', et.WEIGHT_REPS),
            ('Plank', et.DURATION),
            ('Calf Raises', et.WEIGHT_REPS),
            ('Crunches', et.BODYWEIGHT_REPS),
        ]):
            Exercise.objects.create(day=self.day, name=name, sets=3,
                                    reps='8-12', measurement_type=mtype,
                                    order=order)

    def ids(self, *names):
        return [str(Exercise.objects.get(day=self.day, name=n).id)
                for n in names]

    def post(self, groups):
        return self.client.post(
            reverse('workout-day-supersets', args=[self.day.id]),
            {'groups': groups}, format='json')

    def test_a_group_of_one_is_still_refused(self):
        res = self.post([self.ids('Dumbbell Bicep Curl')])
        self.assertEqual(res.status_code, 400)
        self.assertIn('two', res.data['detail'])

    def test_a_group_beyond_the_cap_is_refused_as_a_circuit(self):
        """
        Five exercises back to back is a circuit, which has different rest
        handling. Refused rather than half-supported.
        """
        res = self.post([self.ids(
            'Dumbbell Bicep Curl', 'Overhead Dumbbell Triceps Extension',
            'Plank', 'Calf Raises', 'Crunches')])
        self.assertEqual(res.status_code, 400)
        self.assertIn('circuit', res.data['detail'])

    def test_the_cap_matches_what_the_picker_is_told(self):
        """
        The picker builds up to max_members. If the endpoint's cap and the
        advertised cap disagreed, the UI would let the user build a group the
        server then rejects.
        """
        res = self.client.get(
            reverse('workout-day-superset-options', args=[self.day.id]))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['max_members'], views.MAX_SUPERSET_MEMBERS)

    def test_pairwise_rules_still_apply_to_every_pair_in_a_triset(self):
        """
        A triset is only as valid as its worst pair. Squat is the first working
        exercise, so including it must fail for that reason -- proving the
        per-member rules are not skipped just because the group is bigger.
        """
        res = self.post([self.ids(
            'Barbell Back Squat', 'Dumbbell Bicep Curl', 'Plank')])
        self.assertEqual(res.status_code, 400)
        self.assertIn('fresh', res.data['detail'])


class RejectionsAreExplainedTests(APITestCase):
    """
    The picker shows WHY a pairing is refused.

    Silently omitting a partner looks like a bug: the user can see the exercise
    is on the day, so the question is why it is not offered. "Both push, both
    chest, each ruins the other" is an answer; an absence is not.
    """

    def setUp(self):
        self.user = User.objects.create_user(email='ssrej@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb',
                                   personal_notes='')
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)
        plan = WorkoutPlan.objects.create(user=self.user, title='P',
                                          duration_weeks=1, current_week=1)
        self.day = WorkoutDay.objects.create(plan=plan, day_number=1,
                                             week_number=1, name='Push')
        for order, (name, mtype) in enumerate([
            ('Warmup & Mobility', et.WARMUP),
            ('Barbell Back Squat', et.WEIGHT_REPS),
            ('Barbell Bench Press', et.WEIGHT_REPS),
            ('Push-ups', et.BODYWEIGHT_REPS),
            ('Chest-Supported T-Bar Row', et.WEIGHT_REPS),
        ]):
            Exercise.objects.create(day=self.day, name=name, sets=3,
                                    reps='8-12', measurement_type=mtype,
                                    order=order)

    def options(self):
        res = self.client.get(
            reverse('workout-day-superset-options', args=[self.day.id]))
        self.assertEqual(res.status_code, 200, res.data)
        return {e['name']: e for e in res.data['exercises']}

    def test_the_reported_bad_pairing_appears_with_a_reason(self):
        bench = self.options()['Barbell Bench Press']
        rejected = {p['name']: p for p in bench['rejected']}
        self.assertIn('Push-ups', rejected,
                      msg='a refused partner must be reported, not hidden')
        self.assertTrue(rejected['Push-ups']['reason'])
        self.assertTrue(rejected['Push-ups']['kind'])

    def test_accepted_and_rejected_do_not_overlap(self):
        for entry in self.options().values():
            accepted = {p['id'] for p in entry['partners']}
            refused = {p['id'] for p in entry['rejected']}
            self.assertEqual(accepted & refused, set(), msg=entry['name'])

    def test_a_good_partner_is_still_offered(self):
        """The guard: explaining rejections must not break acceptance."""
        bench = self.options()['Barbell Bench Press']
        self.assertIn('Chest-Supported T-Bar Row',
                      {p['name'] for p in bench['partners']})
