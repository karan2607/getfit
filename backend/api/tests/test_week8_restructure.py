"""
The restructure that failed four times, as a regression test.

Week 8 needed: legs moved from Day 2 to Day 3, Day 4 turned into Pull + Abs,
Day 5 renamed "Upper Body Focus". Every attempt also swapped Days 2 and 3 in
weeks 6 and 7, which had already been trained. Week 8 is right now; week 6's
record is still wrong and is deliberately left alone.

The whole edit is driven end to end through the agent's own tool and the
apply endpoint, then weeks 6 and 7 are compared against snapshots taken
before it ran. Anything that reaches them fails.
"""
from django.test import TestCase
from django.urls import reverse
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from api import agent_tools
from api.models import (
    Exercise, PlanChange, User, WorkoutDay, WorkoutPlan,
)
from api.plan_changes import snapshot_plan

#: The split as it stood, repeated in every week.
DAYS = ((1, 'Push'), (2, 'Legs'), (3, 'Pull'), (4, 'Accessories'),
        (5, 'Full Body'))
EXERCISES = {
    'Push': ['Barbell Bench Press', 'Machine Shoulder Press'],
    'Legs': ['Barbell Squat', 'Leg Press'],
    'Pull': ['Lat Pulldown', 'Seated Cable Rows'],
    'Accessories': ['Dumbbell Hammer Curl'],
    'Full Body': ['Kettlebell Swings'],
}


class Week8RestructureTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='w8@test.local',
                                             password='x')
        self.plan = WorkoutPlan.objects.create(
            user=self.user, title='P', is_active=True, duration_weeks=8,
            current_week=8)
        for week in (6, 7, 8):
            for number, name in DAYS:
                day = WorkoutDay.objects.create(
                    plan=self.plan, day_number=number, week_number=week,
                    name=name)
                for order, exercise in enumerate(EXERCISES[name]):
                    Exercise.objects.create(
                        day=day, name=exercise, sets=3, reps='8-12',
                        order=order, measurement_type='weight_reps')
        self.before_6 = snapshot_plan(self.plan, week_numbers=[6])
        self.before_7 = snapshot_plan(self.plan, week_numbers=[7])

        self.client = APIClient()
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)

    def propose(self, changes):
        return agent_tools.run_tool(
            self.user, 'propose_plan_change',
            {'plan_id': str(self.plan.id), 'changes': changes})

    def restructure(self):
        """Week 8 only, the way the agent must now express it."""
        return [
            {'action': 'move_day', 'day': 'Legs', 'week': 8,
             'to_position': 3},
            {'action': 'rename_day', 'day': 'Accessories', 'week': 8,
             'name': 'Pull + Abs'},
            {'action': 'rename_day', 'day': 'Full Body', 'week': 8,
             'name': 'Upper Body Focus'},
        ]

    def test_the_restructure_leaves_weeks_six_and_seven_untouched(self):
        result, error = self.propose(self.restructure())
        self.assertEqual(error, '', msg=error)

        res = self.client.post(
            reverse('workout-plan-apply-change',
                    args=[self.plan.id, result['change_id']]),
            {}, format='json')
        self.assertEqual(res.status_code, 200, res.data)

        self.assertEqual(snapshot_plan(self.plan, week_numbers=[6]),
                         self.before_6, 'week 6 was changed')
        self.assertEqual(snapshot_plan(self.plan, week_numbers=[7]),
                         self.before_7, 'week 7 was changed')

    def test_week_eight_actually_got_what_was_asked_for(self):
        """A change that touches nothing is also not the goal."""
        result, error = self.propose(self.restructure())
        self.assertEqual(error, '', msg=error)
        self.client.post(
            reverse('workout-plan-apply-change',
                    args=[self.plan.id, result['change_id']]),
            {}, format='json')

        week8 = {d.day_number: d.name for d in
                 WorkoutDay.objects.filter(plan=self.plan, week_number=8)}
        self.assertEqual(week8[3], 'Legs')
        self.assertIn('Pull + Abs', week8.values())
        self.assertIn('Upper Body Focus', week8.values())

    def test_the_same_edit_without_a_week_is_refused(self):
        """
        The shape that did the damage. Four attempts looked like this.
        """
        result, error = self.propose([
            {'action': 'move_day', 'day': 'Legs', 'to_position': 3}])
        self.assertIsNone(result)
        self.assertIn('week', error.lower())
        self.assertEqual(PlanChange.objects.count(), 0)
        self.assertEqual(snapshot_plan(self.plan, week_numbers=[6]),
                         self.before_6)

    def test_a_deliberate_programme_wide_move_still_reaches_every_week(self):
        """Week 0 is a real capability, not a formality."""
        result, error = self.propose([
            {'action': 'rename_day', 'day': 'Accessories',
             'week': 0, 'name': 'Arms'}])
        self.assertEqual(error, '', msg=error)
        self.client.post(
            reverse('workout-plan-apply-change',
                    args=[self.plan.id, result['change_id']]),
            {}, format='json')
        for week in (6, 7, 8):
            names = set(WorkoutDay.objects.filter(
                plan=self.plan, week_number=week).values_list('name', flat=True))
            self.assertIn('Arms', names, 'week %d missed it' % week)
