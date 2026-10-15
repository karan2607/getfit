"""
MCP health, diet and body-weight tools.

Added so an external agent can post daily health data and meal logs. The
emphasis here is the same as for the existing tools: every one must scope to the
authenticated user, and the write tools must not become a way to corrupt
someone else's records or to smuggle a bad value past validation.
"""

import json

from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework.authtoken.models import Token

from api import mcp, exercise_types as et
from api.models import (
    User, UserProfile, WorkoutPlan, WorkoutDay, Exercise, WorkoutSession,
    HealthDailySummary, HealthWorkout, MealLog,
)

KG_PER_LB = 0.453592


class McpBase(APITestCase):
    def setUp(self):
        self.url = reverse('mcp')
        self.user = User.objects.create_user(email='mh@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb',
                                   weight_kg=80.0)
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)

    def call(self, name, arguments=None):
        return self.client.post(self.url, {
            'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
            'params': {'name': name, 'arguments': arguments or {}},
        }, format='json')

    def ok(self, name, arguments=None):
        res = self.call(name, arguments)
        self.assertEqual(res.status_code, 200, res.data)
        result = res.data['result']
        self.assertFalse(result.get('isError'),
                         msg=result.get('content'))
        return result['structuredContent']

    def err(self, name, arguments=None):
        res = self.call(name, arguments)
        self.assertEqual(res.status_code, 200, res.data)
        result = res.data['result']
        self.assertTrue(result.get('isError'), msg=result)
        return result['content'][0]['text']


class LogHealthDayTests(McpBase):
    def test_stores_supplied_metrics(self):
        data = self.ok('log_health_day', {
            'steps': 12000, 'sleep_hours': 7.5, 'resting_heart_rate': 54})
        self.assertEqual(data['date'], timezone.now().date().isoformat())
        summary = HealthDailySummary.objects.get()
        self.assertEqual(summary.steps, 12000)
        self.assertEqual(summary.sleep_hours, 7.5)

    def test_partial_update_does_not_erase_earlier_values(self):
        """The daily-sync case: two calls on the same day must merge."""
        self.ok('log_health_day', {'steps': 5000, 'sleep_hours': 8})
        self.ok('log_health_day', {'steps': 9000})
        summary = HealthDailySummary.objects.get()
        self.assertEqual(summary.steps, 9000)
        self.assertEqual(summary.sleep_hours, 8)

    def test_explicit_date(self):
        data = self.ok('log_health_day', {'date': '2026-10-01', 'steps': 100})
        self.assertEqual(data['date'], '2026-10-01')

    def test_malformed_date_is_an_error_not_a_crash(self):
        self.assertIn('ISO format', self.err('log_health_day',
                                             {'date': 'yesterday', 'steps': 1}))

    def test_future_date_is_rejected(self):
        self.assertIn('future', self.err('log_health_day',
                                         {'date': '2206-01-01', 'steps': 1}))

    def test_no_metrics_is_an_error(self):
        self.assertIn('no valid metrics', self.err('log_health_day', {}))

    def test_out_of_range_value_is_rejected(self):
        self.assertIn('no valid metrics',
                      self.err('log_health_day', {'steps': 5000000}))
        self.assertEqual(HealthDailySummary.objects.count(), 0)

    def test_non_numeric_is_reported(self):
        msg = self.err('log_health_day', {'steps': 'lots'})
        self.assertIn('steps', msg)


class LogHealthWorkoutTests(McpBase):
    def test_stores_a_workout(self):
        data = self.ok('log_health_workout', {
            'external_id': 'w-1', 'activity_type': 'Running',
            'start_time': '2026-10-14T07:00:00Z',
            'duration_seconds': 1800, 'calories': 320})
        self.assertTrue(data['created'])
        w = HealthWorkout.objects.get()
        self.assertEqual(w.activity_type, 'Running')
        self.assertEqual(w.duration_seconds, 1800)

    def test_resending_the_same_id_updates_rather_than_duplicates(self):
        for cals in (300, 400):
            self.ok('log_health_workout', {
                'external_id': 'w-1', 'activity_type': 'Running',
                'start_time': '2026-10-14T07:00:00Z', 'calories': cals})
        self.assertEqual(HealthWorkout.objects.count(), 1)
        self.assertEqual(HealthWorkout.objects.get().calories, 400)

    def test_start_time_is_required(self):
        self.assertIn('start_time', self.err('log_health_workout',
                                             {'activity_type': 'Run'}))

    def test_unparseable_start_time_is_an_error(self):
        self.assertIn('ISO 8601', self.err('log_health_workout',
                                           {'start_time': 'this morning'}))

    def test_external_id_defaults_to_something_stable(self):
        data = self.ok('log_health_workout',
                       {'start_time': '2026-10-14T07:00:00Z'})
        self.assertTrue(data['external_id'])


