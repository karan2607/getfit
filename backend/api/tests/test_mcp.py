"""
Tests for the MCP JSON-RPC endpoint.

The important assertions are the isolation ones: this endpoint gives external
agents read/write access, so every tool must scope to the authenticated user and
must not become a way to read or mutate someone else's data. The write tools
must also respect the measurement-type contract, or an agent becomes a new way
to reintroduce the seconds-in-weight_kg corruption.
"""

import json
import uuid

from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework.authtoken.models import Token

from api import mcp, exercise_types as et
from api.models import (
    User, UserProfile, WorkoutPlan, WorkoutDay, Exercise, WorkoutSession, SetLog,
    DietPlan, Meal,
)


class McpTestBase(APITestCase):
    def setUp(self):
        self.url = reverse('mcp')
        self.user = User.objects.create_user(email='mcp@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb',
                                   weight_kg=80.0)
        self.token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)

        self.plan = WorkoutPlan.objects.create(
            user=self.user, title='My Plan', is_active=True, current_week=1)
        self.day = WorkoutDay.objects.create(
            plan=self.plan, day_number=1, week_number=1, name='Upper Body')
        self.bench = Exercise.objects.create(
            day=self.day, name='Barbell Bench Press', sets=3, reps='8-12',
            measurement_type=et.WEIGHT_REPS)
        self.plank = Exercise.objects.create(
            day=self.day, name='Plank', sets=3, reps='60 sec',
            measurement_type=et.DURATION)
        self.session = WorkoutSession.objects.create(
            user=self.user, exercise_day=self.day, day_name='Upper Body')

    def rpc(self, method, params=None, rpc_id=1):
        payload = {'jsonrpc': '2.0', 'id': rpc_id, 'method': method}
        if params is not None:
            payload['params'] = params
        return self.client.post(self.url, payload, format='json')

    def call(self, name, arguments=None):
        return self.rpc('tools/call', {'name': name, 'arguments': arguments or {}})

    def result_of(self, res):
        self.assertEqual(res.status_code, 200, res.data)
        self.assertNotIn('error', res.data, res.data)
        return res.data['result']

    def content_of(self, res):
        """structuredContent from a successful tools/call."""
        result = self.result_of(res)
        self.assertFalse(result.get('isError'), result)
        return result['structuredContent']


class ProtocolTests(McpTestBase):
    def test_initialize(self):
        result = self.result_of(self.rpc('initialize'))
        self.assertEqual(result['protocolVersion'], mcp.PROTOCOL_VERSION)
        self.assertEqual(result['serverInfo']['name'], 'getfit')
        self.assertIn('tools', result['capabilities'])

    def test_ping(self):
        self.result_of(self.rpc('ping'))

    def test_notifications_are_accepted(self):
        res = self.rpc('notifications/initialized')
        self.assertEqual(res.status_code, 200)
        self.assertNotIn('error', res.data)

    def test_tools_list_is_generated_from_the_registry(self):
        result = self.result_of(self.rpc('tools/list'))
        names = {t['name'] for t in result['tools']}
        self.assertEqual(names, set(mcp.TOOLS))
        for tool in result['tools']:
            self.assertIn('description', tool)
            self.assertIn('inputSchema', tool)
            self.assertEqual(tool['inputSchema']['type'], 'object')

    def test_read_only_tools_are_annotated(self):
        result = self.result_of(self.rpc('tools/list'))
        hints = {t['name']: t['annotations']['readOnlyHint'] for t in result['tools']}
        self.assertTrue(hints['get_profile'])
        self.assertTrue(hints['get_exercise_history'])
        self.assertFalse(hints['log_set'])
        self.assertFalse(hints['update_personal_notes'])

    def test_unknown_method_is_a_jsonrpc_error(self):
        res = self.rpc('does/not/exist')
        self.assertEqual(res.data['error']['code'], mcp.METHOD_NOT_FOUND)

    def test_missing_method_is_an_invalid_request(self):
        res = self.client.post(self.url, {'jsonrpc': '2.0', 'id': 1}, format='json')
        self.assertEqual(res.data['error']['code'], mcp.INVALID_REQUEST)

    def test_rpc_id_is_echoed(self):
        res = self.rpc('ping', rpc_id=99)
        self.assertEqual(res.data['id'], 99)

    def test_unknown_tool_is_reported_as_an_error_result(self):
        res = self.call('no_such_tool')
        self.assertEqual(res.data['error']['code'], mcp.METHOD_NOT_FOUND)

    def test_missing_required_argument_is_an_error_result(self):
        result = self.result_of(self.call('get_workout_session'))
        self.assertTrue(result['isError'])
        self.assertIn('session_id', result['content'][0]['text'])

    def test_tools_call_without_name_is_invalid_params(self):
        res = self.rpc('tools/call', {'arguments': {}})
        self.assertEqual(res.data['error']['code'], mcp.INVALID_PARAMS)

    def test_content_is_valid_json_text(self):
        """Agents that only read `content` must still get parseable output."""
        result = self.result_of(self.call('get_profile'))
        parsed = json.loads(result['content'][0]['text'])
        self.assertEqual(parsed['email'], 'mcp@test.local')


