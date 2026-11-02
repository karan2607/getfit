"""
The verification diff: proof that a plan change did what was approved.

The failure being caught is a plan edit that quietly drops something -- the
calf raise that vanished in a v2 update and had to be restored in v3. The
model saying "done" is not evidence, because the model is the component that
lost the exercise. So the server records both sides and computes the diff
itself.

The rule these tests mostly defend: a change that touched anything outside the
approved draft must never be reported as success, and a keep-list entry that
was removed is a failure of the change, not a footnote.
"""

from django.test import TestCase

from api.models import Composite, Exercise, User, WorkoutDay, WorkoutPlan
from api.plan_changes import diff_snapshots, snapshot_plan


class SnapshotTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='snap@test.local', password='x')
        self.plan = WorkoutPlan.objects.create(
            user=self.user, title='P', is_active=True)
        self.day = WorkoutDay.objects.create(
            plan=self.plan, day_number=1, week_number=1, name='Upper Push')
        self.bench = Exercise.objects.create(
            day=self.day, name='Barbell Bench Press', sets=4, reps='6-10',
            order=0, measurement_type='weight_reps')

    def test_it_records_every_tracked_field(self):
        snap = snapshot_plan(self.plan)
        entry = snap['weeks']['1'][0]['exercises'][0]
        self.assertEqual(entry['name'], 'Barbell Bench Press')
        self.assertEqual(entry['sets'], 4)
        self.assertEqual(entry['reps'], '6-10')
        self.assertEqual(entry['measurement_type'], 'weight_reps')
        self.assertIsNone(entry['composite_id'])

    def test_it_can_be_scoped_to_named_weeks(self):
        WorkoutDay.objects.create(
            plan=self.plan, day_number=1, week_number=2, name='Upper Push')
        self.assertEqual(sorted(snapshot_plan(self.plan)['weeks']), ['1', '2'])
        self.assertEqual(
            sorted(snapshot_plan(self.plan, week_numbers=[2])['weeks']), ['2'])

    def test_composite_membership_is_recorded(self):
        composite = Composite.objects.create(day=self.day, order=0, rest_seconds=90)
        self.bench.composite = composite
        self.bench.save()
        entry = snapshot_plan(self.plan)['weeks']['1'][0]['exercises'][0]
        self.assertEqual(entry['composite_id'], str(composite.id))


