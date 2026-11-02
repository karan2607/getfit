"""
The tool surface the chat agent sees.

Built ON TOP of `mcp.TOOLS` rather than beside it. That registry already
holds a description, a JSON Schema and a handler for all 22 tools, every
handler is user-scoped, and it is covered by 87 tests. A second
implementation for the agent would be a second set of scoping bugs, and the
two would drift the first time one gained a field.

So this module does three things and no more:

  1. translates an MCP tool definition into the provider's tool format
  2. decides which tools a given domain may see
  3. dispatches, and records the call in `ToolCallLog`

What a domain can reach
-----------------------
The router sends workouts to Claude and food to Gemini, so each side is given
only the tools it can act on. That is not cosmetic: a model offered
`log_meal` on a workout turn will eventually use it, and the cheapest way to
prevent a wrong tool call is for the tool not to be on the list.
"""

import time

from . import mcp

WORKOUT = 'workout'
FOOD = 'food'

# Which domain each MCP tool belongs to. Explicit rather than inferred from
# the name: `set_body_weight` reads as a workout tool and is body composition,
# and `get_profile` is needed by both.
_DOMAIN = {
    'get_profile': 'both',
    'update_personal_notes': 'both',

    'get_plan_week': WORKOUT,
    'propose_plan_change': WORKOUT,

    'list_workout_plans': WORKOUT,
    'get_workout_plan': WORKOUT,
    'get_todays_workout': WORKOUT,
    'list_workout_sessions': WORKOUT,
    'get_workout_session': WORKOUT,
    'get_exercise_history': WORKOUT,
    'log_set': WORKOUT,
    'update_exercise_notes': WORKOUT,
    'get_health_summary': WORKOUT,
    'get_health_series': WORKOUT,
    'log_health_day': WORKOUT,
    'update_health_day': WORKOUT,
    'delete_health_day': WORKOUT,
    'log_health_workout': WORKOUT,
    'delete_health_workout': WORKOUT,

    'list_diet_plans': FOOD,
    'get_diet_plan': FOOD,
    'get_meal_logs': FOOD,
    'log_meal': FOOD,
    'set_body_weight': FOOD,
}


def _spec(name):
    """A tool's definition, from either registry."""
    return _PLAN_TOOL_SPECS.get(name) or mcp.TOOLS.get(name)


def tools_for(domain):
    """The tool names this domain may call."""
    return sorted(
        name for name, assigned in _DOMAIN.items()
        if _spec(name) is not None and assigned in (domain, 'both'))


def anthropic_tools(domain):
    """
    MCP definitions in Anthropic's tool format.

    `strict: True` on every tool, with `additionalProperties: false`: it is
    what guarantees the arguments validate against the schema, and it is how
    determinism is obtained now that `temperature` is rejected outright.
    """
    out = []
    for name in tools_for(domain):
        spec = _spec(name)
        schema = dict(spec['schema'])
        schema['additionalProperties'] = False
        schema.setdefault('required', [])
        out.append({
            'name': name,
            'description': spec['description'],
            'input_schema': schema,
            'strict': True,
        })
    return out


def _summarise(result):
    """
    A short description of a result, never the result itself.

    Storing full payloads would duplicate the database into a log table and
    grow without bound; the arguments are what answer "what did it do".
    """
    if isinstance(result, dict):
        for key in ('detail', 'status', 'message'):
            if isinstance(result.get(key), str):
                return result[key][:500]
        return ('keys: %s' % ', '.join(sorted(result)[:8]))[:500]
    if isinstance(result, list):
        return '%d item(s)' % len(result)
    return str(result)[:500]


def run_tool(user, name, arguments, session=None, model=''):
    """
    Dispatch one tool call and record it.

    Returns (result, error). A refusal or failure comes back as an error
    string rather than an exception, because the agent loop has to hand the
    model a `tool_result` either way -- dropping a failed call silently
    teaches it the call succeeded.
    """
    from .models import ToolCallLog

    spec = _spec(name)
    started = time.monotonic()
    result = None
    error = ''

    if spec is None:
        error = 'unknown tool: %s' % name
    else:
        try:
            result = spec['handler'](user, arguments or {})
        except mcp.McpError as exc:
            error = str(exc)
        except Exception as exc:                        # noqa: BLE001
            error = 'tool failed: %s' % exc

    try:
        ToolCallLog.objects.create(
            user=user,
            session=session,
            tool=name[:64],
            arguments=arguments or {},
            result_summary='' if error else _summarise(result),
            model=(model or '')[:64],
            ok=not error,
            error=error[:300],
            duration_ms=int((time.monotonic() - started) * 1000),
        )
    except Exception:                                   # noqa: BLE001
        # An audit-log failure must not fail the call it describes.
        pass

    return result, error


