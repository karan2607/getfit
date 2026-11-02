"""
A plan edit must not destroy the training history attached to the plan.

The bug
-------
`workout_plan_detail`'s PUT branch replaces a plan by deleting every day and
rebuilding from the incoming JSON:

    plan.days.all().delete()

Nothing in the UI reports this, but the cascade is total:

  * `Composite.day` is a plain FK, so every superset in the plan is deleted.
  * `SetLog.exercise` is SET_NULL, so every logged set in the plan is orphaned
    from the exercise it was performed on.
  * `WorkoutSession.exercise_day` is SET_NULL, so sessions lose their day.
  * `WorkoutDay.last_completed_at` is gone with the row.

Every AI chat plan edit goes through this path. That is the real reason an
exercise "disappeared" from a plan after an edit -- the server rebuilt the plan
from whatever JSON came back, and anything the model omitted was deleted along
with its history. Upgrade step 9 deliberately leaves orphaned SetLogs alone, so
the damage accumulates permanently.

These tests pin the behaviour we want: an edit that touches ONE exercise must
leave every other row, and every FK into the plan, exactly where it was.

They fail against the delete-and-rebuild implementation. That failure is the
point -- it is the proof the bug is real before `_apply_plan_diff` replaces it.
"""

from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework.authtoken.models import Token

from api import exercise_names
from api.models import (
    User, UserProfile, WorkoutPlan, WorkoutDay, Exercise, Composite,
    WorkoutSession, SetLog,
)


class PlanMutationPreservesHistoryTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='history@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='kg')
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)
        self.plan = self._make_plan_with_history()

    # -- fixture ----------------------------------------------------------

    def _make_plan_with_history(self):
        """
        A two-day plan where day 2 carries a superset and real logged sets.

        Shaped like the live plan: a warmup day, then a working day whose first
        two working movements are grouped into a Composite.
        """
        plan = WorkoutPlan.objects.create(
            user=self.user, title='8-Week Program', is_active=True,
            duration_weeks=1)

        warm = WorkoutDay.objects.create(
            plan=plan, day_number=1, week_number=1, name='Warmup & Mobility',
            order=0)
        Exercise.objects.create(
            day=warm, name='Hamstring Stretch', sets=1, reps='30 sec',
            order=0, measurement_type='warmup')

        main = WorkoutDay.objects.create(
            plan=plan, day_number=2, week_number=1, name='Upper Push',
            order=1, last_completed_at=timezone.now())

        self.lead = Exercise.objects.create(
            day=main, name='Machine Chest Press', sets=3, reps='8-12',
            order=0, measurement_type='weight_reps')

        # The pair that must survive as a group.
        composite = Composite.objects.create(day=main, order=0, rest_seconds=90)
        self.sup_a = Exercise.objects.create(
            day=main, name='Dumbbell Lateral Raise', sets=3, reps='12-15',
            order=1, measurement_type='weight_reps', composite=composite)
        self.sup_b = Exercise.objects.create(
            day=main, name='Cable Tricep Pushdown', sets=3, reps='12-15',
            order=2, measurement_type='weight_reps', composite=composite)

        # The exercise the edit will replace. Its history is expected to be
        # orphaned by the edit -- that is legitimate; nothing else should be.
        self.doomed = Exercise.objects.create(
            day=main, name='Barbell Bench Press', sets=4, reps='6-10',
            order=3, measurement_type='weight_reps')

        session = WorkoutSession.objects.create(
            user=self.user, exercise_day=main, day_name='Upper Push',
            is_completed=True, completed_at=timezone.now())
        self.session = session

        for n, ex in enumerate([self.lead, self.sup_a, self.sup_b], start=1):
            SetLog.objects.create(
                workout_session=session, exercise=ex, exercise_name=ex.name,
                set_number=n, reps_completed=10, weight_kg=40.0,
                is_completed=True)

        self.composite_id = composite.id
        self.main_day_id = main.id
        return plan

    # -- the edit ---------------------------------------------------------

    def _edit_payload(self):
        """
        One change: Barbell Bench Press becomes Machine Chest Press (Incline).

        Everything else is sent back unchanged, which is exactly what the chat
        flow does -- it returns the whole plan with one line different.
        """
        return {
            'title': '8-Week Program',
            'days': [
                {
                    'day_number': 1, 'name': 'Warmup & Mobility',
                    'is_rest_day': False,
                    'exercises': [
                        {'name': 'Hamstring Stretch', 'sets': 1,
                         'reps': '30 sec', 'measurement_type': 'warmup'},
                    ],
                },
                {
                    'day_number': 2, 'name': 'Upper Push', 'is_rest_day': False,
                    'exercises': [
                        {'name': 'Machine Chest Press', 'sets': 3,
                         'reps': '8-12', 'measurement_type': 'weight_reps'},
                        {'name': 'Dumbbell Lateral Raise', 'sets': 3,
                         'reps': '12-15', 'measurement_type': 'weight_reps',
                         'superset_group': 1, 'rest_seconds': 90},
                        {'name': 'Cable Tricep Pushdown', 'sets': 3,
                         'reps': '12-15', 'measurement_type': 'weight_reps',
                         'superset_group': 1, 'rest_seconds': 90},
                        {'name': 'Machine Incline Press', 'sets': 4,
                         'reps': '6-10', 'measurement_type': 'weight_reps'},
                    ],
                },
            ],
        }

    def _put(self):
        url = reverse('workout-plan-detail', args=[self.plan.id])
        return self.client.put(url, self._edit_payload(), format='json')

    # -- tests ------------------------------------------------------------

    def test_logged_sets_keep_their_exercise(self):
        """
        THE regression. Three sets were logged against exercises the edit does
        not touch. After the edit they must still point at a real Exercise.
        """
        self.assertEqual(
            SetLog.objects.filter(workout_session=self.session,
                                  exercise__isnull=True).count(), 0)

        resp = self._put()
        self.assertEqual(resp.status_code, 200, resp.data)

        orphans = list(SetLog.objects.filter(
            workout_session=self.session, exercise__isnull=True))
        self.assertEqual(
            [s.exercise_name for s in orphans], [],
            'the edit orphaned logged sets from exercises it did not change')

    def test_untouched_exercises_keep_their_primary_key(self):
        """
        Identity, not just the name, has to survive -- SetLog points at the pk.
        """
        before = {self.lead.id, self.sup_a.id, self.sup_b.id}
        resp = self._put()
        self.assertEqual(resp.status_code, 200, resp.data)

        after = set(Exercise.objects.filter(day__plan=self.plan)
                    .values_list('id', flat=True))
        self.assertTrue(
            before <= after,
            'exercises that were not edited were deleted and recreated')

    def test_the_superset_survives(self):
        """
        `Composite.day` is a plain FK, so deleting days takes every grouping
        with it even when both members are sent back unchanged.
        """
        resp = self._put()
        self.assertEqual(resp.status_code, 200, resp.data)

        self.assertTrue(
            Composite.objects.filter(id=self.composite_id).exists(),
            'the superset container was destroyed by an unrelated edit')

        # Canonical spelling, not the spelling the payload used -- the server
        # owns exercise identity (`exercise_names.canonical`), so "Dumbbell
        # Lateral Raise" is stored as "Dumbbell Lateral Raises".
        members = Exercise.objects.filter(
            composite_id=self.composite_id).order_by('order')
        self.assertEqual(
            [m.name for m in members],
            [exercise_names.canonical('Dumbbell Lateral Raise'),
             exercise_names.canonical('Cable Tricep Pushdown')])

    def test_the_session_keeps_its_day(self):
        """`WorkoutSession.exercise_day` is SET_NULL on the same cascade."""
        resp = self._put()
        self.assertEqual(resp.status_code, 200, resp.data)

        self.session.refresh_from_db()
        self.assertIsNotNone(
            self.session.exercise_day_id,
            'a completed session lost the day it was performed on')
        self.assertEqual(self.session.exercise_day_id, self.main_day_id)

    def test_last_completed_at_survives(self):
        """Deleting the day loses when it was last trained."""
        resp = self._put()
        self.assertEqual(resp.status_code, 200, resp.data)

        day = WorkoutDay.objects.filter(
            plan=self.plan, week_number=1, day_number=2).first()
        self.assertIsNotNone(day)
        self.assertIsNotNone(
            day.last_completed_at,
            'the edit discarded when this day was last completed')

    def test_the_requested_change_still_happens(self):
        """
        Preserving history must not come at the cost of applying the edit.
        """
        resp = self._put()
        self.assertEqual(resp.status_code, 200, resp.data)

        names = set(Exercise.objects.filter(day__plan=self.plan)
                    .values_list('name', flat=True))
        self.assertIn('Machine Incline Press', names)
        self.assertNotIn('Barbell Bench Press', names)
