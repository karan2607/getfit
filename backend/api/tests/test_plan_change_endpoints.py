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

from unittest import skipUnless

from django.db import connection
from django.test import TransactionTestCase
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
        self.assertIn(exercise_names.canonical('Leg Extension'), names)

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

    def test_a_draft_naming_an_unknown_day_is_refused_at_propose(self):
        """
        This used to be recorded happily and refused only at apply.

        That is the worst possible moment: the card renders, the user reads
        it, presses Confirm, and gets a 400. A draft that can never apply is
        not a pending decision -- the same reasoning that makes
        `_pending_change` withhold stale drafts. Refusing it here also lets
        the model correct itself in the same turn.
        """
        response = self.client.post(
            reverse('workout-plan-propose-change', args=[self.plan.id]),
            {'changes': [{'day': 'Nonexistent Day', 'action': 'remove',
                          'name': 'Anything'}]},
            format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('Nonexistent Day', response.data['detail'])
        self.assertFalse(PlanChange.objects.filter(plan=self.plan).exists())

    def test_removing_something_already_gone_is_not_an_error(self):
        """Idempotence at the operation level: the end state is what matters."""
        change_id = self.propose([{
            'day': 'Upper Push', 'action': 'remove', 'name': 'Not In The Plan',
        }])
        self.assertEqual(self.apply(change_id).status_code, 200)


class PendingChangeTests(APITestCase):
    """
    A draft the server is holding has to be answerable.

    Reported as "it is not able to update the plan now and just doesn't give
    me the button too". The draft was never the problem: it stayed applicable
    on the server the whole time. It was reachable from exactly one place --
    the React state of the turn that proposed it -- so a reload, a follow-up
    question, or a proposal made by anything other than the chat page left it
    permanently unconfirmable.
    """

    def setUp(self):
        self.user = User.objects.create_user(email='pend@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)

        self.plan = WorkoutPlan.objects.create(
            user=self.user, title='P', is_active=True, duration_weeks=1)
        self.day = WorkoutDay.objects.create(
            plan=self.plan, day_number=1, week_number=1, name='Push')
        Exercise.objects.create(
            day=self.day, name='Dumbbell Lateral Raises', sets=3, reps='8-12',
            order=0, measurement_type='weight_reps')

        self.url = reverse('workout-plan-propose-change', args=[self.plan.id])

    def propose(self, changes=None):
        response = self.client.post(self.url, {'changes': changes or [
            {'day': 'Push', 'action': 'remove',
             'name': 'Dumbbell Lateral Raises'}]}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return response.data['change_id']

    def test_a_pending_draft_is_offered_back(self):
        change_id = self.propose()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        pending = response.data['proposed_change']
        self.assertEqual(pending['change_id'], change_id)
        self.assertEqual(pending['plan_id'], str(self.plan.id))
        self.assertTrue(pending['needs_confirmation'])
        self.assertFalse(pending['applied'])

    def test_it_comes_back_in_the_shape_the_card_already_renders(self):
        """One card for a draft just proposed and a draft found on load."""
        self.propose()
        pending = self.client.get(self.url).data['proposed_change']
        self.assertEqual(
            sorted(pending),
            ['applied', 'change_id', 'draft', 'keep_list', 'needs_confirmation',
             'plan_id', 'warnings'])

    def test_the_draft_itself_is_returned_verbatim(self):
        self.propose()
        pending = self.client.get(self.url).data['proposed_change']
        self.assertEqual(pending['draft'], [
            {'day': 'Push', 'action': 'remove',
             'name': 'Dumbbell Lateral Raises'}])

    def test_nothing_is_pending_on_an_untouched_plan(self):
        self.assertIsNone(self.client.get(self.url).data['proposed_change'])

    def test_an_applied_draft_is_no_longer_pending(self):
        change_id = self.propose()
        applied = self.client.post(
            reverse('workout-plan-apply-change',
                    args=[self.plan.id, change_id]), {}, format='json')
        self.assertEqual(applied.status_code, 200, applied.data)
        self.assertIsNone(self.client.get(self.url).data['proposed_change'])

    def test_a_stale_draft_is_withheld_rather_than_offered(self):
        """
        Offering it would put up a Confirm button that can only fail: apply
        refuses on a base_hash mismatch. A draft the plan has moved past is
        not a pending decision, it is history.
        """
        self.propose()
        Exercise.objects.create(
            day=self.day, name='Cable Triceps Pushdown', sets=3, reps='10',
            order=1, measurement_type='weight_reps')
        self.assertIsNone(self.client.get(self.url).data['proposed_change'])

    def test_the_newest_draft_wins_when_several_are_pending(self):
        self.propose()
        second = self.propose([
            {'day': 'Push', 'action': 'add', 'name': 'Cable Lateral Raise'}])
        pending = self.client.get(self.url).data['proposed_change']
        self.assertEqual(pending['change_id'], second)

    def test_another_users_plan_is_not_readable(self):
        intruder = User.objects.create_user(email='nope@test.local', password='x')
        token, _ = Token.objects.get_or_create(user=intruder)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_it_needs_authentication(self):
        self.client.credentials()
        self.assertIn(self.client.get(self.url).status_code, (401, 403))


class PendingChangeWithoutAPlanIdTests(APITestCase):
    """
    The chat page has no plan id, and still has to find the draft.

    Reported twice as "still no update plan button". The plan-scoped lookup
    fixed the workout drawer, which knows which plan it is showing. The main
    chat page does not: a proposal made there can name any plan, including an
    inactive one, and the page only learns which from the draft itself. So
    the draft stayed reachable for exactly one render there -- verified on
    production, where reopening the session that proposed it showed the
    coach's "confirm change <id>" message and no button.
    """

    def setUp(self):
        self.user = User.objects.create_user(email='anyplan@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)
        self.url = reverse('workout-pending-change')

    def make_plan(self, title, active):
        plan = WorkoutPlan.objects.create(
            user=self.user, title=title, is_active=active, duration_weeks=1)
        day = WorkoutDay.objects.create(
            plan=plan, day_number=1, week_number=1, name='Push')
        Exercise.objects.create(
            day=day, name='Dumbbell Lateral Raises', sets=3, reps='8-12',
            order=0, measurement_type='weight_reps')
        return plan, day

    def propose(self, plan, changes=None):
        response = self.client.post(
            reverse('workout-plan-propose-change', args=[plan.id]),
            {'changes': changes or [{'day': 'Push', 'action': 'remove',
                                     'name': 'Dumbbell Lateral Raises'}]},
            format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return response.data['change_id']

    def test_a_draft_is_found_without_being_told_which_plan(self):
        plan, _ = self.make_plan('P', active=True)
        change_id = self.propose(plan)
        pending = self.client.get(self.url).data['proposed_change']
        self.assertEqual(pending['change_id'], change_id)
        self.assertEqual(pending['plan_id'], str(plan.id))

    def test_a_draft_on_an_inactive_plan_is_found_too(self):
        """Guessing the active plan would have missed this one entirely."""
        plan, _ = self.make_plan('Old', active=False)
        change_id = self.propose(plan)
        pending = self.client.get(self.url).data['proposed_change']
        self.assertEqual(pending['change_id'], change_id)

    def test_the_card_shape_is_identical_to_the_plan_scoped_lookup(self):
        plan, _ = self.make_plan('P', active=True)
        self.propose(plan)
        pending = self.client.get(self.url).data['proposed_change']
        self.assertEqual(
            sorted(pending),
            ['applied', 'change_id', 'draft', 'keep_list', 'needs_confirmation',
             'plan_id', 'warnings'])

    def test_nothing_pending_reads_as_nothing_pending(self):
        self.make_plan('P', active=True)
        self.assertIsNone(self.client.get(self.url).data['proposed_change'])

    def test_a_stale_draft_is_withheld_here_as_well(self):
        plan, day = self.make_plan('P', active=True)
        self.propose(plan)
        Exercise.objects.create(
            day=day, name='Cable Triceps Pushdown', sets=3, reps='10',
            order=1, measurement_type='weight_reps')
        self.assertIsNone(self.client.get(self.url).data['proposed_change'])

    def test_a_live_draft_is_still_found_past_a_stale_one(self):
        """
        A plan the user has moved on from must not hide an answerable draft
        on another plan -- otherwise one abandoned proposal silently disables
        the button forever.
        """
        stale_plan, stale_day = self.make_plan('Stale', active=False)
        self.propose(stale_plan)
        Exercise.objects.create(
            day=stale_day, name='Cable Triceps Pushdown', sets=3, reps='10',
            order=1, measurement_type='weight_reps')
        live_plan, _ = self.make_plan('Live', active=True)
        change_id = self.propose(live_plan)
        pending = self.client.get(self.url).data['proposed_change']
        self.assertEqual(pending['change_id'], change_id)

    def test_an_applied_draft_is_not_offered_again(self):
        plan, _ = self.make_plan('P', active=True)
        change_id = self.propose(plan)
        applied = self.client.post(
            reverse('workout-plan-apply-change', args=[plan.id, change_id]),
            {}, format='json')
        self.assertEqual(applied.status_code, 200, applied.data)
        self.assertIsNone(self.client.get(self.url).data['proposed_change'])

    def test_another_users_draft_is_never_offered(self):
        plan, _ = self.make_plan('P', active=True)
        self.propose(plan)
        intruder = User.objects.create_user(email='nope2@test.local', password='x')
        token, _ = Token.objects.get_or_create(user=intruder)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)
        self.assertIsNone(self.client.get(self.url).data['proposed_change'])

    def test_it_needs_authentication(self):
        self.client.credentials()
        self.assertIn(self.client.get(self.url).status_code, (401, 403))


class OneValidatorForBothDoorsTests(APITestCase):
    """
    The agent and the HTTP endpoint record drafts into the same table.

    They did not enforce the same rules: the endpoint checked a draft's
    shape, while `propose_plan_change` checked only that the list was not
    empty. So the model could propose against a day it had invented, the
    server recorded it, the card rendered, and Confirm answered 400 -- the
    "button that can only fail" that withholding stale drafts exists to
    prevent, reached through the other door.
    """

    def setUp(self):
        self.user = User.objects.create_user(email='bothdoors@test.local',
                                             password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        self.plan = WorkoutPlan.objects.create(
            user=self.user, title='P', is_active=True, duration_weeks=1)
        day = WorkoutDay.objects.create(
            plan=self.plan, day_number=1, week_number=1, name='Push')
        Exercise.objects.create(
            day=day, name='Dumbbell Lateral Raises', sets=3, reps='8-12',
            order=0, measurement_type='weight_reps')

    def propose_as_agent(self, changes):
        """Returns (result, error) -- a refusal comes back as a string."""
        from api import agent_tools
        return agent_tools.run_tool(
            self.user, 'propose_plan_change',
            {'plan_id': str(self.plan.id), 'changes': changes})

    def test_the_agent_cannot_record_a_draft_for_a_day_that_is_not_there(self):
        result, error = self.propose_as_agent([
            {'day': 'Leg Day That Does Not Exist', 'action': 'remove',
             'name': 'Anything'}])
        self.assertIsNone(result)
        self.assertIn('Leg Day That Does Not Exist', error)
        self.assertFalse(PlanChange.objects.filter(plan=self.plan).exists())

    def test_the_agent_cannot_record_a_move_with_nowhere_to_move_to(self):
        result, error = self.propose_as_agent([
            {'day': 'Push', 'action': 'move_day'}])
        self.assertIsNone(result)
        self.assertIn('position', error)
        self.assertFalse(PlanChange.objects.filter(plan=self.plan).exists())

    def test_a_good_draft_still_goes_through(self):
        result, error = self.propose_as_agent([
            {'day': 'Push', 'action': 'remove',
             'name': 'Dumbbell Lateral Raises'}])
        self.assertFalse(error)
        self.assertTrue(result['change_id'])

    def test_the_agent_card_carries_the_same_keys_as_the_reload_card(self):
        """
        Two places assert "one card renders either". They were not the same
        shape: keep_list was on the reload payload and missing here, so a
        client reading it off the chat card got undefined.
        """
        result, _error = self.propose_as_agent([
            {'day': 'Push', 'action': 'remove',
             'name': 'Dumbbell Lateral Raises'}])
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)
        pending = self.client.get(
            reverse('workout-plan-propose-change', args=[self.plan.id]),
        ).data['proposed_change']
        self.assertEqual(sorted(result), sorted(pending))


@skipUnless(connection.vendor == 'postgresql',
            'row locking is a no-op on SQLite, so this would pass for the '
            'wrong reason')
class ConcurrentApplyTests(TransactionTestCase):
    """
    Two confirms landing together must apply once.

    THIS DOES NOT RUN LOCALLY. Local tests use SQLite, where
    `select_for_update` is ignored and the whole table locks instead -- so
    the test would either error or pass without exercising anything. It is
    written against the production backend, and the same case is checked by
    hand on a deployed build. A skipped test is not a passing one, and this
    comment is here so nobody reads the green suite as covering it.

    `is_applied` and the staleness digest were read outside any transaction
    with no row lock, so a double tap -- or a client retrying an apply that
    had succeeded but timed out -- could have both requests pass the checks
    and both apply. For an `add` that is the exercise in the plan twice.

    TransactionTestCase, not APITestCase: the usual test case wraps
    everything in one transaction, so `select_for_update` has nothing to
    contend with and a concurrency test inside it proves nothing.
    """

    def setUp(self):
        self.user = User.objects.create_user(email='race@test.local',
                                             password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        self.token, _ = Token.objects.get_or_create(user=self.user)
        self.plan = WorkoutPlan.objects.create(
            user=self.user, title='P', is_active=True, duration_weeks=1)
        self.day = WorkoutDay.objects.create(
            plan=self.plan, day_number=1, week_number=1, name='Push')
        Exercise.objects.create(
            day=self.day, name='Dumbbell Lateral Raises', sets=3, reps='8-12',
            order=0, measurement_type='weight_reps')

    def test_two_simultaneous_confirms_add_the_exercise_once(self):
        from concurrent.futures import ThreadPoolExecutor
        from django.db import connections
        from rest_framework.test import APIClient

        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        change_id = client.post(
            reverse('workout-plan-propose-change', args=[self.plan.id]),
            {'changes': [{'day': 'Push', 'action': 'add',
                          'name': 'Cable Lateral Raise'}]},
            format='json').data['change_id']

        url = reverse('workout-plan-apply-change',
                      args=[self.plan.id, change_id])

        def apply_once():
            try:
                each = APIClient()
                each.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
                return each.post(url, {}, format='json').status_code
            finally:
                # Each thread gets its own connection; close it or the test
                # database cannot be torn down.
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            codes = [f.result() for f in
                     [pool.submit(apply_once), pool.submit(apply_once)]]

        self.assertEqual(sorted(codes), [200, 200])
        self.assertEqual(
            Exercise.objects.filter(day=self.day,
                                    name='Cable Lateral Raise').count(),
            1,
            'the exercise was added twice: the apply checks raced')