# ---------------------------------------------------------------------------
# Plan mutation
# ---------------------------------------------------------------------------
#
# These are NOT in `mcp.TOOLS` and deliberately so: MCP exposes read tools and
# narrow writes, and the plan-mutation flow needs a draft the user confirms
# before anything is applied. The agent may PROPOSE; only the user's
# confirmation applies.
#
# This is also what makes a week-scoped prompt safe. While chat edited plans by
# re-emitting the whole thing as JSON, it had to be SHOWN the whole thing --
# ~32,000 tokens in and the same back out to change one exercise, with every
# re-emitted day a chance to drop something. An agent that edits through a
# draft never reproduces the weeks it did not touch, so it never needs to see
# them.

PLAN_TOOLS = ('get_plan_week', 'propose_plan_change')


def _tool_get_plan_week(user, args):
    """One week of a plan, plus the phase map."""
    from .models import WorkoutPlan
    from .plan_changes import snapshot_plan

    plan = WorkoutPlan.objects.filter(
        pk=args.get('plan_id'), user=user).first()
    if plan is None:
        raise mcp.McpError('plan not found')
    week = args.get('week_number') or plan.current_week or 1
    snapshot = snapshot_plan(plan, week_numbers=[int(week)])
    return {
        'plan_id': str(plan.id),
        'title': plan.title,
        'week_number': int(week),
        'weeks_total': plan.duration_weeks,
        # The phase map travels with every week. Without it a week-local edit
        # cannot tell accumulation from a deload and will happily add volume
        # to the week that exists to remove it.
        'program_outline': plan.program_outline or [],
        'days': snapshot['weeks'].get(str(week), []),
    }


def _tool_propose_plan_change(user, args):
    """
    Record a draft for the user to confirm. Applies NOTHING.

    Returns the change_id the client confirms against, so the model cannot
    apply its own proposal even if it decides it should.
    """
    from .models import PlanChange, PlanSnapshot, WorkoutPlan
    from .plan_changes import library_warnings, plan_digest, snapshot_plan

    plan = WorkoutPlan.objects.filter(
        pk=args.get('plan_id'), user=user).first()
    if plan is None:
        raise mcp.McpError('plan not found')

    changes = args.get('changes') or []
    if not isinstance(changes, list) or not changes:
        raise mcp.McpError('changes must be a non-empty list')

    snapshot = snapshot_plan(plan)
    change = PlanChange.objects.create(
        plan=plan, draft=changes,
        keep_list=args.get('keep_list') or [],
        warnings=library_warnings(changes, user=user),
        base_hash=plan_digest(snapshot))
    PlanSnapshot.objects.create(
        plan=plan, change_id=change.id, phase=PlanSnapshot.BEFORE,
        data=snapshot)

    return {
        'change_id': str(change.id),
        'needs_confirmation': True,
        'applied': False,
        'warnings': change.warnings,
        'draft': change.draft,
    }


_PLAN_TOOL_SPECS = {
    'get_plan_week': {
        'description': (
            'Read ONE week of a workout plan, with the program phase map. '
            'Use this instead of asking for the whole plan: a week is what '
            'an edit touches, and the phase map says whether a week is '
            'accumulation, intensification or a deload.'),
        'schema': {
            'type': 'object',
            'properties': {
                'plan_id': {'type': 'string'},
                'week_number': {'type': 'integer'},
            },
            'required': ['plan_id'],
        },
        'handler': _tool_get_plan_week,
        'writes': False,
    },
    'propose_plan_change': {
        'description': (
            'Propose a change to a plan for the user to confirm. This applies '
            'NOTHING. Describe each change as the day and the exercise name, '
            'and list in keep_list any exercise that must survive the change. '
            'The user confirms the returned change_id; you cannot apply it '
            'yourself.'),
        'schema': {
            'type': 'object',
            'properties': {
                'plan_id': {'type': 'string'},
                'changes': {
                    'type': 'array',
                    'items': {
                        'type': 'object',
                        'properties': {
                            'day': {'type': 'string'},
                            'name': {'type': 'string'},
                            'week': {'type': 'integer'},
                            'action': {'type': 'string'},
                        },
                        'required': ['day', 'name'],
                    },
                },
                'keep_list': {'type': 'array', 'items': {'type': 'string'}},
            },
            'required': ['plan_id', 'changes'],
        },
        'handler': _tool_propose_plan_change,
        'writes': True,
    },
}