class DiffTests(TestCase):
    """Hand-built snapshots, so the diff is tested without the ORM."""

    def snap(self, exercises):
        return {
            'plan_id': 'p', 'title': 'P',
            'weeks': {'1': [{
                'day_id': 'd1', 'day_number': 1, 'name': 'Upper Push',
                'is_rest_day': False, 'exercises': exercises,
            }]},
        }

    def ex(self, pk, name, **over):
        base = {'id': pk, 'name': name, 'sets': 4, 'reps': '6-10',
                'rest_seconds': 90, 'notes': '', 'measurement_type':
                'weight_reps', 'composite_id': None}
        base.update(over)
        return base

    def test_an_untouched_plan_diffs_to_nothing(self):
        snap = self.snap([self.ex('1', 'Barbell Bench Press')])
        result = diff_snapshots(snap, snap)
        self.assertEqual(result['added'], [])
        self.assertEqual(result['removed'], [])
        self.assertEqual(result['changed'], [])
        self.assertTrue(result['clean'])

    def test_it_sees_an_added_and_a_removed_exercise(self):
        before = self.snap([self.ex('1', 'Barbell Bench Press')])
        after = self.snap([self.ex('2', 'Machine Chest Press')])
        result = diff_snapshots(before, after)
        self.assertEqual([e['name'] for e in result['added']],
                         ['Machine Chest Press'])
        self.assertEqual([e['name'] for e in result['removed']],
                         ['Barbell Bench Press'])

    def test_it_reports_which_fields_changed(self):
        before = self.snap([self.ex('1', 'Barbell Bench Press', sets=4)])
        after = self.snap([self.ex('1', 'Barbell Bench Press', sets=3,
                                   reps='8-12')])
        changed = diff_snapshots(before, after)['changed']
        self.assertEqual(len(changed), 1)
        self.assertEqual(changed[0]['changes'],
                         {'sets': (4, 3), 'reps': ('6-10', '8-12')})

    def test_reordering_alone_is_not_a_change(self):
        """
        `_apply_plan_diff` rewrites `order` on every pass to keep composite
        members contiguous. Tracking it would flag every edit as touching
        everything.
        """
        before = self.snap([self.ex('1', 'A'), self.ex('2', 'B')])
        after = self.snap([self.ex('2', 'B'), self.ex('1', 'A')])
        self.assertTrue(diff_snapshots(before, after)['clean'])

    # -- the rule that matters -------------------------------------------

    def test_an_unapproved_removal_is_flagged(self):
        """THE calf-raise case: an exercise disappears that nobody approved."""
        before = self.snap([self.ex('1', 'Barbell Bench Press'),
                            self.ex('2', 'Standing Calf Raise')])
        after = self.snap([self.ex('3', 'Machine Chest Press')])
        result = diff_snapshots(
            before, after,
            approved=[{'day': 'Upper Push', 'name': 'Machine Chest Press'},
                      {'day': 'Upper Push', 'name': 'Barbell Bench Press'}])

        self.assertFalse(result['clean'])
        self.assertEqual([e['name'] for e in result['unapproved']],
                         ['Standing Calf Raise'])

    def test_an_approved_change_is_clean(self):
        before = self.snap([self.ex('1', 'Barbell Bench Press')])
        after = self.snap([self.ex('2', 'Machine Chest Press')])
        result = diff_snapshots(
            before, after,
            approved=[{'day': 'Upper Push', 'name': 'Barbell Bench Press'},
                      {'day': 'Upper Push', 'name': 'Machine Chest Press'}])
        self.assertTrue(result['clean'])
        self.assertEqual(result['unapproved'], [])

    def test_approval_matches_on_identity_not_spelling(self):
        """
        The draft and the stored row routinely differ in spelling, and a
        string compare would report every approved change as unapproved.
        """
        before = self.snap([self.ex('1', 'Dumbbell Lateral Raises')])
        after = self.snap([self.ex('1', 'Dumbbell Lateral Raises', sets=3)])
        result = diff_snapshots(
            before, after,
            approved=[{'day': 'Upper Push', 'name': 'Dumbbell Lateral Raise'}])
        self.assertTrue(result['clean'])

    def test_no_draft_means_nothing_can_be_unapproved(self):
        """`approved=None` is "not checked", which is not "approved nothing"."""
        before = self.snap([self.ex('1', 'A')])
        after = self.snap([self.ex('2', 'B')])
        self.assertTrue(diff_snapshots(before, after, approved=None)['clean'])

    def test_an_empty_draft_approves_nothing(self):
        before = self.snap([self.ex('1', 'A')])
        after = self.snap([self.ex('2', 'B')])
        result = diff_snapshots(before, after, approved=[])
        self.assertFalse(result['clean'])
        self.assertEqual(len(result['unapproved']), 2)

    # -- keep list --------------------------------------------------------

    def test_a_removed_keep_list_entry_fails_the_change(self):
        before = self.snap([self.ex('1', 'Standing Calf Raise')])
        after = self.snap([])
        result = diff_snapshots(before, after,
                                keep_list=['Standing Calf Raise'])
        self.assertFalse(result['clean'])
        self.assertEqual([e['name'] for e in result['keep_list_violations']],
                         ['Standing Calf Raise'])

    def test_an_altered_keep_list_entry_also_fails(self):
        before = self.snap([self.ex('1', 'Standing Calf Raise', sets=3)])
        after = self.snap([self.ex('1', 'Standing Calf Raise', sets=1)])
        result = diff_snapshots(before, after,
                                keep_list=['Standing Calf Raise'])
        self.assertFalse(result['clean'])
        self.assertEqual(len(result['keep_list_violations']), 1)

    def test_an_untouched_keep_list_entry_is_fine(self):
        before = self.snap([self.ex('1', 'Standing Calf Raise'),
                            self.ex('2', 'Barbell Bench Press')])
        after = self.snap([self.ex('1', 'Standing Calf Raise'),
                           self.ex('3', 'Machine Chest Press')])
        result = diff_snapshots(
            before, after,
            approved=[{'day': 'Upper Push', 'name': 'Barbell Bench Press'},
                      {'day': 'Upper Push', 'name': 'Machine Chest Press'}],
            keep_list=['Standing Calf Raise'])
        self.assertTrue(result['clean'])


