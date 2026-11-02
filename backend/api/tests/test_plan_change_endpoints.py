"""
Draft -> confirm -> apply -> verify, over HTTP.

The premise is that the model cannot be trusted to apply a plan change
faithfully; that is why the whole flow exists. So these tests are mostly about
what the server REFUSES:

  * applying a draft the plan has moved past (stale)
  * applying the same confirmed change twice
  * reporting success when the result did not match what was approved
  * touching another user's plan

The happy path gets one test. The refusals get the rest, because every one of
them is a way real training data gets quietly destroyed.
"""

import json

from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework.authtoken.models import Token

from api import exercise_names
from api.models import (
    Exercise, PlanChange, PlanSnapshot, User, UserProfile, WorkoutDay,
    WorkoutPlan,
)


class PlanChangeEndpointTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='pc@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)

        self.plan = WorkoutPlan.objects.create(
            user=self.user, title='P', is_active=True, duration_weeks=1)
        self.day = WorkoutDay.objects.create(
            plan=self.plan, day_number=1, week_number=1, name='Lower Body')
        for order, name in enumerate(['Barbell Bench Press',
                                      'Standing Calf Raise',
                                      'Leg Extension']):
            Exercise.objects.create(
                day=self.day, name=name, sets=3, reps='8-12', order=order,
                measurement_type='weight_reps')

    # -- helpers ----------------------------------------------------------

    def propose(self, changes=None, keep_list=None):
        return self.client.post(
            reverse('workout-plan-propose-change', args=[self.plan.id]),
            {'changes': changes if changes is not None else [
                {'day': 'Lower Body', 'name': 'Barbell Bench Press'},
                {'day': 'Lower Body', 'name': 'Machine Chest Press'}],
             'keep_list': keep_list or ['Standing Calf Raise']},
            format='json')

    def payload(self, exercises):
        return {'plan': {
            'title': 'P',
            'days': [{'day_number': 1, 'name': 'Lower Body',
                      'is_rest_day': False,
                      'exercises': [
                          {'name': n, 'sets': 3, 'reps': '8-12',
                           'measurement_type': 'weight_reps'}
                          for n in exercises]}],
        }}

    def apply(self, change_id, exercises):
        return self.client.post(
            reverse('workout-plan-apply-change',
                    args=[self.plan.id, change_id]),
            self.payload(exercises), format='json')

    # -- propose ----------------------------------------------------------

    def test_proposing_applies_nothing(self):
        before = list(Exercise.objects.filter(day__plan=self.plan)
                      .values_list('name', flat=True))
        resp = self.propose()
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertTrue(resp.data['needs_confirmation'])
        self.assertFalse(resp.data['applied'])
        self.assertEqual(
            list(Exercise.objects.filter(day__plan=self.plan)
                 .values_list('name', flat=True)), before)

    def test_proposing_records_the_before_snapshot(self):
        change_id = self.propose().data['change_id']
        snap = PlanSnapshot.objects.get(change_id=change_id,
                                        phase=PlanSnapshot.BEFORE)
        names = [e['name'] for e in snap.data['weeks']['1'][0]['exercises']]
        self.assertIn('Standing Calf Raise', names)

    def test_an_unknown_exercise_warns_and_does_not_block(self):
        """
        Rejecting would stop the model ever introducing a new movement. The
        hazard is silent near-matching, not novelty.
        """
        resp = self.propose(changes=[
            {'day': 'Lower Body', 'name': 'Jefferson Curl With A Kettlebell'}])
        self.assertEqual(resp.status_code, 201)
        self.assertEqual([w['type'] for w in resp.data['warnings']],
                         ['unknown_exercise'])
        self.assertIn('not in your library', resp.data['warnings'][0]['message'])

    def test_an_empty_draft_is_rejected(self):
        self.assertEqual(self.propose(changes=[]).status_code, 400)

    # -- apply ------------------------------------------------------------

    def test_applying_an_approved_change_is_clean(self):
        change_id = self.propose().data['change_id']
        resp = self.apply(change_id, ['Machine Chest Press',
                                      'Standing Calf Raise', 'Leg Extension'])
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertTrue(resp.data['applied'])
        self.assertTrue(resp.data['clean'], resp.data['verification_diff'])

    def test_a_dropped_exercise_is_reported_and_not_called_clean(self):
        """THE calf-raise case, end to end over HTTP."""
        change_id = self.propose().data['change_id']
        resp = self.apply(change_id, ['Machine Chest Press', 'Leg Extension'])

        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.data['clean'])
        diff = resp.data['verification_diff']
        self.assertIn('Standing Calf Raise',
                      [e['name'] for e in diff['unapproved']])
        self.assertIn('Standing Calf Raise',
                      [e['name'] for e in diff['keep_list_violations']])

    def test_applying_twice_applies_once(self):
        """
        A timed-out request that actually succeeded is the normal way this
        happens; double-applying would be an edit nobody approved.
        """
        change_id = self.propose().data['change_id']
        first = self.apply(change_id, ['Machine Chest Press',
                                       'Standing Calf Raise', 'Leg Extension'])
        self.assertEqual(first.status_code, 200)

        second = self.apply(change_id, ['Something Else Entirely'])
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.data['already_applied'])

        names = set(Exercise.objects.filter(day__plan=self.plan)
                    .values_list('name', flat=True))
        self.assertNotIn('Something Else Entirely', names)

    def test_a_stale_draft_is_refused(self):
        """
        The plan moved on between draft and confirm. Applying would overwrite
        work the user never saw -- the same data-loss class as the rebuild,
        narrower.
        """
        change_id = self.propose().data['change_id']

        Exercise.objects.create(
            day=self.day, name='Seated Leg Curl', sets=3, reps='8-12',
            order=9, measurement_type='weight_reps')

        resp = self.apply(change_id, ['Machine Chest Press'])
        self.assertEqual(resp.status_code, 409, resp.data)
        self.assertEqual(resp.data['code'], 'stale_draft')
        self.assertTrue(
            Exercise.objects.filter(day__plan=self.plan,
                                    name='Seated Leg Curl').exists())

    def test_a_refused_stale_draft_changes_nothing(self):
        change_id = self.propose().data['change_id']
        Exercise.objects.create(
            day=self.day, name='Seated Leg Curl', sets=3, reps='8-12',
            order=9, measurement_type='weight_reps')
        before = set(Exercise.objects.filter(day__plan=self.plan)
                     .values_list('name', flat=True))

        self.apply(change_id, ['Machine Chest Press'])

        self.assertEqual(
            set(Exercise.objects.filter(day__plan=self.plan)
                .values_list('name', flat=True)), before)
        PlanChange.objects.get(pk=change_id)
        self.assertFalse(PlanChange.objects.get(pk=change_id).is_applied)

    def test_both_snapshots_are_recorded_after_applying(self):
        change_id = self.propose().data['change_id']
        self.apply(change_id, ['Machine Chest Press', 'Standing Calf Raise',
                               'Leg Extension'])
        self.assertEqual(
            sorted(PlanSnapshot.objects.filter(change_id=change_id)
                   .values_list('phase', flat=True)),
            ['after', 'before'])

    # -- snapshot and ownership ------------------------------------------

    def test_the_snapshot_endpoint_returns_a_digest(self):
        resp = self.client.get(
            reverse('workout-plan-snapshot', args=[self.plan.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data['digest']), 64)
        self.assertIn('1', resp.data['snapshot']['weeks'])

    def test_another_users_plan_is_not_found(self):
        other = User.objects.create_user(email='o@test.local', password='x')
        theirs = WorkoutPlan.objects.create(user=other, title='Theirs')
        for name, args in [
                ('workout-plan-snapshot', [theirs.id]),
                ('workout-plan-propose-change', [theirs.id])]:
            url = reverse(name, args=args)
            resp = (self.client.get(url) if 'snapshot' in name
                    else self.client.post(url, {'changes': [{'name': 'x'}]},
                                          format='json'))
            self.assertEqual(resp.status_code, 404, name)

    def test_it_requires_authentication(self):
        self.client.credentials()
        resp = self.client.get(
            reverse('workout-plan-snapshot', args=[self.plan.id]))
        self.assertIn(resp.status_code, (401, 403))


class DraftBuildsItsOwnPayloadTests(APITestCase):
    """
    Confirming a draft applies it, without the model re-emitting the plan.

    That re-emission was the original failure: chat was shown all eight weeks
    and asked to return all eight, so one dropped line in a 56-day reply
    deleted an exercise and its history -- at ~32,000 tokens each way to
    change one thing.

    Here the model proposes OPERATIONS and the server applies them to the plan
    it already has. An exercise the draft does not mention cannot be dropped,
    because nothing re-states it.
    """

    def setUp(self):
        from rest_framework.authtoken.models import Token

        self.user = User.objects.create_user(email='build@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)

        self.plan = WorkoutPlan.objects.create(
            user=self.user, title='P', is_active=True, duration_weeks=2)
        for week in (1, 2):
            day = WorkoutDay.objects.create(
                plan=self.plan, day_number=1, week_number=week,
                name='Upper Push', order=week)
            for order, name in enumerate(['Barbell Bench Press',
                                          'Standing Calf Raise',
                                          'Leg Extension']):
                Exercise.objects.create(
                    day=day, name=name, sets=3, reps='8-12', order=order,
                    measurement_type='weight_reps')

    def propose(self, changes, keep_list=None):
        return self.client.post(
            reverse('workout-plan-propose-change', args=[self.plan.id]),
            {'changes': changes, 'keep_list': keep_list or []},
            format='json').data['change_id']

    def apply(self, change_id):
        return self.client.post(
            reverse('workout-plan-apply-change',
                    args=[self.plan.id, change_id]), {}, format='json')

    def names(self, week=1):
        return list(
            Exercise.objects
            .filter(day__plan=self.plan, day__week_number=week)
            .order_by('order').values_list('name', flat=True))

    def test_a_replacement_swaps_only_that_exercise(self):
        change_id = self.propose([{
            'day': 'Upper Push', 'action': 'replace',
            'replaces': 'Barbell Bench Press', 'name': 'Machine Chest Press',
        }])
        resp = self.apply(change_id)
        self.assertEqual(resp.status_code, 200, resp.data)

        # Canonical spelling: the server owns exercise identity, so
        # "Standing Calf Raise" is stored as "Standing Calf Raises (Machine)".
        names = self.names()
        self.assertIn('Machine Chest Press', names)
        self.assertNotIn('Barbell Bench Press', names)
        # The untouched exercises are still there.
        self.assertIn(exercise_names.canonical('Standing Calf Raise'), names)
        self.assertIn('Leg Extension', names)

    def test_a_replacement_keeps_the_slots_prescription(self):
        """A substitution changes the exercise, not the programming."""
        change_id = self.propose([{
            'day': 'Upper Push', 'action': 'replace',
            'replaces': 'Barbell Bench Press', 'name': 'Machine Chest Press',
        }])
        self.apply(change_id)
        swapped = Exercise.objects.get(
            day__plan=self.plan, day__week_number=1, name='Machine Chest Press')
        self.assertEqual(swapped.sets, 3)
        self.assertEqual(swapped.reps, '8-12')

    def test_a_day_without_a_week_applies_to_every_week(self):
        """
        "Take bench press out" means out, not out of week 1 only -- which
        would leave it in the others and look like the change failed.
        """
        change_id = self.propose([{
            'day': 'Upper Push', 'action': 'remove',
            'name': 'Barbell Bench Press',
        }])
        self.apply(change_id)
        self.assertNotIn('Barbell Bench Press', self.names(1))
        self.assertNotIn('Barbell Bench Press', self.names(2))

    def test_naming_a_week_scopes_the_change_to_it(self):
        change_id = self.propose([{
            'day': 'Upper Push', 'week': 2, 'action': 'remove',
            'name': 'Barbell Bench Press',
        }])
        self.apply(change_id)
        self.assertIn('Barbell Bench Press', self.names(1))
        self.assertNotIn('Barbell Bench Press', self.names(2))

    def test_an_addition_leaves_everything_else_alone(self):
        change_id = self.propose([{
            'day': 'Upper Push', 'action': 'add',
            'name': 'Cable Face Pulls', 'sets': 3, 'reps': '12-15',
        }])
        self.apply(change_id)
        names = self.names()
        self.assertIn('Cable Face Pulls', names)
        self.assertEqual(len(names), 4)

    def test_the_result_diffs_clean_against_the_approved_draft(self):
        change_id = self.propose(
            [{'day': 'Upper Push', 'action': 'replace',
              'replaces': 'Barbell Bench Press', 'name': 'Machine Chest Press'}],
            keep_list=['Standing Calf Raise'])
        resp = self.apply(change_id)
        self.assertTrue(resp.data['clean'], resp.data['verification_diff'])

    def test_a_draft_naming_an_unknown_day_is_refused(self):
        change_id = self.propose([{
            'day': 'Nonexistent Day', 'action': 'remove', 'name': 'Anything',
        }])
        resp = self.apply(change_id)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data['code'], 'bad_draft')

    def test_removing_something_already_gone_is_not_an_error(self):
        """Idempotence at the operation level: the end state is what matters."""
        change_id = self.propose([{
            'day': 'Upper Push', 'action': 'remove', 'name': 'Not In The Plan',
        }])
        self.assertEqual(self.apply(change_id).status_code, 200)
