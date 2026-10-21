"""
Per-week plan generation.

Why this exists: an 8-week plan emitted in one call is ~25,000 output tokens,
and at that size the response gets truncated at the model's output ceiling —
a sweep of four models hit 2 unparseable responses in 14 attempts (~14%), and
`call_gemini_json` turned the first one into a 503 for the user. That is the
"Generate plan is throwing request fail" report. Week-sized calls are ~3,000
tokens.

The bigger payoff is quality: weeks 2+ used to be written before the user had
trained a single session. They were guesses. Now each week is generated with
the previous weeks' real logged performance in the prompt — tested in
WeekPromptTests.
"""

import datetime as dt
from unittest import mock

from django.urls import reverse
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from api import views, gemini, exercise_types as et
from api.models import (
    User, UserProfile, WorkoutPlan, WorkoutDay, Exercise, WorkoutSession,
    SetLog,
)


def phase(start, end, name='Phase', focus='focus'):
    return {'start_week': start, 'end_week': end, 'name': name, 'focus': focus}


def day(day_number=1, name='Push', exercises=None, is_rest_day=False):
    return {'day_number': day_number, 'name': name, 'focus': name,
            'is_rest_day': is_rest_day,
            'exercises': exercises if exercises is not None else [
                {'name': 'Warmup & Mobility', 'sets': 1, 'reps': '5-10 min',
                 'measurement_type': et.WARMUP},
                {'name': 'Barbell Bench Press', 'sets': 3, 'reps': '8-12',
                 'measurement_type': et.WEIGHT_REPS},
            ]}


class OutlineValidationTests(APITestCase):
    """
    Ranges are integers, not strings. `"1-4"` is the same class of
    unvalidatable free text that produced `Cable Crossover (Mid-to-High)`.
    The UI offers 4, 6, 8 and 12 weeks, so the check must hold for all of them.
    """

    def assert_valid(self, outline, weeks):
        problems = views.validate_program_outline(outline, weeks)
        self.assertEqual(problems, [], msg=problems)

    def assert_invalid(self, outline, weeks, fragment=None):
        problems = views.validate_program_outline(outline, weeks)
        self.assertTrue(problems, msg='should have been rejected')
        if fragment:
            self.assertTrue(any(fragment in p for p in problems), msg=problems)

    def test_every_offered_duration_tiles_correctly(self):
        self.assert_valid([phase(1, 4, 'Linear')], 4)
        self.assert_valid([phase(1, 3), phase(4, 6)], 6)
        self.assert_valid([phase(1, 4), phase(5, 7), phase(8, 8)], 8)
        self.assert_valid([phase(1, 4), phase(5, 8), phase(9, 11),
                           phase(12, 12)], 12)

    def test_phases_out_of_order_are_still_valid(self):
        self.assert_valid([phase(5, 8), phase(1, 4)], 8)

    def test_a_gap_is_rejected(self):
        self.assert_invalid([phase(1, 3), phase(5, 8)], 8, 'gap')

    def test_an_overlap_is_rejected(self):
        self.assert_invalid([phase(1, 5), phase(4, 8)], 8, 'overlaps')

    def test_not_reaching_the_end_is_rejected(self):
        self.assert_invalid([phase(1, 4)], 8, 'not 8')

    def test_not_starting_at_one_is_rejected(self):
        self.assert_invalid([phase(2, 8)], 8, 'not 1')

    def test_running_past_the_end_is_rejected(self):
        self.assert_invalid([phase(1, 4), phase(5, 12)], 8)

    def test_string_ranges_are_rejected(self):
        self.assert_invalid([{'start_week': '1-4', 'end_week': '5-8'}], 8,
                            'integer')

    def test_reversed_range_is_rejected(self):
        self.assert_invalid([phase(4, 1)], 8)

    def test_junk_is_rejected_without_raising(self):
        for outline in (None, [], 'phases', [None], [{}], 42):
            self.assertTrue(views.validate_program_outline(outline, 8))

    def test_unknown_duration_is_rejected(self):
        self.assert_invalid([phase(1, 4)], None, 'duration_weeks')


class PlanCreationTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='wg@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)
        self.url = reverse('workout-plans')

    def post(self, payload):
        return self.client.post(self.url, payload, format='json')

    def base(self, **extra):
        payload = {
            'title': 'P', 'duration_weeks': 8,
            'program_outline': [phase(1, 4), phase(5, 8)],
            'week': [day(1), day(2, 'Pull')],
        }
        payload.update(extra)
        return payload

    def test_a_week_payload_creates_one_week(self):
        res = self.post(self.base())
        self.assertEqual(res.status_code, 201, res.data)
        plan = WorkoutPlan.objects.get(pk=res.data['id'])
        self.assertEqual(plan.weeks_ready, 1)
        self.assertEqual(plan.days.count(), 2)
        self.assertEqual(plan.duration_weeks, 8)

    def test_the_outline_is_stored(self):
        res = self.post(self.base())
        plan = WorkoutPlan.objects.get(pk=res.data['id'])
        self.assertEqual(len(plan.program_outline), 2)
        self.assertEqual(plan.program_outline[0]['start_week'], 1)

    def test_an_invalid_outline_is_dropped_but_the_plan_saves(self):
        """
        Display-only data must not cost the user their plan — the same call
        made for prescription mismatches.
        """
        res = self.post(self.base(program_outline=[phase(1, 3), phase(5, 8)]))
        self.assertEqual(res.status_code, 201, res.data)
        plan = WorkoutPlan.objects.get(pk=res.data['id'])
        self.assertIsNone(plan.program_outline)
        self.assertEqual(plan.days.count(), 2)

    def test_a_legacy_multi_week_payload_still_works(self):
        """Plans created by the old format must keep importing."""
        res = self.post({'title': 'Legacy', 'duration_weeks': 2,
                         'weeks': [[day(1)], [day(1)]]})
        self.assertEqual(res.status_code, 201, res.data)
        plan = WorkoutPlan.objects.get(pk=res.data['id'])
        self.assertEqual(plan.weeks_ready, 2)

    def test_a_legacy_days_payload_still_works(self):
        res = self.post({'title': 'Legacy', 'duration_weeks': 1,
                         'days': [day(1)]})
        self.assertEqual(res.status_code, 201, res.data)

    def test_an_empty_week_is_rejected(self):
        self.assertEqual(self.post(self.base(week=[])).status_code, 400)