class LogMealTests(McpBase):
    def test_stores_a_meal(self):
        data = self.ok('log_meal', {
            'meal_type': 'lunch', 'food_name': 'Paneer salad',
            'calories': 450, 'protein_g': 30, 'carbs_g': 20, 'fat_g': 25})
        self.assertEqual(data['meal_type'], 'lunch')
        log = MealLog.objects.get()
        self.assertEqual(log.food_name, 'Paneer salad')
        self.assertEqual(log.calories, 450)

    def test_macros_default_to_zero(self):
        self.ok('log_meal', {'meal_type': 'snack', 'food_name': 'Apple'})
        log = MealLog.objects.get()
        self.assertEqual(log.calories, 0)
        self.assertEqual(log.protein_g, 0)

    def test_invalid_meal_type_is_rejected(self):
        msg = self.err('log_meal',
                       {'meal_type': 'brunch', 'food_name': 'Eggs'})
        self.assertIn('meal_type must be one of', msg)
        self.assertEqual(MealLog.objects.count(), 0)

    def test_food_name_is_required(self):
        self.assertIn('food_name', self.err('log_meal', {'meal_type': 'lunch'}))

    def test_non_numeric_macro_is_rejected(self):
        self.assertIn('protein_g', self.err('log_meal', {
            'meal_type': 'lunch', 'food_name': 'X', 'protein_g': 'lots'}))


class GetMealLogsTests(McpBase):
    def test_returns_todays_meals_with_totals(self):
        self.ok('log_meal', {'meal_type': 'breakfast', 'food_name': 'Oats',
                             'calories': 400, 'protein_g': 20})
        self.ok('log_meal', {'meal_type': 'lunch', 'food_name': 'Rice',
                             'calories': 600, 'protein_g': 25})
        data = self.ok('get_meal_logs')
        self.assertEqual(len(data['meals']), 2)
        self.assertEqual(data['totals']['calories'], 1000)
        self.assertEqual(data['totals']['protein_g'], 45)

    def test_window_covers_multiple_days(self):
        self.ok('log_meal', {'date': '2026-10-10', 'meal_type': 'lunch',
                             'food_name': 'Old', 'calories': 100})
        today = self.ok('get_meal_logs')
        self.assertEqual(len(today['meals']), 0)
        wide = self.ok('get_meal_logs', {'date': '2026-10-14', 'days': 10})
        self.assertEqual(len(wide['meals']), 1)

    def test_empty_is_not_an_error(self):
        data = self.ok('get_meal_logs')
        self.assertEqual(data['meals'], [])


class SetBodyWeightTests(McpBase):
    def test_lb_is_converted_to_kg(self):
        data = self.ok('set_body_weight', {'weight': 176.4})
        self.assertEqual(data['unit'], 'lb')
        self.user.profile.refresh_from_db()
        self.assertAlmostEqual(self.user.profile.weight_kg, 80.0, places=1)
        self.assertAlmostEqual(data['stored_kg'], 80.0, places=1)

    def test_explicit_kg(self):
        self.ok('set_body_weight', {'weight': 75, 'unit': 'kg'})
        self.user.profile.refresh_from_db()
        self.assertAlmostEqual(self.user.profile.weight_kg, 75.0, places=1)

    def test_implausible_weight_is_rejected(self):
        self.assertIn('plausible', self.err('set_body_weight', {'weight': 5}))
        self.assertIn('plausible', self.err('set_body_weight',
                                            {'weight': 2000, 'unit': 'kg'}))

    def test_bad_unit_is_rejected(self):
        self.assertIn("'kg' or 'lb'", self.err('set_body_weight',
                                               {'weight': 80, 'unit': 'stone'}))

    def test_weight_is_required(self):
        # The registry's required-argument check fires before the handler's own,
        # so the message comes from there.
        self.assertIn('Missing required argument(s): weight',
                      self.err('set_body_weight', {}))