class DiffCatchesARealDroppedExerciseTests(TestCase):
    """
    End to end through the real save path, not hand-built snapshots.

    This is the reported bug in miniature: an edit asks for one substitution,
    the payload silently omits an unrelated exercise, and without a diff the
    reply says "plan updated successfully". The server has to catch it.
    """

    def setUp(self):
        from rest_framework.authtoken.models import Token
        from api.models import UserProfile

        self.user = User.objects.create_user(email='drop@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials = None
        self.token = token

        self.plan = WorkoutPlan.objects.create(
            user=self.user, title='P', is_active=True, duration_weeks=1)
        day = WorkoutDay.objects.create(
            plan=self.plan, day_number=1, week_number=1, name='Lower Body')
        for order, (name, sets) in enumerate([
                ('Barbell Bench Press', 4),
                ('Standing Calf Raise', 3),
                ('Leg Extension', 3)]):
            Exercise.objects.create(
                day=day, name=name, sets=sets, reps='8-12', order=order,
                measurement_type='weight_reps')

    def put(self, payload):
        from django.urls import reverse
        return self.client.put(
            reverse('workout-plan-detail', args=[self.plan.id]),
            data=payload, content_type='application/json',
            HTTP_AUTHORIZATION='Token ' + self.token.key)

    def test_a_silently_dropped_exercise_is_reported_as_unapproved(self):
        import json

        before = snapshot_plan(self.plan)

        # The user approved ONE substitution. The payload also drops the calf
        # raise, which nobody asked for.
        payload = {
            'title': 'P',
            'days': [{
                'day_number': 1, 'name': 'Lower Body', 'is_rest_day': False,
                'exercises': [
                    {'name': 'Machine Chest Press', 'sets': 4, 'reps': '8-12',
                     'measurement_type': 'weight_reps'},
                    {'name': 'Leg Extension', 'sets': 3, 'reps': '8-12',
                     'measurement_type': 'weight_reps'},
                ],
            }],
        }
        resp = self.put(json.dumps(payload))
        self.assertEqual(resp.status_code, 200, getattr(resp, 'data', resp))

        after = snapshot_plan(self.plan)
        result = diff_snapshots(
            before, after,
            approved=[{'day': 'Lower Body', 'name': 'Barbell Bench Press'},
                      {'day': 'Lower Body', 'name': 'Machine Chest Press'}],
            keep_list=['Standing Calf Raise'])

        self.assertFalse(
            result['clean'],
            'the diff reported success while an exercise had been dropped')
        self.assertIn('Standing Calf Raise',
                      [e['name'] for e in result['unapproved']])
        self.assertIn('Standing Calf Raise',
                      [e['name'] for e in result['keep_list_violations']])

    def test_the_approved_substitution_alone_diffs_clean(self):
        import json

        before = snapshot_plan(self.plan)
        payload = {
            'title': 'P',
            'days': [{
                'day_number': 1, 'name': 'Lower Body', 'is_rest_day': False,
                'exercises': [
                    {'name': 'Machine Chest Press', 'sets': 4, 'reps': '8-12',
                     'measurement_type': 'weight_reps'},
                    {'name': 'Standing Calf Raise', 'sets': 3, 'reps': '8-12',
                     'measurement_type': 'weight_reps'},
                    {'name': 'Leg Extension', 'sets': 3, 'reps': '8-12',
                     'measurement_type': 'weight_reps'},
                ],
            }],
        }
        self.assertEqual(self.put(json.dumps(payload)).status_code, 200)

        result = diff_snapshots(
            before, snapshot_plan(self.plan),
            approved=[{'day': 'Lower Body', 'name': 'Barbell Bench Press'},
                      {'day': 'Lower Body', 'name': 'Machine Chest Press'}],
            keep_list=['Standing Calf Raise'])
        self.assertTrue(result['clean'],
                        'unapproved: %s' % result['unapproved'])


class CanonicalisationIsNotAChangeTests(TestCase):
    """
    The server renames on save, and that must not read as an unapproved edit.

    A payload saying "Standing Calf Raise" is stored as "Standing Calf Raises
    (Machine)". Comparing names as strings flagged that on EVERY edit, so the
    diff reported unapproved changes every single time -- and a diff that
    cries wolf constantly is one nobody reads, which quietly defeats the whole
    point of verifying changes.
    """

    def snap(self, name):
        return {
            'plan_id': 'p', 'title': 'P',
            'weeks': {'1': [{
                'day_id': 'd', 'day_number': 1, 'name': 'Day', 
                'is_rest_day': False,
                'exercises': [{
                    'id': '1', 'name': name, 'sets': 3, 'reps': '8-12',
                    'rest_seconds': None, 'notes': '',
                    'measurement_type': 'weight_reps', 'composite_id': None,
                }],
            }]},
        }

    def test_canonicalising_a_name_is_not_reported(self):
        result = diff_snapshots(self.snap('Dumbbell Lateral Raise'),
                                self.snap('Dumbbell Lateral Raises'))
        self.assertEqual(result['changed'], [])
        self.assertTrue(result['clean'])

    def test_the_pushup_spellings_are_one_exercise(self):
        """Depends on the alias added alongside the library seed."""
        result = diff_snapshots(self.snap('pushups'), self.snap('Push-ups'))
        self.assertTrue(result['clean'])

    def test_a_genuine_rename_is_still_reported(self):
        result = diff_snapshots(self.snap('Barbell Bench Press'),
                                self.snap('Machine Chest Press'))
        self.assertEqual(len(result['changed']), 1)
        self.assertIn('name', result['changed'][0]['changes'])


class ReplacementApprovesBothSidesTests(TestCase):
    """
    A `replace` draft approves the exercise going OUT as well as the one
    coming in.

    The draft names the incoming exercise in `name` and the outgoing one in
    `replaces`. Matching only on `name` approved the addition and flagged the
    removal, so every substitution -- the most common edit there is --
    reported "Applied, but not as approved". A diff that cries wolf on the
    normal case is a diff nobody reads, which quietly undoes the whole point.
    """

    def snap(self, names):
        return {
            'plan_id': 'p', 'title': 'P',
            'weeks': {'1': [{
                'day_id': 'd', 'day_number': 1, 'name': 'Upper Push',
                'is_rest_day': False,
                'exercises': [
                    {'id': str(i), 'name': n, 'sets': 3, 'reps': '8-12',
                     'rest_seconds': None, 'notes': '',
                     'measurement_type': 'weight_reps', 'composite_id': None}
                    for i, n in enumerate(names)
                ],
            }]},
        }

    def test_a_replacement_diffs_clean(self):
        result = diff_snapshots(
            self.snap(['Barbell Bench Press', 'Leg Extension']),
            self.snap(['Machine Chest Press', 'Leg Extension']),
            approved=[{'day': 'Upper Push', 'action': 'replace',
                       'replaces': 'Barbell Bench Press',
                       'name': 'Machine Chest Press'}],
        )
        self.assertTrue(result['clean'], result['unapproved'])

    def test_an_unrelated_removal_is_still_caught(self):
        """The fix must not blanket-approve everything a draft touches."""
        result = diff_snapshots(
            self.snap(['Barbell Bench Press', 'Standing Calf Raise']),
            self.snap(['Machine Chest Press']),
            approved=[{'day': 'Upper Push', 'action': 'replace',
                       'replaces': 'Barbell Bench Press',
                       'name': 'Machine Chest Press'}],
        )
        self.assertFalse(result['clean'])
        self.assertEqual([e['name'] for e in result['unapproved']],
                         ['Standing Calf Raise'])
