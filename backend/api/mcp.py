"""
Model Context Protocol (MCP) server over HTTP.

Lets other AI agents read and write against this service directly. Exposed as a
single JSON-RPC 2.0 endpoint at POST /api/mcp/, reusing the existing DRF
TokenAuthentication -- an agent authenticates with the same
`Authorization: Token <key>` header the app uses.

Design notes
------------
* Tools are declared in the TOOLS registry below. Each entry carries its own
  JSON Schema and handler, so `tools/list` is generated rather than maintained
  by hand.
* Every handler receives the authenticated `user` and MUST scope its queries to
  that user. There is no cross-user access by design.
* Writes are opt-in per tool via `writes=True`, so the read surface can be
  audited separately from the mutating one.
* All weights crossing this boundary are in the user's preferred unit, with the
  unit stated explicitly in the payload -- the same rule the rest of the app
  follows after the kg/lb confusion.

Capacity: gunicorn runs 1 worker / 4 threads, so agent traffic contends with
real app requests. Raise the thread count before pointing anything chatty at it.
"""

from django.shortcuts import get_object_or_404

from . import exercise_types
from .models import (
    WorkoutPlan, WorkoutDay, Exercise, WorkoutSession, SetLog,
    DietPlan, UserProfile, HealthDailySummary,
)

PROTOCOL_VERSION = '2024-11-05'
SERVER_NAME = 'getfit'
SERVER_VERSION = '1.0.0'

# JSON-RPC 2.0 error codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class McpError(Exception):
    """Raised by a handler to return a structured JSON-RPC error."""

    def __init__(self, message, code=INVALID_PARAMS):
        super().__init__(message)
        self.message = message
        self.code = code


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

def _unit(user):
    profile = getattr(user, 'profile', None)
    return getattr(profile, 'preferred_unit', 'lb') or 'lb'


def _weight_out(kg, unit):
    """kg in storage -> the user's unit, for output."""
    if kg is None:
        return None
    return round(kg / 0.453592, 1) if unit == 'lb' else round(kg, 1)


def _weight_in(value, unit):
    """A value in the user's unit -> kg for storage."""
    if value is None:
        return None
    return float(value) * 0.453592 if unit == 'lb' else float(value)


def _exercise_out(exercise):
    return {
        'id': str(exercise.id),
        'name': exercise.name,
        'sets': exercise.sets,
        'reps': exercise.reps,
        'rest_seconds': exercise.rest_seconds,
        'notes': exercise.notes,
        'measurement_type': exercise.measurement_type,
        # Tell the agent which fields it may log for this exercise, so it
        # cannot put seconds into a weight field.
        'loggable_fields': list(
            exercise_types.FIELD_CONTRACT.get(exercise.measurement_type, ())),
    }


def _day_out(day, include_exercises=True):
    out = {
        'id': str(day.id),
        'day_number': day.day_number,
        'week_number': day.week_number,
        'name': day.name,
        'focus': day.focus,
        'is_rest_day': day.is_rest_day,
        'last_completed_at': day.last_completed_at.isoformat()
        if day.last_completed_at else None,
    }
    if include_exercises:
        out['exercises'] = [_exercise_out(e) for e in day.exercises.all()]
    return out


def _set_log_out(log, unit):
    return {
        'id': str(log.id),
        'exercise_id': str(log.exercise_id) if log.exercise_id else None,
        'exercise_name': log.exercise_name,
        'set_number': log.set_number,
        'weight': _weight_out(log.weight_kg, unit),
        'weight_unit': unit,
        'reps_completed': log.reps_completed,
        'duration_seconds': log.duration_seconds,
        'is_completed': log.is_completed,
    }


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

def _tool_get_profile(user, args):
    profile = getattr(user, 'profile', None)
    unit = _unit(user)
    if profile is None:
        return {'email': user.email, 'name': user.name, 'profile': None}
    return {
        'email': user.email,
        'name': user.name,
        'weight': _weight_out(profile.weight_kg, unit),
        'weight_unit': unit,
        'height_cm': profile.height_cm,
        'age': profile.age,
        'gender': profile.gender,
        'fitness_goal': profile.fitness_goal,
        'experience_level': profile.experience_level,
        'dietary_preference': profile.dietary_preference,
        'activity_level': profile.activity_level,
        'personal_notes': profile.personal_notes,
    }