class ExerciseNotesMapTests(APITestCase):
    """
    Notes were 46% of an 8-week payload (47KB of 101KB) with only 117 distinct
    strings across 408 exercises — the same cue re-emitted every week. The map
    is a WIRE FORMAT change only: `Exercise.notes` is still written per row.
    """

    def setUp(self):
        self.user = User.objects.create_user(email='wg2@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)

    def create(self, **extra):
        payload = {'title': 'P', 'duration_weeks': 1,
                   'week': [day(1, exercises=[
                       {'name': 'Barbell Bench Press', 'sets': 3,
                        'reps': '8-12', 'measurement_type': et.WEIGHT_REPS}])]}
        payload.update(extra)
        res = self.client.post(reverse('workout-plans'), payload, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        return WorkoutPlan.objects.get(pk=res.data['id'])

    def note(self, plan):
        return plan.days.first().exercises.first().notes

    def test_the_map_populates_the_row(self):
        plan = self.create(exercise_notes={
            'Barbell Bench Press': 'Pause 1s at the chest.'})
        self.assertEqual(self.note(plan), 'Pause 1s at the chest.')

    def test_an_inline_note_still_wins(self):
        """Older payloads and AI chat edits must keep working."""
        payload = {'title': 'P', 'duration_weeks': 1,
                   'week': [day(1, exercises=[
                       {'name': 'Barbell Bench Press', 'sets': 3,
                        'reps': '8-12', 'measurement_type': et.WEIGHT_REPS,
                        'notes': 'Inline cue.'}])]}
        res = self.client.post(reverse('workout-plans'), payload, format='json')
        plan = WorkoutPlan.objects.get(pk=res.data['id'])
        self.assertEqual(self.note(plan), 'Inline cue.')

    def test_a_missing_key_leaves_the_note_blank(self):
        plan = self.create(exercise_notes={'Something Else': 'cue'})
        self.assertEqual(self.note(plan), '')

    def test_no_map_at_all_is_fine(self):
        self.assertEqual(self.note(self.create()), '')

    def test_a_non_canonical_key_still_resolves(self):
        """The map key may be spelled differently from the exercise."""
        payload = {'title': 'P', 'duration_weeks': 1,
                   'week': [day(1, exercises=[
                       {'name': 'Dumbbell Incline Press', 'sets': 3,
                        'reps': '8-12', 'measurement_type': et.WEIGHT_REPS}])],
                   'exercise_notes': {'Incline Dumbbell Press': 'Elbows in.'}}
        res = self.client.post(reverse('workout-plans'), payload, format='json')
        plan = WorkoutPlan.objects.get(pk=res.data['id'])
        self.assertEqual(self.note(plan), 'Elbows in.')

    def test_the_notes_gate_still_runs_on_mapped_notes(self):
        """A progression rule in the map must be stripped like an inline one."""
        plan = self.create(exercise_notes={
            'Barbell Bench Press': 'Pause 1s. Add 5lb when all sets are clean.'})
        self.assertEqual(self.note(plan), 'Pause 1s.')


class PreparationLockTests(APITestCase):
    """The concurrency and recovery behaviour, which is what makes a bare
    thread acceptable on a single-worker deployment."""

    def setUp(self):
        self.user = User.objects.create_user(email='wg3@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)
        self.plan = WorkoutPlan.objects.create(
            user=self.user, title='P', duration_weeks=8, current_week=1)
        WorkoutDay.objects.create(plan=self.plan, day_number=1, week_number=1,
                                  name='Push')

    def url(self, week):
        return reverse('workout-plan-prepare-next-week',
                       args=[self.plan.id, week])

    def test_preparing_returns_202_and_sets_the_lock(self):
        with mock.patch.object(views, '_generate_week') as gen:
            with mock.patch('threading.Thread') as thread:
                res = self.client.post(self.url(2), {}, format='json')
        self.assertEqual(res.status_code, 202, res.data)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.preparing_week, 2)
        self.assertTrue(self.plan.is_preparing)
        self.assertTrue(thread.called)
        self.assertFalse(gen.called)        # runs in the thread, not inline

    def test_a_second_trigger_is_refused_while_one_is_running(self):
        with mock.patch('threading.Thread'):
            self.assertEqual(
                self.client.post(self.url(2), {}, format='json').status_code, 202)
            res = self.client.post(self.url(3), {}, format='json')
        self.assertEqual(res.status_code, 409)

    def test_a_stale_lock_is_reclaimable(self):
        """
        The hole a thread-based approach would otherwise leave: a dyno restart
        mid-generation must not wedge the plan forever.
        """
        WorkoutPlan.objects.filter(pk=self.plan.pk).update(
            preparing_week=2,
            prepare_started_at=timezone.now() - dt.timedelta(minutes=10))
        self.plan.refresh_from_db()
        self.assertTrue(self.plan.prepare_lock_is_stale)
        self.assertFalse(self.plan.is_preparing)
        with mock.patch('threading.Thread'):
            res = self.client.post(self.url(2), {}, format='json')
        self.assertEqual(res.status_code, 202, res.data)

    def test_a_fresh_lock_is_not_stale(self):
        WorkoutPlan.objects.filter(pk=self.plan.pk).update(
            preparing_week=2, prepare_started_at=timezone.now())
        self.plan.refresh_from_db()
        self.assertFalse(self.plan.prepare_lock_is_stale)

    def test_an_existing_week_is_a_200_not_an_error(self):
        """The client may have raced its own auto-trigger."""
        WorkoutDay.objects.create(plan=self.plan, day_number=1, week_number=2,
                                  name='Push')
        res = self.client.post(self.url(2), {}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.data['started'])

    def test_week_one_cannot_be_prepared(self):
        res = self.client.post(self.url(1), {}, format='json')
        self.assertEqual(res.status_code, 409)
        self.assertIn('week 1', res.data['detail'])

    def test_a_week_beyond_the_program_is_refused(self):
        res = self.client.post(self.url(99), {}, format='json')
        self.assertEqual(res.status_code, 409)
        self.assertIn('beyond', res.data['detail'])

    def test_another_users_plan_is_404(self):
        other = User.objects.create_user(email='wg4@test.local', password='x')
        token, _ = Token.objects.get_or_create(user=other)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)
        self.assertEqual(
            self.client.post(self.url(2), {}, format='json').status_code, 404)

    def test_requires_auth(self):
        self.client.credentials()
        self.assertIn(self.client.post(self.url(2), {}, format='json').status_code,
                      (401, 403))


class GenerateWeekTests(APITestCase):
    """`_generate_week` itself — the thread body."""

    def setUp(self):
        self.user = User.objects.create_user(email='wg5@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        self.plan = WorkoutPlan.objects.create(
            user=self.user, title='P', duration_weeks=8, current_week=1,
            program_outline=[phase(1, 4, 'Accum'), phase(5, 8, 'Intens')])
        self.w1 = WorkoutDay.objects.create(
            plan=self.plan, day_number=1, week_number=1, name='Push')
        Exercise.objects.create(day=self.w1, name='Barbell Bench Press',
                                sets=3, reps='8-12',
                                measurement_type=et.WEIGHT_REPS)
        WorkoutPlan.objects.filter(pk=self.plan.pk).update(
            preparing_week=2, prepare_started_at=timezone.now())

    def run_with(self, response):
        def _call(**kwargs):
            if isinstance(response, Exception):
                raise response
            return response
        with mock.patch.object(gemini, 'call_gemini_json', _call), \
                mock.patch.object(views, 'call_gemini_json', _call):
            views._generate_week(self.plan.pk, 2)
        self.plan.refresh_from_db()

    def test_a_good_response_creates_the_week_and_clears_the_lock(self):
        self.run_with({'week': [day(1), day(2, 'Pull')]})
        self.assertEqual(self.plan.days.filter(week_number=2).count(), 2)
        self.assertIsNone(self.plan.preparing_week)
        self.assertEqual(self.plan.prepare_error, '')
        self.assertEqual(self.plan.weeks_ready, 2)

    def test_notes_from_the_map_are_applied(self):
        self.run_with({'week': [day(1)],
                       'exercise_notes': {'Barbell Bench Press': 'Pause 1s.'}})
        ex = Exercise.objects.get(day__week_number=2,
                                  name='Barbell Bench Press')
        self.assertEqual(ex.notes, 'Pause 1s.')

    def test_a_failure_records_the_error_and_releases_the_lock(self):
        self.run_with(gemini.GeminiError('AI returned a malformed response'))
        self.assertIsNone(self.plan.preparing_week)
        self.assertIn('malformed', self.plan.prepare_error)
        self.assertEqual(self.plan.weeks_ready, 1)

    def test_a_failure_leaves_no_partial_week(self):
        """A half-created week is worse than none: the split would be wrong
        and weeks_ready would lie."""
        self.run_with({'week': 'not a list'})
        self.assertEqual(self.plan.days.filter(week_number=2).count(), 0)
        self.assertTrue(self.plan.prepare_error)

    def test_an_empty_response_is_an_error_not_an_empty_week(self):
        self.run_with({'week': []})
        self.assertEqual(self.plan.days.filter(week_number=2).count(), 0)
        self.assertTrue(self.plan.prepare_error)

    def test_running_twice_does_not_duplicate_the_week(self):
        self.run_with({'week': [day(1)]})
        WorkoutPlan.objects.filter(pk=self.plan.pk).update(
            preparing_week=2, prepare_started_at=timezone.now())
        self.run_with({'week': [day(1)]})
        self.assertEqual(self.plan.days.filter(week_number=2).count(), 1)

    def test_a_deleted_plan_does_not_raise(self):
        plan_id = self.plan.pk
        self.plan.delete()
        views._generate_week(plan_id, 2)        # must not raise

    def test_day_order_continues_after_existing_days(self):
        self.run_with({'week': [day(1), day(2, 'Pull')]})
        orders = list(self.plan.days.filter(week_number=2)
                      .order_by('order').values_list('order', flat=True))
        self.assertEqual(orders, sorted(orders))
        self.assertGreater(min(orders), self.w1.order)


class WeekPromptTests(APITestCase):
    """
    The quality claim, tested: a week prepared now is written against what the
    user ACTUALLY lifted. Weeks 2+ used to be guessed before any session
    existed.
    """

    def setUp(self):
        self.user = User.objects.create_user(email='wg6@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        self.plan = WorkoutPlan.objects.create(
            user=self.user, title='P', duration_weeks=8, current_week=1,
            program_outline=[phase(1, 4, 'Accum', 'volume'),
                             phase(5, 8, 'Intens', 'load')])
        self.day1 = WorkoutDay.objects.create(
            plan=self.plan, day_number=1, week_number=1, name='Push')
        Exercise.objects.create(day=self.day1, name='Barbell Bench Press',
                                sets=3, reps='8-12',
                                measurement_type=et.WEIGHT_REPS)
        # Three sessions at 135lb x 12 — topped the range, earns more load.
        for days_ago in (21, 14, 7):
            session = WorkoutSession.objects.create(
                user=self.user, exercise_day=self.day1, day_name='Push',
                is_completed=True)
            WorkoutSession.objects.filter(pk=session.pk).update(
                started_at=timezone.now() - dt.timedelta(days=days_ago))
            for number in (1, 2, 3):
                SetLog.objects.create(
                    workout_session=session,
                    exercise_name='Barbell Bench Press', set_number=number,
                    weight_kg=135 * 0.453592, reps_completed=12,
                    is_completed=True)
        WorkoutPlan.objects.filter(pk=self.plan.pk).update(
            preparing_week=2, prepare_started_at=timezone.now())

    def captured_prompt(self, user_note=''):
        seen = {}

        def _call(*, system_prompt, user_prompt, **kw):
            seen['system'] = system_prompt
            seen['user'] = user_prompt
            return {'week': [day(1)]}

        with mock.patch.object(gemini, 'call_gemini_json', _call), \
                mock.patch.object(views, 'call_gemini_json', _call):
            views._generate_week(self.plan.pk, 2, user_note)
        return seen

    def test_the_prompt_contains_the_logged_performance(self):
        prompt = self.captured_prompt()['user']
        self.assertIn('ACTUALLY LIFTED', prompt)
        self.assertIn('Barbell Bench Press', prompt)
        self.assertIn('135', prompt)

    def test_the_prompt_names_the_phase_for_that_week(self):
        prompt = self.captured_prompt()['user']
        self.assertIn('Accum', prompt)
        self.assertIn('WEEK 2', prompt)

    def test_the_prompt_carries_the_previous_week_structure(self):
        prompt = self.captured_prompt()['user']
        self.assertIn("Last week's structure", prompt)

    def test_a_user_note_reaches_the_prompt_as_a_constraint(self):
        prompt = self.captured_prompt('knee felt off on squats')['user']
        self.assertIn('knee felt off on squats', prompt)
        self.assertIn('constraint', prompt)

    def test_the_system_prompt_is_the_same_doctrine_as_week_one(self):
        """
        Two copies would drift, and the drift would show up as weeks 2+
        quietly violating gates week 1 passes.
        """
        system = self.captured_prompt()['system']
        self.assertEqual(system, views._workout_system_prompt('lb'))
        self.assertIn('measurement_type', system)
        self.assertIn('CONVENTIONAL SCHEMES', system)


class AutoPrepareTests(APITestCase):
    """Finishing a week should start the next one without being asked."""

    def setUp(self):
        self.user = User.objects.create_user(email='wg7@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)
        self.plan = WorkoutPlan.objects.create(
            user=self.user, title='P', duration_weeks=8, current_week=1)
        self.days = [
            WorkoutDay.objects.create(plan=self.plan, day_number=n,
                                      week_number=1, name='D%d' % n)
            for n in (1, 2)
        ]

    def complete(self, day):
        session = WorkoutSession.objects.create(
            user=self.user, exercise_day=day, day_name=day.name)
        with mock.patch('threading.Thread') as thread:
            res = self.client.patch(
                reverse('workout-session-detail', args=[session.id]),
                {'is_completed': True}, format='json')
        self.assertEqual(res.status_code, 200, getattr(res, 'data', None))
        return thread

    def test_finishing_the_last_day_starts_the_next_week(self):
        self.complete(self.days[0])
        self.plan.refresh_from_db()
        self.assertIsNone(self.plan.preparing_week)    # week not finished yet

        self.complete(self.days[1])
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.preparing_week, 2)

    def test_rest_days_do_not_block_the_trigger(self):
        WorkoutDay.objects.create(plan=self.plan, day_number=3, week_number=1,
                                  name='Rest', is_rest_day=True)
        self.complete(self.days[0])
        self.complete(self.days[1])
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.preparing_week, 2)

    def test_nothing_happens_when_the_next_week_exists(self):
        WorkoutDay.objects.create(plan=self.plan, day_number=1, week_number=2,
                                  name='Push')
        self.complete(self.days[0])
        self.complete(self.days[1])
        self.plan.refresh_from_db()
        self.assertIsNone(self.plan.preparing_week)

    def test_nothing_happens_on_the_final_week(self):
        WorkoutPlan.objects.filter(pk=self.plan.pk).update(duration_weeks=1)
        self.complete(self.days[0])
        self.complete(self.days[1])
        self.plan.refresh_from_db()
        self.assertIsNone(self.plan.preparing_week)

    def test_a_session_with_no_plan_day_is_harmless(self):
        session = WorkoutSession.objects.create(
            user=self.user, day_name='Ad hoc')
        res = self.client.patch(
            reverse('workout-session-detail', args=[session.id]),
            {'is_completed': True}, format='json')
        self.assertEqual(res.status_code, 200)

    def test_an_autoprepare_failure_never_breaks_completing_a_workout(self):
        with mock.patch.object(views, '_start_week_preparation',
                               side_effect=RuntimeError('boom')):
            session = WorkoutSession.objects.create(
                user=self.user, exercise_day=self.days[0], day_name='D1')
            res = self.client.patch(
                reverse('workout-session-detail', args=[session.id]),
                {'is_completed': True}, format='json')
        self.assertEqual(res.status_code, 200)


class SerializerTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='wg8@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)

    def test_weeks_ready_reflects_the_days_that_exist(self):
        plan = WorkoutPlan.objects.create(
            user=self.user, title='P', duration_weeks=8, current_week=1)
        self.assertEqual(plan.weeks_ready, 0)
        WorkoutDay.objects.create(plan=plan, day_number=1, week_number=1,
                                  name='A')
        WorkoutDay.objects.create(plan=plan, day_number=1, week_number=3,
                                  name='B')
        self.assertEqual(plan.weeks_ready, 3)

    def test_a_legacy_plan_reports_its_full_duration(self):
        """Plans created before per-week generation need no special-casing."""
        plan = WorkoutPlan.objects.create(
            user=self.user, title='Legacy', duration_weeks=8, current_week=1)
        for week in range(1, 9):
            WorkoutDay.objects.create(plan=plan, day_number=1,
                                      week_number=week, name='D')
        self.assertEqual(plan.weeks_ready, 8)

    def test_the_detail_endpoint_exposes_the_preparation_state(self):
        plan = WorkoutPlan.objects.create(
            user=self.user, title='P', duration_weeks=8, current_week=1,
            program_outline=[phase(1, 8)])
        res = self.client.get(reverse('workout-plan-detail', args=[plan.id]))
        self.assertEqual(res.status_code, 200)
        for field in ('program_outline', 'weeks_ready', 'preparing_week',
                      'is_preparing', 'prepare_error'):
            self.assertIn(field, res.data)