class AuthenticationTests(McpTestBase):
    def test_unauthenticated_is_rejected(self):
        self.client.credentials()
        res = self.rpc('tools/list')
        self.assertIn(res.status_code, (401, 403))

    def test_bad_token_is_rejected(self):
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + 'f' * 40)
        res = self.rpc('tools/list')
        self.assertIn(res.status_code, (401, 403))


class ReadToolTests(McpTestBase):
    def test_get_profile_reports_weight_in_user_unit(self):
        data = self.content_of(self.call('get_profile'))
        self.assertEqual(data['weight_unit'], 'lb')
        self.assertEqual(data['weight'], 176.4)   # 80 kg

    def test_get_profile_in_kg_for_a_kg_user(self):
        self.user.profile.preferred_unit = 'kg'
        self.user.profile.save()
        data = self.content_of(self.call('get_profile'))
        self.assertEqual(data['weight_unit'], 'kg')
        self.assertEqual(data['weight'], 80.0)

    def test_list_workout_plans(self):
        data = self.content_of(self.call('list_workout_plans'))
        self.assertEqual([p['title'] for p in data['plans']], ['My Plan'])

    def test_get_workout_plan_defaults_to_active(self):
        data = self.content_of(self.call('get_workout_plan'))
        self.assertEqual(data['title'], 'My Plan')
        self.assertEqual(len(data['days']), 1)

    def test_exercises_report_type_and_loggable_fields(self):
        """An agent must be told which fields apply before it writes."""
        data = self.content_of(self.call('get_workout_plan'))
        by_name = {e['name']: e for e in data['days'][0]['exercises']}

        self.assertEqual(by_name['Plank']['measurement_type'], et.DURATION)
        self.assertEqual(by_name['Plank']['loggable_fields'], ['duration_seconds'])

        self.assertEqual(sorted(by_name['Barbell Bench Press']['loggable_fields']),
                         ['reps_completed', 'weight_kg'])

    def test_get_workout_plan_week_filter(self):
        WorkoutDay.objects.create(plan=self.plan, day_number=1, week_number=2,
                                  name='Week 2 Day')
        data = self.content_of(self.call('get_workout_plan', {'week_number': 2}))
        self.assertEqual([d['name'] for d in data['days']], ['Week 2 Day'])

    def test_get_workout_plan_with_no_active_plan_errors_clearly(self):
        self.plan.is_active = False
        self.plan.save()
        result = self.result_of(self.call('get_workout_plan'))
        self.assertTrue(result['isError'])
        self.assertIn('No active plan', result['content'][0]['text'])

    def test_exercise_history_duration_is_in_seconds(self):
        SetLog.objects.create(workout_session=self.session, exercise=self.plank,
                              exercise_name='Plank', set_number=1,
                              duration_seconds=180, is_completed=True)
        data = self.content_of(
            self.call('get_exercise_history', {'exercise_name': 'Plank'}))
        self.assertEqual(data['measurement_type'], et.DURATION)
        self.assertEqual(data['metric'], 'duration_seconds')
        self.assertEqual(data['logs'][0]['duration_seconds'], 180)
        self.assertIsNone(data['logs'][0]['weight'],
                          'a plank must never be reported with a weight')

    def test_exercise_history_weight_is_converted(self):
        SetLog.objects.create(workout_session=self.session, exercise=self.bench,
                              exercise_name='Barbell Bench Press', set_number=1,
                              weight_kg=60.0, reps_completed=10, is_completed=True)
        data = self.content_of(self.call('get_exercise_history',
                                         {'exercise_name': 'Barbell Bench Press'}))
        self.assertEqual(data['weight_unit'], 'lb')
        self.assertEqual(data['logs'][0]['weight'], 132.3)   # 60 kg

    def test_get_health_summary_is_empty_not_an_error(self):
        data = self.content_of(self.call('get_health_summary'))
        self.assertEqual(data['days'], [])

    def test_get_diet_plan_with_none_errors_clearly(self):
        result = self.result_of(self.call('get_diet_plan'))
        self.assertTrue(result['isError'])

    def test_get_diet_plan(self):
        plan = DietPlan.objects.create(
            user=self.user, title='Cut', is_active=True, target_calories=2200,
            protein_g=180, carbs_g=200, fat_g=70)
        Meal.objects.create(plan=plan, day_number=1, meal_type='breakfast',
                            name='Oats', description='', calories=400,
                            protein_g=30, carbs_g=50, fat_g=10, order=0)
        data = self.content_of(self.call('get_diet_plan'))
        self.assertEqual(data['title'], 'Cut')
        self.assertEqual(data['meals'][0]['name'], 'Oats')


