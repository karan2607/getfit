"""
The week-N prompt must know who it is programming for.

`_week_generate_prompt` took `profile` as an argument and the word `profile`
appeared exactly once in the whole function -- in the signature. It was never
read. So for an 8-week plan, weeks 2 through 8 (seven eighths of the program)
were written with no age, no sex, no bodyweight, no height, no experience
level, no stated goals, and -- the serious one -- **no injuries**.

`personal_notes` is where injuries live. There is an entire eval rule called
`injury_constraints_respected` because it matters. A stated knee problem
constrained week 1 and then silently vanished for every week after it.

The first test below fails before the fix. That is the point of it.
"""

from django.test import TestCase

from api import views
from api.models import (
    User, UserProfile, WorkoutPlan, WorkoutDay, Exercise,
)


class WeekPromptCarriesTheClientTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='wk@test.local', password='x')
        self.profile = UserProfile.objects.create(
            user=self.user,
            age=29, gender='female', weight_kg=63.5, height_cm=165,
            experience_level='beginner',
            activity_level='lightly_active',
            fitness_goal='lose_fat',
            preferred_unit='lb',
            personal_notes='Lower back disc issue — no spinal loading, '
                           'no barbell deadlift or back squat.',
        )
        self.plan = WorkoutPlan.objects.create(
            user=self.user, title='P', duration_weeks=8, current_week=1,
            equipment='full gym',
            specific_goal='build muscle, reduce belly fat',
            program_target={'metric': 'weight', 'label': 'Body weight',
                            'recommended_value': 58, 'current_value': 63.5,
                            'unit': 'kg'},
            program_outline=[
                {'start_week': 1, 'end_week': 4, 'name': 'Accumulation',
                 'focus': 'volume'},
                {'start_week': 5, 'end_week': 8, 'name': 'Intensification',
                 'focus': 'load'},
            ],
        )
        day = WorkoutDay.objects.create(
            plan=self.plan, day_number=1, week_number=1, name='Pull')
        Exercise.objects.create(day=day, name='Lat Pulldown', sets=3,
                                reps='12', order=0)

    def week_prompt(self, week=5):
        system, user = views._week_generate_prompt(
            self.plan, week, self.profile, preferred_unit='lb')
        return system + '\n' + user

    # -- the one that matters ------------------------------------------------

    def test_a_stated_injury_reaches_week_five(self):
        """
        THE bug. A back injury constrained week 1 and vanished for weeks 2-8,
        so the model was free to prescribe deadlifts in week 5 for someone who
        had said they cannot do them.
        """
        prompt = self.week_prompt(5)
        self.assertIn('INJURIES & LIMITATIONS', prompt)
        self.assertIn('disc issue', prompt)
        self.assertIn('no barbell deadlift', prompt)

    def test_an_absent_injury_note_says_so_explicitly(self):
        """
        'None stated' rather than silence: an empty section reads to the model
        as missing context, which is how it ends up inventing caution or
        ignoring the topic entirely.
        """
        UserProfile.objects.filter(pk=self.profile.pk).update(personal_notes='')
        self.profile.refresh_from_db()
        self.assertIn('INJURIES & LIMITATIONS: None stated', self.week_prompt())

    # -- the rest of the client ---------------------------------------------

    def test_demographics_reach_the_week_prompt(self):
        prompt = self.week_prompt()
        self.assertIn('29yo', prompt)
        self.assertIn('female', prompt)
        self.assertIn('165cm', prompt)
        # Weight is shown in the user's unit, not raw kg.
        self.assertIn('140', prompt)       # 63.5kg -> 140.0lb

    def test_experience_and_activity_level_reach_the_week_prompt(self):
        prompt = self.week_prompt()
        self.assertIn('Experience: beginner', prompt)
        # Rendered human-readable; activity_level previously reached NO
        # workout prompt at all, week 1 included.
        self.assertIn('Activity level outside training: lightly active',
                      prompt)

    def test_the_goals_and_target_reach_the_week_prompt(self):
        prompt = self.week_prompt()
        self.assertIn('reduce belly fat', prompt)
        self.assertIn('Body weight', prompt)

    def test_equipment_still_reaches_it(self):
        """Shipped earlier; this is the guard that the refactor kept it."""
        self.assertIn('full gym', self.week_prompt())

    # -- it must not break what worked --------------------------------------

    def test_the_week_number_and_phase_are_still_right(self):
        prompt = self.week_prompt(5)
        self.assertIn('WEEK 5', prompt)
        self.assertIn('Intensification', prompt)

    def test_a_missing_profile_does_not_crash(self):
        """
        Callers pass `getattr(plan.user, 'profile', None)`, so None is
        reachable for a user whose profile row was never created.
        """
        system, user = views._week_generate_prompt(
            self.plan, 3, None, preferred_unit='lb')
        self.assertIn('WEEK 3', system + user)