def _tool_list_workout_plans(user, args):
    plans = WorkoutPlan.objects.filter(user=user)
    if args.get('active_only'):
        plans = plans.filter(is_active=True)
    return {'plans': [{
        'id': str(p.id),
        'title': p.title,
        'description': p.description,
        'is_active': p.is_active,
        'duration_weeks': p.duration_weeks,
        'current_week': p.current_week,
        'current_day_order': p.current_day_order,
        'specific_goal': p.specific_goal,
        'created_at': p.created_at.isoformat(),
    } for p in plans[:50]]}


def _tool_get_workout_plan(user, args):
    plan_id = args.get('plan_id')
    if plan_id:
        plan = get_object_or_404(WorkoutPlan, pk=plan_id, user=user)
    else:
        plan = WorkoutPlan.objects.filter(user=user, is_active=True).first()
        if plan is None:
            raise McpError('No active plan. Pass plan_id or activate a plan.')

    week = args.get('week_number')
    days = plan.days.all()
    if week is not None:
        days = days.filter(week_number=week)

    return {
        'id': str(plan.id),
        'title': plan.title,
        'description': plan.description,
        'is_active': plan.is_active,
        'duration_weeks': plan.duration_weeks,
        'current_week': plan.current_week,
        'days': [_day_out(d) for d in days],
    }


def _tool_list_workout_sessions(user, args):
    sessions = WorkoutSession.objects.filter(user=user)
    if args.get('completed_only'):
        sessions = sessions.filter(is_completed=True)
    limit = min(int(args.get('limit') or 20), 100)
    return {'sessions': [{
        'id': str(s.id),
        'day_name': s.exercise_day.name if s.exercise_day_id else s.day_name,
        'started_at': s.started_at.isoformat(),
        'completed_at': s.completed_at.isoformat() if s.completed_at else None,
        'is_completed': s.is_completed,
    } for s in sessions[:limit]]}


def _tool_get_workout_session(user, args):
    session = get_object_or_404(WorkoutSession, pk=args['session_id'], user=user)
    unit = _unit(user)
    return {
        'id': str(session.id),
        'day_name': session.exercise_day.name if session.exercise_day_id
        else session.day_name,
        'started_at': session.started_at.isoformat(),
        'is_completed': session.is_completed,
        'exercise_day': _day_out(session.exercise_day)
        if session.exercise_day_id else None,
        'set_logs': [_set_log_out(l, unit) for l in session.set_logs.all()],
    }


def _tool_get_exercise_history(user, args):
    name = (args.get('exercise_name') or '').strip()
    if not name:
        raise McpError('exercise_name is required.')
    unit = _unit(user)

    measurement_type = (
        Exercise.objects
        .filter(day__plan__user=user, name__iexact=name)
        .values_list('measurement_type', flat=True)
        .first()
    ) or exercise_types.classify(name)

    metric = exercise_types.CHART_CONTRACT.get(
        measurement_type,
        exercise_types.CHART_CONTRACT[exercise_types.DEFAULT_TYPE],
    )['metric']

    qs = SetLog.objects.filter(
        workout_session__user=user, exercise_name__iexact=name, is_completed=True)
    if metric:
        qs = qs.filter(**{'%s__isnull' % metric: False})

    limit = min(int(args.get('limit') or 50), 200)
    logs = qs.order_by('-workout_session__started_at')[:limit]
    return {
        'exercise_name': name,
        'measurement_type': measurement_type,
        'metric': metric,
        'weight_unit': unit,
        'logs': [dict(_set_log_out(l, unit),
                      session_date=l.workout_session.started_at.date().isoformat())
                 for l in logs],
    }