class WriteToolTests(McpTestBase):
    def test_log_set_respects_the_contract_for_duration(self):
        """An agent must not be able to record seconds as a weight."""
        data = self.content_of(self.call('log_set', {
            'session_id': str(self.session.id),
            'exercise_id': str(self.plank.id),
            'set_number': 1,
            'weight': 60,              # nonsense for a plank
            'duration_seconds': 90,
            'is_completed': True,
        }))
        self.assertIsNone(data['logged']['weight'])
        self.assertEqual(data['logged']['duration_seconds'], 90)
        self.assertIn('weight_kg', data['ignored_fields'],
                      'the agent should be told what was dropped')

        log = SetLog.objects.get(exercise=self.plank)
        self.assertIsNone(log.weight_kg)
        self.assertEqual(log.duration_seconds, 90)

    def test_log_set_converts_weight_from_user_unit(self):
        self.content_of(self.call('log_set', {
            'session_id': str(self.session.id),
            'exercise_id': str(self.bench.id),
            'set_number': 1,
            'weight': 132.3,           # lb
            'reps_completed': 10,
            'is_completed': True,
        }))
        log = SetLog.objects.get(exercise=self.bench)
        self.assertAlmostEqual(log.weight_kg, 60.0, places=1,
                               msg='lb must be stored as kg')

    def test_log_set_is_idempotent(self):
        for _ in range(3):
            self.call('log_set', {
                'session_id': str(self.session.id),
                'exercise_id': str(self.bench.id),
                'set_number': 1, 'weight': 100, 'reps_completed': 8,
            })
        self.assertEqual(SetLog.objects.filter(exercise=self.bench).count(), 1)

    def test_log_set_rejects_a_completed_session(self):
        self.session.is_completed = True
        self.session.save()
        result = self.result_of(self.call('log_set', {
            'session_id': str(self.session.id),
            'exercise_id': str(self.bench.id),
            'set_number': 1, 'weight': 100,
        }))
        self.assertTrue(result['isError'])

    def test_update_exercise_notes(self):
        data = self.content_of(self.call('update_exercise_notes', {
            'exercise_id': str(self.bench.id), 'notes': 'Pause at the chest.'}))
        self.assertEqual(data['updated']['notes'], 'Pause at the chest.')
        self.bench.refresh_from_db()
        self.assertEqual(self.bench.notes, 'Pause at the chest.')

    def test_update_personal_notes(self):
        data = self.content_of(self.call(
            'update_personal_notes', {'notes': 'Left shoulder impingement.'}))
        self.assertEqual(data['personal_notes'], 'Left shoulder impingement.')
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.personal_notes,
                         'Left shoulder impingement.')

    def test_long_notes_are_truncated_not_rejected(self):
        self.content_of(self.call('update_personal_notes', {'notes': 'a' * 9000}))
        self.user.profile.refresh_from_db()
        self.assertEqual(len(self.user.profile.personal_notes), 4000)


