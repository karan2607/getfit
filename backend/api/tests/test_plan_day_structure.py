"""
Renaming a day, and moving it to another slot in the week.

Why these exist at all: asked to restructure a week -- "make the pull day the
second day, and call the accessory day Pull + Abs" -- the coach replied that
it could not, because the draft vocabulary could only ever address an
exercise. That is a schema gap presenting as a model limitation, and the user
reported it as the AI being unable to move days around.

The rule most of these defend is the one that makes a move safe: a day moves
by changing its OWN day_number, never by handing its exercises to another
day's row. Days are matched on (week_number, day_number), so a content swap
would give day 2's exercises to day 3's row -- every Exercise pk replaced and
every SetLog orphaned from the movement it was logged against, which is the
exact destruction `_apply_plan_diff` exists to prevent.
"""

from django.test import TestCase
from django.urls import reverse
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from api import plan_changes
from api.models import (
    Exercise, PlanChange, SetLog, User, UserProfile, WorkoutDay, WorkoutPlan,
    WorkoutSession,
)
from api.plan_changes import (
    DraftError, apply_day_structure, build_payload, diff_snapshots,
    library_warnings, snapshot_plan,
)


DAYS = ['Upper Push', 'Lower Body', 'Upper Pull', 'Cardio, Abs & Accessories']
LIFTS = ['Machine Chest Press', 'Leg Press', 'Lat Pulldown', 'Cable Curl']


def build_plan(user, weeks=2):
    plan = WorkoutPlan.objects.create(
        user=user, title='P', is_active=True, duration_weeks=weeks)
    for week in range(1, weeks + 1):
        for number, name in enumerate(DAYS, start=1):
            day = WorkoutDay.objects.create(
                plan=plan, day_number=number, week_number=week, name=name,
                order=number - 1)
            # A plain weight/reps movement per day. The name deliberately
            # carries no cardio word: the server reclassifies by name on
            # save, and a fixture called "Cardio Lift" would churn its own
            # measurement_type and read as an unapproved change.
            Exercise.objects.create(
                day=day, name=LIFTS[number - 1], sets=3, reps='8-12',
                order=0, measurement_type='weight_reps')
    return plan


def positions(plan, week=1):
    return [(d.day_number, d.name) for d in
            plan.days.filter(week_number=week).order_by('day_number')]


class RenameDayTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='rn@test.local', password='x')
        self.plan = build_plan(self.user)

    def test_it_renames_the_day_in_every_week(self):
        apply_day_structure(self.plan, [
            {'action': 'rename_day', 'day': 'Cardio, Abs & Accessories',
             'name': 'Pull + Abs'}])
        for week in (1, 2):
            self.assertIn((4, 'Pull + Abs'), positions(self.plan, week))

    def test_one_week_can_be_renamed_alone(self):
        apply_day_structure(self.plan, [
            {'action': 'rename_day', 'day': 'Upper Pull',
             'name': 'Pull + Abs', 'week': 2}])
        self.assertIn((3, 'Upper Pull'), positions(self.plan, 1))
        self.assertIn((3, 'Pull + Abs'), positions(self.plan, 2))

    def test_the_day_keeps_its_row_and_its_exercises(self):
        day = self.plan.days.get(week_number=1, day_number=3)
        before_ids = {str(e.id) for e in day.exercises.all()}
        apply_day_structure(self.plan, [
            {'action': 'rename_day', 'day': 'Upper Pull', 'name': 'Pull Day'}])
        day.refresh_from_db()
        self.assertEqual(day.name, 'Pull Day')
        self.assertEqual({str(e.id) for e in day.exercises.all()}, before_ids)

    def test_a_rename_with_no_new_name_is_refused(self):
        with self.assertRaises(DraftError):
            apply_day_structure(self.plan, [
                {'action': 'rename_day', 'day': 'Upper Pull'}])

    def test_a_rename_of_a_day_that_is_not_there_is_refused(self):
        with self.assertRaises(DraftError):
            apply_day_structure(self.plan, [
                {'action': 'rename_day', 'day': 'Mobility', 'name': 'Pull'}])

    def test_renaming_to_the_same_name_reports_nothing(self):
        ops, renames = apply_day_structure(self.plan, [
            {'action': 'rename_day', 'day': 'Upper Pull', 'name': 'Upper Pull'}])
        self.assertEqual(ops, [])
        self.assertEqual(renames, {})


class MoveDayTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='mv@test.local', password='x')
        self.plan = build_plan(self.user)

    def test_it_moves_the_day_and_shifts_the_rest_around_it(self):
        apply_day_structure(self.plan, [
            {'action': 'move_day', 'day': 'Upper Pull', 'to_position': 2}])
        self.assertEqual(
            [name for _n, name in positions(self.plan, 1)],
            ['Upper Push', 'Upper Pull', 'Lower Body',
             'Cardio, Abs & Accessories'])
        self.assertEqual([n for n, _name in positions(self.plan, 1)],
                         [1, 2, 3, 4])

    def test_it_moves_the_day_in_every_week(self):
        apply_day_structure(self.plan, [
            {'action': 'move_day', 'day': 'Upper Pull', 'to_position': 2}])
        self.assertEqual([name for _n, name in positions(self.plan, 2)][1],
                         'Upper Pull')

    def test_the_rows_survive_the_move_with_their_history(self):
        """The whole reason a move is not a content swap."""
        day = self.plan.days.get(week_number=1, day_number=3)
        day_id = day.id
        exercise = day.exercises.first()
        exercise_id = exercise.id
        session = WorkoutSession.objects.create(
            user=self.user, exercise_day=day, day_name=day.name)
        SetLog.objects.create(
            workout_session=session, exercise=exercise,
            exercise_name=exercise.name, set_number=1,
            reps_completed=8, weight_kg=60)

        apply_day_structure(self.plan, [
            {'action': 'move_day', 'day': 'Upper Pull', 'to_position': 2}])

        day.refresh_from_db()
        self.assertEqual(day.id, day_id)
        self.assertEqual(day.day_number, 2)
        self.assertEqual(day.exercises.first().id, exercise_id)
        session.refresh_from_db()
        self.assertEqual(session.exercise_day_id, day_id)
        self.assertEqual(SetLog.objects.get().exercise_id, exercise_id)

    def test_a_position_past_the_end_means_last(self):
        apply_day_structure(self.plan, [
            {'action': 'move_day', 'day': 'Upper Push', 'to_position': 99}])
        self.assertEqual([name for _n, name in positions(self.plan, 1)][-1],
                         'Upper Push')

    def test_moving_a_day_where_it_already_is_reports_nothing(self):
        ops, _renames = apply_day_structure(self.plan, [
            {'action': 'move_day', 'day': 'Upper Push', 'to_position': 1}])
        self.assertEqual(ops, [])

    def test_a_move_with_no_position_is_refused(self):
        with self.assertRaises(DraftError):
            apply_day_structure(self.plan, [
                {'action': 'move_day', 'day': 'Upper Pull'}])

    def test_a_position_below_one_is_refused(self):
        with self.assertRaises(DraftError):
            apply_day_structure(self.plan, [
                {'action': 'move_day', 'day': 'Upper Pull', 'to_position': 0}])

    def test_day_numbers_stay_unique_through_the_reshuffle(self):
        apply_day_structure(self.plan, [
            {'action': 'move_day', 'day': 'Cardio, Abs & Accessories',
             'to_position': 1}])
        numbers = [n for n, _name in positions(self.plan, 1)]
        self.assertEqual(numbers, sorted(set(numbers)))
        self.assertEqual(numbers, [1, 2, 3, 4])


class DraftWithDayAndExerciseChangesTests(TestCase):
    """A restructure is one proposal: move the days, rename them, fill them."""

    def setUp(self):
        self.user = User.objects.create_user(email='mix@test.local', password='x')
        self.plan = build_plan(self.user, weeks=1)

    def test_a_later_change_may_still_call_the_day_by_its_old_name(self):
        draft = [
            {'action': 'rename_day', 'day': 'Cardio, Abs & Accessories',
             'name': 'Pull + Abs'},
            {'action': 'add', 'day': 'Cardio, Abs & Accessories',
             'name': 'Seated Cable Row', 'sets': 3, 'reps': '8'},
        ]
        apply_day_structure(self.plan, draft)
        payload, touched = build_payload(self.plan, draft)
        day = next(d for d in payload['days'] if d['name'] == 'Pull + Abs')
        self.assertIn('Seated Cable Row', [e['name'] for e in day['exercises']])
        self.assertEqual(touched, {1})

    def test_a_day_only_draft_still_speaks_for_its_week(self):
        """Otherwise the payload has no days and validation rejects it."""
        draft = [{'action': 'move_day', 'day': 'Upper Pull', 'to_position': 2}]
        apply_day_structure(self.plan, draft)
        payload, touched = build_payload(self.plan, draft)
        self.assertEqual(touched, {1})
        self.assertEqual(len(payload['days']), len(DAYS))

    def test_a_day_name_is_never_reported_as_an_unknown_exercise(self):
        warnings = library_warnings([
            {'action': 'rename_day', 'day': 'Upper Pull', 'name': 'Pull + Abs'},
        ], user=self.user)
        self.assertEqual(warnings, [])

    def test_an_unknown_action_is_still_refused(self):
        with self.assertRaises(DraftError):
            build_payload(self.plan, [
                {'action': 'reshuffle', 'day': 'Upper Pull', 'name': 'x'}])


class DayDiffTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='dd@test.local', password='x')
        self.plan = build_plan(self.user, weeks=1)

    def test_a_move_is_reported_as_a_day_change_and_not_as_exercise_churn(self):
        before = snapshot_plan(self.plan)
        draft = [{'action': 'move_day', 'day': 'Upper Pull', 'to_position': 2}]
        apply_day_structure(self.plan, draft)
        diff = diff_snapshots(before, snapshot_plan(self.plan), approved=draft)

        self.assertEqual(diff['added'], [])
        self.assertEqual(diff['removed'], [])
        self.assertEqual(diff['changed'], [])
        self.assertTrue(diff['clean'])
        moved = [d for d in diff['day_changes'] if d['name'] == 'Upper Pull']
        self.assertEqual(len(moved), 1)
        self.assertEqual((moved[0]['from_position'], moved[0]['position']),
                         (3, 2))

    def test_a_rename_is_reported_with_both_names(self):
        before = snapshot_plan(self.plan)
        draft = [{'action': 'rename_day', 'day': 'Upper Pull',
                  'name': 'Pull + Abs'}]
        apply_day_structure(self.plan, draft)
        diff = diff_snapshots(before, snapshot_plan(self.plan), approved=draft)
        entry = next(d for d in diff['day_changes'] if d['name'] == 'Pull + Abs')
        self.assertEqual(entry['from_name'], 'Upper Pull')
        self.assertTrue(diff['clean'])

    def test_an_untouched_plan_reports_no_day_changes(self):
        snap = snapshot_plan(self.plan)
        self.assertEqual(diff_snapshots(snap, snap)['day_changes'], [])

    def test_an_exercise_change_on_a_renamed_day_still_counts_as_approved(self):
        """
        The draft names the day the user named; the plan now calls it
        something else. Without the rename map this reads as unapproved, and
        the diff cries wolf on the restructure it was asked to make.
        """
        before = snapshot_plan(self.plan)
        draft = [
            {'action': 'rename_day', 'day': 'Upper Pull', 'name': 'Pull + Abs'},
            {'action': 'add', 'day': 'Upper Pull', 'name': 'Seated Cable Row'},
        ]
        apply_day_structure(self.plan, draft)
        day = self.plan.days.get(week_number=1, name='Pull + Abs')
        Exercise.objects.create(day=day, name='Seated Cable Row', sets=3,
                                reps='8-12', order=9,
                                measurement_type='weight_reps')
        diff = diff_snapshots(before, snapshot_plan(self.plan), approved=draft)
        self.assertEqual([e['name'] for e in diff['added']],
                         ['Seated Cable Row'])
        self.assertTrue(diff['clean'])


class DayStructureOverHttpTests(APITestCase):
    """Propose -> confirm -> apply, the way the coach actually reaches this."""

    def setUp(self):
        self.user = User.objects.create_user(email='http@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)
        self.plan = build_plan(self.user, weeks=1)

    def apply_draft(self, changes):
        proposed = self.client.post(
            reverse('workout-plan-propose-change', args=[self.plan.id]),
            {'changes': changes}, format='json')
        self.assertEqual(proposed.status_code, 201, proposed.data)
        change_id = proposed.data['change_id']
        return self.client.post(
            reverse('workout-plan-apply-change',
                    args=[self.plan.id, change_id]), {}, format='json')

    def test_the_week_is_reordered_and_the_diff_is_clean(self):
        response = self.apply_draft([
            {'action': 'move_day', 'day': 'Upper Pull', 'name': 'Upper Pull',
             'to_position': 2}])
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['clean'], response.data)
        self.assertEqual([name for _n, name in positions(self.plan, 1)][1],
                         'Upper Pull')

    def test_a_restructure_renames_moves_and_fills_in_one_confirmation(self):
        response = self.apply_draft([
            {'action': 'move_day', 'day': 'Upper Pull', 'name': 'Upper Pull',
             'to_position': 2},
            {'action': 'rename_day', 'day': 'Cardio, Abs & Accessories',
             'name': 'Pull + Abs'},
            {'action': 'add', 'day': 'Pull + Abs', 'name': 'Lat Pulldown',
             'sets': 3, 'reps': '8'},
        ])
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            [name for _n, name in positions(self.plan, 1)],
            ['Upper Push', 'Upper Pull', 'Lower Body', 'Pull + Abs'])
        day = self.plan.days.get(week_number=1, name='Pull + Abs')
        self.assertIn('Lat Pulldown',
                      [e.name for e in day.exercises.all()])
        self.assertTrue(response.data['clean'], response.data)

    def test_a_day_that_is_not_there_is_refused_and_changes_nothing(self):
        response = self.apply_draft([
            {'action': 'move_day', 'day': 'Mobility', 'name': 'Mobility',
             'to_position': 1}])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['code'], 'bad_draft')
        self.assertEqual([name for _n, name in positions(self.plan, 1)], DAYS)
        self.assertEqual(
            PlanChange.objects.get().status, PlanChange.PROPOSED)

    def test_a_refused_draft_leaves_an_earlier_day_op_rolled_back(self):
        """One confirmation is one transaction, or a half-applied week."""
        response = self.apply_draft([
            {'action': 'move_day', 'day': 'Upper Pull', 'name': 'Upper Pull',
             'to_position': 2},
            {'action': 'add', 'day': 'Mobility', 'name': 'Lat Pulldown'},
        ])
        self.assertEqual(response.status_code, 400)
        self.assertEqual([name for _n, name in positions(self.plan, 1)], DAYS)
