"""
Templates: a way to get a plan when the AI cannot help.

Generation was the only route to a plan, so when it failed the app was a dead
end -- onboarding finished, the dashboard said "no active plan", and every
other screen needed one. During the billing lapse that was the entire product
for a new user.

The property that matters most here is CONTRAINDICATION FILTERING. Offering
someone a barbell-squat template the day after they said their knee is injured
is worse than offering nothing: it reads as the app having read their note and
disagreed with it.
"""

from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework.authtoken.models import Token

from api import exercise_names, plan_templates
from api.models import (
    Exercise, ExerciseLibraryEntry, User, UserProfile, WorkoutPlan,
)


def key_of(name):
    return ' '.join(sorted(exercise_names.key(name)))


class AvailableTemplatesTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='tpl@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)

    def contraindicate(self, name):
        ExerciseLibraryEntry.objects.create(
            canonical_key=key_of(name), name=name, user=self.user,
            contraindicated=True, contraindication_reason='test')

    def test_all_templates_are_offered_by_default(self):
        out = plan_templates.available_templates(self.user)
        self.assertEqual(len(out), len(plan_templates.TEMPLATES))

    def test_a_contraindicated_exercise_is_removed(self):
        self.contraindicate('Barbell Squat')
        for template in plan_templates.available_templates(self.user):
            for _day, exercises in template['days']:
                self.assertNotIn('Barbell Squat', exercises)

    def test_a_filtered_template_says_it_was_adjusted(self):
        """
        Silently handing back a different plan than the one named would be
        worse than saying so.
        """
        self.contraindicate('Barbell Squat')
        self.contraindicate('Leg Press')
        self.contraindicate('Leg Extension')
        self.contraindicate('Lying Hamstring Curl')
        self.contraindicate('Standing Calf Raises (Machine)')
        adjusted = [t for t in plan_templates.available_templates(self.user)
                    if t['adjusted']]
        self.assertTrue(adjusted)

    def test_a_template_gutted_by_filtering_is_not_offered(self):
        """A lower-body plan with the legs taken out is not a plan."""
        for name in ['Machine Chest Press', 'Lat Pulldown (Neutral Grip)',
                     'Leg Press', 'Dumbbell Lateral Raises', 'Plank',
                     'Machine Shoulder Press', 'Seated Cable Row',
                     'Lying Hamstring Curl', 'Dumbbell Hammer Curl',
                     'Incline Dumbbell Press', 'Leg Extension',
                     'Cable Triceps Pushdown (Rope Attachment)']:
            self.contraindicate(name)
        keys = [t['key'] for t in plan_templates.available_templates(self.user)]
        self.assertNotIn('beginner_full_body', keys)

    def test_another_users_contraindications_do_not_apply(self):
        other = User.objects.create_user(email='tpl2@test.local', password='x')
        ExerciseLibraryEntry.objects.create(
            canonical_key=key_of('Barbell Squat'), name='Barbell Squat',
            user=other, contraindicated=True)
        names = {n for t in plan_templates.available_templates(self.user)
                 for _d, ex in t['days'] for n in ex}
        self.assertIn('Barbell Squat', names)


class TemplateEndpointTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='tpl3@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)
        self.url = reverse('workout-plan-templates')

    def test_it_lists_templates(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data['templates'])
        self.assertIn('summary', resp.data['templates'][0])

    def test_it_creates_a_usable_plan_with_no_ai_call(self):
        """
        The whole point: this works with every AI provider down.
        """
        resp = self.client.post(
            self.url, {'template': 'machine_focused'}, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)

        plan = WorkoutPlan.objects.get(user=self.user)
        self.assertFalse(plan.generated_by_ai)
        self.assertTrue(plan.days.exists())
        self.assertTrue(
            Exercise.objects.filter(day__plan=plan).exists())

    def test_every_day_starts_with_a_warmup(self):
        self.client.post(self.url, {'template': 'upper_lower'}, format='json')
        plan = WorkoutPlan.objects.get(user=self.user)
        for day in plan.days.all():
            first = day.exercises.order_by('order').first()
            self.assertEqual(first.measurement_type, 'warmup', day.name)

    def test_measurement_types_are_decided_by_the_server(self):
        """
        Templates go through the same create path as generated plans, so the
        field contract cannot drift between the two.
        """
        self.client.post(self.url, {'template': 'beginner_full_body'},
                         format='json')
        plank = Exercise.objects.filter(
            day__plan__user=self.user, name='Plank').first()
        self.assertIsNotNone(plank)
        self.assertEqual(plank.measurement_type, 'duration')

    def test_an_unknown_template_is_refused(self):
        resp = self.client.post(self.url, {'template': 'nope'}, format='json')
        self.assertEqual(resp.status_code, 400)

    def test_it_requires_authentication(self):
        self.client.credentials()
        self.assertIn(self.client.get(self.url).status_code, (401, 403))