class CrossUserIsolationTests(McpTestBase):
    """
    The security surface. This endpoint hands read/write access to external
    agents, so no tool may reach another user's data.
    """

    def setUp(self):
        super().setUp()
        self.other = User.objects.create_user(email='victim@test.local',
                                              password='x')
        UserProfile.objects.create(user=self.other, preferred_unit='kg',
                                   weight_kg=70.0, personal_notes='private')
        self.other_plan = WorkoutPlan.objects.create(
            user=self.other, title='Victim Plan', is_active=True)
        self.other_day = WorkoutDay.objects.create(
            plan=self.other_plan, day_number=1, week_number=1, name='Theirs')
        self.other_ex = Exercise.objects.create(
            day=self.other_day, name='Squat', sets=3, reps='5',
            measurement_type=et.WEIGHT_REPS)
        self.other_session = WorkoutSession.objects.create(
            user=self.other, exercise_day=self.other_day, day_name='Theirs')
        SetLog.objects.create(
            workout_session=self.other_session, exercise=self.other_ex,
            exercise_name='Squat', set_number=1, weight_kg=200.0,
            reps_completed=5, is_completed=True)
        self.other_diet = DietPlan.objects.create(
            user=self.other, title='Victim Diet', is_active=True,
            target_calories=1800, protein_g=1, carbs_g=1, fat_g=1)

    def test_cannot_list_another_users_plans(self):
        data = self.content_of(self.call('list_workout_plans'))
        titles = [p['title'] for p in data['plans']]
        self.assertNotIn('Victim Plan', titles)

    def test_cannot_read_another_users_plan_by_id(self):
        result = self.result_of(
            self.call('get_workout_plan', {'plan_id': str(self.other_plan.id)}))
        self.assertTrue(result['isError'])

    def test_cannot_read_another_users_session_by_id(self):
        result = self.result_of(self.call(
            'get_workout_session', {'session_id': str(self.other_session.id)}))
        self.assertTrue(result['isError'])

    def test_cannot_read_another_users_diet_plan_by_id(self):
        result = self.result_of(
            self.call('get_diet_plan', {'plan_id': str(self.other_diet.id)}))
        self.assertTrue(result['isError'])

    def test_cannot_see_another_users_exercise_history(self):
        data = self.content_of(
            self.call('get_exercise_history', {'exercise_name': 'Squat'}))
        self.assertEqual(data['logs'], [],
                         "another user's logs must not be visible")

    def test_cannot_list_another_users_sessions(self):
        data = self.content_of(self.call('list_workout_sessions'))
        names = [s['day_name'] for s in data['sessions']]
        self.assertNotIn('Theirs', names)

    def test_cannot_write_to_another_users_session(self):
        result = self.result_of(self.call('log_set', {
            'session_id': str(self.other_session.id),
            'exercise_id': str(self.bench.id),
            'set_number': 1, 'weight': 100,
        }))
        self.assertTrue(result['isError'])

    def test_cannot_write_to_another_users_exercise(self):
        result = self.result_of(self.call('log_set', {
            'session_id': str(self.session.id),
            'exercise_id': str(self.other_ex.id),
            'set_number': 1, 'weight': 100,
        }))
        self.assertTrue(result['isError'])

    def test_cannot_edit_another_users_exercise_notes(self):
        result = self.result_of(self.call('update_exercise_notes', {
            'exercise_id': str(self.other_ex.id), 'notes': 'injected'}))
        self.assertTrue(result['isError'])
        self.other_ex.refresh_from_db()
        self.assertEqual(self.other_ex.notes, '')

    def test_update_personal_notes_only_touches_the_caller(self):
        self.content_of(self.call('update_personal_notes', {'notes': 'mine'}))
        self.other.profile.refresh_from_db()
        self.assertEqual(self.other.profile.personal_notes, 'private')

    def test_nonexistent_ids_do_not_leak_existence(self):
        result = self.result_of(
            self.call('get_workout_plan', {'plan_id': str(uuid.uuid4())}))
        self.assertTrue(result['isError'])


class RegistryIntegrityTests(APITestCase):
    """Guards the registry so a new tool cannot be added half-wired."""

    def test_every_tool_is_fully_declared(self):
        for name, spec in mcp.TOOLS.items():
            self.assertIn('description', spec, msg=name)
            self.assertTrue(spec['description'].strip(), msg=name)
            self.assertIn('schema', spec, msg=name)
            self.assertEqual(spec['schema']['type'], 'object', msg=name)
            self.assertTrue(callable(spec['handler']), msg=name)
            self.assertIsInstance(spec['writes'], bool, msg=name)

    def test_required_args_exist_in_the_schema_properties(self):
        for name, spec in mcp.TOOLS.items():
            props = spec['schema']['properties']
            for required in spec['schema'].get('required', []):
                self.assertIn(required, props,
                              msg='%s requires %r but does not declare it'
                                  % (name, required))

    def test_tool_names_are_snake_case(self):
        import re
        for name in mcp.TOOLS:
            self.assertRegex(name, r'^[a-z][a-z0-9_]*$')