class GetTodaysWorkoutTests(McpBase):
    def test_returns_the_scheduled_day_with_loggable_fields(self):
        plan = WorkoutPlan.objects.create(
            user=self.user, title='P', is_active=True, current_week=1,
            current_day_order=0)
        day = WorkoutDay.objects.create(plan=plan, day_number=1, week_number=1,
                                        name='Upper Body')
        Exercise.objects.create(day=day, name='Plank', sets=3, reps='45 sec',
                                measurement_type=et.DURATION)

        data = self.ok('get_todays_workout')
        self.assertEqual(data['day']['name'], 'Upper Body')
        ex = data['day']['exercises'][0]
        self.assertEqual(ex['measurement_type'], et.DURATION)
        self.assertEqual(ex['loggable_fields'], ['duration_seconds'])
        self.assertIsNone(data['open_session_id'])

    def test_reports_an_open_session(self):
        plan = WorkoutPlan.objects.create(user=self.user, title='P',
                                          is_active=True, current_week=1)
        day = WorkoutDay.objects.create(plan=plan, day_number=1, week_number=1,
                                        name='Upper Body')
        session = WorkoutSession.objects.create(
            user=self.user, exercise_day=day, day_name='Upper Body')
        data = self.ok('get_todays_workout')
        self.assertEqual(data['open_session_id'], str(session.id))

    def test_no_active_plan_is_a_clear_error(self):
        self.assertIn('No active plan', self.err('get_todays_workout'))


class CrossUserIsolationTests(McpBase):
    """Every new tool must only ever touch the caller's own records."""

    def setUp(self):
        super().setUp()
        self.other = User.objects.create_user(email='victim2@test.local',
                                              password='x')
        UserProfile.objects.create(user=self.other, preferred_unit='kg',
                                   weight_kg=70.0)

    def test_health_day_writes_only_to_the_caller(self):
        self.ok('log_health_day', {'steps': 1234})
        self.assertFalse(
            HealthDailySummary.objects.filter(user=self.other).exists())

    def test_meal_writes_only_to_the_caller(self):
        self.ok('log_meal', {'meal_type': 'lunch', 'food_name': 'Mine'})
        self.assertFalse(MealLog.objects.filter(user=self.other).exists())

    def test_body_weight_writes_only_to_the_caller(self):
        self.ok('set_body_weight', {'weight': 200})
        self.other.profile.refresh_from_db()
        self.assertEqual(self.other.profile.weight_kg, 70.0)

    def test_reusing_another_users_workout_id_cannot_steal_their_row(self):
        HealthWorkout.objects.create(
            user=self.other, terra_workout_id='shared-1',
            activity_type='Victim Run', start_time=timezone.now())
        self.ok('log_health_workout', {
            'external_id': 'shared-1', 'activity_type': 'Mine',
            'start_time': '2026-10-14T07:00:00Z'})

        self.assertEqual(HealthWorkout.objects.count(), 2)
        self.assertEqual(
            HealthWorkout.objects.get(user=self.other).activity_type,
            'Victim Run')

    def test_meal_logs_do_not_leak(self):
        MealLog.objects.create(user=self.other, date=timezone.now().date(),
                               meal_type='lunch', food_name='Private',
                               calories=500)
        data = self.ok('get_meal_logs')
        self.assertEqual(data['meals'], [])

    def test_health_summary_does_not_leak(self):
        HealthDailySummary.objects.create(
            user=self.other, date=timezone.now().date(), steps=9999)
        data = self.ok('get_health_summary')
        self.assertEqual(data['days'], [])

    def test_todays_workout_does_not_leak(self):
        plan = WorkoutPlan.objects.create(user=self.other, title='Theirs',
                                          is_active=True, current_week=1)
        WorkoutDay.objects.create(plan=plan, day_number=1, week_number=1,
                                  name='Theirs')
        self.assertIn('No active plan', self.err('get_todays_workout'))


class RegistryIntegrityTests(APITestCase):
    def test_all_new_tools_are_declared(self):
        for name in ('log_health_day', 'log_health_workout', 'log_meal',
                     'set_body_weight', 'get_meal_logs', 'get_todays_workout'):
            self.assertIn(name, mcp.TOOLS)

    def test_write_tools_are_flagged_as_writes(self):
        for name in ('log_health_day', 'log_health_workout', 'log_meal',
                     'set_body_weight'):
            self.assertTrue(mcp.TOOLS[name]['writes'], msg=name)

    def test_read_tools_are_flagged_read_only(self):
        for name in ('get_meal_logs', 'get_todays_workout'):
            self.assertFalse(mcp.TOOLS[name]['writes'], msg=name)

    def test_tools_list_exposes_the_new_tools_with_schemas(self):
        from rest_framework.authtoken.models import Token
        user = User.objects.create_superuser(email='reg@test.local', password='x')
        UserProfile.objects.create(user=user, preferred_unit='lb')
        token, _ = Token.objects.get_or_create(user=user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)
        res = self.client.post(reverse('mcp'), {
            'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}, format='json')
        names = {t['name'] for t in res.data['result']['tools']}
        self.assertIn('log_health_day', names)
        self.assertIn('log_meal', names)
        for tool in res.data['result']['tools']:
            self.assertEqual(tool['inputSchema']['type'], 'object', tool['name'])