def _tool_log_set(user, args):
    """
    Write a set result.

    Field acceptance follows the measurement-type contract, exactly as the REST
    endpoint does, so an agent cannot record seconds as a weight.
    """
    session = get_object_or_404(WorkoutSession, pk=args['session_id'], user=user)
    if session.is_completed:
        raise McpError('That session is already completed.')
    exercise = get_object_or_404(
        Exercise, pk=args['exercise_id'], day__plan__user=user)

    set_number = args.get('set_number')
    if set_number is None:
        raise McpError('set_number is required.')

    unit = _unit(user)
    log, _created = SetLog.objects.get_or_create(
        workout_session=session, exercise=exercise, set_number=set_number,
        defaults={'exercise_name': exercise.name},
    )
    log.exercise_name = exercise.name

    incoming = {
        'weight_kg': log.weight_kg,
        'reps_completed': log.reps_completed,
        'duration_seconds': log.duration_seconds,
    }
    # `weight` is accepted in the user's unit and converted here.
    if 'weight' in args:
        incoming['weight_kg'] = _weight_in(args['weight'], unit)
    if 'reps_completed' in args:
        incoming['reps_completed'] = args['reps_completed']
    if 'duration_seconds' in args:
        incoming['duration_seconds'] = args['duration_seconds']

    coerced = exercise_types.coerce_set_fields(exercise.measurement_type, incoming)
    log.weight_kg = coerced['weight_kg']
    log.reps_completed = coerced['reps_completed']
    log.duration_seconds = coerced['duration_seconds']
    if 'is_completed' in args:
        log.is_completed = bool(args['is_completed'])
    log.save()

    return {
        'logged': _set_log_out(log, unit),
        'measurement_type': exercise.measurement_type,
        'ignored_fields': [
            f for f in exercise_types.ALL_SET_FIELDS
            if not exercise_types.uses_field(exercise.measurement_type, f)
            and (f in args or (f == 'weight_kg' and 'weight' in args))
        ],
    }


def _tool_update_exercise_notes(user, args):
    exercise = get_object_or_404(
        Exercise, pk=args['exercise_id'], day__plan__user=user)
    exercise.notes = (args.get('notes') or '')[:2000]
    exercise.save(update_fields=['notes'])
    return {'updated': _exercise_out(exercise)}


def _tool_update_personal_notes(user, args):
    profile, _ = UserProfile.objects.get_or_create(user=user)
    profile.personal_notes = (args.get('notes') or '')[:4000]
    profile.save(update_fields=['personal_notes'])
    return {'personal_notes': profile.personal_notes}


def _tool_list_diet_plans(user, args):
    plans = DietPlan.objects.filter(user=user)
    if args.get('active_only'):
        plans = plans.filter(is_active=True)
    return {'plans': [{
        'id': str(p.id),
        'title': p.title,
        'is_active': p.is_active,
        'target_calories': p.target_calories,
        'protein_g': p.protein_g,
        'carbs_g': p.carbs_g,
        'fat_g': p.fat_g,
        'created_at': p.created_at.isoformat(),
    } for p in plans[:50]]}


def _tool_get_diet_plan(user, args):
    plan_id = args.get('plan_id')
    if plan_id:
        plan = get_object_or_404(DietPlan, pk=plan_id, user=user)
    else:
        plan = DietPlan.objects.filter(user=user, is_active=True).first()
        if plan is None:
            raise McpError('No active diet plan. Pass plan_id.')
    return {
        'id': str(plan.id),
        'title': plan.title,
        'target_calories': plan.target_calories,
        'protein_g': plan.protein_g,
        'carbs_g': plan.carbs_g,
        'fat_g': plan.fat_g,
        'meals': [{
            'day_number': m.day_number,
            'meal_type': m.meal_type,
            'name': m.name,
            'description': m.description,
            'calories': m.calories,
            'protein_g': m.protein_g,
            'carbs_g': m.carbs_g,
            'fat_g': m.fat_g,
        } for m in plan.meals.all()],
    }


def _tool_get_health_summary(user, args):
    limit = min(int(args.get('days') or 7), 60)
    rows = HealthDailySummary.objects.filter(user=user)[:limit]
    return {'days': [{
        'date': r.date.isoformat(),
        'steps': r.steps,
        'active_calories': r.active_calories,
        'resting_calories': r.resting_calories,
        'resting_heart_rate': r.resting_heart_rate,
        'sleep_hours': r.sleep_hours,
    } for r in rows]}


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------

def _obj(properties, required=None):
    return {
        'type': 'object',
        'properties': properties,
        'required': required or [],
    }


_STR = {'type': 'string'}
_INT = {'type': 'integer'}
_NUM = {'type': 'number'}
_BOOL = {'type': 'boolean'}