class ClientContextLinesTests(TestCase):
    """
    The shared helper. Week 1 and week N must build this identically -- two
    copies would drift, and a drifted copy is exactly how the injuries went
    missing in the first place.
    """

    def setUp(self):
        self.user = User.objects.create_user(email='ctx@test.local', password='x')

    def profile(self, **fields):
        return UserProfile.objects.create(user=self.user, **fields)

    def lines(self, profile, **kwargs):
        return '\n'.join(views._client_context_lines(profile, **kwargs))

    def test_all_four_demographics_render_as_one_line(self):
        """Byte-identical to the line this replaced, for a complete profile."""
        p = self.profile(age=29, gender='female', weight_kg=63.5,
                         height_cm=165, preferred_unit='lb')
        self.assertEqual(self.client_line(p),
                         'Client: 29yo female, 140.0lb, 165cm')

    def client_line(self, profile, **kwargs):
        """Just the 'Client:' line. 'None stated' in the injuries line is
        legitimate English, so a blanket assertNotIn('None') is useless."""
        for line in views._client_context_lines(profile, **kwargs):
            if line.startswith('Client:'):
                return line
        return ''

    def test_a_partial_profile_keeps_what_it_has(self):
        """
        The all-or-nothing bug: one missing field used to delete all four, so
        the model was told nothing at all about the client.
        """
        p = self.profile(gender='female', weight_kg=63.5, preferred_unit='lb')
        line = self.client_line(p)
        self.assertIn('female', line)
        self.assertIn('140.0lb', line)
        self.assertNotIn('None', line)

    def test_nothing_known_produces_no_client_line(self):
        p = self.profile(preferred_unit='lb')
        self.assertNotIn('Client:', self.lines(p))

    def test_it_never_emits_the_string_None(self):
        """
        An f-string over a missing field writes the literal 'None', which the
        model reads as a value. Asserted across every single-field profile.
        """
        for field, value in (('age', 29), ('gender', 'female'),
                             ('weight_kg', 63.5), ('height_cm', 165)):
            UserProfile.objects.filter(user=self.user).delete()
            p = self.profile(preferred_unit='lb', **{field: value})
            self.assertNotIn('None', self.client_line(p), msg=field)

    def test_kg_users_see_kg(self):
        """
        Caught a real precedence bug: the ternary swallowed the whole
        expression, so a kg user would have been shown '80kglb'.
        """
        p = self.profile(age=29, gender='male', weight_kg=80, height_cm=180,
                         preferred_unit='kg')
        line = self.client_line(p, preferred_unit='kg')
        self.assertIn('80kg', line)
        self.assertNotIn('lb', line)


class ProfileCompletenessTests(TestCase):
    """
    `is_complete` was dead code checking the wrong fields.

    It asked for goal/experience/diet and was referenced nowhere, so nothing
    collected age/gender/height/weight and nothing noticed they were missing.
    The plan prompt then dropped all four if any one was absent — the model was
    told nothing at all about the client and fell back to a generic template.
    """

    def setUp(self):
        self.user = User.objects.create_user(email='comp2@test.local', password='x')

    def profile(self, **fields):
        base = dict(age=29, gender='female', height_cm=165, weight_kg=63.5,
                    fitness_goal='lose_fat', experience_level='beginner',
                    dietary_preference='vegetarian')
        base.update(fields)
        return UserProfile.objects.create(user=self.user, **base)

    def test_a_full_profile_is_complete(self):
        p = self.profile()
        self.assertTrue(p.is_complete)
        self.assertEqual(p.missing_for_planning, [])

    def test_the_demographic_fields_are_now_required(self):
        """
        The specific regression. Each of these alone used to leave the profile
        'complete' while silently emptying the prompt's client context.
        """
        for field in ('age', 'gender', 'height_cm', 'weight_kg'):
            UserProfile.objects.filter(user=self.user).delete()
            p = self.profile(**{field: None})
            self.assertFalse(p.is_complete, msg=field)
            self.assertIn(field, p.missing_for_planning, msg=field)

    def test_it_reports_which_fields_are_missing_not_just_a_boolean(self):
        """
        A boolean cannot drive a useful prompt. The client needs the names so
        it can ask for those specific values.
        """
        UserProfile.objects.filter(user=self.user).delete()
        p = self.profile(age=None, gender=None)
        self.assertEqual(sorted(p.missing_for_planning), ['age', 'gender'])

    def test_the_original_fields_are_still_required(self):
        """Widening the check must not drop what it already enforced."""
        for field in ('fitness_goal', 'experience_level', 'dietary_preference'):
            UserProfile.objects.filter(user=self.user).delete()
            p = self.profile(**{field: None})
            self.assertIn(field, p.missing_for_planning, msg=field)

    def test_the_api_exposes_it_so_the_client_can_prompt(self):
        """
        Existing users predate the onboarding requirement. They must be ASKED,
        not locked out — so the gap has to be visible to the client.
        """
        from django.urls import reverse
        from rest_framework.authtoken.models import Token
        from rest_framework.test import APIClient
        self.profile(age=None)
        token, _ = Token.objects.get_or_create(user=self.user)
        # APIClient, not Django's test client: `credentials()` is DRF's.
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)
        res = client.get(reverse('profile'))
        self.assertEqual(res.status_code, 200)
        self.assertIn('age', res.data['missing_for_planning'])