TOOLS = {
    'get_profile': {
        'description': 'Get the signed-in user\'s profile: body stats, goal, '
                       'experience level, dietary preference and personal notes. '
                       'Weights are in the user\'s preferred unit.',
        'schema': _obj({}),
        'handler': _tool_get_profile,
        'writes': False,
    },
    'list_workout_plans': {
        'description': 'List the user\'s workout plans, newest first.',
        'schema': _obj({'active_only': _BOOL}),
        'handler': _tool_list_workout_plans,
        'writes': False,
    },
    'get_workout_plan': {
        'description': 'Get one workout plan with its days and exercises. '
                       'Defaults to the active plan. Each exercise reports its '
                       'measurement_type and which fields may be logged for it.',
        'schema': _obj({'plan_id': _STR, 'week_number': _INT}),
        'handler': _tool_get_workout_plan,
        'writes': False,
    },
    'list_workout_sessions': {
        'description': 'List workout sessions (training logs), newest first.',
        'schema': _obj({'completed_only': _BOOL, 'limit': _INT}),
        'handler': _tool_list_workout_sessions,
        'writes': False,
    },
    'get_workout_session': {
        'description': 'Get one workout session with all of its set logs.',
        'schema': _obj({'session_id': _STR}, ['session_id']),
        'handler': _tool_get_workout_session,
        'writes': False,
    },
    'get_exercise_history': {
        'description': 'Full logged history for one exercise by name. Returns '
                       'measurement_type and the metric that matters for it, so '
                       'duration work is reported in seconds and never as weight.',
        'schema': _obj({'exercise_name': _STR, 'limit': _INT}, ['exercise_name']),
        'handler': _tool_get_exercise_history,
        'writes': False,
    },
    'list_diet_plans': {
        'description': 'List the user\'s diet plans.',
        'schema': _obj({'active_only': _BOOL}),
        'handler': _tool_list_diet_plans,
        'writes': False,
    },
    'get_diet_plan': {
        'description': 'Get one diet plan with its meals and macro targets. '
                       'Defaults to the active plan.',
        'schema': _obj({'plan_id': _STR}),
        'handler': _tool_get_diet_plan,
        'writes': False,
    },
    'get_health_summary': {
        'description': 'Recent daily health metrics: steps, calories, resting '
                       'heart rate and sleep.',
        'schema': _obj({'days': _INT}),
        'handler': _tool_get_health_summary,
        'writes': False,
    },

    # -- writes ------------------------------------------------------------
    'log_set': {
        'description': 'Record a set result in an active session. `weight` is in '
                       'the user\'s preferred unit. Fields that do not apply to '
                       'the exercise\'s measurement_type are ignored and reported '
                       'back in `ignored_fields`.',
        'schema': _obj({
            'session_id': _STR,
            'exercise_id': _STR,
            'set_number': _INT,
            'weight': _NUM,
            'reps_completed': _INT,
            'duration_seconds': _INT,
            'is_completed': _BOOL,
        }, ['session_id', 'exercise_id', 'set_number']),
        'handler': _tool_log_set,
        'writes': True,
    },
    'update_exercise_notes': {
        'description': 'Replace the coaching notes on one exercise.',
        'schema': _obj({'exercise_id': _STR, 'notes': _STR},
                       ['exercise_id', 'notes']),
        'handler': _tool_update_exercise_notes,
        'writes': True,
    },
    'update_personal_notes': {
        'description': 'Replace the user\'s personal notes (injuries, '
                       'preferences, context the AI should remember).',
        'schema': _obj({'notes': _STR}, ['notes']),
        'handler': _tool_update_personal_notes,
        'writes': True,
    },
}


def tool_definitions():
    """The `tools/list` payload, generated from TOOLS."""
    return [{
        'name': name,
        'description': spec['description'],
        'inputSchema': spec['schema'],
        'annotations': {
            'readOnlyHint': not spec['writes'],
        },
    } for name, spec in sorted(TOOLS.items())]


def call_tool(user, name, arguments):
    """
    Dispatch one tool call.

    Returns the handler's result dict. Raises McpError for anything the caller
    can fix (unknown tool, bad arguments, missing object).
    """
    spec = TOOLS.get(name)
    if spec is None:
        raise McpError('Unknown tool %r. Call tools/list for the available set.'
                       % name, code=METHOD_NOT_FOUND)

    args = arguments if isinstance(arguments, dict) else {}
    missing = [key for key in spec['schema'].get('required', []) if key not in args]
    if missing:
        raise McpError('Missing required argument(s): %s' % ', '.join(missing))

    return spec['handler'](user, args)
